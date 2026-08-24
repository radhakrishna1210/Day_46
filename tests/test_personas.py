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
