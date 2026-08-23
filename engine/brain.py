"""The brain -- picks exactly one action per invoice, per simulated day.

Safety-critical, so it is rules almost all the way down. The LLM is consulted
for one narrowly defined ambiguous case, and even then it may only make us
gentler: it can turn a send into a wait, never the reverse.

The ladder, in ids shared with engine.law.available_rung():

    0  WAIT         an active promise has not yet fallen due
    1  SOFT NUDGE   courtesy reminder, no legal content
    2  FIRM         the statutory position and the interest accruing
    3  LEGAL FACTS  the buyer's own tax cost and their disclosure duty
    4  STOP+HANDOFF no message; draft the reference, flag a human

The invariant, enforced by construction rather than by inspection:

    chosen == 0   OR   1 <= chosen <= available_rung

`available_rung` is the ceiling from the law engine. It is applied once, at the
end of rung selection, and re-applied after the escalation walk. Every path to
a send or a handoff passes through it, and engine.rungs raises RungNotAvailable
as an independent second barrier.

Ordering note worth keeping: rung 4 has max_messages of 0, so the per-rung
exhaustion check would swallow every rung-4 decision into a wait and the final
rung would be unreachable. The rung-4 branch therefore sits ABOVE every
send-gating check -- those checks govern contacting a buyer, and a handoff
contacts nobody.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from engine import audit, rungs, samadhaan
from engine.config import rules
from engine.llm import LLMError, llm

#: Kinds of action. `wait` is recoverable, `stop` is terminal, `handoff` gives
#: the case to a human, `send` is the only one that produces a message.
WAIT, SEND, HANDOFF, STOP = "wait", "send", "handoff", "stop"

HANDOFF_RUNG = 4

#: How long to sit on a case that has hit the legal ceiling with no room left.
#: The ceiling rises on its own as the invoice ages, so this is a pause.
CEILING_REVIEW_DAYS = 7


@dataclass(frozen=True)
class Action:
    """One decision, with everything needed to justify it later."""

    kind: str
    rung: int
    reason: str
    source: str
    invoice_id: str
    buyer_id: str
    available_rung: int
    escalation_capped: bool = False
    next_review_date: date | None = None
    skeleton: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# reading the inputs
# --------------------------------------------------------------------------

def _as_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def band(score_value: int, config: dict[str, Any]) -> str:
    """Which pacing band a score falls into."""
    bands = config["score"]["bands"]
    if score_value >= int(bands["good_from"]):
        return "good"
    if score_value < int(bands["poor_below"]):
        return "poor"
    return "medium"


def contacts_total(history: list[dict[str, Any]]) -> int:
    return len(history)


def contacts_at_rung(history: list[dict[str, Any]], rung_id: int) -> int:
    return sum(1 for entry in history if entry.get("rung") == rung_id)


def last_contact_date(history: list[dict[str, Any]]) -> date | None:
    dates = [_as_date(entry["date"]) for entry in history if entry.get("date")]
    return max(dates) if dates else None


def highest_rung_used(history: list[dict[str, Any]]) -> int | None:
    used = [int(entry["rung"]) for entry in history if entry.get("rung") is not None]
    return max(used) if used else None


def active_promise(promises: list[dict[str, Any]], today: date, grace_days: int) -> dict | None:
    """A promise still within its grace period. Chasing over it would be rude."""
    for promise in promises or []:
        if promise.get("status") != "open":
            continue
        if _as_date(promise["promised_date"]) + timedelta(days=grace_days) >= today:
            return promise
    return None


def broken_promises(promises: list[dict[str, Any]], today: date, grace_days: int) -> int:
    """Promises whose date and grace have passed with the money still missing."""
    return sum(
        1 for promise in promises or []
        if promise.get("status") in {"open", "broken"}
        and _as_date(promise["promised_date"]) + timedelta(days=grace_days) < today
    )


def _is_ambiguous(
    invoice: dict[str, Any],
    legal_position: dict[str, Any],
    history: list[dict[str, Any]],
) -> bool:
    """The one case the rules admit they cannot settle.

    A buyer has paid part of the invoice and then said something we could not
    classify. ARCHITECTURE names exactly this as the LLM's judgment call.
    """
    part_paid = legal_position["principal_paise"] < int(invoice.get("amount_paise", 0))
    latest = history[-1] if history else {}
    return bool(part_paid) and latest.get("outcome") == "unclear_reply"


def _ask_llm(invoice: dict[str, Any], legal_position: dict[str, Any],
             history: list[dict[str, Any]], proposed: str) -> tuple[str, str]:
    """Consult the model on an ambiguous case. It may only soften the outcome.

    Returns (decision, reasoning). Anything other than "wait" leaves the rule
    outcome standing -- the model is never allowed to decide to keep pushing.
    """
    latest = history[-1] if history else {}
    prompt = (
        f"Invoice {invoice.get('invoice_id')} is {legal_position['days_overdue']} days "
        f"overdue with part of it paid. The buyer replied: "
        f"{latest.get('reply', '(no text recorded)')!r}. "
        f"The rules propose to {proposed}. Should we wait instead? "
        f"Answer with a JSON object containing decision (wait or proceed) and reason."
    )
    try:
        raw = llm(prompt, purpose="judgment_call")
    except LLMError as exc:
        # The model may only ever make us gentler, so losing it costs nothing:
        # the rules have already decided and their decision stands.
        return "proceed", f"the model was unavailable, so the rule stands: {exc}"
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    decision, reasoning = "proceed", raw.strip()
    if match:
        try:
            parsed = json.loads(match.group(0))
            decision = str(parsed.get("decision", "proceed")).lower()
            reasoning = str(parsed.get("reason", reasoning))
        except (ValueError, TypeError):
            pass
    if decision not in {"wait", "proceed"}:
        decision = "proceed"
    return decision, reasoning


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------

def _emit(action: Action, today: date, log: bool = True) -> Action:
    """Write the decision to the audit trail and hand it back."""
    if log:
        detail = {k: v for k, v in action.detail.items() if k != "skeleton"}
        detail.update({
            "rung": action.rung,
            "available_rung": action.available_rung,
            "escalation_capped": action.escalation_capped,
        })
        audit.record(
            invoice_id=action.invoice_id,
            action=action.kind,
            reason=action.reason,
            source=action.source,
            today=today,
            buyer_id=action.buyer_id,
            actor="brain",
            detail=detail,
        )
    return action


def decide(
    invoice: dict[str, Any],
    buyer: dict[str, Any],
    score: dict[str, Any],
    legal_position: dict[str, Any],
    promises: list[dict[str, Any]],
    history: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    log: bool = True,
) -> Action:
    """Choose exactly one action for one invoice today.

    Args:
        invoice: the invoice record.
        buyer: the buyer record -- needed for opt-out, which outranks everything.
        score: engine.score.score_buyer output.
        legal_position: engine.law.legal_position output. Also supplies the
            clock, so the brain and the law engine can never disagree on today.
        promises: promises recorded against this invoice.
        history: contacts already made on this invoice (not payments).
        config: rules; defaults to config/rules.yaml.
        log: write to the audit trail. False for dry runs.

    Returns:
        One Action, carrying the reason and everything needed to audit it.
    """
    config = config or rules()
    today = _as_date(legal_position["as_of"])
    ceiling = int(legal_position["available_rung"])
    stop_rules = config["stop_rules"]
    ladder = config["ladder"]

    invoice_id = invoice.get("invoice_id")
    buyer_id = invoice.get("buyer_id")
    total = contacts_total(history)
    last = last_contact_date(history)
    days_since = (today - last).days if last else None

    base_detail = {
        "contacts_total": total,
        "days_overdue": legal_position["days_overdue"],
        "days_since_last_contact": days_since,
        "score": score.get("score"),
        "confidence": score.get("confidence"),
        "history_count": score.get("history_count"),
    }

    def act(kind: str, rung_id: int, reason: str, *, source: str = "rule",
            capped: bool = False, review: date | None = None,
            skeleton: dict | None = None, extra: dict | None = None) -> Action:
        return _emit(
            Action(
                kind=kind, rung=rung_id, reason=reason, source=source,
                invoice_id=invoice_id, buyer_id=buyer_id, available_rung=ceiling,
                escalation_capped=capped, next_review_date=review,
                skeleton=skeleton, detail={**base_detail, **(extra or {})},
            ),
            today, log,
        )

    # 1. Opt-out outranks everything, including a 200-day-overdue case.
    if stop_rules.get("opt_out_stops_everything") and buyer.get("opted_out"):
        return act(STOP, 0, "the buyer has opted out of contact, which stops everything")

    # 2. A disputed invoice goes to a human before anything else can happen.
    if stop_rules.get("dispute_triggers_handoff") and legal_position.get("dispute_hold"):
        return act(HANDOFF, 0, "the invoice is disputed, so it goes to a human immediately")

    # 3. Nothing owed, nothing to do.
    if legal_position["principal_paise"] <= 0 or invoice.get("status") == "paid":
        return act(STOP, 0, "the invoice is settled")

    # 4. No claim exists yet.
    if legal_position["days_overdue"] <= 0:
        return act(WAIT, 0, "the invoice is not yet due",
                   review=_as_date(legal_position["statutory_due_date"]) + timedelta(days=1))

    # 5. We have said everything we are permitted to say.
    if total >= int(stop_rules["max_total"]):
        return act(HANDOFF, min(highest_rung_used(history) or 1, ceiling),
                   f"{total} contacts already made, reaching the limit of "
                   f"{stop_rules['max_total']} for one invoice")

    # 6. A promise that has not yet fallen due buys silence.
    grace = int(ladder.get("promise_grace_days", 0))
    promise = active_promise(promises, today, grace)
    if promise:
        return act(WAIT, 0,
                   f"the buyer promised payment by {promise['promised_date']}, "
                   f"which has not yet passed",
                   review=_as_date(promise["promised_date"]) + timedelta(days=grace + 1),
                   extra={"promise_date": promise["promised_date"]})

    # 7. Rung selection.
    scored_band = band(int(score.get("score", 0)), config)
    effective_band = scored_band
    clamp = ladder.get("low_confidence_band")
    if clamp and score.get("confidence") == "low":
        effective_band = clamp
    pacing = ladder["pacing"][effective_band]
    base = int(pacing["start_rung"])
    current = highest_rung_used(history)
    current = base if current is None else max(current, base)

    step_up = 1 if (days_since is None or days_since >= int(pacing["days_between_rungs"])) else 0
    if not history:
        step_up = 0                      # the first contact starts at the base rung
    jump = int(ladder.get("broken_promise_rung_jump", 1)) * broken_promises(promises, today, grace)

    desired = max(base, current + step_up + jump)
    chosen = min(desired, ceiling)                       # <-- the ceiling, applied

    # 7b. A rung with no room left escalates, if the law allows it.
    while chosen < ceiling and contacts_at_rung(history, chosen) >= int(
            rungs.rung(chosen)["max_messages"]):
        chosen += 1
    chosen = min(chosen, ceiling)                        # <-- re-applied after the walk

    base_detail.update({"scored_band": scored_band, "effective_band": effective_band})
    capped = desired > ceiling
    cap_note = (f"; wanted rung {desired} but the law supports at most {ceiling}"
                if capped else "")
    if effective_band != scored_band:
        seen = int(score.get("history_count", 0) or 0)
        how_paced = (f"{scored_band} band, paced as {effective_band}: low confidence "
                     f"from {seen} settled invoice{'' if seen == 1 else 's'}")
    else:
        how_paced = f"{scored_band} band"
    why = (f"score {score.get('score')} ({how_paced}) starts at rung {base}; "
           f"{legal_position['days_overdue']} days overdue; ceiling {ceiling}{cap_note}")

    # 8. Rung 4 is a stop, not a message. This MUST precede every send gate:
    #    rung 4 has max_messages of 0, so the exhaustion check below would
    #    otherwise swallow it into a wait and no draft would ever be produced.
    if chosen >= HANDOFF_RUNG:
        draft = samadhaan.build_draft(invoice, buyer, legal_position, today)
        return act(HANDOFF, HANDOFF_RUNG,
                   f"escalated to the final rung, so contact stops and a human takes over ({why})",
                   capped=capped,
                   extra={"samadhaan_draft": {
                       "ready": draft["ready"],
                       "blockers": draft["blockers"],
                       "warnings": draft["warnings"],
                   }})

    rung_config = rungs.rung(chosen)

    # 9. No room left at this rung and no room to escalate: pause, do not stop.
    #    The ceiling rises on its own as the invoice ages.
    if contacts_at_rung(history, chosen) >= int(rung_config["max_messages"]):
        return act(WAIT, chosen,
                   f"rung {chosen} has used all {rung_config['max_messages']} of its "
                   f"messages and the law does not yet support going higher{cap_note}",
                   capped=capped, review=today + timedelta(days=CEILING_REVIEW_DAYS))

    # 10. Weekends are for people.
    if not stop_rules.get("send_on_weekends", False) and today.weekday() >= 5:
        monday = today + timedelta(days=7 - today.weekday())
        return act(WAIT, chosen, "no messages are sent at weekends",
                   capped=capped, review=monday)

    # 11. Give them room to breathe between messages at the same rung.
    spacing = int(rung_config["min_days_between_contacts"])
    if days_since is not None and days_since < spacing:
        return act(WAIT, chosen,
                   f"last contacted {days_since} days ago; rung {chosen} asks for "
                   f"{spacing} days between messages",
                   capped=capped, review=last + timedelta(days=spacing))

    # 12. Send -- unless this is the ambiguous case, where we ask for a view.
    skeleton = rungs.fact_skeleton(chosen, legal_position, invoice, buyer)
    if _is_ambiguous(invoice, legal_position, history):
        decision, reasoning = _ask_llm(invoice, legal_position, history,
                                       f"send a rung {chosen} message")
        if decision == "wait":
            return act(WAIT, chosen,
                       f"partial payment and an unclear reply; holding off. {reasoning}",
                       source="llm", capped=capped,
                       review=today + timedelta(days=spacing),
                       extra={"llm_decision": decision})
        return act(SEND, chosen, f"{why}. Model saw no reason to hold off: {reasoning}",
                   source="llm", capped=capped, skeleton=skeleton,
                   extra={"llm_decision": decision, "llm_ignored": decision != "wait"})

    return act(SEND, chosen, why, capped=capped, skeleton=skeleton)


def stop_reason(case: dict[str, Any], today: date) -> str | None:
    """Return why we must not send anything, or None if sending is allowed.

    Kept for callers that only need the yes/no. decide() is the real entry
    point and applies the same rules in the same order.
    """
    action = decide(
        case["invoice"], case["buyer"], case["score"], case["legal_position"],
        case.get("promises", []), case.get("history", []), log=False,
    )
    return None if action.kind == SEND else action.reason
