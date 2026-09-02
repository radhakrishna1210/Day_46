"""The single door to the LLM.

Every AI call in this project goes through :func:`llm`. No module anywhere else
may import the google-genai SDK or hold an API key.

Mode is read from ``LLM_MODE`` in ``.env``:

* ``mock`` (default) -- deterministic canned responses. No API key, no network,
  no cost. This is what a judge cloning the repo gets out of the box.
* ``live``  -- real Gemini API calls through the google-genai SDK, with the
  model chosen per purpose in config/rules.yaml and the key read from .env.

The key is read from .env and passed to the client explicitly. It is never
exported into the shell: a global GEMINI_API_KEY would make every tool on
this machine bill the API, which is not what anyone wants from a demo.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Final

from dotenv import load_dotenv

# Allow running this file directly as a script as well as importing it.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

MOCK: Final = "mock"
LIVE: Final = "live"

#: Every purpose the rest of the codebase is allowed to ask for. Keeping this
#: closed means a typo raises instead of silently getting a generic answer.
PURPOSES: Final[tuple[str, ...]] = (
    "parse_reply",      # buyer's free text -> structured intent (promises.py)
    "draft_message",    # write the message for a rung (writer.py)
    "judgment_call",    # genuinely ambiguous case, reasoning goes to audit (brain.py)
)

#: Deterministic stand-in responses, one per purpose. Day 6 replaces these with
#: shapes that match what the real prompts return.
_CANNED: Final[dict[str, str]] = {
    "parse_reply": '{"intent": "promise", "date": "2026-09-05", "amount": "full"}',
    "draft_message": (
        "MOCK DRAFT: Dear Sir/Madam, this is a reminder about the outstanding "
        "invoice noted above. Kindly let us know the expected payment date."
    ),
    "judgment_call": '{"decision": "wait", "reason": "MOCK: insufficient signal to escalate"}',
}


def get_mode() -> str:
    """Return the configured LLM mode, lowercased. Defaults to ``mock``."""
    return os.getenv("LLM_MODE", MOCK).strip().lower() or MOCK


def llm(prompt: str, purpose: str, variant: str | None = None) -> str:
    """Ask the model for something and get text back.

    Args:
        prompt: The fully rendered prompt.
        purpose: One of :data:`PURPOSES`. Chooses the canned response in mock
            mode and (later) the model and system prompt in live mode.
        variant: Mock mode only. Selects which canned response to return where
            one purpose has several shapes -- a message draft differs by rung
            and language. Live mode ignores it entirely.

    Returns:
        The model's text response.

    Raises:
        ValueError: If ``purpose`` is not a known purpose.
        NotImplementedError: If mode is ``live`` (not built yet).
        RuntimeError: If ``LLM_MODE`` is set to something unrecognised.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"unknown llm purpose {purpose!r}; expected one of {PURPOSES}")

    mode = get_mode()
    if mode == MOCK:
        return _mock_response(prompt, purpose, variant)
    if mode == LIVE:
        return _live_response(prompt, purpose)
    raise RuntimeError(f"LLM_MODE={mode!r} is not recognised; expected {MOCK!r} or {LIVE!r}")


#: Token usage from the most recent live call, for cost reporting.
last_usage: dict[str, Any] = {}


class LLMError(RuntimeError):
    """Base for anything that stops the model answering."""


class LLMUnavailable(LLMError):
    """Configuration or transport failure -- no key, network down, bad model id."""


class LLMRefused(LLMError):
    """Content safety blocked the prompt or the response.

    Distinct from LLMUnavailable on purpose: a refusal is about what was asked,
    and callers respond to it differently -- the writer falls back to the plain
    factual message rather than retrying an identical request.
    """


#: The response contract for each purpose. Gemini enforces these server-side,
#: so a malformed answer is impossible rather than merely unlikely. The parsers
#: downstream still validate, because a schema is the model's contract and not
#: a guarantee.
SCHEMAS: Final[dict[str, dict[str, Any]]] = {
    "draft_message": {
        "type": "object",
        "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
        "required": ["subject", "body"],
    },
    "parse_reply": {
        "type": "object",
        "properties": {
            "intent": {"type": "string",
                       "enum": ["promise", "dispute", "refusal", "question", "noise"]},
            "date_hint": {"type": ["string", "null"]},
            "amount": {"type": ["string", "null"], "enum": ["full", "partial", None]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "quote": {"type": "string"},
        },
        "required": ["intent", "confidence", "quote"],
    },
    "judgment_call": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["wait", "proceed"]},
            "reason": {"type": "string"},
        },
        "required": ["decision", "reason"],
    },
}


def _safety_settings(types: Any) -> list[Any]:
    """Thresholds from config, never the SDK defaults.

    Left at default, a message stating statutory interest and a tax deadline is
    the shape of text a generic filter reads as coercive.
    """
    from engine.config import rules

    return [
        types.SafetySetting(category=category, threshold=threshold)
        for category, threshold in (rules()["llm"].get("safety") or {}).items()
    ]


def _record_usage(response: Any, purpose: str, model: str) -> None:
    """Capture token counts defensively -- a renamed field must not break a call."""
    usage = getattr(response, "usage_metadata", None)
    last_usage.clear()
    last_usage.update({
        "purpose": purpose,
        "model": model,
        "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
    })


def _text_or_refusal(response: Any, purpose: str) -> str:
    """Pull the text out, or raise a refusal that names why.

    Checked in this order because an input-side block produces no candidates at
    all, and reading .text first could raise a less informative error.
    """
    feedback = getattr(response, "prompt_feedback", None)
    blocked = getattr(feedback, "block_reason", None) if feedback else None
    if blocked:
        raise LLMRefused(f"the prompt was blocked by content safety ({blocked})")

    candidates = list(getattr(response, "candidates", None) or [])
    finish = getattr(candidates[0], "finish_reason", None) if candidates else None
    if finish and str(finish).upper().endswith("SAFETY"):
        ratings = getattr(candidates[0], "safety_ratings", None) or []
        detail = ", ".join(
            f"{getattr(r, 'category', '?')}={getattr(r, 'probability', '?')}"
            for r in ratings
        )
        raise LLMRefused(f"the response was blocked by content safety ({detail or finish})")

    try:
        text = response.text
    except Exception as exc:                     # some SDKs raise on empty parts
        raise LLMRefused(f"no usable response ({type(exc).__name__}: {exc})") from exc

    if not text or not text.strip():
        raise LLMRefused(f"the model returned no text (finish_reason={finish})")
    return text


def _client(key: str) -> Any:
    from google import genai

    # Explicit api_key: the SDK also honours GOOGLE_API_KEY, which takes
    # precedence, and an ambient credential must not decide who gets billed.
    return genai.Client(api_key=key)


def _live_response(prompt: str, purpose: str) -> str:
    """Call the real API. Model, limits and safety come from config; key from .env."""
    from google.genai import types

    from engine.config import rules

    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise LLMUnavailable(
            "LLM_MODE=live but GEMINI_API_KEY is empty in .env. "
            "Put the key there, not in your shell."
        )

    config = rules()["llm"]
    model = config["models"][purpose]
    client = _client(key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=int(config["max_tokens"][purpose]),
                response_mime_type="application/json",
                response_json_schema=SCHEMAS[purpose],
                safety_settings=_safety_settings(types),
            ),
        )
    except LLMError:
        raise
    except Exception as exc:
        raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

    _record_usage(response, purpose, model)
    return _text_or_refusal(response, purpose)


def _mock_response(prompt: str, purpose: str, variant: str | None = None) -> str:
    """Canned response, tagged with a stable digest of the prompt.

    The digest makes it obvious in logs which call produced which line, while
    staying byte-for-byte reproducible across runs -- so mock-mode simulations
    are as repeatable as the seeded random data.

    Message drafts come from config/messages.yaml rather than from this file.
    They are content a human should be able to review and retune without
    touching Python, and they carry no legal prose of their own: every legal
    sentence reaches them already rendered from config/legal.yaml. The
    placeholders they contain are substituted by engine/writer.py after the
    call, so a mock draft travels the same guardrail path as a live one.
    """
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]

    if purpose == "parse_reply" and variant:
        from engine.config import replies

        fixtures = {item["key"]: item for item in replies()["fixtures"]}
        if variant not in fixtures:
            raise KeyError(
                f"no canned reply for variant {variant!r}; "
                f"known variants: {sorted(fixtures)}"
            )
        return json.dumps(fixtures[variant]["response"], ensure_ascii=False)

    if purpose == "draft_message" and variant:
        from engine.config import messages

        drafts = messages()["mock_drafts"]
        if variant not in drafts:
            raise KeyError(
                f"no mock draft for variant {variant!r}; "
                f"known variants: {sorted(drafts)}"
            )
        draft = drafts[variant]
        return json.dumps(
            {"subject": draft["subject"], "body": draft["body"].strip(),
             "mock": f"{purpose}:{variant}:{digest}"},
            ensure_ascii=False,
        )

    return f"[mock:{purpose}:{digest}] {_CANNED[purpose]}"

# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

#: Replies fed to the real model, with what the fixtures claim they mean. The
#: point is to find out whether a model that has NOT been handed the answer
#: reaches the same one.
CALIBRATION_REPLIES = (
    ("boss thoda time do, 5 tarikh tak ho jayega", "promise"),
    ("material mein problem thi, 12 units damage the -- pehle credit note bhejo", "dispute"),
    ("ok", "noise"),
)


def calibrate() -> int:
    """Draft three messages and parse three replies against the real API.

    Everything downstream has only ever seen fixtures written by hand, so the
    guardrail has never met unconstrained model prose and the reply parser has
    never met a reply it was not handed the answer to. This finds out whether
    the guardrail is too strict (silent fallbacks in the simulator, which would
    measure the wrong system) or too loose, before Day 8 depends on it.
    """
    from datetime import date

    from data import store
    from engine import law, promises, rungs, watchdog, writer
    from engine.money import enable_unicode_output

    enable_unicode_output()
    if get_mode() != LIVE:
        print(f"LLM_MODE is {get_mode()!r}. Set LLM_MODE=live in .env to calibrate.")
        return 1
    if not (os.getenv("GEMINI_API_KEY") or "").strip():
        print("GEMINI_API_KEY is empty in .env. Put the key there, not in your shell.")
        return 1
    if not store.dataset_exists():
        print("no dataset found -- run: python data/generate.py --seed 42")
        return 1

    today = date.fromisoformat(store.load_meta()["simulation_start"])
    buyers = {b["buyer_id"]: b for b in store.load_buyers()}
    invoice = watchdog.overdue_invoices(store.load_invoices(), today)[0]
    buyer = buyers[invoice["buyer_id"]]
    position = law.legal_position(invoice, today)

    spend: list[dict[str, Any]] = []
    passed = failed = 0

    print("=" * 74)
    print("DRAFTING -- does real model prose survive the guardrail?")
    print("=" * 74)
    for rung in (1, 2, 3):
        if rung > position["available_rung"]:
            continue
        skeleton = rungs.fact_skeleton(rung, position, invoice, buyer)
        drafted = writer.write_message(skeleton, invoice=invoice, buyer=buyer,
                                       today=today, log=False)
        spend.append(dict(last_usage))
        clean = not drafted["fallback_used"]
        passed += clean
        failed += not clean
        print()
        print(f"rung {rung} [{drafted['language']}] "
              f"{'PASSED' if clean else 'FELL BACK'} after {drafted['attempts']} attempt(s)")
        for refused in drafted["rejected_drafts"]:
            print(f"  refused: {'; '.join(refused['failures'])}")
        print(f"  subject: {drafted['subject']}")
        for line in drafted["body"].splitlines()[:8]:
            print(f"  | {line}")

    print()
    print("=" * 74)
    print("PARSING -- does an unprompted model read these the way we assume?")
    print("=" * 74)
    for reply, expected in CALIBRATION_REPLIES:
        parsed = promises.parse_reply(reply, today, log=False)
        spend.append(dict(last_usage))
        agrees = parsed["intent"] == expected
        passed += agrees
        failed += not agrees
        print()
        print(f"{'AGREES' if agrees else 'DIFFERS'}  {reply!r}")
        print(f"  fixture says {expected}, live model says {parsed['intent']}"
              f"{', date ' + parsed['date'] if parsed['date'] else ''}")
        if parsed.get("downgraded"):
            print(f"  downgraded: {'; '.join(parsed['downgraded'])}")

    print()
    print("=" * 74)
    tokens_in = sum(u.get("input_tokens", 0) for u in spend)
    tokens_out = sum(u.get("output_tokens", 0) for u in spend)
    print(f"{passed} as expected, {failed} not. "
          f"{len(spend)} calls, {tokens_in} input + {tokens_out} output tokens.")
    if failed:
        print("Read the failures above before building the simulator: a guardrail")
        print("that rejects good prose would make Day 9 measure the fallback path.")
    return 0


def list_models() -> int:
    """Print the models this key can actually reach.

    The ids in config/rules.yaml were taken from published pricing pages, not
    from an authoritative list, so they need confirming before a real run.
    """
    from engine.config import rules
    from engine.money import enable_unicode_output

    enable_unicode_output()
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        print("GEMINI_API_KEY is empty in .env. Put the key there, not in your shell.")
        return 1

    try:
        client = _client(key)
        available = list(client.models.list())
    except Exception as exc:
        print(f"could not list models: {type(exc).__name__}: {exc}")
        return 1

    names = sorted(
        (getattr(m, "name", "") or "").removeprefix("models/") for m in available
    )
    print(f"{len(names)} models reachable with this key:")
    for name in names:
        print(f"  {name}")

    print()
    print("configured in config/rules.yaml:")
    for purpose, model in rules()["llm"]["models"].items():
        mark = "ok" if model in names else "NOT IN THE LIST ABOVE"
        print(f"  {purpose:<16}{model:<28}{mark}")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check the live model against the guardrail.")
    parser.add_argument("--calibrate", action="store_true",
                        help="draft 3 messages and parse 3 replies against the real API")
    parser.add_argument("--list-models", action="store_true",
                        help="print the model ids this key can reach, and check config")
    args = parser.parse_args()
    if args.list_models:
        return list_models()
    if not args.calibrate:
        print(f"LLM_MODE={get_mode()}. Run with --calibrate or --list-models.")
        return 0
    return calibrate()


if __name__ == "__main__":
    raise SystemExit(main())
