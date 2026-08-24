"""Live-mode request shape, exercised against a fake SDK.

None of this spends quota. The point is to catch a parameter or format
mismatch before it reaches the wire: the Anthropic version of these tests
caught an effort parameter that one of the models rejects outright, which
would otherwise have been a 400 on the very first real call.

The second half is about what happens when the model will not answer. Gemini
applies its own content safety, and a rung 3 message states statutory
interest, a tax deadline and a tribunal reference -- legitimate, and exactly
the shape of text a generic filter can read as coercive. Nothing downstream
may die of that.
"""

from __future__ import annotations

import sys
import types
from datetime import date

import pytest

from engine import audit, brain, law, llm, promises, rungs, writer
from engine.config import rules


class FakeResponse:
    def __init__(self, text="{}", finish_reason=None, block_reason=None, ratings=None):
        self.text = text
        candidate = types.SimpleNamespace(finish_reason=finish_reason,
                                          safety_ratings=ratings or [])
        self.candidates = [candidate] if (finish_reason or text) else []
        self.prompt_feedback = (types.SimpleNamespace(block_reason=block_reason)
                                if block_reason else None)
        self.usage_metadata = types.SimpleNamespace(prompt_token_count=17,
                                                    candidates_token_count=42)


def fake_sdk(monkeypatch, response=None, raises=None):
    """Install a stand-in google.genai that records the request it was given."""
    captured: dict = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            if raises:
                raise raises
            return response or FakeResponse('{"subject": "s", "body": "b"}')

    class FakeClient:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key
            self.models = FakeModels()

    fake_types = types.SimpleNamespace(
        GenerateContentConfig=lambda **kw: kw,
        SafetySetting=lambda category, threshold: {"category": category,
                                                   "threshold": threshold},
    )
    # `from google import genai` is an attribute lookup on the google module,
    # so the stand-in package has to carry genai as an attribute, not just sit
    # in sys.modules under that name.
    fake_genai = types.SimpleNamespace(Client=FakeClient, types=fake_types)
    monkeypatch.setitem(sys.modules, "google", types.SimpleNamespace(genai=fake_genai))
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("LLM_MODE", "live")
    return captured


# --- the request we actually build ---------------------------------------

def test_the_model_matches_config_for_the_purpose(monkeypatch) -> None:
    captured = fake_sdk(monkeypatch)
    llm.llm("draft this", purpose="draft_message")
    assert captured["model"] == rules()["llm"]["models"]["draft_message"]


def test_classification_routes_to_the_cheaper_tier(monkeypatch) -> None:
    captured = fake_sdk(monkeypatch)
    llm.llm("classify this", purpose="parse_reply")
    assert captured["model"] == rules()["llm"]["models"]["parse_reply"]


def test_the_tier_split_is_currently_collapsed_pending_billing() -> None:
    """NOT a forgotten TODO: this is the documented billing-constrained tradeoff.

    The design is a cheap tier for classification and a stronger tier for
    drafting/judgment. draft_message and judgment_call were moved onto the
    same flash-tier model as parse_reply because this key's free tier has
    zero pro-tier quota and billing isn't available for it -- see CLAUDE.md
    ("draft_message/judgment_call are on Flash not Pro") and README's Future
    Work. Once billing is available, re-split the tiers and swap this back to
    asserting they differ.
    """
    models = rules()["llm"]["models"]
    assert models["draft_message"] == models["judgment_call"] == models["parse_reply"]


def test_the_key_is_passed_explicitly(monkeypatch) -> None:
    """The SDK also honours GOOGLE_API_KEY, which takes precedence if set.

    Relying on ambient resolution would let a stray environment variable decide
    which account gets billed.
    """
    captured = fake_sdk(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "some-other-key")
    llm.llm("draft this", purpose="draft_message")
    assert captured["api_key"] == "test-key-not-real"


def test_json_mode_is_requested_with_the_schema_for_that_purpose(monkeypatch) -> None:
    captured = fake_sdk(monkeypatch)
    llm.llm("classify this", purpose="parse_reply")
    config = captured["config"]
    assert config["response_mime_type"] == "application/json"
    assert config["response_json_schema"] is llm.SCHEMAS["parse_reply"]
    assert config["max_output_tokens"] == rules()["llm"]["max_tokens"]["parse_reply"]


def test_every_purpose_has_a_schema() -> None:
    assert set(llm.SCHEMAS) == set(llm.PURPOSES)


def test_the_reply_schema_constrains_intent_to_the_five_we_handle() -> None:
    """An out-of-range intent becomes structurally impossible, not merely rare."""
    assert set(llm.SCHEMAS["parse_reply"]["properties"]["intent"]["enum"]) == set(
        promises.INTENTS)


def test_the_prompt_is_passed_as_contents(monkeypatch) -> None:
    captured = fake_sdk(monkeypatch)
    llm.llm("the actual prompt text", purpose="draft_message")
    assert captured["contents"] == "the actual prompt text"


def test_usage_is_recorded_for_cost_reporting(monkeypatch) -> None:
    fake_sdk(monkeypatch)
    llm.llm("draft this", purpose="draft_message")
    assert llm.last_usage["input_tokens"] == 17
    assert llm.last_usage["output_tokens"] == 42


# --- safety settings ------------------------------------------------------

def test_safety_thresholds_are_sent_explicitly(monkeypatch) -> None:
    """Left at default, a message stating statutory interest and a tax deadline
    is the shape of text a generic filter reads as coercive."""
    captured = fake_sdk(monkeypatch)
    llm.llm("draft this", purpose="draft_message")
    settings = captured["config"]["safety_settings"]
    assert len(settings) == 4
    assert {s["threshold"] for s in settings} == {"BLOCK_ONLY_HIGH"}


def test_safety_is_permissive_but_not_switched_off() -> None:
    """Our own guardrail is stricter and more specific than a generic filter,
    but turning filtering off altogether would still be the wrong instinct."""
    thresholds = set(rules()["llm"]["safety"].values())
    assert thresholds == {"BLOCK_ONLY_HIGH"}
    assert not thresholds & {"OFF", "BLOCK_NONE"}


# --- what a block actually looks like ------------------------------------

def test_a_blocked_response_raises_a_refusal_naming_the_category(monkeypatch) -> None:
    fake_sdk(monkeypatch, response=FakeResponse(
        text="", finish_reason="SAFETY",
        ratings=[types.SimpleNamespace(category="HARM_CATEGORY_HARASSMENT",
                                       probability="HIGH")]))
    with pytest.raises(llm.LLMRefused, match="HARASSMENT"):
        llm.llm("draft this", purpose="draft_message")


def test_a_blocked_prompt_raises_a_refusal(monkeypatch) -> None:
    """An input-side block produces no candidates at all, so it is checked first."""
    fake_sdk(monkeypatch, response=FakeResponse(text="", block_reason="SAFETY"))
    with pytest.raises(llm.LLMRefused, match="prompt was blocked"):
        llm.llm("draft this", purpose="draft_message")


def test_empty_text_is_treated_as_a_refusal(monkeypatch) -> None:
    """The docs do not say whether .text raises or returns nothing when blocked,
    so an empty answer is refused either way rather than sent as a message."""
    fake_sdk(monkeypatch, response=FakeResponse(text=""))
    with pytest.raises(llm.LLMRefused):
        llm.llm("draft this", purpose="draft_message")


def test_a_transport_failure_is_unavailable_not_refused(monkeypatch) -> None:
    """The distinction matters: a refusal should not be retried identically."""
    fake_sdk(monkeypatch, raises=OSError("connection reset"))
    with pytest.raises(llm.LLMUnavailable, match="connection reset"):
        llm.llm("draft this", purpose="draft_message")


def test_an_empty_key_fails_clearly(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(llm.LLMUnavailable, match="GEMINI_API_KEY"):
        llm.llm("anything", purpose="draft_message")


# --- nothing downstream may die of it ------------------------------------

def sample_invoice() -> dict:
    return {"invoice_id": "INV-2026-0204", "buyer_id": "BUY-01",
            "description": "400 kg HDPE granules", "po_number": None,
            "issue_date": "2025-06-01", "acceptance_date": "2025-06-01",
            "written_agreement": False, "agreed_days": None, "agreed_due_date": None,
            "amount_paise": 50_000_000, "status": "open", "partial_payments": [],
            "amount_paid_paise": 0, "paid_date": None}


BUYER = {"buyer_id": "BUY-01", "name": "ABC Traders", "profile": "corporate",
         "language_pref": "english", "contact_name": "R. Kumar"}


def raiser(exc):
    def boom(*args, **kwargs):
        raise exc
    return boom


def test_a_safety_block_falls_back_instead_of_aborting_the_run(
    monkeypatch, tmp_path,
) -> None:
    """Ninety invoices must not be lost because one message was filtered.

    Before this was handled, llm() was called with no try/except: a block would
    have propagated out of the writer and killed the whole run.
    """
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    monkeypatch.setattr(writer, "llm", raiser(llm.LLMRefused(
        "the response was blocked by content safety (HARASSMENT=HIGH)")))

    record, today = sample_invoice(), date(2026, 8, 24)
    skeleton = rungs.fact_skeleton(2, law.legal_position(record, today), record, BUYER)
    message = writer.write_message(skeleton, invoice=record, buyer=BUYER, today=today)

    assert message["fallback_used"] is True
    assert len(message["body"]) > 60
    assert any("content safety" in failure
               for draft in message["rejected_drafts"]
               for failure in draft["failures"])


def test_the_safety_block_reaches_the_audit_trail(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    audit.enable()
    monkeypatch.setattr(writer, "llm", raiser(llm.LLMRefused("blocked (HARASSMENT=HIGH)")))

    record, today = sample_invoice(), date(2026, 8, 24)
    skeleton = rungs.fact_skeleton(2, law.legal_position(record, today), record, BUYER)
    writer.write_message(skeleton, invoice=record, buyer=BUYER, today=today)

    entry = audit.entries_for("INV-2026-0204")[-1]
    assert entry["action"] == "writer_fallback"
    assert "HARASSMENT" in str(entry["detail"]["rejected_drafts"])


def test_a_blocked_reply_becomes_noise_rather_than_killing_the_sweep(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    monkeypatch.setattr(promises, "llm", raiser(llm.LLMRefused("blocked")))
    parsed = promises.parse_reply("kuch bhi", date(2026, 8, 24), log=False)
    assert parsed["intent"] == "noise"
    assert any("could not read" in note for note in parsed["downgraded"])


def test_a_blocked_judgment_call_leaves_the_rule_standing(monkeypatch) -> None:
    """The model may only ever soften, so losing it costs nothing."""
    monkeypatch.setattr(brain, "llm", raiser(llm.LLMUnavailable("network down")))
    decision, reason = brain._ask_llm(sample_invoice(), {"days_overdue": 50}, [],
                                      "send a rung 2 message")
    assert decision == "proceed"
    assert "rule stands" in reason


# --- the sole door still holds -------------------------------------------

def test_mock_mode_is_untouched_by_any_of_this(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODE", "mock")
    first = llm.llm("same prompt", purpose="draft_message", variant="rung1_english")
    second = llm.llm("same prompt", purpose="draft_message", variant="rung1_english")
    assert first == second
