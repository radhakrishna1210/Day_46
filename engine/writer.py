"""Message writer -- the AI writes the actual words. Calls engine.llm only.

This is the first place an LLM touches something a buyer would read, so the
whole module is built around not trusting it. The model gets the rung's fact
skeleton and may set tone, order and phrasing. It may not add a fact, drop a
citation, or move a digit, and a guardrail checks every draft before it can be
returned.

    draft -> guardrail -> pass? return
                       -> fail? regenerate once
                                -> pass? return
                                -> fail? fall back to the plain skeleton,
                                         and log the fallback

The fallback cannot fail the guardrail, because it assembles nothing but the
skeleton's own content. Falling back silently would be the worst outcome here,
so every use is written to the audit trail with the failures that caused it.

Legal sentences are never composed in this module or in config/messages.yaml.
They arrive already rendered from the clauses in config/legal.yaml -- the same
strings the Samadhaan draft quotes -- and are interpolated whole.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from typing import Any

from engine import audit
from engine.config import legal, messages, rules, supplier
from engine.llm import llm
from engine.money import format_inr


@lru_cache(maxsize=1)
def citation_pattern() -> re.Pattern[str]:
    """Detects a statutory reference in drafted text.

    The pattern itself lives in config/legal.yaml: this module must contain
    no statute names, even in a regex that only looks for them.
    """
    return re.compile(legal()["citation_pattern"], re.IGNORECASE)


#: Currency amounts, in either notation the system emits.
CURRENCY_PATTERN = re.compile(r"(?:₹|Rs\.?\s?)\d(?:[\d,]*\d)?(?:\.\d{2})?")

#: A Python None that leaked into the text, as opposed to the ordinary English
#: word. "None of this is where either of us wants to be" is a perfectly good
#: sentence; ": None" or "(None)" is a bug. Only value positions are flagged.
LEAKED_NONE_PATTERN = re.compile(r"[:=(\[]\s*None\b|\bNone\s*[,)\]]|^None$", re.MULTILINE)

#: Generic slot -> the fact keys that can fill it. A template says
#: {fact_section_15} without needing to know which of the three applies.
FACT_SLOTS: dict[str, tuple[str, ...]] = {
    "fact_section_15": ("section_15_capped", "section_15_no_agreement", "section_15_agreed"),
    "fact_section_16": ("section_16",),
    "fact_section_16_running": ("section_16_running",),
    "fact_section_22": ("section_22",),
    "fact_section_23": ("section_23",),
    "fact_tax": ("tax_deduction_crystallised", "tax_deduction_upcoming"),
}


class NotSendable(ValueError):
    """Raised when asked to draft for a rung that sends nothing to the buyer."""


# --------------------------------------------------------------------------
# choosing how to speak
# --------------------------------------------------------------------------

def choose_language(buyer: dict[str, Any], config: dict[str, Any] | None = None) -> str:
    """Hinglish only where the profile allows it and the buyer prefers it.

    A corporate accounts desk gets English whatever the stated preference: a
    WhatsApp register to a finance team reads as unprofessional, not friendly.
    """
    config = config or rules()
    allowed = set(config.get("writer", {}).get("hinglish_profiles", []))
    if buyer.get("profile") in allowed and buyer.get("language_pref") == "hinglish":
        return "hinglish"
    return "english"


def choose_tone(buyer: dict[str, Any], score: dict[str, Any] | None) -> str:
    """Warm for a good buyer having a bad month, firm for a habitual delayer."""
    profile = buyer.get("profile", "corporate")
    table = messages()["tone"].get(profile, messages()["tone"]["corporate"])
    if not score:
        return table["medium"]
    from engine.brain import band

    return table[band(int(score.get("score", 0)), rules())]


# --------------------------------------------------------------------------
# the values a template may use
# --------------------------------------------------------------------------

def _values(skeleton: dict[str, Any], invoice: dict[str, Any],
            buyer: dict[str, Any]) -> dict[str, str]:
    """Everything a template may interpolate, already formatted for display."""
    numbers = skeleton["numbers"]
    profile = supplier()["supplier"]
    values: dict[str, str] = {
        "invoice_id": str(numbers.get("invoice_id", "")),
        "goods": str(numbers.get("description") or "goods supplied"),
        "outstanding": format_inr(int(numbers.get("outstanding_paise", 0))),
        "contact_name": str(buyer.get("contact_name") or "Sir/Madam"),
        "buyer_name": str(buyer.get("name") or ""),
        "supplier_name": profile["legal_name"],
        "supplier_contact": profile["contact_name"],
    }
    if "days_overdue" in numbers:
        values["days_overdue"] = str(numbers["days_overdue"])
    if "statutory_due_date" in numbers:
        values["statutory_due_date"] = str(numbers["statutory_due_date"])
    if "interest_paise" in numbers:
        values["interest"] = format_inr(int(numbers["interest_paise"]), decimals=True)
    if "interest_per_day_average_paise" in numbers:
        values["per_day"] = format_inr(
            int(numbers["interest_per_day_average_paise"]), decimals=True)
    if "tax_exposure_paise" in numbers:
        values["tax_exposure"] = format_inr(int(numbers["tax_exposure_paise"]))

    by_key = skeleton.get("facts_by_key", {})
    for slot, candidates in FACT_SLOTS.items():
        values[slot] = next((by_key[key] for key in candidates if key in by_key), "")
    values["facts"] = "\n\n".join(skeleton.get("facts", []))
    return values


def _fill(text: str, values: dict[str, str]) -> str:
    """Substitute only known placeholders, leaving any other braces untouched.

    Mock drafts arrive as templates and live drafts arrive as finished prose,
    so both go through the same substitution: in live mode there is simply
    nothing to replace. A naive str.format would crash on a stray brace in
    model output.
    """
    def swap(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))

    filled = re.sub(r"\{([a-z_0-9]+)\}", swap, text)
    return re.sub(r"\n{3,}", "\n\n", filled).strip()


# --------------------------------------------------------------------------
# the guardrail
# --------------------------------------------------------------------------

def required_numbers(skeleton: dict[str, Any], values: dict[str, str]) -> list[str]:
    """Figures the message must contain, character for character."""
    required = [values["invoice_id"], values["outstanding"]]
    if skeleton["rung"] >= 2:
        required += [values.get("days_overdue", ""), values.get("statutory_due_date", ""),
                     values.get("interest", "")]
    if skeleton["rung"] >= 3:
        required.append(values.get("tax_exposure", ""))
    return [item for item in required if item]


def allowed_amounts(values: dict[str, str]) -> set[str]:
    """Every currency figure the message is permitted to contain."""
    allowed: set[str] = set()
    for key in ("outstanding", "interest", "per_day", "tax_exposure"):
        if values.get(key):
            allowed.add(values[key])
    # facts already carry approved figures; harvest them so quoting a fact
    # verbatim can never look like an invented amount
    for slot in list(FACT_SLOTS) + ["facts"]:
        allowed.update(CURRENCY_PATTERN.findall(values.get(slot, "")))
    return allowed


def passes_guardrail(message: dict[str, Any], skeleton: dict[str, Any],
                     values: dict[str, str]) -> tuple[bool, list[str]]:
    """Check a draft against the checklist. A message that fails is never sent."""
    failures: list[str] = []
    subject = str(message.get("subject") or "")
    body = str(message.get("body") or "")
    whole = f"{subject}\n{body}"
    lowered = whole.lower()

    if not subject.strip():
        failures.append("the subject is empty")
    if len(body.strip()) < 40:
        failures.append("the body is empty or too short to be a message")

    for figure in required_numbers(skeleton, values):
        if figure not in whole:
            failures.append(f"required figure {figure!r} is missing")

    permitted = allowed_amounts(values)
    for found in CURRENCY_PATTERN.findall(whole):
        if found not in permitted:
            failures.append(f"amount {found!r} does not come from the law engine")

    for word in messages()["threat_words"]:
        # Word boundaries, not substrings: "sue" must not fire on "issue", and
        # a Hinglish "koi issue ho to bata dijiye" is not a threat.
        if re.search(rf"\b{re.escape(word.lower())}\b", lowered):
            failures.append(f"threatening language: {word!r}")

    if skeleton["rung"] <= 1:
        citation = citation_pattern().search(whole)
        if citation:
            failures.append(f"a courtesy reminder cites {citation.group(0)!r}")
        for word in messages()["rung_one_banned_words"]:
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                failures.append(f"a courtesy reminder mentions {word!r}")

    if re.search(r"\{[a-z_0-9]+\}", whole):
        failures.append("an unfilled placeholder survived")
    if LEAKED_NONE_PATTERN.search(whole):
        failures.append("a stray None reached the page")

    return not failures, failures


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------

def build_prompt(skeleton: dict[str, Any], values: dict[str, str],
                 buyer: dict[str, Any], score: dict[str, Any] | None,
                 language: str, promises: list[dict[str, Any]] | None) -> str:
    """The prompt sent through engine.llm. Facts arrive whole, never composed."""
    lines = [
        f"You are writing a payment reminder for {values['supplier_name']}, a small "
        f"Indian manufacturer, to {values['contact_name']} at {values['buyer_name']}.",
        "",
        f"RUNG {skeleton['rung']} -- {skeleton['name']}: {skeleton['intent']}",
        f"TONE: {choose_tone(buyer, score)}",
        f"LANGUAGE: {' '.join(messages()['language_instruction'][language].split())}",
        "",
        "THE ONLY FACTS YOU MAY STATE",
    ]
    if skeleton["facts"]:
        lines += [f"  - {fact}" for fact in skeleton["facts"]]
    else:
        lines.append("  (none -- this is a courtesy reminder with no legal content)")

    lines += ["", "THE ONLY NUMBERS YOU MAY USE, copied exactly as written"]
    for label, key in (("invoice", "invoice_id"), ("goods", "goods"),
                       ("amount outstanding", "outstanding"),
                       ("days overdue", "days_overdue"),
                       ("payment was due", "statutory_due_date"),
                       ("interest to date", "interest"),
                       ("adding per day", "per_day"),
                       ("tax cost to them", "tax_exposure")):
        if values.get(key):
            lines.append(f"  {label:<20}{values[key]}")

    lines += ["", "PROMISE HISTORY"]
    if promises:
        for promise in promises:
            lines.append(f"  Committed to paying by {promise.get('promised_date')} "
                         f"({promise.get('status')}).")
    else:
        lines.append("  None recorded.")

    lines += ["", "YOU MUST NOT"]
    lines += [f"  - {rule}" for rule in skeleton["forbidden"]]
    lines += ["", 'Reply with a JSON object: {"subject": "...", "body": "..."}']
    return "\n".join(lines)


def _parse(raw: str) -> dict[str, str]:
    """Pull subject and body out of a model response."""
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return {"subject": str(parsed.get("subject", "")),
                    "body": str(parsed.get("body", ""))}
        except (ValueError, TypeError):
            pass
    return {"subject": "", "body": raw.strip()}


def fallback_message(values: dict[str, str], language: str, rung: int) -> dict[str, str]:
    """The plain factual assembly used when a draft cannot be trusted.

    Deliberately unpolished. It contains only the skeleton's own content, so it
    passes the guardrail by construction -- which means it has to carry every
    figure the guardrail requires at that rung, and at rung 1 it must stay as
    free of legal content as the drafted version would have been.
    """
    shape = "courtesy" if rung <= 1 else "factual"
    template = messages()["fallback"][language][shape]
    return {"subject": _fill(template["subject"], values),
            "body": _fill(template["body"], values)}


def write_message(
    skeleton: dict[str, Any],
    *,
    invoice: dict[str, Any],
    buyer: dict[str, Any],
    score: dict[str, Any] | None = None,
    promises: list[dict[str, Any]] | None = None,
    today: date | None = None,
    log: bool = True,
) -> dict[str, Any]:
    """Draft the message for one invoice at one rung.

    Args:
        skeleton: engine.rungs.fact_skeleton output. The contract the draft may
            not deviate from.
        invoice, buyer: the records, for names and goods only -- every figure
            comes from the skeleton.
        score: used for tone. Absent means neutral.
        promises: promise history, quoted in the prompt so a broken commitment
            can be referenced.
        today: simulation clock, for the audit entry.
        log: write fallbacks to the audit trail.

    Returns:
        subject, body, language, plus guardrail, attempts, fallback_used and
        source -- the audit trail needs all four and re-deriving them later
        would mean guessing.

    Raises:
        NotSendable: if this rung sends nothing to the buyer.
    """
    if not skeleton.get("sends_to_buyer"):
        raise NotSendable(
            f"rung {skeleton['rung']} sends nothing to the buyer "
            f"(dispute_hold={skeleton.get('dispute_hold')})"
        )

    language = choose_language(buyer)
    values = _values(skeleton, invoice, buyer)
    prompt = build_prompt(skeleton, values, buyer, score, language, promises)
    variant = f"rung{skeleton['rung']}_{language}"
    attempts_allowed = 1 + int(rules().get("writer", {}).get("regeneration_attempts", 1))

    failures: list[str] = []
    for attempt in range(1, attempts_allowed + 1):
        raw = llm(prompt if attempt == 1 else f"{prompt}\n\nYour previous draft was "
                  f"rejected: {'; '.join(failures)}. Fix it.",
                  purpose="draft_message", variant=variant)
        message = _parse(raw)
        message = {"subject": _fill(message["subject"], values),
                   "body": _fill(message["body"], values)}
        ok, failures = passes_guardrail(message, skeleton, values)
        if ok:
            return {**message, "language": language, "guardrail": "passed",
                    "attempts": attempt, "fallback_used": False, "source": "llm"}

    message = fallback_message(values, language, skeleton["rung"])
    ok, fallback_failures = passes_guardrail(message, skeleton, values)
    if log:
        audit.record(
            invoice_id=invoice.get("invoice_id"),
            action="writer_fallback",
            reason=("the drafted message failed the guardrail after "
                    f"{attempts_allowed} attempts: {'; '.join(failures)}"),
            source="rule",
            today=today or date.today(),
            buyer_id=invoice.get("buyer_id"),
            actor="writer",
            detail={"rung": skeleton["rung"], "language": language,
                    "draft_failures": failures,
                    "fallback_clean": ok, "fallback_failures": fallback_failures},
        )
    return {**message, "language": language,
            "guardrail": "failed" if not ok else "passed (fallback)",
            "attempts": attempts_allowed, "fallback_used": True, "source": "rule"}
