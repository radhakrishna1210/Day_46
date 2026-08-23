"""The single door to the LLM.

Every AI call in this project goes through :func:`llm`. No module anywhere else
may import the anthropic SDK or hold an API key.

Mode is read from ``LLM_MODE`` in ``.env``:

* ``mock`` (default) -- deterministic canned responses. No API key, no network,
  no cost. This is what a judge cloning the repo gets out of the box.
* ``live``  -- real Anthropic API calls through the Anthropic SDK, with the
  model chosen per purpose in config/rules.yaml and the key read from .env.

The key is read from .env and passed to the client explicitly. It is never
exported into the shell: a global ANTHROPIC_API_KEY would make every tool on
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


def _live_response(prompt: str, purpose: str) -> str:
    """Call the real API. Model and limits come from config, the key from .env."""
    import anthropic

    from engine.config import rules

    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "LLM_MODE=live but ANTHROPIC_API_KEY is empty in .env. "
            "Put the key there, not in your shell."
        )

    config = rules()["llm"]
    model = config["models"][purpose]
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": int(config["max_tokens"][purpose]),
        "messages": [{"role": "user", "content": prompt}],
    }
    effort = (config.get("effort") or {}).get(purpose)
    if effort:
        request["output_config"] = {"effort": effort}

    # Explicit api_key: without it the SDK would fall back to an ambient
    # credential and bill something other than this project's key.
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(**request)

    last_usage.clear()
    last_usage.update({
        "purpose": purpose,
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    })
    return "".join(block.text for block in response.content if block.type == "text")


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
    if not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        print("ANTHROPIC_API_KEY is empty in .env. Put the key there, not in your shell.")
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


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check the live model against the guardrail.")
    parser.add_argument("--calibrate", action="store_true",
                        help="draft 3 messages and parse 3 replies against the real API")
    args = parser.parse_args()
    if not args.calibrate:
        print(f"LLM_MODE={get_mode()}. Run with --calibrate to test against the real API.")
        return 0
    return calibrate()


if __name__ == "__main__":
    raise SystemExit(main())
