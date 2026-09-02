"""Learned recovery probabilities -- the fitted posteriors, read at decision time.

scripts/fit_recovery.py fits one Beta posterior per cell from exploration-mode
training runs and writes config/learned_recovery.yaml (provenance:
docs/learning_data.md; analysis: docs/learning_findings.md). This module is the
ONLY reader of that file inside engine/, and it reads it through
engine/config.py's cached loader like every other config file -- no second
loading mechanism.

Behind config/rules.yaml's `learning.enabled` master switch, which SHIPS OFF.

FLAG SEMANTICS (config/rules.yaml, learning block):

    enabled: false
        engine/negotiation.py uses its hand-typed recovery_probability grid.
        Nothing here is consulted. Behaviour is byte-identical to before this
        module existed -- and the ev_mode: off snapshot tests still pass
        unchanged, because they never reach the EV path at all.

    enabled: true  AND  brain.ev_mode not on
        check_config() raises LearningConfigError at startup. Learned
        probabilities feed ONLY the EV formula, so with EV off the switch
        does nothing -- rejected, never silently ignored. ("on" here is
        config.ev_mode_on: YAML's bare `on` (a boolean) and the string "on"
        both count.)

    enabled: true  AND  brain.ev_mode on  AND  learning.mode: offline
        engine/negotiation.py's recovery_probability() takes each cell's
        posterior MEAN from config/learned_recovery.yaml in place of its
        hand-typed value. A cell MISSING from the YAML falls back to the
        hand-typed value and logs the fallback once; a missing cell never
        stops a run.

    enabled: true  AND  brain.ev_mode on  AND  learning.mode: online
        OnlineLearner does Thompson sampling instead of using the mean:
        engine/negotiation.py's recovery_probability() draws once from each
        eligible cell's Beta(alpha, beta) posterior and feeds that SAMPLE to
        the EV formula. The posteriors start from config/learned_recovery.yaml
        (warm) or from uniform Beta(1,1) if learning.cold_start: true, and are
        updated IN MEMORY as the run's payment attribution resolves each action
        (sim/run_sim.py drives the attribution and calls OnlineLearner.update()).
        At end of run the final posteriors are written to
        report/out/learned_posteriors_final.yaml -- a record, never read back,
        never overwriting the input file. The sampling RNG is sim/run_sim.py's
        own seeded _rng(seed, invoice, day, "thompson"), handed in via
        online_sampling(); this module never creates an RNG, so an online run
        is as reproducible as any other.

CELL RESOLUTION -- the ONE resolver, _resolve_cell(), used for both offline
lookups and online sample/update:

  * payment_plan / counter_settle  -> recovery.<quadrant>.<action_kind>, a
    flat cell. These map 1:1 from what EV selected to what was executed.
  * soft_nudge / firm / legal_facts -> recovery.<quadrant>.send.<tier>, a
    per-DELIVERED-RUNG SEND cell. scripts/fit_recovery.py groups a SEND by the
    rung the escalation walk actually delivered it at, NOT by the label EV
    nominally selected -- brain.py overrides that label on ~56% of SENDs, so
    fitting on the label would fit a confounded target (see
    docs/learning_findings.md). The tier names ARE the ladder's rung names
    (config/rules.yaml: rung 1 = soft_nudge, 2 = firm, 3 = legal_facts), read
    from engine.rungs, so the three negotiation actions resolve by identity.
  * a quadrant with no rung-1 SENDs in training has no soft_nudge cell -- that
    request falls back to the hand-typed grid (logged once), same as any other
    absent cell.

The rung-1 (soft_nudge) SEND cells are thin (n = 9-79): the walk rarely stops
at rung 1. Their ci95_width is wide and that is the signal to distrust the
point estimate -- engine/negotiation.py still uses the mean/sample, it does
not special-case thinness.

MONEY-SAFETY NOTE, same as engine/negotiation.py's: every number here is
advisory arithmetic over a hypothetical action. It is compared, never written
to ledger state.
"""

from __future__ import annotations

import copy
import random
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from engine import rungs
from engine.config import ev_mode_on, learned_recovery, rules

#: 100 percentage points per unit probability -- the hand-typed grid in
#: config/rules.yaml is written as 0-100, the posteriors as 0-1.
_PERCENT = 100

#: engine.brain.Action.kind values, spelled out by value: engine.brain imports
#: engine.negotiation which imports this module, so importing brain back would
#: be a cycle.
_KIND_SEND = "send"
_KIND_PLANS = frozenset({"payment_plan", "counter_settle"})

#: (quadrant, action_kind) pairs already reported as falling back to the
#: hand-typed value, so the notice is logged once rather than every call.
_fallback_logged: set[tuple[str, str]] = set()

_DUMP_HEADER = """\
# report/out/learned_posteriors_final.yaml
#
# GENERATED at the end of an online (learning.mode: online) simulation run, by
# engine/learning.py's OnlineLearner. Same nested schema as
# config/learned_recovery.yaml (schema v2). A RECORD of where the
# Thompson-sampling posteriors ended up -- NOT read back, and it does NOT
# overwrite config/learned_recovery.yaml.
"""


class LearningConfigError(RuntimeError):
    """config/rules.yaml's learning block is internally inconsistent.

    Raised at startup (see check_config), never mid-run: a run that has begun
    deciding must not stop over configuration.
    """


def enabled() -> bool:
    """config/rules.yaml learning.enabled -- the master switch. Ships false."""
    return bool(rules()["learning"]["enabled"])


def mode() -> str:
    """config/rules.yaml learning.mode -- "offline" (posterior mean) or
    "online" (Thompson sampling + in-run updates)."""
    return str(rules()["learning"]["mode"])


def cold_start() -> bool:
    """config/rules.yaml learning.cold_start -- online mode starts from uniform
    Beta(1,1) priors instead of the fitted warm start. Defaults false."""
    return bool(rules()["learning"].get("cold_start", False))


def check_config(config: dict[str, Any] | None = None) -> None:
    """Fail fast on a meaningless learning configuration. Call once, at startup.

    Does nothing when learning.enabled is false -- the shipped state, and what
    every test that does not opt in exercises. When it IS enabled:

      * brain.ev_mode not "on"  -- learned probabilities feed only the EV
        formula, so the switch would do nothing;
      * learning.mode not one of {offline, online}  -- a typo;
      * config/learned_recovery.yaml missing or unparseable  -- enabled with
        no cell structure to start from (warm OR cold start both need it).
    """
    settings = config if config is not None else rules()
    if not bool(settings["learning"]["enabled"]):
        return

    if not ev_mode_on(settings):
        raise LearningConfigError(
            "config/rules.yaml has learning.enabled: true but brain.ev_mode is not "
            "on. Learned recovery probabilities only feed the EV formula, so this "
            "combination does nothing. Set brain.ev_mode: on to use the learned "
            "numbers, or learning.enabled: false to keep the hand-typed grid."
        )

    learning_mode = str(settings["learning"].get("mode", "offline"))
    if learning_mode not in ("offline", "online"):
        raise LearningConfigError(
            f"config/rules.yaml has learning.mode: {learning_mode!r}; "
            f"expected 'offline' or 'online'."
        )

    try:
        learned_recovery()
    except FileNotFoundError as exc:
        raise LearningConfigError(
            "config/rules.yaml has learning.enabled: true but "
            "config/learned_recovery.yaml is missing. Run scripts/fit_recovery.py "
            "to generate it, or set learning.enabled: false."
        ) from exc
    except yaml.YAMLError as exc:
        raise LearningConfigError(
            "config/rules.yaml has learning.enabled: true but "
            "config/learned_recovery.yaml does not parse. Re-run "
            "scripts/fit_recovery.py, or set learning.enabled: false."
        ) from exc


# --------------------------------------------------------------------------
# cell resolution -- the one resolver
# --------------------------------------------------------------------------

def _send_tier_names() -> frozenset[str]:
    """config/rules.yaml's ladder names for the buyer-facing rungs --
    soft_nudge / firm / legal_facts. Read from engine.rungs (the ladder
    reader), never restated here: these are the negotiation actions whose
    cell lives nested under recovery.<quadrant>.send.<tier>."""
    return frozenset(entry["name"] for entry in rungs.all_rungs()
                     if entry["id"] in rungs.BUYER_FACING_RUNGS)


def _resolve_cell(recovery: dict[str, Any], quadrant: str, action_kind: str) -> Any:
    """The cell dict for (quadrant, action_kind) inside `recovery`, or None.

    The ONE resolver, used for offline lookups AND online sample/update:
    SEND tiers -> recovery[quadrant]["send"][tier]; payment_plan /
    counter_settle -> recovery[quadrant][action_kind].
    """
    quad = recovery.get(quadrant, {})
    if action_kind in _send_tier_names():
        return (quad.get("send") or {}).get(action_kind)
    return quad.get(action_kind)


def _cell(quadrant: str, action_kind: str) -> Any:
    """_resolve_cell against the file-backed offline posteriors."""
    return _resolve_cell(_learned_cells(), quadrant, action_kind)


def delivered_action_kind(executed_kind: str, rung: int) -> str | None:
    """Which cell key an EXECUTED action's resolved attribution belongs to.

    A SEND -> its DELIVERED ladder tier (soft_nudge/firm/legal_facts, via
    engine.rungs): the escalation walk, not the EV label, chose the rung, so
    the payment (or its absence) informs the tier that actually went out.
    payment_plan / counter_settle -> themselves. handoff / wait / anything
    else -> None: no cell, excluded exactly as the offline fit excludes
    handoff rows.
    """
    if executed_kind == _KIND_SEND:
        return next((entry["name"] for entry in rungs.all_rungs()
                     if entry["id"] == rung and entry["id"] in rungs.BUYER_FACING_RUNGS),
                    None)
    if executed_kind in _KIND_PLANS:
        return executed_kind
    return None


def _learned_cells() -> dict[str, dict[str, Any]]:
    """The `recovery` block of config/learned_recovery.yaml, or {} if the file
    is missing or unreadable -- so every cell falls back rather than a run
    stopping over it. check_config() surfaces a missing/broken file loudly at
    startup; this is the belt-and-braces for a caller that skipped that."""
    try:
        return learned_recovery().get("recovery", {}) or {}
    except (OSError, yaml.YAMLError):
        return {}


def has_cell(quadrant: str, action_kind: str) -> bool:
    """True when config/learned_recovery.yaml carries a fitted mean for this
    cell -- i.e. the offline recovery_probability() returns a learned number
    rather than fall back. Lets a caller label the source honestly in its
    audit trail without re-doing the lookup."""
    cell = _cell(quadrant, action_kind)
    return isinstance(cell, dict) and cell.get("mean") is not None


def _hand_typed(quadrant: str, action_kind: str) -> float:
    """The hand-typed negotiation.recovery_probability value for this cell,
    scaled from 0-100 to 0-1. This is the fallback, and for any action in
    engine.negotiation.ACTIONS it is always present."""
    grid = rules()["negotiation"]["recovery_probability"]
    return float(grid[quadrant][action_kind]) / _PERCENT


def recovery_probability(quadrant: str, action_kind: str) -> float:
    """Offline posterior-MEAN P(recover) for this cell, in [0.0, 1.0].

    Falls back to config/rules.yaml's hand-typed
    negotiation.recovery_probability value (scaled to 0-1) when the cell is
    absent from config/learned_recovery.yaml, logging that fallback once per
    (quadrant, action_kind). Never raises over a missing cell. Online mode
    uses OnlineLearner.sample() / sample_probability() instead of this.
    """
    cell = _cell(quadrant, action_kind)
    if isinstance(cell, dict) and cell.get("mean") is not None:
        return float(cell["mean"])

    key = (quadrant, action_kind)
    if key not in _fallback_logged:
        _fallback_logged.add(key)
        print(
            f"engine.learning: no learned cell for ({quadrant}, {action_kind}) in "
            f"config/learned_recovery.yaml -- falling back to the hand-typed "
            f"negotiation.recovery_probability value",
            file=sys.stderr,
        )
    return _hand_typed(quadrant, action_kind)


def fallbacks_logged() -> frozenset[tuple[str, str]]:
    """Which cells have fallen back to the hand-typed value so far this process.
    For inspection and tests; not part of a decision."""
    return frozenset(_fallback_logged)


# --------------------------------------------------------------------------
# online learning -- Thompson sampling + in-run posterior updates
# --------------------------------------------------------------------------

def _build_posteriors(recovery: dict[str, Any], cold: bool) -> dict[str, Any]:
    """A deep, mutable copy of the fitted `recovery` block keeping only
    alpha/beta (+ delivered_rung on the SEND tiers). `cold` resets every
    alpha/beta to 1 (uniform Beta(1,1)) while keeping the SAME set of cells --
    the structure always comes from config/learned_recovery.yaml."""
    def one(cell: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "delivered_rung" in cell:
            out["delivered_rung"] = int(cell["delivered_rung"])
        out["alpha"] = 1 if cold else int(cell["alpha"])
        out["beta"] = 1 if cold else int(cell["beta"])
        return out

    posteriors: dict[str, Any] = {}
    for quadrant, cells in recovery.items():
        q: dict[str, Any] = {}
        for key, val in cells.items():
            if key == "send":
                q["send"] = {tier: one(tier_cell) for tier, tier_cell in val.items()}
            else:
                q[key] = one(val)
        posteriors[quadrant] = q
    return posteriors


class OnlineLearner:
    """Beta posteriors updated in memory over one simulation run.

    Sampled from (Thompson) for action selection, updated as the run's payment
    attribution resolves each action. Never touches config/learned_recovery.yaml.
    """

    def __init__(self, *, cold_start: bool = False) -> None:
        self.cold_start = bool(cold_start)
        recovery = learned_recovery().get("recovery", {})   # FileNotFoundError if absent
        self._posteriors = _build_posteriors(recovery, self.cold_start)
        self._n_success = 0
        self._n_failure = 0

    # -- action selection ------------------------------------------------

    def sample(self, quadrant: str, action_kind: str, rng: random.Random) -> float | None:
        """One Thompson draw from this cell's Beta(alpha, beta) posterior, in
        [0, 1]. None when there is no cell -- the caller falls back to the
        hand-typed grid, exactly as the offline path does. `rng` MUST be a
        seeded stream; this method never creates one."""
        cell = _resolve_cell(self._posteriors, quadrant, action_kind)
        if not isinstance(cell, dict):
            return None
        return rng.betavariate(cell["alpha"], cell["beta"])

    # -- learning ------------------------------------------------------

    def update(self, quadrant: str, action_kind: str, *, success: bool) -> None:
        """Fold one resolved attribution into this cell: alpha += 1 on a
        recovered payment, beta += 1 on a horizon that elapsed with nothing
        recovered. No-op when there is no cell for (quadrant, action_kind) --
        the offline fit had none either. `action_kind` is a resolved cell key
        (a SEND tier, or payment_plan / counter_settle); callers holding an
        executed Action.kind + rung pass it through delivered_action_kind()
        first."""
        cell = _resolve_cell(self._posteriors, quadrant, action_kind)
        if not isinstance(cell, dict):
            return
        if success:
            cell["alpha"] += 1
            self._n_success += 1
        else:
            cell["beta"] += 1
            self._n_failure += 1

    # -- output ------------------------------------------------------

    @property
    def updates_applied(self) -> dict[str, int]:
        return {"successes": self._n_success, "failures": self._n_failure}

    def snapshot(self) -> dict[str, Any]:
        """A deep copy of the current posteriors, in the nested v2 shape. For
        reproducibility checks and the run report."""
        return copy.deepcopy(self._posteriors)

    def dump(self, path: Path, *, seed: int | None = None) -> Path:
        """Write the final posteriors to `path`, in config/learned_recovery.yaml's
        nested v2 schema. Never overwrites the input file."""
        from scipy.stats import beta as beta_dist   # lazy: only the dump needs scipy

        def cell_out(cell: dict[str, Any]) -> dict[str, Any]:
            a, b = int(cell["alpha"]), int(cell["beta"])
            lo, hi = beta_dist.ppf([0.025, 0.975], a, b)
            out: dict[str, Any] = {}
            if "delivered_rung" in cell:
                out["delivered_rung"] = int(cell["delivered_rung"])
            out.update({
                "alpha": a,
                "beta": b,
                "mean": round(a / (a + b), 4),
                "ci95_width": round(float(hi - lo), 4),
                "observations": a + b - 2,
                "successes": a - 1,
                "failures": b - 1,
            })
            return out

        recovery: dict[str, Any] = {}
        for quadrant in sorted(self._posteriors):
            cells = self._posteriors[quadrant]
            q: dict[str, Any] = {}
            if "send" in cells:
                grp = cells["send"]
                q["send"] = {tier: cell_out(grp[tier])
                             for tier in sorted(grp, key=lambda t: grp[t].get("delivered_rung", 0))}
            for key in sorted(k for k in cells if k != "send"):
                q[key] = cell_out(cells[key])
            recovery[quadrant] = q

        payload = {
            "version": 2,
            "meta": {
                "generated": date.today().isoformat(),
                "source": "online Thompson-sampling update during a simulation run "
                          "(engine.learning.OnlineLearner) -- a record, not read back",
                "seed": seed,
                "start": ("cold -- uniform Beta(1,1) priors" if self.cold_start
                          else "warm -- config/learned_recovery.yaml"),
                "updates_applied": self.updates_applied,
            },
            "recovery": recovery,
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False, width=88)
        path.write_text(_DUMP_HEADER + "\n" + body, encoding="utf-8")
        return path


#: The online learner + its seeded RNG for the CURRENT decision, set by
#: online_sampling() for the duration of one engine.brain.decide() call. None
#: everywhere else, which is what keeps offline mode and every non-online run
#: byte-identical.
_active_learner: OnlineLearner | None = None
_active_rng: random.Random | None = None


@contextmanager
def online_sampling(learner: OnlineLearner | None, rng: random.Random | None):
    """Make `learner`'s Thompson samples the base rate for
    engine.negotiation.recovery_probability(), for the duration of the block.

    A None learner is a no-op -- this is how every non-online run stays
    unchanged. `rng` MUST be a seeded stream: sim/run_sim.py passes its own
    per-(seed, invoice, day) _rng(); this module never constructs one.
    """
    global _active_learner, _active_rng
    if learner is None:
        yield
        return
    previous = (_active_learner, _active_rng)
    _active_learner, _active_rng = learner, rng
    try:
        yield
    finally:
        _active_learner, _active_rng = previous


def sample_probability(quadrant: str, action_kind: str) -> float | None:
    """The active online learner's Thompson sample for this cell in [0, 1], or
    None when no online learner is active or the cell does not exist."""
    if _active_learner is None:
        return None
    return _active_learner.sample(quadrant, action_kind, _active_rng)
