"""Tests for the rung fact-skeletons.

A skeleton is the contract between the law engine and the message writer: these
numbers, these sentences, nothing else. The writer may rephrase; it may not add
a fact, drop a citation, or move a digit.

The two assertions that matter most:
  * rung 1 carries NO legal facts at all -- a courtesy nudge that mentions the
    Act is not a courtesy nudge
  * rung 3's tax figure equals engine.law exactly -- two sources of truth for a
    money number is how a wrong number reaches a buyer
"""

from __future__ import annotations

from datetime import date

import pytest

from engine import law, rungs
from engine.config import rules

TODAY = date(2026, 8, 24)


def invoice(
    *,
    acceptance: str = "2026-01-01",
    written: bool = True,
    agreed_days: int | None = 90,
    amount: int = 50_000_000,
    status: str = "open",
    payments: list[dict] | None = None,
) -> dict:
    payments = payments or []
    return {
        "invoice_id": "INV-2026-0204",
        "buyer_id": "BUY-01",
        "description": "400 kg HDPE granules",
        "po_number": "PO/25-26/04821",
        "acceptance_date": acceptance,
        "issue_date": acceptance,
        "written_agreement": written,
        "agreed_days": agreed_days,
        "agreed_due_date": "2026-04-01",
        "amount_paise": amount,
        "status": status,
        "partial_payments": payments,
        "amount_paid_paise": sum(p["amount_paise"] for p in payments),
        "paid_date": None,
    }


BUYER = {"buyer_id": "BUY-01", "name": "ABC Traders", "profile": "corporate",
         "language_pref": "english", "contact_name": "R. Kumar"}


def skeleton(rung: int, record: dict | None = None, today: date = TODAY) -> dict:
    record = record if record is not None else invoice()
    position = law.legal_position(record, today)
    return rungs.fact_skeleton(rung, position, record, BUYER)


# --- the ladder is data, and it is well formed ---------------------------

def test_every_rung_in_config_is_loadable() -> None:
    assert [r["id"] for r in rungs.all_rungs()] == [0, 1, 2, 3, 4]


def test_rung_lookup_by_id() -> None:
    assert rungs.rung(2)["name"] == "firm"
    assert rungs.rung(4)["name"] == "stop_handoff"


def test_unknown_rung_is_refused() -> None:
    with pytest.raises(ValueError):
        rungs.rung(9)


def test_every_allowed_fact_key_exists_in_legal_config() -> None:
    """A typo in rules.yaml would silently drop a fact from a message."""
    from engine.config import legal
    known = set(legal()["facts"])
    for record in rungs.all_rungs():
        unknown = set(record["allowed_facts"]) - known
        assert not unknown, f"rung {record['id']} allows unknown fact keys: {unknown}"


def test_allowed_facts_only_widen_as_the_ladder_climbs() -> None:
    """Rung n+1 may say everything rung n could, and more. Never less."""
    previous: set[str] = set()
    for record in rungs.all_rungs():
        if record["id"] == 0:
            continue
        current = set(record["allowed_facts"])
        assert previous <= current, f"rung {record['id']} narrows the facts"
        previous = current


# --- rung 0 and rung 1 say nothing legal ---------------------------------

def test_rung_zero_sends_nothing() -> None:
    result = skeleton(0)
    assert result["max_messages"] == 0
    assert result["facts"] == []


def test_rung_one_carries_no_legal_facts() -> None:
    """DEFINITION OF DONE: a courtesy nudge mentions no law whatsoever."""
    result = skeleton(1)
    assert result["facts"] == []
    assert result["allowed_fact_keys"] == []


def test_rung_one_exposes_no_legal_numbers() -> None:
    """Not even quietly, in the numbers block the writer is handed."""
    numbers = skeleton(1)["numbers"]
    for banned in ("interest_paise", "tax_exposure_paise", "interest_per_day_paise",
                   "cost_of_waiting_paise", "statutory_due_date"):
        assert banned not in numbers, f"rung 1 leaked {banned}"


def test_rung_one_still_knows_the_invoice_and_the_amount() -> None:
    """It has to be able to ask about something."""
    numbers = skeleton(1)["numbers"]
    assert numbers["invoice_id"] == "INV-2026-0204"
    assert numbers["outstanding_paise"] == 50_000_000


def test_rung_one_forbids_the_legal_vocabulary_explicitly() -> None:
    forbidden = " ".join(skeleton(1)["forbidden"]).lower()
    assert "interest" in forbidden
    assert "statutory" in forbidden or "legal" in forbidden


# --- rung 2 states the statutory position --------------------------------

def test_rung_two_states_interest_but_not_tax() -> None:
    result = skeleton(2)
    keys = result["allowed_fact_keys"]
    assert "section_16" in keys
    assert "tax_deduction_upcoming" not in keys
    assert "tax_deduction_crystallised" not in keys
    assert "section_22" not in keys


def test_rung_two_interest_matches_the_law_engine_exactly() -> None:
    record = invoice()
    expected = law.interest_owed_paise(record, TODAY)
    assert skeleton(2, record)["numbers"]["interest_paise"] == expected
    assert expected > 0


# --- rung 3 states the tax cost, and it must match law.py ----------------

def test_rung_three_tax_number_matches_the_law_engine_exactly() -> None:
    """DEFINITION OF DONE: one source of truth for the 37(2)(g) figure."""
    record = invoice()
    expected = law.buyer_tax_exposure_paise(record, TODAY)
    assert expected > 0
    assert skeleton(3, record)["numbers"]["tax_exposure_paise"] == expected


def test_rung_three_quotes_the_current_provision_not_the_repealed_one() -> None:
    text = " ".join(skeleton(3)["facts"])
    assert "Section 37(2)(g)" in text


def test_rung_three_adds_disclosure_and_non_deductibility() -> None:
    keys = skeleton(3)["allowed_fact_keys"]
    assert "section_22" in keys
    assert "section_23" in keys


def test_the_tax_figure_in_the_prose_matches_the_number_block() -> None:
    """The sentence and the field cannot disagree, or the writer picks one."""
    from engine.money import format_inr
    result = skeleton(3)
    rendered = format_inr(result["numbers"]["tax_exposure_paise"])
    assert any(rendered in fact for fact in result["facts"])


# --- rung 4 stops talking to the buyer -----------------------------------

def test_rung_four_sends_no_buyer_message() -> None:
    result = skeleton(4)
    assert result["max_messages"] == 0
    assert result["sends_to_buyer"] is False


def test_rung_four_facts_are_for_the_draft_not_the_buyer() -> None:
    result = skeleton(4)
    assert "samadhaan" in result["allowed_fact_keys"]
    assert result["audience"] == "internal"


def test_lower_rungs_do_send_to_the_buyer() -> None:
    for rung_id in (1, 2, 3):
        assert skeleton(rung_id)["sends_to_buyer"] is True


# --- the ceiling invariant -----------------------------------------------

def test_a_skeleton_cannot_exceed_the_legal_ceiling() -> None:
    """Asking for rung 3 on a not-yet-due invoice is a bug, and is refused."""
    not_due = invoice(acceptance="2026-08-20", written=False, agreed_days=None)
    with pytest.raises(rungs.RungNotAvailable):
        skeleton(3, not_due)


def test_the_ceiling_permits_choosing_a_lower_rung() -> None:
    """Law sets the maximum; the brain may always be gentler."""
    record = invoice()
    assert law.legal_position(record, TODAY)["available_rung"] == 4
    assert skeleton(1, record)["rung"] == 1


def test_rung_zero_is_always_allowed() -> None:
    """Waiting is never a legal question."""
    not_due = invoice(acceptance="2026-08-20", written=False, agreed_days=None)
    assert skeleton(0, not_due)["rung"] == 0


# --- disputes ------------------------------------------------------------

def test_a_disputed_invoice_yields_no_sendable_skeleton() -> None:
    record = invoice(status="disputed")
    result = skeleton(2, record)
    assert result["dispute_hold"] is True
    assert result["sends_to_buyer"] is False
    assert result["facts"] == []


# --- what the writer is told it must not do ------------------------------

def test_every_skeleton_carries_the_no_threats_rule() -> None:
    for rung_id in (1, 2, 3):
        forbidden = " ".join(skeleton(rung_id)["forbidden"]).lower()
        assert "threat" in forbidden


def test_the_skeleton_pins_the_numbers_the_writer_may_use() -> None:
    result = skeleton(3)
    assert set(result["numbers"]) >= {
        "invoice_id", "outstanding_paise", "interest_paise",
        "tax_exposure_paise", "days_overdue", "statutory_due_date",
    }


def test_skeleton_records_where_its_pacing_came_from() -> None:
    result = skeleton(2)
    config = rules()["ladder"]["rungs"][2]
    assert result["min_days_between_contacts"] == config["min_days_between_contacts"]
    assert result["max_messages"] == config["max_messages"]
