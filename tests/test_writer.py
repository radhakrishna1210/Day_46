"""Tests for the message writer.

This is the first place an LLM touches something a buyer would read, so most of
these tests are about not trusting it: the guardrail, the fallback, and the
rule that no legal wording may be authored here.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from engine import audit, law, rungs, writer
from engine.config import messages
from engine.money import format_inr

TODAY = date(2026, 8, 24)
MESSAGES_PATH = Path(__file__).resolve().parents[1] / "config" / "messages.yaml"


@pytest.fixture(autouse=True)
def _quiet_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    audit.enable()
    yield


def invoice(*, acceptance: str = "2025-06-01", agreed_days: int | None = 90,
            written: bool = True, amount: int = 50_000_000) -> dict:
    return {
        "invoice_id": "INV-2026-0204", "buyer_id": "BUY-01",
        "description": "400 kg HDPE granules", "po_number": "PO/25-26/04821",
        "issue_date": acceptance, "acceptance_date": acceptance,
        "written_agreement": written, "agreed_days": agreed_days,
        "agreed_due_date": None, "amount_paise": amount, "status": "open",
        "partial_payments": [], "amount_paid_paise": 0, "paid_date": None,
    }


def buyer(**overrides) -> dict:
    return {"buyer_id": "BUY-01", "name": "ABC Traders Pvt Ltd",
            "profile": "corporate", "language_pref": "english",
            "contact_name": "R. Kumar", "opted_out": False, **overrides}


def trader(**overrides) -> dict:
    return buyer(**{"profile": "small_trader", "language_pref": "hinglish",
                    "name": "Verma Hardware Stores",
                    "contact_name": "Suresh Verma", **overrides})


def skeleton(rung: int, record=None, who=None):
    record = record if record is not None else invoice()
    return rungs.fact_skeleton(rung, law.legal_position(record, TODAY), record,
                               who or buyer())


def draft(rung: int, record=None, who=None, **kwargs):
    record = record if record is not None else invoice()
    who = who or buyer()
    return writer.write_message(skeleton(rung, record, who), invoice=record,
                                buyer=who, today=TODAY, **kwargs)


# --- no legal wording is authored in messages.yaml ------------------------

def test_the_message_config_contains_no_legal_figure_or_citation() -> None:
    """The whole point of the design: one wording, in config/legal.yaml.

    A percentage, a currency figure or a statute reference appearing here would
    be a fourth restatement of a legal position and could drift from the other
    three.
    """
    body = "\n".join(
        line for line in MESSAGES_PATH.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )
    assert not re.search(r"\d+(\.\d+)?\s*%", body), "a percentage is authored here"
    assert not re.search(r"(?:₹|Rs\.?\s?)\s?\d", body), "a currency figure is authored here"
    for citation in ("MSMED", "Income-tax", "Samadhaan", "43B", "37(2)(g)"):
        assert citation not in body, f"{citation} is cited here"
    assert not re.search(r"Section\s*\d", body), "a section number is cited here"


def test_legal_sentences_reach_the_message_through_the_skeleton() -> None:
    """The rendered fact must appear verbatim, not paraphrased."""
    sk = skeleton(2)
    message = draft(2)
    assert sk["facts_by_key"]["section_16"] in message["body"]


# --- language selection ---------------------------------------------------

def test_hinglish_is_used_for_a_small_trader() -> None:
    assert draft(1, who=trader())["language"] == "hinglish"


def test_a_corporate_gets_english_even_when_it_prefers_hinglish() -> None:
    """A WhatsApp register to a finance team reads as unprofessional."""
    stubborn = buyer(language_pref="hinglish")
    assert draft(1, who=stubborn)["language"] == "english"


def test_a_small_trader_preferring_english_gets_english() -> None:
    assert draft(1, who=trader(language_pref="english"))["language"] == "english"


def test_the_hinglish_message_reads_as_hinglish() -> None:
    body = draft(1, who=trader())["body"]
    assert "Sir," in body
    assert "pending hai" in body or "bata dijiye" in body


def test_hinglish_keeps_the_legal_sentence_in_canonical_english() -> None:
    """Code-switching is authentic, and it keeps one legal wording."""
    sk = skeleton(2, who=trader())
    message = draft(2, who=trader())
    assert message["language"] == "hinglish"
    assert sk["facts_by_key"]["section_16"] in message["body"]


# --- the guardrail --------------------------------------------------------

def values_for(rung: int):
    record = invoice()
    sk = skeleton(rung, record)
    return sk, writer._values(sk, record, buyer())


def test_a_wrong_interest_figure_is_rejected() -> None:
    """One paisa out and the message never leaves."""
    sk, values = values_for(2)
    wrong = format_inr(int(sk["numbers"]["interest_paise"]) + 1, decimals=True)
    message = {"subject": "Invoice INV-2026-0204",
               "body": f"Invoice {values['invoice_id']} for {values['outstanding']}, "
                       f"due {values['statutory_due_date']}, {values['days_overdue']} "
                       f"days late. Interest to date: {wrong}. Please pay."}
    ok, failures = writer.passes_guardrail(message, sk, values)
    assert not ok
    assert any("does not come from the law engine" in f for f in failures)


def test_a_missing_required_figure_is_rejected() -> None:
    sk, values = values_for(2)
    message = {"subject": "Invoice INV-2026-0204",
               "body": f"Invoice {values['invoice_id']} for {values['outstanding']} "
                       f"is late. Please pay when you can, it would help us a lot."}
    ok, failures = writer.passes_guardrail(message, sk, values)
    assert not ok
    assert any("is missing" in f for f in failures)


def test_an_invented_amount_is_rejected() -> None:
    sk, values = values_for(2)
    message = {"subject": "Invoice",
               "body": f"{values['invoice_id']} {values['outstanding']} "
                       f"{values['statutory_due_date']} {values['days_overdue']} "
                       f"{values['interest']} plus a handling charge of ₹99,999."}
    ok, failures = writer.passes_guardrail(message, sk, values)
    assert not ok
    assert any("₹99,999" in f for f in failures)


@pytest.mark.parametrize("word", messages()["threat_words"])
def test_every_threat_word_is_rejected(word: str) -> None:
    sk, values = values_for(2)
    message = {"subject": "Invoice INV-2026-0204",
               "body": f"Invoice {values['invoice_id']} for {values['outstanding']}, "
                       f"due {values['statutory_due_date']}, {values['days_overdue']} "
                       f"days. Interest: {values['interest']}. We may pursue {word}."}
    ok, failures = writer.passes_guardrail(message, sk, values)
    assert not ok
    assert any("threatening language" in f for f in failures)


def test_a_rung_one_message_carries_no_legal_citation() -> None:
    message = draft(1)
    whole = f"{message['subject']}\n{message['body']}"
    assert not writer.citation_pattern().search(whole)
    for word in ("interest", "statutory", "tax", "deduction"):
        assert not re.search(rf"\b{word}\b", whole.lower())


def test_a_rung_one_draft_that_mentions_the_act_is_rejected() -> None:
    sk, values = values_for(1)
    message = {"subject": "Invoice INV-2026-0204",
               "body": f"Invoice {values['invoice_id']} for {values['outstanding']} "
                       f"is due under Section 15 of the MSMED Act 2006. Please pay."}
    ok, failures = writer.passes_guardrail(message, sk, values)
    assert not ok
    assert any("courtesy reminder cites" in f for f in failures)


def test_an_unfilled_placeholder_is_rejected() -> None:
    sk, values = values_for(1)
    message = {"subject": "Invoice {invoice_id}",
               "body": "Dear {contact_name}, please settle {invoice_id} for "
                       "{outstanding}. Thank you very much for your help here."}
    ok, failures = writer.passes_guardrail(message, sk, values)
    assert not ok
    assert any("placeholder" in f for f in failures)


# --- the leaked-None check, which must not flag ordinary prose -----------

@pytest.mark.parametrize(("text", "flagged"), [
    ("None of this is where either of us wants to be.", False),
    ("Nonetheless, we would appreciate payment.", False),
    ("There is none outstanding.", False),
    ("Amount: None", True),
    ("Interest (None) accrued", True),
    ("due date = None", True),
])
def test_the_none_check_catches_leaks_not_the_english_word(text: str, flagged: bool) -> None:
    """A model writing "None of this..." must not be rejected for it."""
    assert bool(writer.LEAKED_NONE_PATTERN.search(text)) is flagged


# --- drafting end to end --------------------------------------------------

@pytest.mark.parametrize("rung", [1, 2, 3])
def test_every_rung_drafts_a_clean_message(rung: int) -> None:
    message = draft(rung)
    assert message["fallback_used"] is False
    assert message["guardrail"] == "passed"
    assert message["subject"] and len(message["body"]) > 60


@pytest.mark.parametrize("rung", [1, 2, 3])
def test_every_rung_drafts_cleanly_in_hinglish_too(rung: int) -> None:
    message = draft(rung, who=trader())
    assert message["fallback_used"] is False
    assert message["language"] == "hinglish"


def test_mock_output_is_deterministic() -> None:
    """Same input twice, identical bytes -- the run has to be reproducible."""
    first, second = draft(2), draft(2)
    assert first == second


def test_the_required_figures_actually_appear() -> None:
    sk = skeleton(3)
    message = draft(3)
    whole = f"{message['subject']}\n{message['body']}"
    for figure in writer.required_numbers(sk, writer._values(sk, invoice(), buyer())):
        assert figure in whole


# --- the fallback ---------------------------------------------------------

def force_failure(monkeypatch):
    monkeypatch.setattr(writer, "llm",
                        lambda prompt, purpose, variant=None:
                        '{"subject": "Pay up", "body": "Settle this or else, we may '
                        'go to court over the ₹1 you owe us right now today."}')


def test_a_draft_that_cannot_be_fixed_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    force_failure(monkeypatch)
    message = draft(2)
    assert message["fallback_used"] is True
    assert message["source"] == "rule"
    assert "or else" not in message["body"].lower()


def test_the_fallback_passes_its_own_guardrail(monkeypatch: pytest.MonkeyPatch) -> None:
    """It assembles only skeleton content, so it must pass by construction."""
    force_failure(monkeypatch)
    for rung in (1, 2, 3):
        message = draft(rung)
        assert message["guardrail"] == "passed (fallback)", (rung, message["guardrail"])


def test_the_fallback_is_logged_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent downgrade to a worse message is exactly what audit exists for."""
    force_failure(monkeypatch)
    draft(2)
    entries = audit.entries_for("INV-2026-0204")
    assert entries and entries[-1]["action"] == "writer_fallback"
    assert entries[-1]["detail"]["draft_failures"]


def test_regeneration_is_attempted_before_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def counting(prompt, purpose, variant=None):
        calls.append(prompt)
        return '{"subject": "x", "body": "we will go to court, pay ₹1 now please ok"}'

    monkeypatch.setattr(writer, "llm", counting)
    message = draft(2)
    assert len(calls) == 2, "the draft was not regenerated before falling back"
    assert "rejected" in calls[1], "the retry did not tell the model what was wrong"
    assert message["attempts"] == 2


# --- rungs that send nothing ---------------------------------------------

def test_rung_four_refuses_to_draft() -> None:
    with pytest.raises(writer.NotSendable):
        draft(4)


def test_a_disputed_invoice_refuses_to_draft() -> None:
    record = dict(invoice(), status="disputed")
    with pytest.raises(writer.NotSendable):
        draft(2, record=record)


# --- substring bugs the guardrail used to have ---------------------------

@pytest.mark.parametrize("phrase", [
    "Koi issue ho to bata dijiye",          # "sue" inside "issue"
    "We will pursue this internally",       # "sue" inside "pursue"
    "Please define a date that works",      # "fine" inside "define"
    "That would be fine by us",             # "fine" as ordinary English
])
def test_benign_phrases_are_not_read_as_threats(phrase: str) -> None:
    """Matching threat words as substrings rejected perfectly good sentences.

    A false rejection is not harmless: it downgrades the buyer to the plainer
    fallback message for no reason.
    """
    sk, values = values_for(2)
    message = {"subject": "Invoice INV-2026-0204",
               "body": f"Invoice {values['invoice_id']} for {values['outstanding']}, "
                       f"due {values['statutory_due_date']}, {values['days_overdue']} "
                       f"days. Interest: {values['interest']}. {phrase}."}
    ok, failures = writer.passes_guardrail(message, sk, values)
    assert ok, failures


@pytest.mark.parametrize("text", [
    "₹5,00,000, which was due",
    "₹58,458.15.",
    "the balance of ₹8,68,900; please confirm",
])
def test_an_amount_followed_by_punctuation_is_read_correctly(text: str) -> None:
    """The currency pattern used to swallow a trailing comma or full stop.

    Every amount followed by a comma then looked like an invented figure, so a
    correct message was rejected.
    """
    found = writer.CURRENCY_PATTERN.findall(text)
    assert found
    for token in found:
        assert not token.endswith((",", ".")), f"punctuation swallowed: {token!r}"
