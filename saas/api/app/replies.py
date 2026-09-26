"""Reading a buyer's reply: what did they actually say about paying?

With LLM_MODE=live the reply goes to the engine's own reader,
engine.promises.parse_reply() -> engine/llm.py (Gemini), with every sanity
rule the engine applies. Without a key (LLM_MODE=mock, the default) the
engine's reader returns one canned answer -- fine for the simulator, useless
for a real reply -- so a small keyword reader stands in. It is labelled as
rules, never as AI, and it produces the same shape: one of the engine's five
intents, a date resolved by the engine's own resolve_date(), and the same
horizon / implausible-amount bounds from config/rules.yaml.

Either way this only SUGGESTS. A person confirms before anything changes.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from engine import llm as engine_llm
from engine import promises
from engine.config import rules

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
            "saturday": 5, "sunday": 6, "somvar": 0, "mangalvar": 1, "budhvar": 2,
            "guruvar": 3, "shukravar": 4, "shanivar": 5, "ravivar": 6}

REFUSAL = ("won't pay", "will not pay", "wont pay", "not going to pay", "refuse", "not paying",
           "can't pay", "cannot pay", "cant pay", "nahi denge", "nahi dunga", "nahi karenge",
           "paisa nahi", "no money", "stop sending", "don't contact", "do not contact")
PAYING = ("pay", "paid", "payment", "transfer", "neft", "rtgs", "imps", "upi", "cheque", "check",
          "clear", "release", "remit", "bhej", "de denge", "de dunga", "kar denge", "kar dunga",
          "ho jayega", "ho jaega", "settle", "dues", "amount")
PARTIAL = ("partial", "part payment", "half", "some amount", "thoda", "kuch amount", "part of")
QUESTION_START = ("what", "when", "which", "why", "how", "can you", "could you", "please send",
                  "pls send", "send me", "kya", "kaunsa", "kab")


def reader() -> str:
    return "ai" if engine_llm.get_mode() == "live" else "rules"


def read(text: str, today: date, outstanding_paise: int | None = None) -> dict[str, Any]:
    """One suggestion for a pasted reply, plus trip-wire flags for a human."""
    text = text.strip()
    if reader() == "ai":
        parsed = promises.parse_reply(text, today, outstanding_paise=outstanding_paise, log=False)
    else:
        parsed = offline(text, today, outstanding_paise)
    flags = []
    if parsed["intent"] != "dispute" and promises._looks_like_a_dispute(text):
        flags.append("It also contains complaint words (damage, quality, mismatch...). "
                     "Check whether this is really a dispute.")
    if parsed["intent"] == "promise" and promises._distinct_amounts_paise(text) >= 2:
        flags.append("It names more than one amount. Only the earliest date is tracked as the "
                     "promise; record the rest yourself.")
    return parsed | {"reader": reader(), "flags": flags}


# --------------------------------------------------------------------------
# the offline reader
# --------------------------------------------------------------------------

def _date_hint(t: str, today: date) -> str | None:
    """A hint in engine.promises.resolve_date()'s grammar, or None."""
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t)
    if m:
        return f"iso:{m.group(0)}"
    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            when = date(y, mo, d)
            if not m.group(3) and when <= today:
                when = date(y + 1, mo, d)
            return f"iso:{when.isoformat()}"
        except ValueError:
            pass
    if re.search(r"\b(day after tomorrow|parso|parson)\b", t):
        return "relative_days:2"
    if re.search(r"\b(tomorrow|tmrw|kal)\b", t):
        return "relative_days:1"
    m = re.search(r"\b(?:in|within|next)\s+(\d{1,3})\s+days?\b|\b(\d{1,3})\s+din\b", t)
    if m:
        return f"relative_days:{int(m.group(1) or m.group(2))}"
    if re.search(r"\b(next week|agle hafte|agle week|is hafte|this week)\b", t):
        return "relative_days:7"
    if re.search(r"\b(month[- ]end|end of (the )?month|mahine ke (end|aakhir|akhir))\b", t):
        return "month_end"
    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            ahead = (weekday - today.weekday()) % 7 or 7
            return f"relative_days:{ahead}"
    m = (re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)\b", t)
         or re.search(r"\b(\d{1,2})\s*(?:tarikh|tareekh|tarik|tareek)\b", t)
         or re.search(r"\b(?:by|on|before)\s+(?:the\s+)?(\d{1,2})\b(?!\s*(?:units?|pcs|pieces|%|days?|lakh|lac|cr))", t))
    if m:
        return f"day_of_month:{int(m.group(1))}"
    return None


def offline(text: str, today: date, outstanding_paise: int | None = None) -> dict[str, Any]:
    t = " ".join(text.lower().split())
    bounds = rules()["promises"]
    hint = _date_hint(t, today)
    when = promises.resolve_date(hint, today)
    downgraded: list[str] = []

    if promises._looks_like_a_dispute(t):
        intent, confidence = "dispute", "medium"
    elif any(w in t for w in REFUSAL):
        intent, confidence = "refusal", "medium"
    elif when is not None and any(w in t for w in PAYING + ("ho jayega", "done", "ok")):
        intent, confidence = "promise", "medium"
    elif when is not None:
        intent, confidence = "promise", "low"
    elif "?" in t or t.startswith(QUESTION_START):
        intent, confidence = "question", "medium"
    elif any(w in t for w in PAYING):
        intent, confidence = "question", "low"
        downgraded.append("talks about paying but gives no date we could read")
    else:
        intent, confidence = "noise", "low"

    if intent == "promise":
        max_days = int(bounds["max_horizon_days"])
        claimed = promises._extract_amount_paise(text)
        if when <= today:
            downgraded.append(f"a promise dated {when.isoformat()}, which is not in the future")
            intent, when = "question", None
        elif (when - today).days > max_days:
            downgraded.append(f"a promise {(when - today).days} days out, beyond the "
                              f"{max_days}-day limit in config/rules.yaml")
            intent, when = "question", None
        elif (outstanding_paise is not None and claimed is not None
              and claimed > outstanding_paise * float(bounds["amount_implausible_multiple"])):
            downgraded.append("the amount named is far more than what is owed")
            intent, when = "question", None

    amount = None
    if intent == "promise":
        amount = "partial" if any(w in t for w in PARTIAL) else "full"
    out: dict[str, Any] = {"intent": intent, "date": when.isoformat() if when else None,
                           "amount": amount, "confidence": confidence,
                           "quote": text[:160], "source": "rule"}
    if downgraded:
        out["downgraded"] = downgraded
    return out
