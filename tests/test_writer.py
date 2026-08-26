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

from engine import audit, brain, consolidate, law, rungs, writer
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
            written: bool = True, amount: int = 50_000_000,
            invoice_id: str = "INV-2026-0204") -> dict:
    return {
        "invoice_id": invoice_id, "buyer_id": "BUY-01",
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
    detail = entries[-1]["detail"]
    assert detail["rejected_drafts"]
    assert detail["rejected_drafts"][0]["failures"]


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


# --- the audit trail records what was written, and what was refused ------

def test_every_drafted_message_is_logged() -> None:
    """A judge asking "what did it actually say to this buyer" needs an answer."""
    message = draft(2)
    entries = audit.entries_for("INV-2026-0204")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "message_drafted"
    assert entry["actor"] == "writer"
    assert entry["source"] == "llm"


def test_the_log_carries_subject_rung_language_and_verdict() -> None:
    message = draft(2)
    detail = audit.entries_for("INV-2026-0204")[0]["detail"]
    assert detail["subject"] == message["subject"]
    assert detail["rung"] == 2
    assert detail["language"] == message["language"]
    assert detail["guardrail"] == "passed"
    assert detail["attempts"] == 1
    assert detail["fallback_used"] is False


def test_the_log_carries_the_words_themselves() -> None:
    """The message is the money-related action, so the words are the record."""
    message = draft(2)
    assert audit.entries_for("INV-2026-0204")[0]["detail"]["body"] == message["body"]


def test_a_clean_draft_records_no_rejected_text() -> None:
    draft(2)
    assert "rejected_drafts" not in audit.entries_for("INV-2026-0204")[0]["detail"]
    assert draft(2)["rejected_drafts"] == []


def test_a_refused_draft_is_kept_beside_its_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The most persuasive artefact this system produces about its own safety."""
    force_failure(monkeypatch)
    message = draft(2)
    detail = audit.entries_for("INV-2026-0204")[0]["detail"]

    rejected = detail["rejected_drafts"]
    assert len(rejected) == 2, "both attempts should be recorded"
    assert "or else" in rejected[0]["body"].lower(), "the refused text is not kept"
    assert any("threatening" in f for f in rejected[0]["failures"])

    # and the replacement sits in the same entry
    assert detail["body"] == message["body"]
    assert "or else" not in detail["body"].lower()


def test_the_reason_names_why_the_draft_was_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_failure(monkeypatch)
    draft(2)
    reason = audit.entries_for("INV-2026-0204")[0]["reason"]
    assert "refused" in reason
    assert "threatening" in reason


def test_the_returned_message_exposes_the_rejected_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So a dry run can show them without re-reading the log."""
    force_failure(monkeypatch)
    message = draft(2)
    assert len(message["rejected_drafts"]) == 2
    assert message["rejected_drafts"][0]["failures"]


def test_a_dry_run_writes_nothing() -> None:
    draft(2, log=False)
    assert audit.entries() == []


def test_the_log_lines_stay_valid_json() -> None:
    import json
    draft(1)
    draft(2)
    for line in audit.LOG_PATH.read_text(encoding="utf-8").strip().splitlines():
        entry = json.loads(line)
        assert entry["actor"] == "writer"
        assert entry["detail"]["subject"]


# ============================================================================
# consolidated (buyer-level) messages -- engine/consolidate.py's bundles
# ============================================================================

def bundle_action(rung: int, record: dict, who: dict | None = None) -> brain.Action:
    who = who or buyer()
    sk = skeleton(rung, record, who)
    return brain.Action(
        kind=brain.SEND, rung=rung, reason="test", source="rule",
        invoice_id=record["invoice_id"], buyer_id=record["buyer_id"],
        available_rung=sk["available_rung"], skeleton=sk,
    )


def two_invoice_bundle(rung_a: int = 1, rung_b: int = 1, who: dict | None = None):
    who = who or buyer()
    invoice_a = invoice(invoice_id="INV-2026-0301")
    invoice_b = invoice(invoice_id="INV-2026-0302")
    actions = [bundle_action(rung_a, invoice_a, who), bundle_action(rung_b, invoice_b, who)]
    invoices_by_id = {invoice_a["invoice_id"]: invoice_a, invoice_b["invoice_id"]: invoice_b}
    return actions, invoices_by_id


def draft_multi(rung_a: int = 1, rung_b: int = 1, who: dict | None = None, **kwargs):
    who = who or buyer()
    actions, invoices_by_id = two_invoice_bundle(rung_a, rung_b, who)
    return writer.write_consolidated_message(
        actions, invoices_by_id=invoices_by_id, buyer=who, today=TODAY, **kwargs)


# --- one envelope, two invoices' own content --------------------------------

def test_a_courtesy_bundle_states_both_invoices_ids_and_amounts() -> None:
    actions, invoices_by_id = two_invoice_bundle(1, 1)
    message = draft_multi(1, 1)
    for action in actions:
        values = writer._values(action.skeleton, invoices_by_id[action.invoice_id], buyer())
        assert values["invoice_id"] in message["body"]
        assert values["outstanding"] in message["body"]


def test_an_escalated_bundle_states_each_invoices_own_legal_facts() -> None:
    actions, _ = two_invoice_bundle(2, 3)
    message = draft_multi(2, 3)
    for action in actions:
        assert action.skeleton["facts_by_key"]["section_16"] in message["body"]


def test_a_courtesy_bundle_never_mentions_legal_content() -> None:
    """The whole point of the tier partition (plan 2c): a rung-1-only bundle
    stays exactly as legal-content-free as a single rung-1 message."""
    message = draft_multi(1, 1)
    lowered = f"{message['subject']}\n{message['body']}".lower()
    for word in messages()["rung_one_banned_words"]:
        assert word not in lowered


def test_hinglish_is_used_for_a_small_trader_bundle() -> None:
    assert draft_multi(1, 1, who=trader())["language"] == "hinglish"


# --- write_consolidated_message's own defensive barriers -------------------

def test_an_empty_bundle_is_rejected() -> None:
    with pytest.raises(ValueError):
        writer.write_consolidated_message([], invoices_by_id={}, buyer=buyer(), today=TODAY)


def test_a_bundle_spanning_two_buyers_is_rejected() -> None:
    """engine.consolidate.bundle_sends() never produces this; this is the
    writer's own second, independent barrier -- see CLAUDE.md's W3 plan."""
    invoice_a = invoice(invoice_id="INV-2026-0301")
    invoice_b = invoice(invoice_id="INV-2026-0302")
    other_buyer = buyer(buyer_id="BUY-02")
    actions = [bundle_action(1, invoice_a, buyer()),
              bundle_action(1, {**invoice_b, "buyer_id": "BUY-02"}, other_buyer)]
    with pytest.raises(ValueError, match="one buyer"):
        writer.write_consolidated_message(
            actions,
            invoices_by_id={invoice_a["invoice_id"]: invoice_a,
                           invoice_b["invoice_id"]: {**invoice_b, "buyer_id": "BUY-02"}},
            buyer=buyer(), today=TODAY,
        )


def test_a_mixed_tier_bundle_is_rejected() -> None:
    """engine.consolidate.bundle_sends() never mixes rung<=1 with rung>=2 for
    one buyer; this is the writer's own second, independent barrier."""
    with pytest.raises(ValueError, match="single rung tier"):
        draft_multi(1, 2)


def test_a_disputed_invoices_skeleton_can_never_be_drafted_here_either() -> None:
    """engine.consolidate.bundle_sends() already excludes a disputed invoice's
    HANDOFF action (it never carries a sendable skeleton). This proves the
    writer itself refuses too, if one somehow arrived -- a second barrier,
    not reliance on the first."""
    record = invoice(invoice_id="INV-2026-0301")
    disputed = {**record, "status": "disputed"}
    position = law.legal_position(disputed, TODAY)
    sk = rungs.fact_skeleton(4, position, disputed, buyer())
    assert sk["sends_to_buyer"] is False
    action = brain.Action(kind=brain.SEND, rung=4, reason="test", source="rule",
                          invoice_id=disputed["invoice_id"], buyer_id=disputed["buyer_id"],
                          available_rung=4, skeleton=sk)
    with pytest.raises(writer.NotSendable):
        writer.write_consolidated_message(
            [action], invoices_by_id={disputed["invoice_id"]: disputed},
            buyer=buyer(), today=TODAY,
        )


# --- the multi-invoice guardrail --------------------------------------------

def entries_for_rungs(rungs_list: list[int]):
    """Distinct invoices -- different amounts too, so each one's own required
    figures are genuinely distinguishable rather than coincidentally equal."""
    entries = []
    for i, rung in enumerate(rungs_list):
        record = invoice(invoice_id=f"INV-2026-030{i}", amount=50_000_000 + i * 12_345_00)
        sk = skeleton(rung, record)
        entries.append((sk, writer._values(sk, record, buyer())))
    return entries


def test_passes_guardrail_multi_rejects_a_message_missing_one_invoices_figure() -> None:
    entries = entries_for_rungs([2, 2])
    (_sk_a, values_a), (_sk_b, values_b) = entries
    message = {
        "subject": "Two invoices",
        "body": f"Invoice {values_a['invoice_id']} for {values_a['outstanding']}, "
                f"due {values_a['statutory_due_date']}, {values_a['days_overdue']} days "
                f"late, interest {values_a['interest']}. "
                f"Invoice {values_b['invoice_id']} is also outstanding, please pay soon.",
    }
    ok, failures = writer.passes_guardrail_multi(message, entries)
    assert not ok
    assert any(values_b["outstanding"] in f for f in failures)


def test_passes_guardrail_multi_rejects_an_amount_belonging_to_neither_invoice() -> None:
    entries = entries_for_rungs([2, 2])
    (_sk_a, values_a), (_sk_b, values_b) = entries
    invented = "₹99,99,999"
    assert invented not in (values_a["outstanding"], values_b["outstanding"])
    body = (f"Invoice {values_a['invoice_id']} for {values_a['outstanding']}, "
           f"due {values_a['statutory_due_date']}, {values_a['days_overdue']} days late, "
           f"interest {values_a['interest']}. Invoice {values_b['invoice_id']} for "
           f"{values_b['outstanding']}, due {values_b['statutory_due_date']}, "
           f"{values_b['days_overdue']} days late, interest {values_b['interest']}. "
           f"Also somehow {invented} is owed.")
    ok, failures = writer.passes_guardrail_multi({"subject": "Two invoices", "body": body}, entries)
    assert not ok
    assert any("does not come from the law engine" in f for f in failures)


def test_passes_guardrail_multi_accepts_a_valid_two_invoice_bundle() -> None:
    entries = entries_for_rungs([2, 2])
    (_sk_a, values_a), (_sk_b, values_b) = entries
    body = (f"Invoice {values_a['invoice_id']} for {values_a['outstanding']}, "
           f"due {values_a['statutory_due_date']}, {values_a['days_overdue']} days late, "
           f"interest {values_a['interest']}. Invoice {values_b['invoice_id']} for "
           f"{values_b['outstanding']}, due {values_b['statutory_due_date']}, "
           f"{values_b['days_overdue']} days late, interest {values_b['interest']}. "
           f"Please pay both when you can.")
    ok, failures = writer.passes_guardrail_multi({"subject": "Two invoices", "body": body}, entries)
    assert ok, failures


def test_passes_guardrail_multi_rejects_a_mixed_tier_bundle_defensively() -> None:
    """Should be structurally unreachable via write_consolidated_message (its
    own ValueError fires first), but the guardrail itself must also refuse a
    mixed bundle if one somehow reached it -- defense in depth."""
    entries = entries_for_rungs([1, 2])
    (_sk_a, values_a), (_sk_b, values_b) = entries
    body = (f"Invoice {values_a['invoice_id']} for {values_a['outstanding']}. "
           f"Invoice {values_b['invoice_id']} for {values_b['outstanding']}, "
           f"due {values_b['statutory_due_date']}, {values_b['days_overdue']} days late, "
           f"interest {values_b['interest']}.")
    ok, failures = writer.passes_guardrail_multi({"subject": "Two invoices", "body": body}, entries)
    assert not ok
    assert any("mixed" in f for f in failures)


# --- the consolidated fallback ----------------------------------------------

def test_fallback_message_multi_always_passes_its_own_guardrail() -> None:
    for tier in (consolidate.COURTESY, consolidate.ESCALATED):
        rungs_list = [1, 1] if tier == consolidate.COURTESY else [2, 3]
        entries = entries_for_rungs(rungs_list)
        sections = "\n\n".join(
            writer._fill(
                messages()["consolidated_section"]["english"][
                    "courtesy" if tier == consolidate.COURTESY else "factual"],
                values,
            )
            for _sk, values in entries
        )
        bundle_values = writer._bundle_wrapper_values(buyer(), sections, len(entries))
        message = writer.fallback_message_multi(bundle_values, "english", tier)
        ok, failures = writer.passes_guardrail_multi(message, entries)
        assert ok, failures


def test_a_bundle_that_repeatedly_fails_the_guardrail_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_llm(prompt, purpose, variant=None):
        import json
        return json.dumps({"subject": "x", "body": "we will sue you if you do not pay immediately"})

    monkeypatch.setattr(writer, "llm", broken_llm)
    message = draft_multi(2, 3)
    assert message["fallback_used"] is True
    assert message["guardrail"] in ("passed (fallback)", "failed")


# --- the audit trail: per-invoice, linked by bundle -------------------------

def test_consolidated_drafting_logs_one_entry_per_invoice_linked_by_bundle() -> None:
    actions, _ = two_invoice_bundle(2, 3)
    draft_multi(2, 3)
    ids = [a.invoice_id for a in actions]
    for inv_id in ids:
        entries = audit.entries_for(inv_id)
        writer_entries = [e for e in entries if e["actor"] == "writer"]
        assert len(writer_entries) == 1
        detail = writer_entries[0]["detail"]
        assert set(detail["bundle_invoice_ids"]) == set(ids)
        assert detail["bundle_tier"] == consolidate.ESCALATED


def test_a_consolidated_dry_run_writes_nothing() -> None:
    draft_multi(1, 1, log=False)
    assert audit.entries() == []
