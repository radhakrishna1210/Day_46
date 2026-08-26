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

from engine import audit, consolidate
from engine.config import legal, messages, rules, supplier
from engine.llm import LLMError, llm
from engine.money import format_inr
from engine.rungs import RUNG_ONE_RULES, UNIVERSAL_RULES


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
    values["promise_reference"] = ""
    return values


def promise_reference(promise: dict[str, Any] | None, language: str) -> str:
    """One sentence naming a promise that was made and missed.

    Every part of it -- the day it was made, the day promised, and the buyer's
    own words -- comes from the stored record. The writer invents nothing, and
    a case with no broken promise gets an empty string rather than a hedge.
    """
    if not promise:
        return ""
    template = messages()["promise_reference"][language]
    return " ".join(template.format(
        promised_on=promise.get("recorded_on", ""),
        promised_date=promise.get("promised_date", ""),
        promise_quote=promise.get("quote", ""),
    ).split())


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


def _log_outcome(result: dict[str, Any], skeleton: dict[str, Any],
                 invoice: dict[str, Any], when: date, log: bool) -> None:
    """Record what was written, or what was refused and written instead.

    Non-negotiable #1 is about money-related actions, and here the message IS
    the action -- so the words go in, not merely a verdict. Where a draft was
    refused, the rejected text is kept beside its replacement: a reviewer
    should be able to read what the model wanted to say and why we would not
    send it.
    """
    if not log:
        return

    fell_back = result["fallback_used"]
    detail: dict[str, Any] = {
        "rung": skeleton["rung"],
        "language": result["language"],
        "subject": result["subject"],
        "body": result["body"],
        "guardrail": result["guardrail"],
        "attempts": result["attempts"],
        "fallback_used": fell_back,
    }
    if result["rejected_drafts"]:
        detail["rejected_drafts"] = result["rejected_drafts"]
    if fell_back:
        detail["fallback_failures"] = result.get("fallback_failures", [])
        refused = "; ".join(
            failure
            for draft in result["rejected_drafts"]
            for failure in draft["failures"]
        )
        reason = (f"the drafted message was refused after {result['attempts']} "
                  f"attempts and the plain skeleton was sent instead: {refused}")
    else:
        reason = (f"rung {skeleton['rung']} message drafted in {result['language']} "
                  f"and passed the guardrail on attempt {result['attempts']}")

    audit.record(
        invoice_id=invoice.get("invoice_id"),
        action="writer_fallback" if fell_back else "message_drafted",
        reason=reason,
        source=result["source"],
        today=when,
        buyer_id=invoice.get("buyer_id"),
        actor="writer",
        detail=detail,
    )


def write_message(
    skeleton: dict[str, Any],
    *,
    invoice: dict[str, Any],
    buyer: dict[str, Any],
    score: dict[str, Any] | None = None,
    promises: list[dict[str, Any]] | None = None,
    broken_promise: dict[str, Any] | None = None,
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
    values["promise_reference"] = promise_reference(broken_promise, language)
    prompt = build_prompt(skeleton, values, buyer, score, language, promises)
    variant = f"rung{skeleton['rung']}_{language}"
    attempts_allowed = 1 + int(rules().get("writer", {}).get("regeneration_attempts", 1))

    when = today or date.today()
    failures: list[str] = []
    rejected: list[dict[str, Any]] = []

    for attempt in range(1, attempts_allowed + 1):
        try:
            raw = llm(prompt if attempt == 1 else f"{prompt}\n\nYour previous draft was "
                      f"rejected: {'; '.join(failures)}. Fix it.",
                      purpose="draft_message", variant=variant)
        except LLMError as exc:
            # A content-safety block or a dead network must not abort a run of
            # ninety invoices. Record it like any other reason a draft could not
            # be trusted and let the loop fall through to the plain factual
            # message, which needs no model at all.
            failures = [f"the model produced nothing usable: {exc}"]
            rejected.append({"attempt": attempt, "subject": "", "body": "",
                             "failures": failures})
            continue
        message = _parse(raw)
        message = {"subject": _fill(message["subject"], values),
                   "body": _fill(message["body"], values)}
        ok, failures = passes_guardrail(message, skeleton, values)
        if ok:
            result = {**message, "language": language, "guardrail": "passed",
                      "attempts": attempt, "fallback_used": False,
                      "source": "llm", "rejected_drafts": rejected}
            _log_outcome(result, skeleton, invoice, when, log)
            return result
        # Keep what was refused. "Here is what the model wrote, here is why we
        # would not send it" is the most useful thing this trail can show.
        rejected.append({"attempt": attempt, "subject": message["subject"],
                         "body": message["body"], "failures": failures})

    message = fallback_message(values, language, skeleton["rung"])
    ok, fallback_failures = passes_guardrail(message, skeleton, values)
    result = {**message, "language": language,
              "guardrail": "failed" if not ok else "passed (fallback)",
              "attempts": attempts_allowed, "fallback_used": True,
              "source": "rule", "rejected_drafts": rejected,
              "fallback_failures": fallback_failures}
    _log_outcome(result, skeleton, invoice, when, log)
    return result


# --------------------------------------------------------------------------
# consolidated (buyer-level) drafting -- engine/consolidate.py's bundles
# --------------------------------------------------------------------------
#
# A bundle is one envelope, one section per invoice, each section built from
# that invoice's OWN skeleton -- never one rung applied to every invoice. The
# tier partition in engine/consolidate.py guarantees a bundle is homogeneous
# (every invoice rung<=1, or every invoice rung>=2), which is what lets the
# rung-1 "no legal content" check below stay a simple whole-message check
# instead of needing to parse the draft into sections.

def _bundle_wrapper_values(buyer: dict[str, Any], sections_text: str, count: int) -> dict[str, str]:
    """Everything the shared greeting/subject/closing may interpolate."""
    profile = supplier()["supplier"]
    return {
        "invoice_count": str(count),
        "contact_name": str(buyer.get("contact_name") or "Sir/Madam"),
        "buyer_name": str(buyer.get("name") or ""),
        "supplier_name": profile["legal_name"],
        "supplier_contact": profile["contact_name"],
        "sections": sections_text,
    }


def required_numbers_multi(entries: list[tuple[dict[str, Any], dict[str, str]]]) -> list[str]:
    """Every figure EVERY invoice in the bundle requires, union'd, order preserved."""
    seen: list[str] = []
    for skeleton, values in entries:
        for figure in required_numbers(skeleton, values):
            if figure not in seen:
                seen.append(figure)
    return seen


def allowed_amounts_multi(entries: list[tuple[dict[str, Any], dict[str, str]]]) -> set[str]:
    """Every currency figure any invoice in the bundle is permitted to state."""
    allowed: set[str] = set()
    for _skeleton, values in entries:
        allowed |= allowed_amounts(values)
    return allowed


def passes_guardrail_multi(
    message: dict[str, Any], entries: list[tuple[dict[str, Any], dict[str, str]]],
) -> tuple[bool, list[str]]:
    """The guardrail for a consolidated draft covering several invoices.

    Generalizes passes_guardrail(): every required figure from every invoice
    must appear somewhere in the whole text, and every currency figure found
    must belong to the union of what any invoice in the bundle is permitted
    to state. This does not verify that a figure sits in the RIGHT invoice's
    section -- passes_guardrail() never verified per-section placement either
    (it only checks presence and set-membership), so this is not a new
    weakening, just the same check generalized to more than one invoice.
    """
    failures: list[str] = []
    subject = str(message.get("subject") or "")
    body = str(message.get("body") or "")
    whole = f"{subject}\n{body}"
    lowered = whole.lower()

    if not subject.strip():
        failures.append("the subject is empty")
    if len(body.strip()) < 40:
        failures.append("the body is empty or too short to be a message")

    for figure in required_numbers_multi(entries):
        if figure not in whole:
            failures.append(f"required figure {figure!r} is missing")

    permitted = allowed_amounts_multi(entries)
    for found in CURRENCY_PATTERN.findall(whole):
        if found not in permitted:
            failures.append(f"amount {found!r} does not come from the law engine")

    for word in messages()["threat_words"]:
        if re.search(rf"\b{re.escape(word.lower())}\b", lowered):
            failures.append(f"threatening language: {word!r}")

    # engine.consolidate.bundle_sends() guarantees every entry shares one
    # tier (all rung<=1, or all rung>=2) -- checked again here, defensively,
    # rather than trusted silently, the same pattern as engine.rungs'
    # RungNotAvailable acting as a second barrier alongside the law ceiling.
    all_courtesy = all(skeleton["rung"] <= 1 for skeleton, _values in entries)
    mixed_tier = len({skeleton["rung"] <= 1 for skeleton, _values in entries}) > 1
    if mixed_tier:
        failures.append(
            "a bundle mixed a rung-1 courtesy invoice with a rung>=2 escalated "
            "invoice -- this should be structurally impossible"
        )
    elif all_courtesy:
        citation = citation_pattern().search(whole)
        if citation:
            failures.append(f"a courtesy bundle cites {citation.group(0)!r}")
        for word in messages()["rung_one_banned_words"]:
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                failures.append(f"a courtesy bundle mentions {word!r}")

    if re.search(r"\{[a-z_0-9]+\}", whole):
        failures.append("an unfilled placeholder survived")
    if LEAKED_NONE_PATTERN.search(whole):
        failures.append("a stray None reached the page")

    return not failures, failures


def fallback_message_multi(
    bundle_values: dict[str, str], language: str, tier: str,
) -> dict[str, str]:
    """The plain factual assembly for a rejected consolidated draft.

    Exactly the same guarantee as fallback_message(): it assembles nothing
    but each invoice's own already-approved section text (bundle_values
    ["sections"], built in write_consolidated_message() from
    consolidated_section templates), so it cannot fail a check the drafted
    version failed.
    """
    shape = "courtesy" if tier == consolidate.COURTESY else "factual"
    template = messages()["fallback_consolidated"][language][shape]
    return {"subject": _fill(template["subject"], bundle_values),
            "body": _fill(template["body"], bundle_values)}


def build_prompt_multi(
    bundle_actions: list[Any],
    entries: list[tuple[dict[str, Any], dict[str, str]]],
    buyer: dict[str, Any],
    score: dict[str, Any] | None,
    language: str,
    tier: str,
    promises_by_invoice: dict[str, list[dict[str, Any]]] | None,
) -> str:
    """The prompt for a consolidated draft. One delimited block per invoice,
    each carrying only that invoice's own facts and numbers -- mirrors
    build_prompt(), repeated per invoice, with an explicit instruction never
    to mix a fact from one invoice's section into another's."""
    profile = supplier()["supplier"]
    lines = [
        f"You are writing ONE payment message for {profile['legal_name']}, a "
        f"small Indian manufacturer, to {buyer.get('contact_name') or 'Sir/Madam'} "
        f"at {buyer.get('name') or ''}, covering {len(bundle_actions)} separate "
        f"invoices in a single envelope.",
        "",
        ("TIER: courtesy -- no legal content of any kind is permitted anywhere "
         "in this message, for any invoice"
         if tier == consolidate.COURTESY else
         "TIER: escalated -- each invoice's own facts below may be stated, but "
         "ONLY for that invoice"),
        f"TONE: {choose_tone(buyer, score)}",
        f"LANGUAGE: {' '.join(messages()['language_instruction'][language].split())}",
        "",
        "Write one clearly separated section per invoice below, in the order "
        "given. Never use a number or fact from one invoice's section in "
        "another invoice's section.",
    ]
    for i, ((skeleton, values), action) in enumerate(zip(entries, bundle_actions), start=1):
        lines += ["", f"--- INVOICE {i} of {len(bundle_actions)}: {values['invoice_id']} ---"]
        if skeleton["facts"]:
            lines.append("THE ONLY FACTS YOU MAY STATE FOR THIS INVOICE:")
            lines += [f"  - {fact}" for fact in skeleton["facts"]]
        else:
            lines.append("  (no legal content -- this invoice is a courtesy reminder only)")
        lines.append("THE ONLY NUMBERS YOU MAY USE FOR THIS INVOICE, copied exactly:")
        for label, key in (("invoice", "invoice_id"), ("goods", "goods"),
                           ("amount outstanding", "outstanding"),
                           ("days overdue", "days_overdue"),
                           ("payment was due", "statutory_due_date"),
                           ("interest to date", "interest"),
                           ("tax cost to them", "tax_exposure")):
            if values.get(key):
                lines.append(f"  {label:<20}{values[key]}")
        plist = (promises_by_invoice or {}).get(action.invoice_id) or []
        if plist:
            lines.append("PROMISE HISTORY FOR THIS INVOICE:")
            for promise in plist:
                lines.append(f"  Committed to paying by {promise.get('promised_date')} "
                             f"({promise.get('status')}).")

    forbidden = list(UNIVERSAL_RULES)
    if tier == consolidate.COURTESY:
        forbidden.extend(RUNG_ONE_RULES)
    lines += ["", "YOU MUST NOT"]
    lines += [f"  - {rule}" for rule in forbidden]
    lines += ["", 'Reply with a JSON object: {"subject": "...", "body": "..."}']
    return "\n".join(lines)


def _log_outcome_multi(
    result: dict[str, Any], entries: list[tuple[dict[str, Any], dict[str, str]]],
    bundle_actions: list[Any], when: date, log: bool,
) -> None:
    """One audit entry PER INVOICE in the bundle, each carrying the shared
    bundle content and a bundle_invoice_ids list linking it to its
    envelope-mates. This is deliberate, not an oversight: audit.entries_for()
    and channels._already_sent() both index strictly by invoice_id, so
    logging once per invoice (instead of inventing a new buyer-level entry
    shape) keeps every existing per-invoice reader working unmodified while
    still recording, for each invoice, that it was sent as part of a bundle
    and with whom -- see CLAUDE.md's W3 plan, point g."""
    if not log:
        return

    fell_back = result["fallback_used"]
    invoice_ids = result["invoice_ids"]
    for action, (skeleton, _values) in zip(bundle_actions, entries):
        detail: dict[str, Any] = {
            "rung": skeleton["rung"],
            "language": result["language"],
            "subject": result["subject"],
            "body": result["body"],
            "guardrail": result["guardrail"],
            "attempts": result["attempts"],
            "fallback_used": fell_back,
            "bundle_tier": result["tier"],
            "bundle_invoice_ids": invoice_ids,
        }
        if result["rejected_drafts"]:
            detail["rejected_drafts"] = result["rejected_drafts"]
        if fell_back:
            detail["fallback_failures"] = result.get("fallback_failures", [])
            refused = "; ".join(
                failure for draft in result["rejected_drafts"] for failure in draft["failures"]
            )
            reason = (f"the drafted consolidated message ({len(invoice_ids)} invoices: "
                      f"{', '.join(invoice_ids)}) was refused after {result['attempts']} "
                      f"attempts and the plain skeleton was sent instead: {refused}")
        else:
            reason = (f"rung {skeleton['rung']} message drafted in {result['language']} as "
                      f"part of a {len(invoice_ids)}-invoice consolidated message "
                      f"({', '.join(invoice_ids)}), and passed the guardrail on attempt "
                      f"{result['attempts']}")

        audit.record(
            invoice_id=action.invoice_id,
            action="writer_fallback" if fell_back else "message_drafted",
            reason=reason, source=result["source"], today=when,
            buyer_id=action.buyer_id, actor="writer", detail=detail,
        )


def write_consolidated_message(
    bundle_actions: list[Any],
    *,
    invoices_by_id: dict[str, dict[str, Any]],
    buyer: dict[str, Any],
    score: dict[str, Any] | None = None,
    promises_by_invoice: dict[str, list[dict[str, Any]]] | None = None,
    today: date | None = None,
    log: bool = True,
) -> dict[str, Any]:
    """Draft ONE message covering every invoice in one engine.consolidate bundle.

    Args:
        bundle_actions: the "actions" list from one engine.consolidate.
            bundle_sends() bundle -- every one a SEND with a buyer-facing
            skeleton, for the same buyer, and the same rung tier.
        invoices_by_id: invoice_id -> invoice record, for names and goods only
            -- every figure still comes from each invoice's own skeleton.
        buyer, score: as write_message().
        promises_by_invoice: invoice_id -> promise history, quoted per
            invoice in the prompt.
        today: simulation clock, for the audit entries.
        log: write to the audit trail, once per invoice -- see _log_outcome_multi.

    Returns:
        subject, body, language, tier, invoice_ids, plus guardrail, attempts,
        fallback_used and source, exactly as write_message() but for the
        whole bundle.

    Raises:
        ValueError: if the bundle is empty, spans more than one buyer, or
            spans more than one rung tier -- engine.consolidate.bundle_sends()
            already guarantees none of these happen; this is the second,
            independent barrier, not the only one.
        NotSendable: if any invoice's own skeleton refuses to send.
    """
    if not bundle_actions:
        raise ValueError("write_consolidated_message called with an empty bundle")
    buyer_ids = {action.buyer_id for action in bundle_actions}
    if len(buyer_ids) != 1:
        raise ValueError(f"a bundle must cover exactly one buyer, got {sorted(buyer_ids)}")
    tiers = {consolidate.COURTESY if action.rung <= 1 else consolidate.ESCALATED
             for action in bundle_actions}
    if len(tiers) != 1:
        raise ValueError(f"a bundle must be a single rung tier, got {sorted(tiers)}")
    tier = tiers.pop()
    for action in bundle_actions:
        if not action.skeleton or not action.skeleton.get("sends_to_buyer"):
            raise NotSendable(
                f"invoice {action.invoice_id} in this bundle sends nothing to the buyer"
            )

    language = choose_language(buyer)
    entries: list[tuple[dict[str, Any], dict[str, str]]] = []
    section_texts: list[str] = []
    for action in bundle_actions:
        skeleton = action.skeleton
        values = _values(skeleton, invoices_by_id[action.invoice_id], buyer)
        entries.append((skeleton, values))
        section_template = messages()["consolidated_section"][language][
            "courtesy" if tier == consolidate.COURTESY else "factual"
        ]
        section_texts.append(_fill(section_template, values))

    bundle_values = _bundle_wrapper_values(buyer, "\n\n".join(section_texts), len(bundle_actions))
    prompt = build_prompt_multi(bundle_actions, entries, buyer, score, language, tier,
                                promises_by_invoice)
    variant = f"consolidated_{tier}_{language}"
    attempts_allowed = 1 + int(rules().get("writer", {}).get("regeneration_attempts", 1))

    when = today or date.today()
    failures: list[str] = []
    rejected: list[dict[str, Any]] = []

    for attempt in range(1, attempts_allowed + 1):
        try:
            raw = llm(prompt if attempt == 1 else f"{prompt}\n\nYour previous draft was "
                      f"rejected: {'; '.join(failures)}. Fix it.",
                      purpose="draft_message", variant=variant)
        except LLMError as exc:
            failures = [f"the model produced nothing usable: {exc}"]
            rejected.append({"attempt": attempt, "subject": "", "body": "",
                             "failures": failures})
            continue
        message = _parse(raw)
        message = {"subject": _fill(message["subject"], bundle_values),
                   "body": _fill(message["body"], bundle_values)}
        ok, failures = passes_guardrail_multi(message, entries)
        if ok:
            result = {**message, "language": language, "guardrail": "passed",
                      "attempts": attempt, "fallback_used": False, "source": "llm",
                      "rejected_drafts": rejected, "tier": tier,
                      "invoice_ids": [action.invoice_id for action in bundle_actions]}
            _log_outcome_multi(result, entries, bundle_actions, when, log)
            return result
        rejected.append({"attempt": attempt, "subject": message["subject"],
                         "body": message["body"], "failures": failures})

    message = fallback_message_multi(bundle_values, language, tier)
    ok, fallback_failures = passes_guardrail_multi(message, entries)
    result = {**message, "language": language,
              "guardrail": "failed" if not ok else "passed (fallback)",
              "attempts": attempts_allowed, "fallback_used": True, "source": "rule",
              "rejected_drafts": rejected, "fallback_failures": fallback_failures,
              "tier": tier, "invoice_ids": [action.invoice_id for action in bundle_actions]}
    _log_outcome_multi(result, entries, bundle_actions, when, log)
    return result
