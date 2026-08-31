"""Tests for the buyer simulator's persona reactions.

These are test-harness tuning knobs, not audited business rules, so the bar
here is lower than engine/law.py or engine/score.py: the table is internally
consistent (rows sum to 1.0), reactions are deterministic and reproducible,
and the promise/dispute replies really do come from fixtures of the intent
they claim to be -- not that the exact probabilities are "correct".
"""

from __future__ import annotations

import random

import pytest

from engine.config import replies
from sim import personas


# --------------------------------------------------------------------------
# the reaction table itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "persona,rung",
    [(p, r) for p in personas.PERSONAS if p in personas.REACTION_TABLE for r in (1, 2, 3)],
)
def test_every_row_sums_to_one(persona: str, rung: int) -> None:
    total = sum(personas.REACTION_TABLE[persona][rung].values())
    assert abs(total - 1.0) < 1e-9, f"{persona} rung {rung} sums to {total}, not 1.0"


def test_every_persona_has_a_reaction_row() -> None:
    assert set(personas.REACTION_TABLE) == set(personas.PERSONAS)


def test_every_persona_has_a_keep_chance() -> None:
    for persona in personas.PERSONAS:
        assert 0.0 <= personas.PROMISE_KEEP_CHANCE.get(persona, -1) <= 1.0


# --------------------------------------------------------------------------
# react()
# --------------------------------------------------------------------------

def test_react_rejects_an_unknown_persona() -> None:
    with pytest.raises(ValueError):
        personas.react("loyal_customer", 1, random.Random(1))


def test_react_rejects_a_non_buyer_facing_rung() -> None:
    for bad_rung in (0, 4, 5):
        with pytest.raises(ValueError):
            personas.react("forgetful", bad_rung, random.Random(1))


def test_react_is_deterministic_for_the_same_seed() -> None:
    first = personas.react("cash_tight", 2, random.Random("fixed-seed"))
    second = personas.react("cash_tight", 2, random.Random("fixed-seed"))
    assert first == second


@pytest.mark.parametrize("persona", personas.PERSONAS)
@pytest.mark.parametrize("rung", (1, 2, 3))
def test_react_always_returns_a_known_outcome(persona: str, rung: int) -> None:
    for seed in range(50):
        result = personas.react(persona, rung, random.Random(seed))
        assert result["outcome"] in personas.OUTCOMES


def test_a_promise_outcome_carries_a_reply_and_variant() -> None:
    # cash_tight at rung 2 promises half the time -- easy to land on quickly.
    for seed in range(50):
        result = personas.react("cash_tight", 2, random.Random(seed))
        if result["outcome"] == personas.PROMISE:
            assert result["variant"] in personas.PROMISE_VARIANTS
            assert result["reply"]
            return
    pytest.fail("never landed on a promise outcome in 50 tries -- table may be broken")


def test_a_dispute_outcome_carries_a_reply_and_variant() -> None:
    for seed in range(50):
        result = personas.react("disputer", 2, random.Random(seed))
        if result["outcome"] == personas.DISPUTE:
            assert result["variant"] in personas.DISPUTE_VARIANTS
            assert result["reply"]
            return
    pytest.fail("never landed on a dispute outcome in 50 tries -- table may be broken")


def test_silence_and_payment_outcomes_carry_no_reply() -> None:
    for seed in range(50):
        result = personas.react("deadbeat", 1, random.Random(seed))
        if result["outcome"] in (personas.SILENCE, personas.PAY_FULL, personas.PAY_PARTIAL):
            assert "reply" not in result
            assert "variant" not in result


# --------------------------------------------------------------------------
# the fixtures a promise/dispute reaction points at are real, and honest
# --------------------------------------------------------------------------

def _fixture_intent(key: str) -> str:
    fixtures = {item["key"]: item for item in replies()["fixtures"]}
    return str(fixtures[key]["response"]["intent"])


@pytest.mark.parametrize("variant", personas.PROMISE_VARIANTS)
def test_every_promise_variant_is_actually_a_promise_fixture(variant: str) -> None:
    assert _fixture_intent(variant) == "promise"


@pytest.mark.parametrize("variant", personas.DISPUTE_VARIANTS)
def test_every_dispute_variant_is_actually_a_dispute_fixture(variant: str) -> None:
    assert _fixture_intent(variant) == "dispute"


# --------------------------------------------------------------------------
# keeps_promise()
# --------------------------------------------------------------------------

class _FixedRandom:
    """A stand-in rng that always reports the same draw -- for exact boundary tests."""

    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def test_keeps_promise_compares_against_the_configured_chance() -> None:
    chance = personas.PROMISE_KEEP_CHANCE["cash_tight"]
    assert personas.keeps_promise("cash_tight", _FixedRandom(chance - 0.01))
    assert not personas.keeps_promise("cash_tight", _FixedRandom(chance))


def test_keeps_promise_defaults_sensibly_for_an_unknown_persona() -> None:
    assert 0.0 <= personas.PROMISE_KEEP_CHANCE.get("nobody", 0.5) <= 1.0


# --------------------------------------------------------------------------
# load_hidden_personas()
# --------------------------------------------------------------------------

def test_load_hidden_personas_reads_the_generated_file() -> None:
    persona_of = personas.load_hidden_personas()
    assert len(persona_of) == 20
    assert set(persona_of.values()) <= set(personas.PERSONAS)


def test_load_hidden_personas_reads_an_explicit_path(tmp_path) -> None:
    import json

    path = tmp_path / "personas.json"
    path.write_text(json.dumps({"personas": {"BUY-01": "deadbeat"}}), encoding="utf-8")
    assert personas.load_hidden_personas(path) == {"BUY-01": "deadbeat"}


# --------------------------------------------------------------------------
# Phase 4 -- action_kind (payment_plan / counter_settle differentiation)
# --------------------------------------------------------------------------

def test_react_rejects_an_unknown_action_kind() -> None:
    with pytest.raises(ValueError):
        personas.react("cash_tight", 2, random.Random(1), action_kind="discount")


#: A snapshot of react()'s output for every persona/rung, taken from the
#: EXACT pre-Phase-4 code (via `git stash` to the pre-Phase-4 tree and back)
#: for a fixed rng label per (persona, rung). The genuine before/after proof
#: this phase's own brief asked for, not just "existing tests still pass" --
#: mirrors tests/test_run_sim.py's PRE_PHASE_3_SNAPSHOT.
PRE_PHASE_4_SNAPSHOT: dict[str, dict[str, str]] = {
    "cash_tight|1": {"outcome": "silence"},
    "cash_tight|2": {"outcome": "promise", "reply": "Will settle at month end once collections come in.",
                     "variant": "promise_month_end"},
    "cash_tight|3": {"outcome": "pay_full"},
    "deadbeat|1": {"outcome": "silence"},
    "deadbeat|2": {"outcome": "silence"},
    "deadbeat|3": {"outcome": "silence"},
    "disputer|1": {"outcome": "dispute",
                   "reply": "material mein problem thi, 12 units damage the — pehle credit note bhejo",
                   "variant": "dispute_damage_hinglish"},
    "disputer|2": {"outcome": "dispute",
                   "reply": "material mein problem thi, 12 units damage the — pehle credit note bhejo",
                   "variant": "dispute_damage_hinglish"},
    "disputer|3": {"outcome": "dispute", "reply": "Your invoice does not match our PO. Please check and revert.",
                   "variant": "dispute_po_mismatch"},
    "forgetful|1": {"outcome": "pay_partial"},
    "forgetful|2": {"outcome": "pay_partial"},
    "forgetful|3": {"outcome": "pay_full"},
    "habitual_delayer|1": {"outcome": "silence"},
    "habitual_delayer|2": {"outcome": "pay_full"},
    "habitual_delayer|3": {"outcome": "promise", "reply": "boss thoda time do, 5 tarikh tak ho jayega",
                           "variant": "promise_tarikh_hinglish"},
}


@pytest.mark.parametrize("pass_action_kind", [False, True])
def test_action_kind_send_is_byte_identical_to_pre_phase_4(pass_action_kind: bool) -> None:
    """react() called without action_kind, or with action_kind="send"
    explicitly, must reproduce the exact pre-Phase-4 snapshot -- proving the
    new parameter changes nothing for every call site that has not opted in."""
    for persona in personas.PERSONAS:
        for rung in (1, 2, 3):
            rng = random.Random(f"{persona}-{rung}-snapshot")
            kwargs = {"action_kind": "send"} if pass_action_kind else {}
            result = personas.react(persona, rung, rng, **kwargs)
            assert result == PRE_PHASE_4_SNAPSHOT[f"{persona}|{rung}"]


def _promise_rate(persona: str, rung: int, action_kind: str, n: int, tag: str) -> float:
    promises = sum(
        1 for i in range(n)
        if personas.react(persona, rung, random.Random(f"{tag}-{persona}-{rung}-{action_kind}-{i}"),
                          action_kind=action_kind)["outcome"] == personas.PROMISE
    )
    return promises / n


@pytest.mark.parametrize("rung", (1, 2, 3))
def test_payment_plan_meaningfully_raises_cash_tights_promise_rate(rung: int) -> None:
    """The Part A sanity check as an assertion, not just a printed table:
    cash_tight (the persona behind the cash_flow_problem quadrant) promises
    noticeably more often when offered a payment_plan than a plain send at
    the same rung. 1000 trials per arm -- enough to be a real signal, not
    two eyeballed runs; the observed deltas are ~0.20-0.27 in practice, so
    0.15 leaves comfortable margin against sampling noise."""
    n = 1000
    send_rate = _promise_rate("cash_tight", rung, "send", n, "sanity")
    plan_rate = _promise_rate("cash_tight", rung, "payment_plan", n, "sanity")
    assert plan_rate - send_rate > 0.15, (
        f"rung {rung}: send={send_rate:.3f} payment_plan={plan_rate:.3f} -- "
        f"not a meaningfully higher promise rate"
    )


@pytest.mark.parametrize("persona", ["forgetful", "habitual_delayer", "disputer", "deadbeat"])
def test_payment_plan_does_not_change_personas_with_no_configured_boost(persona: str) -> None:
    """Only cash_tight has a configured PAYMENT_PLAN_PROMISE_BOOST -- every
    other persona reacts to a payment_plan exactly as it would to a plain
    send (a good payer accepting a plan it did not need, or an unwilling
    payer offered something it should never structurally receive per
    config/rules.yaml's negotiation.eligible_actions, are both non-events
    here, not a crash and not an invented improvement)."""
    for rung in (1, 2, 3):
        for seed in range(20):
            send = personas.react(persona, rung, random.Random(f"noboost-{persona}-{rung}-{seed}"),
                                  action_kind="send")
            plan = personas.react(persona, rung, random.Random(f"noboost-{persona}-{rung}-{seed}"),
                                  action_kind="payment_plan")
            assert send == plan


def test_counter_settle_meaningfully_raises_habitual_delayers_reduced_terms_share() -> None:
    """habitual_delayer (the can_pay_but_wont persona counter_settle targets)
    promises with REDUCED terms -- the existing partial-promise fixture,
    which engine.promises/sim.run_sim already resolve to a genuine partial
    payment when kept -- far more often under counter_settle than under a
    plain send, representing continued lowballing rather than full
    acceptance. Reuses the existing mechanic; no new outcome category."""
    n = 1500
    for rung in (2, 3):
        send_reduced = send_total = plan_reduced = plan_total = 0
        for i in range(n):
            send = personas.react("habitual_delayer", rung,
                                  random.Random(f"cs-send-{rung}-{i}"), action_kind="send")
            if send["outcome"] == personas.PROMISE:
                send_total += 1
                send_reduced += send["variant"] == personas._REDUCED_TERMS_VARIANT
            cs = personas.react("habitual_delayer", rung,
                                random.Random(f"cs-cs-{rung}-{i}"), action_kind="counter_settle")
            if cs["outcome"] == personas.PROMISE:
                plan_total += 1
                plan_reduced += cs["variant"] == personas._REDUCED_TERMS_VARIANT
        send_share = send_reduced / send_total
        plan_share = plan_reduced / plan_total
        assert plan_share - send_share > 0.3, (
            f"rung {rung}: send reduced-share={send_share:.3f} "
            f"counter_settle reduced-share={plan_share:.3f}"
        )


def test_counter_settle_does_not_change_a_persona_with_no_configured_bias() -> None:
    """disputer's reaction table is already dominated by DISPUTE, not
    PROMISE -- it has no configured COUNTER_SETTLE_PARTIAL_BIAS, so
    counter_settle behaves exactly like a plain send for it."""
    for rung in (1, 2, 3):
        for seed in range(20):
            send = personas.react("disputer", rung, random.Random(f"nobias-{rung}-{seed}"),
                                  action_kind="send")
            cs = personas.react("disputer", rung, random.Random(f"nobias-{rung}-{seed}"),
                                action_kind="counter_settle")
            assert send == cs


def test_boost_promise_keeps_the_row_summing_to_one() -> None:
    table = {personas.PAY_FULL: 0.1, personas.PAY_PARTIAL: 0.1, personas.PROMISE: 0.2,
            personas.DISPUTE: 0.0, personas.SILENCE: 0.6}
    boosted = personas._boost_promise(table, 0.25)
    assert abs(sum(boosted.values()) - 1.0) < 1e-9
    assert boosted[personas.SILENCE] == 0.6 - 0.25
    assert boosted[personas.PROMISE] == 0.2 + 0.25


def test_boost_promise_clamps_to_what_silence_actually_has() -> None:
    table = {personas.PAY_FULL: 0.5, personas.PAY_PARTIAL: 0.0, personas.PROMISE: 0.4,
            personas.DISPUTE: 0.0, personas.SILENCE: 0.1}
    boosted = personas._boost_promise(table, 0.9)
    assert boosted[personas.SILENCE] == 0.0
    assert boosted[personas.PROMISE] == 0.5
    assert abs(sum(boosted.values()) - 1.0) < 1e-9
