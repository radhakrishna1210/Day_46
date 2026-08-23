"""The single door to the LLM.

Every AI call in this project goes through :func:`llm`. No module anywhere else
may import the anthropic SDK or hold an API key.

Mode is read from ``LLM_MODE`` in ``.env``:

* ``mock`` (default) -- deterministic canned responses. No API key, no network,
  no cost. This is what a judge cloning the repo gets out of the box.
* ``live``  -- real Anthropic API calls. Not implemented yet (Day 6).
"""

from __future__ import annotations

import hashlib
import os
from typing import Final

from dotenv import load_dotenv

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


def llm(prompt: str, purpose: str) -> str:
    """Ask the model for something and get text back.

    Args:
        prompt: The fully rendered prompt.
        purpose: One of :data:`PURPOSES`. Chooses the canned response in mock
            mode and (later) the model and system prompt in live mode.

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
        return _mock_response(prompt, purpose)
    if mode == LIVE:
        raise NotImplementedError(
            "LLM_MODE=live is not implemented yet (planned for Day 6). "
            f"Set LLM_MODE={MOCK} in .env."
        )
    raise RuntimeError(f"LLM_MODE={mode!r} is not recognised; expected {MOCK!r} or {LIVE!r}")


def _mock_response(prompt: str, purpose: str) -> str:
    """Canned response, tagged with a stable digest of the prompt.

    The digest makes it obvious in logs which call produced which line, while
    staying byte-for-byte reproducible across runs -- so mock-mode simulations
    are as repeatable as the seeded random data.
    """
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
    return f"[mock:{purpose}:{digest}] {_CANNED[purpose]}"
