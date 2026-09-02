"""Tests for engine/learning.py -- the learned-recovery switch.

The whole point of this module is that it is INERT until config/rules.yaml's
learning.enabled is flipped on, and that flipping it on without brain.ev_mode
on is a loud startup error rather than a silent no-op. So the tests split
three ways:

  * the shipped state (enabled: false) -- nothing here is consulted, and the
    hand-typed grid is used byte-for-byte;
  * check_config() -- every combination that must raise, and every one that
    must not;
  * the enabled path -- a present cell returns its posterior mean, a missing
    cell falls back to the hand-typed value and says so, and the SEND tiers
    (soft_nudge / firm / legal_facts) resolve to their nested per-delivered-
    rung cell while payment_plan / counter_settle resolve flat.
"""

from __future__ import annotations

import copy
from datetime import date

import pytest

from engine import brain
from engine import config as cfg
from engine import law
from engine import learning
from engine import negotiation as neg
from engine.config import learned_recovery, rules


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _config(*, enabled: bool, ev_mode: object = "on", mode: str = "offline") -> dict:
    """A deep copy of the real config with the learning block set as asked.

    `ev_mode` is deliberately typed loosely: YAML parses a bare `on` as the
    boolean True, while tests and the simulator pass the string "on", and
    check_config must accept both.
    """
    settings = copy.deepcopy(rules())
    settings["learning"]["enabled"] = enabled
    settings["learning"]["mode"] = mode
    settings["brain"] = {**settings.get("brain", {}), "ev_mode": ev_mode}
    return settings


@pytest.fixture(autouse=True)
def _isolate_fallback_log() -> object:
    """engine.learning._fallback_logged is a process-global dedupe set; keep
    each test from seeing another's entries."""
    saved = set(learning._fallback_logged)
    learning._fallback_logged.clear()
    yield
    learning._fallback_logged.clear()
    learning._fallback_logged.update(saved)


@pytest.fixture
def learning_on(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Point every module that reads rules() on the learned path at an
    enabled + ev_mode:on config, and undo it after the test."""
    settings = _config(enabled=True, ev_mode="on")
    stub = lambda: settings
    monkeypatch.setattr(cfg, "rules", stub)
    monkeypatch.setattr(learning, "rules", stub)
    monkeypatch.setattr(neg, "rules", stub)
    return settings


# --------------------------------------------------------------------------
# the shipped state
# --------------------------------------------------------------------------

def test_learning_ships_off() -> None:
    assert learning.enabled() is False
    assert learning.mode() == "offline"


def test_check_config_is_a_noop_with_the_shipped_config() -> None:
    learning.check_config()  # must not raise


def test_disabled_ignores_ev_mode_and_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """A meaningless mode/ev_mode combination is fine as long as learning is
    off -- the keys simply do not matter yet."""
    learning.check_config(_config(enabled=False, ev_mode="off", mode="online"))


def test_negotiation_uses_the_hand_typed_grid_when_learning_is_off() -> None:
    grid = rules()["negotiation"]["recovery_probability"]
    for quadrant, row in grid.items():
        for action, value in row.items():
            result = neg.recovery_probability(quadrant, action)
            assert result["probability"] == value
            assert result["breakdown"][0]["detail"].startswith("assumed P(recover)")


# --------------------------------------------------------------------------
# check_config -- the coherence guard
# --------------------------------------------------------------------------

def test_enabled_without_ev_mode_on_is_a_startup_error() -> None:
    with pytest.raises(learning.LearningConfigError, match="brain.ev_mode"):
        learning.check_config(_config(enabled=True, ev_mode="off"))


def test_enabled_with_ev_mode_on_is_accepted() -> None:
    learning.check_config(_config(enabled=True, ev_mode="on"))  # must not raise


@pytest.mark.parametrize("ev_mode, on", [
    ("on", True), ("On", True), (True, True),
    ("off", False), (False, False), (None, False),
])
def test_ev_mode_is_read_the_same_way_a_bare_yaml_on_and_the_string_on(
    ev_mode: object, on: bool,
) -> None:
    """config.ev_mode_on -- the one interpreter both brain.py and learning.py
    use -- treats YAML's boolean `on` and the explicit string "on" alike."""
    assert cfg.ev_mode_on(_config(enabled=True, ev_mode=ev_mode)) is on


def test_a_bare_yaml_on_satisfies_check_config() -> None:
    """YAML parses `brain.ev_mode: on` as the boolean True; check_config must
    not reject that as 'not on'."""
    learning.check_config(_config(enabled=True, ev_mode=True))  # must not raise


def test_enabled_with_mode_online_is_accepted() -> None:
    """online is implemented now (Thompson sampling + in-run updates) -- it
    must NOT be rejected the way an unknown mode is."""
    learning.check_config(_config(enabled=True, ev_mode="on", mode="online"))  # must not raise


def test_enabled_with_an_unknown_mode_is_a_startup_error() -> None:
    with pytest.raises(learning.LearningConfigError, match="offline"):
        learning.check_config(_config(enabled=True, ev_mode="on", mode="typo"))


def test_enabled_with_a_missing_learned_file_is_a_startup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing() -> dict:
        raise FileNotFoundError("config/learned_recovery.yaml")

    monkeypatch.setattr(learning, "learned_recovery", _missing)
    with pytest.raises(learning.LearningConfigError, match="fit_recovery"):
        learning.check_config(_config(enabled=True, ev_mode="on"))


def test_the_error_type_is_catchable_as_a_runtime_error() -> None:
    assert issubclass(learning.LearningConfigError, RuntimeError)


# --------------------------------------------------------------------------
# the enabled path -- posterior means and fallback
# --------------------------------------------------------------------------

# A (quadrant, negotiation-action) pair with no learned cell: can_pay_but_wont
# has no rung-1 SENDs in the training data, so it has no soft_nudge tier cell.
ABSENT_CELL = ("can_pay_but_wont", "soft_nudge")


def _iter_cells(recovery: dict) -> list[tuple[str, str, dict]]:
    """(quadrant, negotiation-action, cell) for every fitted cell -- SEND
    tiers flattened out of the nested `send` group."""
    out = []
    for quadrant, row in recovery.items():
        for key, val in row.items():
            if key == "send":
                out.extend((quadrant, tier, cell) for tier, cell in val.items())
            else:
                out.append((quadrant, key, val))
    return out


def test_a_present_cell_returns_its_posterior_mean(learning_on: dict) -> None:
    for quadrant, action, cell in _iter_cells(learned_recovery()["recovery"]):
        assert learning.recovery_probability(quadrant, action) == cell["mean"]
        assert learning.has_cell(quadrant, action) is True


def test_send_tiers_resolve_to_the_nested_per_rung_cell(learning_on: dict) -> None:
    """soft_nudge/firm/legal_facts must read recovery.<q>.send.<tier>, not a
    flat top-level cell (there is no flat send cell any more)."""
    send = learned_recovery()["recovery"]["good_customer"]["send"]
    for tier in ("soft_nudge", "firm", "legal_facts"):
        assert learning.recovery_probability("good_customer", tier) == send[tier]["mean"]
        assert send[tier]["delivered_rung"] in (1, 2, 3)
    # and there is no flat "send" key left to accidentally match
    assert "send" not in {a for _q, a, _c in _iter_cells(learned_recovery()["recovery"])}


def test_payment_plan_and_counter_settle_resolve_flat_unchanged(learning_on: dict) -> None:
    plan = learned_recovery()["recovery"]["cash_flow_problem"]["payment_plan"]
    settle = learned_recovery()["recovery"]["can_pay_but_wont"]["counter_settle"]
    assert learning.recovery_probability("cash_flow_problem", "payment_plan") == plan["mean"]
    assert learning.recovery_probability("can_pay_but_wont", "counter_settle") == settle["mean"]


def test_a_missing_cell_falls_back_to_the_hand_typed_value(
    learning_on: dict, capsys: pytest.CaptureFixture[str],
) -> None:
    quadrant, action = ABSENT_CELL
    hand_typed = rules()["negotiation"]["recovery_probability"][quadrant][action]
    assert learning.has_cell(quadrant, action) is False

    value = learning.recovery_probability(quadrant, action)
    assert value == hand_typed / 100

    err = capsys.readouterr().err
    assert f"no learned cell for ({quadrant}, {action})" in err
    assert (quadrant, action) in learning.fallbacks_logged()


def test_the_fallback_notice_is_logged_once_per_cell(
    learning_on: dict, capsys: pytest.CaptureFixture[str],
) -> None:
    learning.recovery_probability(*ABSENT_CELL)
    capsys.readouterr()  # drain
    learning.recovery_probability(*ABSENT_CELL)
    assert capsys.readouterr().err == ""


def test_recovery_probability_never_raises_over_a_missing_cell(learning_on: dict) -> None:
    # every negotiation action, every quadrant -- send tiers and payment_plan/
    # counter_settle mostly learned, wait/handoff/escalation always fallback,
    # none should raise.
    from engine import ability_willingness as aw

    for quadrant in aw.QUADRANTS:
        for action in neg.ACTIONS:
            assert 0.0 <= learning.recovery_probability(quadrant, action) <= 1.0


# --------------------------------------------------------------------------
# negotiation integration
# --------------------------------------------------------------------------

@pytest.mark.parametrize("quadrant, action", [
    ("cash_flow_problem", "payment_plan"),   # flat cell
    ("good_customer", "firm"),               # send tier -> recovery.<q>.send.firm
])
def test_negotiation_uses_the_posterior_mean_when_learning_is_on(
    learning_on: dict, quadrant: str, action: str,
) -> None:
    learned = learning.recovery_probability(quadrant, action)   # 0-1
    result = neg.recovery_probability(quadrant, action)

    assert result["probability"] == round(learned * 100)
    assert result["breakdown"][0]["detail"].startswith("learned P(recover) (posterior mean)")
    # the hand-typed value for this cell differs -- proving the number really
    # moved, not just the label.
    assert result["probability"] != (
        rules()["negotiation"]["recovery_probability"][quadrant][action]
    )


def test_negotiation_labels_a_fallback_cell_honestly(learning_on: dict) -> None:
    quadrant, action = ABSENT_CELL
    result = neg.recovery_probability(quadrant, action)
    detail = result["breakdown"][0]["detail"]
    assert "no learned cell" in detail
    assert result["probability"] == (
        rules()["negotiation"]["recovery_probability"][quadrant][action]
    )


def test_the_probability_breakdown_still_sums_exactly_with_a_learned_base(
    learning_on: dict,
) -> None:
    for broken in (0, 1, 3):
        result = neg.recovery_probability("cash_flow_problem", "payment_plan",
                                          broken_promises=broken)
        assert sum(item["points"] for item in result["breakdown"]) == pytest.approx(
            result["probability"])


def test_learned_probability_flows_into_the_ev_ranking(learning_on: dict) -> None:
    """rank_actions() must consume the learned number, not just recovery_probability()."""
    ranked = neg.rank_actions("cash_flow_problem", 50_000_000, broken_promises=0)
    by_action = {r["action"]: r for r in ranked}
    rec = learned_recovery()["recovery"]["cash_flow_problem"]
    assert by_action["payment_plan"]["probability"] == round(rec["payment_plan"]["mean"] * 100)
    # a SEND tier flows through the same path, via its nested cell
    assert by_action["firm"]["probability"] == round(rec["send"]["firm"]["mean"] * 100)


# --------------------------------------------------------------------------
# end to end: config -> brain.decide -> negotiation -> learning
# --------------------------------------------------------------------------

def _overdue_invoice() -> dict:
    old = "2026-06-01"          # old enough that the legal ceiling is fully open
    return {
        "invoice_id": "INV-LEARN-01", "buyer_id": "BUY-01",
        "description": "goods", "po_number": "PO/1", "issue_date": old,
        "acceptance_date": old, "written_agreement": False, "agreed_days": None,
        "agreed_due_date": None, "amount_paise": 50_000_000, "status": "open",
        "partial_payments": [], "amount_paid_paise": 0, "paid_date": None,
    }


def _two_axis_score(quadrant: str) -> dict:
    return {"buyer_id": "BUY-01", "name": "ABC Traders", "score": 55,
            "confidence": "high", "history_count": 12,
            "signals": {"broken_promises": 0}, "quadrant": quadrant}


def test_brain_decide_uses_the_learned_mean_with_a_bare_yaml_ev_mode_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole chain, and specifically the boolean spelling: a config whose
    brain.ev_mode is the boolean True (what YAML makes of a bare `on`) plus
    learning.enabled True must land a payment_plan whose recorded probability
    is the learned posterior mean, not the hand-typed grid value."""
    settings = _config(enabled=True, ev_mode=True)         # bool, not "on"
    monkeypatch.setattr(cfg, "rules", lambda: settings)
    monkeypatch.setattr(learning, "rules", lambda: settings)
    monkeypatch.setattr(neg, "rules", lambda: settings)
    monkeypatch.setattr(brain, "rules", lambda: settings)

    record = _overdue_invoice()
    position = law.legal_position(record, date(2026, 8, 24))
    action = brain.decide(record, {"buyer_id": "BUY-01", "name": "ABC Traders",
                                   "opted_out": False, "profile": "corporate"},
                          _two_axis_score("cash_flow_problem"), position,
                          promises=[], history=[], log=False)

    assert action.detail["negotiation_action"] == "payment_plan"
    plan_cell = learned_recovery()["recovery"]["cash_flow_problem"]["payment_plan"]
    assert action.detail["ev"]["probability"] == round(plan_cell["mean"] * 100)
    assert action.detail["ev"]["probability_breakdown"][0]["detail"].startswith(
        "learned P(recover) (posterior mean)")


def test_brain_decide_stays_inert_with_ev_mode_off_even_when_learning_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """learning.enabled true but ev_mode off: decide() must not touch the EV
    path at all (check_config is what stops this reaching a real run; this
    proves decide() itself is still inert if it somehow does)."""
    settings = _config(enabled=True, ev_mode="off")
    monkeypatch.setattr(cfg, "rules", lambda: settings)
    monkeypatch.setattr(learning, "rules", lambda: settings)
    monkeypatch.setattr(neg, "rules", lambda: settings)
    monkeypatch.setattr(brain, "rules", lambda: settings)

    record = _overdue_invoice()
    position = law.legal_position(record, date(2026, 8, 24))
    action = brain.decide(record, {"buyer_id": "BUY-01", "name": "ABC Traders",
                                   "opted_out": False, "profile": "corporate"},
                          _two_axis_score("cash_flow_problem"), position,
                          promises=[], history=[], log=False)
    assert "negotiation_action" not in action.detail
    assert action.kind == brain.SEND


# --------------------------------------------------------------------------
# learned-decision provenance in the audit trail (engine/brain.py)
# --------------------------------------------------------------------------

_BUYER = {"buyer_id": "BUY-01", "name": "ABC Traders", "opted_out": False,
          "profile": "corporate"}
_LEARNED_KEYS = ("learning_method", "estimated_probability", "observations",
                 "bandit_top_choice", "executed_action", "gate_reason")


def _decide_learned(quadrant: str, config: dict, *, record: dict | None = None,
                    history: list | None = None) -> brain.Action:
    record = record or _overdue_invoice()
    position = law.legal_position(record, date(2026, 8, 24))
    return brain.decide(record, _BUYER, _two_axis_score(quadrant), position,
                        promises=[], history=history or [], log=False, config=config)


@pytest.mark.parametrize("quadrant",
                         ["good_customer", "cash_flow_problem", "can_pay_but_wont", "high_risk"])
def test_brain_decide_records_the_learned_decision_fields(learning_on: dict, quadrant: str) -> None:
    action = _decide_learned(quadrant, learning_on)
    d = action.detail
    for key in _LEARNED_KEYS:
        assert key in d, key
    assert d["learning_method"] in ("thompson_sampling", "posterior_mean", "hardcoded")
    assert 0.0 <= d["estimated_probability"] <= 1.0
    assert d["observations"] is None or isinstance(d["observations"], int)
    assert d["bandit_top_choice"] in neg.ACTIONS
    assert d["executed_action"] in neg.ACTIONS
    # the existing fields are untouched, and executed_action mirrors them
    assert d["executed_action"] == d["negotiation_action"]
    assert "ev" in d
    assert action.reason and action.source in ("rule", "llm")


def test_brain_decide_records_no_learning_fields_when_learning_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ev_mode on but learning.enabled false: the EV branch still runs, but not
    one learned-decision key is written -- byte-identical to before this change."""
    settings = _config(enabled=False, ev_mode="on")
    stub = lambda: settings
    monkeypatch.setattr(cfg, "rules", stub)
    monkeypatch.setattr(learning, "rules", stub)
    monkeypatch.setattr(neg, "rules", stub)

    action = _decide_learned("cash_flow_problem", settings)
    assert "negotiation_action" in action.detail, "the EV branch must have run"
    for key in _LEARNED_KEYS:
        assert key not in action.detail, key


def test_brain_decide_flags_when_eligible_actions_overrules_the_bandit(learning_on: dict) -> None:
    """good_customer's eligible_actions menu withholds every legal/handoff
    action, yet raw EV over the FULL action space ranks one on top (the
    documented 'surprising result' in engine/negotiation.py). The bandit's pick
    can never execute, and the audit trail must say so."""
    menu = learning_on["negotiation"]["eligible_actions"]["good_customer"]
    full_top = neg.rank_actions("good_customer", 50_000_000, broken_promises=0)[0]["action"]
    assert full_top not in menu, f"fixture: expected a non-menu action on top, got {full_top}"

    d = _decide_learned("good_customer", learning_on).detail
    assert d["bandit_top_choice"] == full_top
    assert d["executed_action"] in menu
    assert d["executed_action"] != full_top
    assert d["gate_reason"] is not None


def test_brain_decide_names_the_law_ceiling_when_it_blocks_the_bandit(learning_on: dict) -> None:
    """The headline case: raw EV wants legal_escalation, but the MSMED-Act
    ceiling sits below the handoff rung, so a rung-<=ceiling send goes out
    instead -- gate_reason == law_ceiling_rung_<ceiling>."""
    record = {**_overdue_invoice(), "issue_date": "2026-08-05", "acceptance_date": "2026-08-05"}
    position = law.legal_position(record, date(2026, 8, 24))
    ceiling = position["available_rung"]
    assert position["days_overdue"] > 0 and ceiling < brain.HANDOFF_RUNG, "fixture assumption"

    full_top = neg.rank_actions("can_pay_but_wont", 50_000_000, broken_promises=0)[0]["action"]
    assert full_top in (neg.HUMAN_HANDOFF, neg.LEGAL_ESCALATION), f"fixture: {full_top}"

    d = _decide_learned("can_pay_but_wont", learning_on, record=record).detail
    assert d["bandit_top_choice"] == full_top
    assert d["gate_reason"] == f"law_ceiling_rung_{ceiling}"
    assert d["executed_action"] != full_top


def test_audit_method_and_observations_resolve_cells(learning_on: dict) -> None:
    # good_customer / firm is the workhorse SEND tier: n=748, fitted
    assert learning.audit_method("good_customer", "firm") == "posterior_mean"
    assert learning.observations("good_customer", "firm") == 748
    # an action the fit never covers, and the None cases
    assert learning.audit_method("good_customer", "human_handoff") == "hardcoded"
    assert learning.observations("good_customer", None) is None
    assert learning.observations("can_pay_but_wont", "soft_nudge") is None   # absent tier


def test_audit_method_is_hardcoded_when_learning_ships_off() -> None:
    assert learning.audit_method("good_customer", "firm") == "hardcoded"


# --------------------------------------------------------------------------
# the CLI entry points call check_config at startup
# --------------------------------------------------------------------------

def test_main_run_fails_fast_on_an_incoherent_learning_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    monkeypatch.setattr(main.learning, "check_config",
                        lambda: (_ for _ in ()).throw(learning.LearningConfigError("boom")))
    with pytest.raises(learning.LearningConfigError):
        main.run(seed=42)


def test_run_sim_main_fails_fast_on_an_incoherent_learning_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sim import run_sim

    monkeypatch.setattr(run_sim.learning, "check_config",
                        lambda: (_ for _ in ()).throw(learning.LearningConfigError("boom")))
    monkeypatch.setattr("sys.argv", ["run_sim.py", "--days", "1"])
    with pytest.raises(learning.LearningConfigError):
        run_sim.main()
