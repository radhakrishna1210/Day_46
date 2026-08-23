"""Tests for the data factory.

Two things matter here. First, reproducibility: the whole experiment rests on
"same seed, same world", so a drift in generation would quietly invalidate the
baseline-vs-agent comparison. Second, the mess is deliberate -- if a refactor
accidentally tidied the dataset (no disputes, no missing agreements, no
low-confidence buyers) the agent would look better than it is.

The leak test is the important one: the hidden persona must never reach
data/seed/, or the buyer score becomes a lookup instead of an inference.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date

import pytest

from data import generate as gen

SEED = 42


@pytest.fixture(scope="module")
def world() -> dict:
    return gen.generate(SEED)


@pytest.fixture(scope="module")
def buyers(world: dict) -> list[dict]:
    return world["buyers"]["buyers"]


@pytest.fixture(scope="module")
def invoices(world: dict) -> list[dict]:
    return world["invoices"]["invoices"]


@pytest.fixture(scope="module")
def current(invoices: list[dict]) -> list[dict]:
    return [inv for inv in invoices if inv["cohort"] == "current"]


@pytest.fixture(scope="module")
def history(invoices: list[dict]) -> list[dict]:
    return [inv for inv in invoices if inv["cohort"] == "history"]


# --- reproducibility ------------------------------------------------------

def test_same_seed_produces_identical_json() -> None:
    first = json.dumps(gen.generate(SEED), sort_keys=True)
    second = json.dumps(gen.generate(SEED), sort_keys=True)
    assert first == second


def test_different_seed_produces_different_data() -> None:
    assert json.dumps(gen.generate(SEED)) != json.dumps(gen.generate(SEED + 1))


def test_output_carries_no_wall_clock_timestamp(world: dict) -> None:
    """A generated_at field is the classic way byte-identity dies.

    Checking that today's date is absent would be wrong -- the simulated world
    legitimately contains dates around today. What must be absent is anything
    read from the real clock: no timestamp keys, and no time-of-day anywhere,
    since every date in the dataset is a plain calendar date.
    """
    blob = json.dumps(world)
    for banned in ("generated_at", "created_at", "timestamp", "now"):
        assert banned not in blob

    for section in ("buyers", "invoices", "personas"):
        for key in world[section]["meta"]:
            assert key in {
                "schema_version", "seed", "buyer_count", "simulation_start",
                "current_invoice_count", "history_invoice_count", "warning",
            }

    # Every date is YYYY-MM-DD. An ISO datetime would mean the clock got in.
    for invoice in world["invoices"]["invoices"]:
        for field in ("issue_date", "acceptance_date", "paid_date"):
            value = invoice[field]
            if value is not None:
                assert date.fromisoformat(value).isoformat() == value


# --- the persona must not leak -------------------------------------------

def test_persona_never_appears_in_seed_data(world: dict) -> None:
    seed_blob = json.dumps(world["buyers"]) + json.dumps(world["invoices"])
    for tag in gen.PERSONA_BEHAVIOUR:
        assert tag not in seed_blob, f"persona {tag!r} leaked into data/seed/"
    assert "persona" not in seed_blob


def test_every_buyer_has_exactly_one_persona(world: dict, buyers: list[dict]) -> None:
    personas = world["personas"]["personas"]
    assert sorted(personas) == sorted(b["buyer_id"] for b in buyers)
    assert Counter(personas.values()) == Counter(dict(gen.PERSONA_COUNTS))


# --- shape ----------------------------------------------------------------

def test_buyer_and_invoice_counts(buyers: list[dict], current: list[dict]) -> None:
    assert len(buyers) == gen.N_BUYERS
    assert len(current) == gen.N_CURRENT_INVOICES


def test_every_invoice_belongs_to_a_known_buyer(buyers: list[dict], invoices: list[dict]) -> None:
    known = {b["buyer_id"] for b in buyers}
    assert {inv["buyer_id"] for inv in invoices} <= known


def test_invoice_ids_are_unique(invoices: list[dict]) -> None:
    ids = [inv["invoice_id"] for inv in invoices]
    assert len(ids) == len(set(ids))


def test_amounts_are_whole_rupees_inside_the_stated_band(invoices: list[dict]) -> None:
    for invoice in invoices:
        amount = invoice["amount_paise"]
        assert gen.AMOUNT_MIN_PAISE <= amount <= gen.AMOUNT_MAX_PAISE
        assert amount % gen.AMOUNT_STEP_PAISE == 0
        assert isinstance(amount, int), "money must be integer paise, never float"


def test_payments_add_up(invoices: list[dict]) -> None:
    for invoice in invoices:
        total = sum(p["amount_paise"] for p in invoice["partial_payments"])
        assert total == invoice["amount_paid_paise"]
        assert total <= invoice["amount_paise"]


def test_dates_are_ordered_within_each_invoice(invoices: list[dict]) -> None:
    for invoice in invoices:
        issue = date.fromisoformat(invoice["issue_date"])
        acceptance = date.fromisoformat(invoice["acceptance_date"])
        assert issue <= acceptance, "an invoice cannot be issued after acceptance"
        if invoice["paid_date"]:
            assert date.fromisoformat(invoice["paid_date"]) > issue


def test_history_is_paid_and_in_the_past(history: list[dict]) -> None:
    for invoice in history:
        assert invoice["status"] == "paid"
        assert invoice["amount_paid_paise"] == invoice["amount_paise"]
        assert date.fromisoformat(invoice["paid_date"]) < gen.SIMULATION_START


def test_current_invoices_are_not_yet_settled(current: list[dict]) -> None:
    for invoice in current:
        assert invoice["status"] in {"open", "partially_paid", "disputed"}
        assert invoice["paid_date"] is None
        assert invoice["amount_paid_paise"] < invoice["amount_paise"]


# --- the mess, which is the point ----------------------------------------

def test_exactly_one_open_dispute(current: list[dict]) -> None:
    disputed = [inv for inv in current if inv["status"] == "disputed"]
    assert len(disputed) == 1
    assert disputed[0]["dispute_note"]


def test_some_invoices_have_no_written_agreement(current: list[dict]) -> None:
    """These are the ones where the 15-day rule bites."""
    missing = [inv for inv in current if not inv["written_agreement"]]
    assert len(missing) >= 15
    assert all(inv["agreed_days"] is None for inv in missing)


def test_some_agreed_terms_exceed_the_statutory_ceiling(current: list[dict]) -> None:
    """Void above 45 days -- this is what the law engine exists to catch."""
    _, ceiling = gen._load_statutory_terms()
    illegal = [inv for inv in current if (inv["agreed_days"] or 0) > ceiling]
    assert len(illegal) >= 10


def test_partial_payments_exist(current: list[dict]) -> None:
    partial = [inv for inv in current if inv["status"] == "partially_paid"]
    assert len(partial) == gen.N_PARTIALLY_PAID
    assert all(0 < inv["amount_paid_paise"] < inv["amount_paise"] for inv in partial)


def test_at_least_three_buyers_have_low_confidence(buyers: list[dict], history: list[dict]) -> None:
    counts = Counter(inv["buyer_id"] for inv in history)
    low = [b for b in buyers if counts[b["buyer_id"]] < 3]
    assert len(low) >= gen.N_LOW_CONFIDENCE_BUYERS


def test_some_invoices_are_not_yet_due(current: list[dict]) -> None:
    """The watchdog must filter, not blast everything in the table."""
    no_agreement, ceiling = gen._load_statutory_terms()
    not_due = [
        inv for inv in current
        if gen.statutory_due_date(inv, no_agreement, ceiling) > gen.SIMULATION_START
    ]
    assert len(not_due) == gen.N_NOT_YET_DUE


def test_exactly_one_buyer_has_opted_out(buyers: list[dict]) -> None:
    assert sum(1 for b in buyers if b["opted_out"]) == 1


def test_both_languages_and_profiles_are_represented(buyers: list[dict]) -> None:
    assert {b["profile"] for b in buyers} == {"corporate", "small_trader"}
    assert {b["language_pref"] for b in buyers} == {"english", "hinglish"}


# --- nobody real gets contacted ------------------------------------------

def test_no_deliverable_contact_details(buyers: list[dict]) -> None:
    """Non-negotiable #4, enforced at the data layer as well as the channel."""
    for buyer in buyers:
        assert buyer["contact_email"].endswith(".example.invalid")
        assert buyer["contact_phone"].startswith("+91-90000-")


# --- the persona signal has to be learnable ------------------------------

def test_personas_produce_distinguishable_payment_behaviour(world: dict, history: list[dict]) -> None:
    """If every persona paid the same way, the score engine would be scoring noise."""
    no_agreement, ceiling = gen._load_statutory_terms()
    personas = world["personas"]["personas"]
    totals: dict[str, list[int]] = {}
    for invoice in history:
        tag = personas[invoice["buyer_id"]]
        due = gen.statutory_due_date(invoice, no_agreement, ceiling)
        delay = (date.fromisoformat(invoice["paid_date"]) - due).days
        totals.setdefault(tag, []).append(delay)

    average = {tag: sum(d) / len(d) for tag, d in totals.items()}
    assert average["forgetful"] < average["cash_tight"] < average["habitual_delayer"] < average["deadbeat"]


# --- display --------------------------------------------------------------

@pytest.mark.parametrize(
    ("paise", "expected"),
    [
        (0, "Rs 0"),
        (800_000, "Rs 8,000"),
        (5_000_000, "Rs 50,000"),
        (50_000_000, "Rs 5,00,000"),
        (120_000_000, "Rs 12,00,000"),
        (1_000_000_000, "Rs 1,00,00,000"),
    ],
)
def test_indian_digit_grouping(paise: int, expected: str) -> None:
    assert gen.format_inr(paise, "Rs ") == expected
