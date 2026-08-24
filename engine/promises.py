"""Promise tracker -- reads buyer replies and remembers what they committed to.

The clearest case in this project for a model rather than a rule: turning

    "boss thoda time do, 5 tarikh tak ho jayega"

into structure is exactly what an LLM is for, and no amount of regex would do
it well. So the classification is the model's.

The calendar is not. The model reports what it saw -- "the 5th", "next week",
"month end" -- and a rule here resolves that against the simulation clock.
Letting a model do date arithmetic is how "5 tarikh" said in late August turns
into a date in early August, which is in the past, which would immediately look
like a broken promise. Same reasoning as the law engine doing its own sums.

Nothing the model returns is trusted on its own:

  * an intent outside the five known ones becomes noise
  * a promise whose date will not resolve becomes a question -- we have to ask
  * a promise dated in the past is refused

Every downgrade is logged with the model's raw output, so a reviewer can see
what was said and what we made of it.

A dispute stops everything. Chasing a buyer who has raised a quality complaint
is how a supplier loses a customer, so the invoice is marked disputed and the
brain hands it to a human on its next pass.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, timedelta
from typing import Any

from engine import audit
from engine.config import rules
from engine.llm import LLMError, llm

#: The only intents the rest of the system understands.
INTENTS = ("promise", "dispute", "refusal", "question", "noise")

OPEN, KEPT, BROKEN = "open", "kept", "broken"


def _as_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


# --------------------------------------------------------------------------
# resolving what the model saw into an actual date
# --------------------------------------------------------------------------

def resolve_date(hint: str | None, today: date) -> date | None:
    """Turn a date hint into a real date, forwards from today.

    Grammar, documented in config/replies.yaml:
        day_of_month:5    the next 5th, this month or next
        relative_days:7   seven days from today
        iso:2026-09-05    a date the buyer stated outright
        month_end         the last day of the current month
    """
    if not hint:
        return None
    hint = str(hint).strip()

    if hint == "month_end":
        return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])

    match = re.fullmatch(r"day_of_month:(\d{1,2})", hint)
    if match:
        day = int(match.group(1))
        if not 1 <= day <= 31:
            return None
        # The next occurrence, never a date already past. "5 tarikh" said on
        # the 26th means next month, not three weeks ago.
        for year, month in ((today.year, today.month),
                            (today.year + (today.month == 12), today.month % 12 + 1)):
            last = calendar.monthrange(year, month)[1]
            if day <= last:
                candidate = date(year, month, day)
                if candidate > today:
                    return candidate
        return None

    match = re.fullmatch(r"relative_days:(\d{1,3})", hint)
    if match:
        return today + timedelta(days=int(match.group(1)))

    match = re.fullmatch(r"iso:(\d{4}-\d{2}-\d{2})", hint)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None

    return None


# --------------------------------------------------------------------------
# a coarse, rule-based amount scan -- for the sanity bound only, never for
# recording a figure the model itself does not report
# --------------------------------------------------------------------------

_AMOUNT_UNITS = {
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "crore": 1_00_00_000, "crores": 1_00_00_000, "cr": 1_00_00_000,
}

#: Only fires on an explicit currency mark or an explicit lakh/crore unit,
#: never on a bare number -- so a day-of-month or any other incidental digit
#: in the reply is never mistaken for money.
_AMOUNT_PATTERNS = (
    re.compile(r"(?:₹|\brs\.?\b|\binr\b)\s*(\d[\d,]*(?:\.\d+)?)"
               r"\s*(lakh|lakhs|lac|lacs|crore|crores|cr)?\b", re.IGNORECASE),
    re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*\b(lakh|lakhs|lac|lacs|crore|crores|cr)\b",
               re.IGNORECASE),
)


def _extract_amount_paise(text: str) -> int | None:
    """The largest rupee figure explicitly named in free text, in paise.

    Not ledger-grade parsing -- a rule, used only to sanity-check a promised
    amount against what is actually outstanding (config/rules.yaml
    promises.amount_implausible_multiple). Returns None when nothing that
    looks like a stated amount is found.
    """
    best: int | None = None
    for pattern in _AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            unit = (match.group(2) or "").lower()
            if unit:
                value *= _AMOUNT_UNITS[unit]
            paise = int(round(value * 100))
            if best is None or paise > best:
                best = paise
    return best


def _distinct_amounts_paise(text: str) -> int:
    """How many DIFFERENT rupee figures are named in free text.

    See docs/edge_cases.md TC-036: "1 lakh Friday ko aur remaining 4 lakh
    next month" names two. Coarse, like _extract_amount_paise -- a trip-wire
    for "this reply may carry more than one instalment", never a ledger.
    """
    found: set[int] = set()
    for pattern in _AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            unit = (match.group(2) or "").lower()
            if unit:
                value *= _AMOUNT_UNITS[unit]
            found.add(int(round(value * 100)))
    return len(found)


#: Coarse, English/Hinglish dispute language -- a trip-wire only, per
#: docs/edge_cases.md TC-032 ("goods were damaged, but I'll pay ... Friday").
#: Never used to change `intent`: a dispute is the model's call to make, this
#: only flags a promise worth a second look by a human reading the trail.
_DISPUTE_WORDS: tuple[str, ...] = (
    "damage", "damaged", "defect", "defective", "faulty", "quality",
    "not received", "never received", "credit note", "dispute", "mismatch",
    "wrong quantity", "rejected", "returning", "reject the",
)


def _looks_like_a_dispute(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in _DISPUTE_WORDS)


# --------------------------------------------------------------------------
# reading a reply
# --------------------------------------------------------------------------

def parse_reply(
    text: str,
    today: date,
    *,
    variant: str | None = None,
    invoice_id: str | None = None,
    buyer_id: str | None = None,
    outstanding_paise: int | None = None,
    config: dict[str, Any] | None = None,
    log: bool = True,
) -> dict[str, Any]:
    """Turn a free-text buyer reply into a structured intent, via engine.llm.

    Args:
        text: what the buyer wrote.
        today: the simulation clock, which every date resolves against.
        variant: mock-mode fixture key from config/replies.yaml.
        outstanding_paise: what is actually still owed on this invoice, if
            known (see engine.law.outstanding_paise). Used only to sanity-
            check a promised amount against config/rules.yaml
            promises.amount_implausible_multiple; omit it (the default) to
            skip that check.
        config: rules; defaults to config/rules.yaml.

    Returns:
        intent, date, amount, confidence, quote, source -- plus `downgraded`
        and `raw` when we did not take the model at its word.
    """
    config = config or rules()
    bounds = config["promises"]
    max_horizon_days = int(bounds["max_horizon_days"])
    amount_multiple = float(bounds["amount_implausible_multiple"])

    prompt = (
        "A buyer has replied to a payment reminder. Classify the reply and "
        "report what it says about payment.\n\n"
        f"Today is {today.isoformat()}.\n"
        f"The reply, which may be in Hinglish:\n{text!r}\n\n"
        "This system tracks exactly ONE intent per reply, so two rules apply "
        "when a reply carries more than one: (1) if it BOTH raises a dispute "
        "(damaged goods, wrong quantity, quality complaint, invoice/PO "
        "mismatch, etc.) AND offers a payment date or amount, classify it as "
        "dispute -- a dispute always takes precedence, because chasing a "
        "buyer who has raised a genuine complaint is how a supplier loses a "
        "customer. (2) if it promises payment in more than one instalment "
        "(more than one amount and/or date), report only the EARLIEST date "
        "and its corresponding amount as date_hint/amount; do not try to "
        "represent every instalment.\n\n"
        f"Answer with a JSON object: intent (one of {', '.join(INTENTS)}), "
        "date_hint (day_of_month:N, relative_days:N, iso:YYYY-MM-DD, month_end "
        "or null -- do NOT compute a date yourself), amount (full, partial or "
        "null), confidence (high, medium, low) and quote (the words that "
        "carried the intent)."
    )
    downgraded: list[str] = []
    try:
        parsed = _load(llm(prompt, purpose="parse_reply", variant=variant))
    except LLMError as exc:
        # Treat an unreadable reply as noise rather than losing the whole
        # sweep. Noise is the safe default: it changes nothing and leaves the
        # case exactly where it was.
        parsed = {}
        downgraded.append(f"the model could not read this reply: {exc}")

    intent = str(parsed.get("intent", "")).lower()

    if intent not in INTENTS:
        downgraded.append(f"unknown intent {intent!r} treated as noise")
        intent = "noise"

    when = resolve_date(parsed.get("date_hint"), today)
    rejected_by_rule: str | None = None
    if intent == "promise":
        if when is None:
            downgraded.append("a promise with no date we could resolve; asking instead")
            intent = "question"
        elif when <= today:
            downgraded.append(f"a promise dated {when.isoformat()}, which is not in the future")
            intent, when = "question", None
        elif (when - today).days > max_horizon_days:
            days_out = (when - today).days
            downgraded.append(
                f"a promise dated {when.isoformat()}, {days_out} days out, beyond "
                f"the {max_horizon_days}-day sanity horizon"
            )
            rejected_by_rule = (
                f"promise dated {when.isoformat()} ({days_out} days out) exceeds the "
                f"{max_horizon_days}-day horizon in config/rules.yaml promises.max_horizon_days"
            )
            intent, when = "question", None
        elif outstanding_paise is not None:
            claimed_paise = _extract_amount_paise(text)
            if claimed_paise is not None and claimed_paise > outstanding_paise * amount_multiple:
                downgraded.append(
                    f"a promised amount of {claimed_paise} paise is implausible against "
                    f"an outstanding balance of {outstanding_paise} paise"
                )
                rejected_by_rule = (
                    f"promised {claimed_paise} paise exceeds {amount_multiple}x the "
                    f"outstanding {outstanding_paise} paise (promises.amount_implausible_multiple)"
                )
                intent, when = "question", None

    result: dict[str, Any] = {
        "intent": intent,
        "date": when.isoformat() if when else None,
        "amount": parsed.get("amount"),
        "amount_paise": parsed.get("amount_paise"),
        "confidence": parsed.get("confidence", "low"),
        "quote": parsed.get("quote") or text[:120],
        "source": "llm",
    }
    if downgraded:
        result["downgraded"] = downgraded
        result["raw"] = parsed

    # Defence in depth for docs/edge_cases.md TC-032/TC-036, on top of the
    # prompt instruction above: coarse, rule-based trip-wires that never
    # change intent/date/amount -- the classification is the model's call --
    # they only make sure a human reading the trail is not left unaware that
    # the raw text looked like it might have carried more than the system
    # ever tracks as a single intent.
    possible_dispute = intent == "promise" and _looks_like_a_dispute(text)
    multiple_amounts = intent == "promise" and _distinct_amounts_paise(text) >= 2

    if log:
        reason = f"read the reply as {intent}"
        if when:
            reason += f", payment offered by {when.isoformat()}"
        if downgraded:
            reason += f"; not taken at face value: {'; '.join(downgraded)}"
        audit.record(
            invoice_id=invoice_id, action="reply_parsed", reason=reason,
            source="llm", today=today, buyer_id=buyer_id, actor="promises",
            detail={"reply": text, **result},
        )
        if rejected_by_rule:
            # A distinct entry, source=rule: the parse above records what the
            # model said, this records that a rule -- not the model -- is why
            # it was not taken at face value. Non-negotiable #1: nothing about
            # a rejected promise happens silently.
            audit.record(
                invoice_id=invoice_id, action="promise_sanity_rejected",
                reason=rejected_by_rule, source="rule", today=today,
                buyer_id=buyer_id, actor="promises",
                detail={"reply": text, "raw_model_output": parsed},
            )
        if possible_dispute:
            audit.record(
                invoice_id=invoice_id, action="promise_may_contain_a_dispute",
                reason=("the reply was classified as a promise, but also contains "
                        "dispute language (damage/quality/mismatch wording); worth a "
                        "human's second look (docs/edge_cases.md TC-032)"),
                source="rule", today=today, buyer_id=buyer_id, actor="promises",
                detail={"reply": text},
            )
        if multiple_amounts:
            audit.record(
                invoice_id=invoice_id, action="promise_may_contain_multiple_amounts",
                reason=("the reply names more than one rupee figure; only the earliest "
                        "date/amount is tracked as the promise, the rest is not "
                        "followed up automatically (docs/edge_cases.md TC-036)"),
                source="rule", today=today, buyer_id=buyer_id, actor="promises",
                detail={"reply": text},
            )
    return result


def _load(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return {}
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {}
    except (ValueError, TypeError):
        return {}


# --------------------------------------------------------------------------
# the promise lifecycle
# --------------------------------------------------------------------------

def record_promise(
    invoice_id: str,
    buyer_id: str,
    parsed: dict[str, Any],
    today: date,
    *,
    log: bool = True,
) -> dict[str, Any]:
    """Store a promise so the watchdog can catch it if it breaks."""
    promise = {
        "promise_id": f"PRM-{invoice_id}-{parsed['date']}",
        "invoice_id": invoice_id,
        "buyer_id": buyer_id,
        "promised_date": parsed["date"],
        "amount": parsed.get("amount"),
        "status": OPEN,
        "quote": parsed.get("quote"),
        "recorded_on": today.isoformat(),
        "broken_on": None,
    }
    if log:
        audit.record(
            invoice_id=invoice_id, action="promise_recorded",
            reason=(f"the buyer committed to paying by {promise['promised_date']}: "
                    f"{promise['quote']!r}"),
            source="llm", today=today, buyer_id=buyer_id, actor="promises",
            detail=promise,
        )
    return promise


def is_broken(promise: dict[str, Any], today: date) -> bool:
    """True once a promised date has passed with the money still missing."""
    return promise.get("status") == OPEN and _as_date(promise["promised_date"]) < today


def sweep(promises: list[dict[str, Any]], today: date, *, log: bool = True) -> list[dict[str, Any]]:
    """Mark every promise whose date has passed as broken. Runs daily."""
    newly: list[dict[str, Any]] = []
    for promise in promises:
        if is_broken(promise, today):
            promise["status"] = BROKEN
            promise["broken_on"] = today.isoformat()
            newly.append(promise)
            if log:
                audit.record(
                    invoice_id=promise["invoice_id"], action="promise_broken",
                    reason=(f"payment was promised by {promise['promised_date']} "
                            f"and has not arrived"),
                    source="rule", today=today,
                    buyer_id=promise.get("buyer_id"), actor="promises",
                    detail=promise,
                )
    return newly


def mark_kept(promise: dict[str, Any], today: date, *, log: bool = True) -> dict[str, Any]:
    """The money arrived. Close the promise."""
    promise["status"] = KEPT
    if log:
        audit.record(
            invoice_id=promise["invoice_id"], action="promise_kept",
            reason=f"payment arrived, against a promise of {promise['promised_date']}",
            source="rule", today=today, buyer_id=promise.get("buyer_id"),
            actor="promises", detail=promise,
        )
    return promise


def latest_broken(promises: list[dict[str, Any]], invoice_id: str) -> dict[str, Any] | None:
    """The most recent broken promise on an invoice, for a message to reference."""
    broken = [p for p in promises
              if p.get("invoice_id") == invoice_id and p.get("status") == BROKEN]
    return max(broken, key=lambda p: p["promised_date"]) if broken else None


# --------------------------------------------------------------------------
# acting on what the reply said
# --------------------------------------------------------------------------

def apply_reply(
    parsed: dict[str, Any],
    invoice: dict[str, Any],
    promises: list[dict[str, Any]],
    today: date,
    *,
    log: bool = True,
) -> dict[str, Any]:
    """Update the world in light of a reply.

    A promise is stored. A dispute marks the invoice disputed, which is what
    makes the brain hand it to a human on its next pass -- the halt is not
    decided here, it is caused here.
    """
    intent = parsed["intent"]
    outcome: dict[str, Any] = {"intent": intent, "promise": None, "handoff": False}

    if intent == "promise" and parsed.get("date"):
        promise = record_promise(invoice["invoice_id"], invoice.get("buyer_id"),
                                 parsed, today, log=log)
        promises.append(promise)
        outcome["promise"] = promise

    elif intent == "dispute":
        invoice["status"] = "disputed"
        invoice["disputed"] = True
        invoice["dispute_note"] = parsed.get("quote")
        outcome["handoff"] = True
        # Open promises are left as they are. They stop mattering because the
        # brain halts on the dispute before it ever looks at them, and
        # rewriting their status would lose the record of what was said.
        if log:
            audit.record(
                invoice_id=invoice["invoice_id"], action="dispute_detected",
                reason=("the buyer has disputed this invoice, so chasing stops "
                        f"and it goes to a human: {parsed.get('quote')!r}"),
                source="llm", today=today, buyer_id=invoice.get("buyer_id"),
                actor="promises", detail={"quote": parsed.get("quote"),
                                          "confidence": parsed.get("confidence")},
            )

    return outcome
