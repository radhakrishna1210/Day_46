"""Tests for engine/learning.py's online mode -- Thompson sampling + in-run
Beta updates (config/rules.yaml learning.mode: online).

The two guarantees that matter:

  * REPRODUCIBLE -- the same seed produces the same final posteriors every
    time, because the only randomness is sim/run_sim.py's own seeded
    per-(seed, invoice, day) stream;
  * IT ACTUALLY LEARNS -- a cell that keeps seeing recovered payments has a
    rising posterior mean.

Both are checked on a nested `send/<tier>` cell, not just a flat
payment_plan / counter_settle one, because the tier resolution is the part
most likely to break.
"""

from __future__ import annotations

import random

import pytest
import yaml

from engine import config as cfg
from engine import learning
from engine import negotiation as neg

SEED = 42
DAYS = 25


def _learner_config(**over: object) -> dict:
    settings = yaml.safe_load(cfg.RULES_PATH.read_text(encoding="utf-8"))
    settings["learning"]["enabled"] = True
    settings["learning"]["mode"] = "online"
    settings["brain"]["ev_mode"] = "on"
    settings["learning"].update(over)
    return settings


@pytest.fixture(autouse=True)
def _isolate_fallback_log():
    saved = set(learning._fallback_logged)
    learning._fallback_logged.clear()
    yield
    learning._fallback_logged.clear()
    learning._fallback_logged.update(saved)


@pytest.fixture
def online_config(tmp_path, monkeypatch):
    """Point engine.config at a temp rules.yaml with online learning on, and
    force a cache re-read. Every `from engine.config import rules` caller shares
    the one lru_cache function object, so this reaches all of them."""
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(_learner_config()), encoding="utf-8")
    monkeypatch.setattr(cfg, "RULES_PATH", path)
    cfg.rules.cache_clear()
    yield
    cfg.rules.cache_clear()


def _mean(learner: learning.OnlineLearner, quadrant: str, action_kind: str) -> float:
    cell = learning._resolve_cell(learner._posteriors, quadrant, action_kind)
    return cell["alpha"] / (cell["alpha"] + cell["beta"])


def _all_cells(posteriors: dict) -> list[tuple[str, str, dict]]:
    out = []
    for quadrant, cells in posteriors.items():
        for key, val in cells.items():
            if key == "send":
                out.extend((quadrant, tier, c) for tier, c in val.items())
            else:
                out.append((quadrant, key, val))
    return out


# --------------------------------------------------------------------------
# reproducibility -- the headline guarantee
# --------------------------------------------------------------------------

def test_same_seed_produces_identical_posteriors_twice(online_config) -> None:
    from sim import run_sim

    first = run_sim.run_agent(SEED, days=DAYS, online=True)
    second = run_sim.run_agent(SEED, days=DAYS, online=True)

    assert first["online_posteriors"] == second["online_posteriors"]
    assert first["online_updates"] == second["online_updates"]
    # and the run actually learned something -- otherwise "identical" is trivial
    total = first["online_updates"]["successes"] + first["online_updates"]["failures"]
    assert total > 0


def test_a_different_seed_moves_the_posteriors(online_config) -> None:
    from sim import run_sim

    a = run_sim.run_agent(SEED, days=DAYS, online=True)["online_posteriors"]
    b = run_sim.run_agent(SEED + 1, days=DAYS, online=True)["online_posteriors"]
    assert a != b


# --------------------------------------------------------------------------
# it actually learns -- rising mean on repeated successes
# --------------------------------------------------------------------------

def test_repeated_successes_raise_a_nested_send_tier_cells_mean(online_config) -> None:
    learner = learning.OnlineLearner()
    before = _mean(learner, "good_customer", "firm")   # -> recovery.good_customer.send.firm
    for _ in range(60):
        learner.update("good_customer", "firm", success=True)
    after = _mean(learner, "good_customer", "firm")
    assert after > before
    assert learner.updates_applied == {"successes": 60, "failures": 0}


def test_repeated_failures_lower_a_flat_payment_plan_cells_mean(online_config) -> None:
    learner = learning.OnlineLearner()
    before = _mean(learner, "cash_flow_problem", "payment_plan")
    for _ in range(60):
        learner.update("cash_flow_problem", "payment_plan", success=False)
    after = _mean(learner, "cash_flow_problem", "payment_plan")
    assert after < before


def test_a_send_tier_update_lands_in_the_nested_cell_not_a_flat_one(online_config) -> None:
    learner = learning.OnlineLearner()
    alpha_before = learner._posteriors["good_customer"]["send"]["firm"]["alpha"]
    learner.update("good_customer", "firm", success=True)
    assert learner._posteriors["good_customer"]["send"]["firm"]["alpha"] == alpha_before + 1
    # nothing flat was created
    assert "firm" not in {k for k in learner._posteriors["good_customer"] if k != "send"}


def test_update_is_a_noop_for_a_cell_that_was_never_fitted(online_config) -> None:
    learner = learning.OnlineLearner()
    before = learner.snapshot()
    learner.update("can_pay_but_wont", "soft_nudge", success=True)   # no rung-1 sends there
    learner.update("good_customer", "human_handoff", success=False)  # no cell at all
    assert learner.snapshot() == before
    assert learner.updates_applied == {"successes": 0, "failures": 0}


# --------------------------------------------------------------------------
# warm vs cold start
# --------------------------------------------------------------------------

def test_warm_start_copies_the_fitted_alpha_beta(online_config) -> None:
    from engine.config import learned_recovery

    learner = learning.OnlineLearner()
    fitted = learned_recovery()["recovery"]["good_customer"]["send"]["firm"]
    cell = learner._posteriors["good_customer"]["send"]["firm"]
    assert (cell["alpha"], cell["beta"]) == (fitted["alpha"], fitted["beta"])
    assert cell["delivered_rung"] == 2


def test_cold_start_resets_every_cell_to_a_uniform_prior(online_config) -> None:
    warm = learning.OnlineLearner()
    cold = learning.OnlineLearner(cold_start=True)

    for _q, _a, cell in _all_cells(cold._posteriors):
        assert (cell["alpha"], cell["beta"]) == (1, 1)
    # same set of cells, though -- structure always comes from the fitted file
    warm_keys = {(q, a) for q, a, _c in _all_cells(warm._posteriors)}
    cold_keys = {(q, a) for q, a, _c in _all_cells(cold._posteriors)}
    assert warm_keys == cold_keys


# --------------------------------------------------------------------------
# Thompson sampling -- seeded, in [0, 1], drives the EV formula
# --------------------------------------------------------------------------

def test_sampling_is_deterministic_for_a_given_rng_seed(online_config) -> None:
    learner = learning.OnlineLearner()
    a = learner.sample("good_customer", "firm", random.Random("stream"))
    b = learner.sample("good_customer", "firm", random.Random("stream"))
    assert a == b
    assert 0.0 <= a <= 1.0


def test_sample_returns_none_for_an_absent_cell(online_config) -> None:
    learner = learning.OnlineLearner()
    assert learner.sample("can_pay_but_wont", "soft_nudge", random.Random("x")) is None
    assert learner.sample("good_customer", "wait", random.Random("x")) is None


def test_online_context_makes_negotiation_use_a_thompson_sample(online_config) -> None:
    learner = learning.OnlineLearner()
    with learning.online_sampling(learner, random.Random("decision")):
        result = neg.recovery_probability("good_customer", "firm")
    assert result["breakdown"][0]["detail"].startswith("online Thompson sample")
    assert 0 <= result["probability"] <= 100


def test_online_context_is_scoped_and_a_noop_when_the_learner_is_none(online_config) -> None:
    # entering with a None learner must not touch negotiation's base-rate source
    with learning.online_sampling(None, None):
        result = neg.recovery_probability("good_customer", "firm")
    assert not result["breakdown"][0]["detail"].startswith("online Thompson sample")
    # and after a real context exits, sampling is off again
    with learning.online_sampling(OnlineOne := learning.OnlineLearner(), random.Random("k")):
        pass
    assert learning.sample_probability("good_customer", "firm") is None


# --------------------------------------------------------------------------
# observations + audit_method -- provenance for the audit trail
# --------------------------------------------------------------------------

def test_observations_track_the_live_posterior(online_config) -> None:
    learner = learning.OnlineLearner()
    start = learner.observations("good_customer", "firm")
    assert start is not None and start > 0            # warm start carries the fit's count
    for _ in range(5):
        learner.update("good_customer", "firm", success=True)
    for _ in range(3):
        learner.update("good_customer", "firm", success=False)
    assert learner.observations("good_customer", "firm") == start + 8
    assert learner.observations("good_customer", "human_handoff") is None


def test_cold_start_observations_begin_at_zero(online_config) -> None:
    cold = learning.OnlineLearner(cold_start=True)
    assert cold.observations("good_customer", "firm") == 0
    cold.update("good_customer", "firm", success=True)
    assert cold.observations("good_customer", "firm") == 1


def test_audit_method_is_thompson_sampling_only_inside_an_online_context(online_config) -> None:
    learner = learning.OnlineLearner()
    assert learning.audit_method("good_customer", "firm") == "posterior_mean"
    with learning.online_sampling(learner, random.Random("d")):
        assert learning.audit_method("good_customer", "firm") == "thompson_sampling"
        assert learning.observations("good_customer", "firm") == \
            learner.observations("good_customer", "firm")
    assert learning.audit_method("good_customer", "firm") == "posterior_mean"


# --------------------------------------------------------------------------
# the dumped file -- report/out/learned_posteriors_final.yaml
# --------------------------------------------------------------------------

def test_dump_writes_the_nested_v2_schema(online_config, tmp_path) -> None:
    learner = learning.OnlineLearner()
    for _ in range(5):
        learner.update("good_customer", "firm", success=True)
    out = tmp_path / "learned_posteriors_final.yaml"
    learner.dump(out, seed=SEED)

    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert doc["version"] == 2
    assert doc["meta"]["seed"] == SEED
    assert doc["meta"]["updates_applied"] == {"successes": 5, "failures": 0}

    firm = doc["recovery"]["good_customer"]["send"]["firm"]
    assert firm["delivered_rung"] == 2
    assert set(firm) >= {"alpha", "beta", "mean", "ci95_width", "observations",
                         "successes", "failures"}
    assert firm["successes"] == firm["alpha"] - 1
    # payment_plan is still a flat cell in the dump
    assert "delivered_rung" not in doc["recovery"]["good_customer"]["payment_plan"]


def test_dump_does_not_touch_config_learned_recovery(online_config, tmp_path) -> None:
    before = cfg.LEARNED_RECOVERY_PATH.read_bytes()
    learning.OnlineLearner().dump(tmp_path / "out.yaml", seed=1)
    assert cfg.LEARNED_RECOVERY_PATH.read_bytes() == before


def test_a_full_online_run_dumps_to_report_out(online_config, tmp_path) -> None:
    from sim import run_sim

    dest = tmp_path / "final.yaml"
    report = run_sim.run_agent(SEED, days=DAYS, online=True, online_posteriors_out=dest)
    assert dest.exists()
    assert report["online_posteriors_path"] == str(dest)
    doc = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert doc["version"] == 2 and "recovery" in doc


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_check_config_accepts_online_mode() -> None:
    learning.check_config(_check_cfg(mode="online"))


def test_check_config_still_rejects_online_without_ev_mode() -> None:
    with pytest.raises(learning.LearningConfigError, match="brain.ev_mode"):
        learning.check_config(_check_cfg(mode="online", ev_mode="off"))


def _check_cfg(*, mode: str, ev_mode: object = "on") -> dict:
    import copy
    s = copy.deepcopy(cfg.rules())
    s["learning"]["enabled"] = True
    s["learning"]["mode"] = mode
    s["brain"] = {**s.get("brain", {}), "ev_mode": ev_mode}
    return s
