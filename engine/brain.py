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

PHASE 3: once every stopping rule and rung gate above has cleared and the
escalation walk has picked a rung, decide() used to build one unconditional
SEND at that rung. With config/rules.yaml's brain.ev_mode set to "on" -- and
only for a caller that supplies a two-axis score (engine.ability_willingness.
two_axis_score()'s output, carrying a "quadrant" key) -- that fallthrough is
replaced by an EV-informed choice among engine.negotiation's action space,
narrowed to whatever config/rules.yaml's negotiation.eligible_actions allows
for this buyer's quadrant. Two new Action kinds exist for it: PAYMENT_PLAN
and COUNTER_SETTLE, both buyer-facing sends at the already-chosen rung, same
as SEND. Reaching a handoff at all is NEVER affected by ev_mode -- chosen
reaching HANDOFF_RUNG is decided entirely by the rung selection above,
exactly as before this phase. (See the rung-4 branch's own comment: it
calls eligible_negotiation_actions() too, but only to let EV pick WHICH
FLAVOR of an already-certain handoff to record -- human_handoff or
legal_escalation -- never whether one happens.) With ev_mode off (the
default) or no quadrant on the score, decide() is byte-for-byte what it
always was -- see tests/test_brain.py's snapshot test.

EXPLORATION (simulator only): decide()'s `explore_rng` argument swaps the EV
branches' argmax for a uniform sample over the SAME already-gated candidate
list. It is an object, not a config key, precisely so nothing but a caller
holding one can turn it on -- see that argument's own docstring, and
sim/run_sim.py's run_agent(explore=True). It never widens what is eligible and
never bypasses a stop rule; tests/test_exploration_respects_gates.py is the
standing proof.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from engine import audit, learning, negotiation, rungs, samadhaan
from engine.ability_willingness import outstanding_paise
from engine.config import ev_mode_on as _ev_mode_selected, rules
from engine.llm import LLMError, llm

#: Kinds of action. `wait` is recoverable, `stop` is terminal, `handoff` gives
#: the case to a human, `send` is the only one that produces a message.
#: PAYMENT_PLAN and COUNTER_SETTLE are Phase 3 additions -- both are
#: buyer-facing sends, exactly like SEND, at the already-chosen rung; only
#: engine.negotiation's EV ranking (behind config/rules.yaml's
#: brain.ev_mode) ever produces one.
WAIT, SEND, HANDOFF, STOP = "wait", "send", "handoff", "stop"
PAYMENT_PLAN, COUNTER_SETTLE = "payment_plan", "counter_settle"

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


def _not_superseded(promises: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop every promise superseded by a later one for the same invoice.

    docs/edge_cases.md TC-014: engine.promises.apply_reply() never cancels a
    prior OPEN promise when the buyer makes a new one for the same invoice --
    it only ever appends. Without this filter, a promise the buyer
    proactively renegotiated before it came due would still count as its own
    separately broken promise once its own grace passed -- confirmed to
    inflate the rung-jump in decide() below and push a case to a premature
    human handoff for a buyer who renegotiated in good faith.

    "Later" is decided by recorded_on (when the buyer said it), not
    promised_date (what they promised) -- a buyer can renegotiate to an
    EARLIER date too, and it is the most recent thing they said that matters.
    Ties (identical or missing recorded_on) prefer the later list position,
    matching append order.
    """
    winner_index: dict[str, int] = {}
    for i, promise in enumerate(promises or []):
        invoice_id = promise.get("invoice_id")
        recorded_on = promise.get("recorded_on") or ""
        current = winner_index.get(invoice_id)
        if current is None or recorded_on >= (promises[current].get("recorded_on") or ""):
            winner_index[invoice_id] = i
    keep = set(winner_index.values())
    return [p for i, p in enumerate(promises or []) if i in keep]


def active_promise(promises: list[dict[str, Any]], today: date, grace_days: int) -> dict | None:
    """A promise still within its grace period. Chasing over it would be rude."""
    for promise in _not_superseded(promises):
        if promise.get("status") != "open":
            continue
        if _as_date(promise["promised_date"]) + timedelta(days=grace_days) >= today:
            return promise
    return None


def broken_promises(promises: list[dict[str, Any]], today: date, grace_days: int) -> int:
    """Promises whose date and grace have passed with the money still missing."""
    return sum(
        1 for promise in _not_superseded(promises)
        if promise.get("status") in {"open", "broken"}
        and _as_date(promise["promised_date"]) + timedelta(days=grace_days) < today
    )


def eligible_negotiation_actions(
    quadrant: str, chosen_rung: int, config: dict[str, Any],
) -> tuple[str, ...]:
    """Which of engine.negotiation.ACTIONS are worth ranking for this buyer today.

    Two independent gates, both config-driven, applied before EV ever ranks
    anything -- the same two-gate shape the ladder itself already uses (a
    quadrant/pacing band decides what is EVER appropriate; the escalation
    walk decides what is reachable TODAY):

        config/rules.yaml's negotiation.eligible_actions says what is ever
        appropriate for THIS buyer's profile -- a good_customer is never
        offered legal pressure, a can_pay_but_wont is never offered a
        payment plan.

        `chosen_rung` gates human_handoff/legal_escalation on the SAME
        condition decide()'s own non-EV rung-4 step uses -- chosen_rung must
        already equal HANDOFF_RUNG. This is deliberately NOT "is the legal
        ceiling open" (available_rung == HANDOFF_RUNG): the ceiling opening
        only means the LAW would permit rung 4 today, not that this
        invoice's own contact history has organically escalated there (a
        broken-promise jump, a rung fully exhausted, or enough elapsed time
        at the top rung already used -- see decide()'s step 7/7b). A
        first-ever contact, for instance, can never reach chosen_rung 4 on
        the backlog formula alone, however wide open the ceiling is. Gating
        on the ceiling instead of chosen_rung would let EV send a case
        straight to a human handoff sooner than the ordinary escalation walk
        ever would have -- exactly the over-eager failure mode this
        parameter name is written to rule out.

    decide() calls this function from two different places, at two different
    values of chosen_rung, for two different purposes:

        Step 13 (choosing the general action, e.g. send vs. wait vs. a
        payment plan) calls it with the rung selection's own `chosen`, which
        is ALWAYS below HANDOFF_RUNG at that point in decide() -- step 8
        above already intercepts and returns a HANDOFF, unconditionally,
        in every case where chosen reaches HANDOFF_RUNG, before step 13 ever
        runs. So at that call site, human_handoff/legal_escalation are
        permanently excluded: EV may choose a different KIND of action
        among whatever is already reachable today, never make MORE
        reachable than the existing escalation walk already allows.

        Step 8 (once a handoff is already certain -- chosen has already
        reached HANDOFF_RUNG) calls this SAME function with that same
        chosen, specifically to let EV pick WHICH FLAVOR of handoff to
        record. There, the chosen_rung gate is a pass-through (chosen_rung
        is never below HANDOFF_RUNG at that call site), so only the
        quadrant filter narrows anything -- a can_pay_but_wont or high_risk
        buyer offers both flavors, cash_flow_problem offers only
        human_handoff, and good_customer offers neither (falling through to
        a plain, undifferentiated HANDOFF). This never changes WHETHER a
        handoff fires, only which negotiation_action label rides along on
        one that was already certain.

    The parameter takes a general chosen_rung (rather than two separate
    hardcoded booleans for these two call sites) so both readings stay
    correct from one piece of logic, and so each can be tested directly, in
    isolation, at any chosen_rung value.

    Rules decide what is possible; EV decides what is best among what is
    possible.
    """
    allowed = list(config["negotiation"]["eligible_actions"][quadrant])
    if chosen_rung < HANDOFF_RUNG:
        allowed = [a for a in allowed
                  if a not in (negotiation.HUMAN_HANDOFF, negotiation.LEGAL_ESCALATION)]
    return tuple(allowed)


def negotiation_rung(action: str) -> int | None:
    """The ladder rung a negotiation action names a MESSAGE at, or None.

    Looked up from the ladder's own names in config/rules.yaml rather than
    hardcoded, because the correspondence is a fact about config, not about
    this module: engine.negotiation's soft_nudge/firm/legal_facts share their
    names with rungs 1-3 deliberately (see that module's docstring), so if a
    rung is ever renumbered the mapping follows on its own.

    None means "this action does not name a rung of its own", which is a
    different thing from rung 0 and is why `wait` is not in here either:

        wait            sends nothing, so the rung carried on a WAIT Action is
                        vestigial -- it records where the ladder HAD got to,
                        not something a wait was clamped away from.
        payment_plan    both ride at whatever rung the escalation walk already
        counter_settle  chose, so neither has a rung a gate could override.

    human_handoff/legal_escalation both answer HANDOFF_RUNG: they are only
    ever selectable once chosen has already reached it (see
    eligible_negotiation_actions()), so they can never be clamped.
    """
    if action in (negotiation.HUMAN_HANDOFF, negotiation.LEGAL_ESCALATION):
        return HANDOFF_RUNG
    return next((int(entry["id"]) for entry in rungs.all_rungs()
                 if entry["name"] == action and int(entry["id"]) in rungs.BUYER_FACING_RUNGS),
                None)


def _pick_negotiation_action(
    candidates: tuple[str, ...],
    *,
    quadrant: str,
    outstanding: int,
    broken_promises: int,
    explore_rng: random.Random | None,
) -> tuple[dict[str, Any], str]:
    """Choose one action out of `candidates`, and say how it was chosen.

    Two selection policies over the SAME already-gated candidate list:

        argmax   engine.negotiation.rank_actions()'s top pick -- the shipped
                 behaviour, and what every caller without an explore_rng gets.
        explore  a uniform sample from `candidates` (the simulator's
                 exploration mode -- see sim/run_sim.py's run_agent(explore=)).

    The critical property, and the reason the sampling lives HERE rather than
    anywhere upstream: `candidates` has already been through
    eligible_negotiation_actions(), and decide() only reaches either call site
    after every stop rule, spacing rule and rung gate above has cleared. So
    exploration can only ever pick differently among options the rules had
    already declared acceptable today -- it can never widen the set, and there
    is no code path by which it reaches an action the gates excluded.

    Returns (evaluation, selection) where `evaluation` is the same
    evaluate_action() record shape either policy produces, so the audit trail
    still carries the EV arithmetic for whatever was actually chosen, and
    `selection` is "argmax" or "explore".
    """
    if explore_rng is None:
        return negotiation.rank_actions(
            quadrant, outstanding, broken_promises=broken_promises, candidates=candidates,
        )[0], "argmax"
    sampled = explore_rng.choice(sorted(candidates))
    return negotiation.evaluate_action(
        sampled, quadrant=quadrant, outstanding_paise=outstanding,
        broken_promises=broken_promises,
    ), "explore"


def _negotiation_extra(evaluation: dict[str, Any], selection: str,
                       executed_rung: int) -> dict[str, Any]:
    """The audit detail every EV/explore decision carries.

    `negotiation_gate_override` is the number sim/run_sim.py's exploration
    mode exists to measure: True when the chosen action named a rung of its
    own and the rung actually executed is a different one, i.e. the escalation
    walk and the law ceiling between them overruled the label. It is
    deliberately not "proposed > executed" -- an action proposed BELOW the
    rung the ladder had already reached is just as much a case of the label
    not describing the message that went out.
    """
    proposed_rung = negotiation_rung(evaluation["action"])
    return {
        "negotiation_action": evaluation["action"],
        "negotiation_selection": selection,
        "negotiation_proposed_rung": proposed_rung,
        "negotiation_gate_override": proposed_rung is not None and proposed_rung != executed_rung,
        "ev": evaluation,
    }


def _learned_cell_key(negotiation_action: str) -> str | None:
    """The config/learned_recovery.yaml cell a negotiation action resolves to,
    or None for one the fit never covers.

    engine.learning._resolve_cell() maps a SEND tier name to send.<tier>;
    payment_plan / counter_settle are flat cells; `wait` and both handoff
    flavors have no learned cell (the fit excludes handoff rows -- post-handoff
    recovery is unobservable in the simulator).
    """
    if negotiation_action in (negotiation.SOFT_NUDGE, negotiation.FIRM,
                              negotiation.LEGAL_FACTS, negotiation.PAYMENT_PLAN,
                              negotiation.COUNTER_SETTLE):
        return negotiation_action
    return None


def _gate_reason(
    *, bandit_top: str, executed: str, selection: str, quadrant_menu: tuple[str, ...],
    chosen_rung: int, ceiling: int, law_capped: bool, rung_overridden: bool,
) -> str | None:
    """Why the executed action differs from what raw EV wanted -- or None when
    the learner got its way and the delivered rung matched its label too.

    That None-versus-a-string is the whole point of the field: per decision, a
    reader can see whether the rules or the learned bandit had the final say.
    The strings name the binding constraint:

      law_ceiling_rung_N               the legal-leverage ceiling (engine.law)
                                       sat below the rung the bandit's pick
                                       needed.
      escalation_rung_N_below_handoff  the bandit wanted a human handoff, but
                                       this invoice's own escalation walk has not
                                       reached the handoff rung (see
                                       eligible_negotiation_actions()).
      eligible_actions_policy          config/rules.yaml's negotiation.
                                       eligible_actions for this quadrant does
                                       not offer the bandit's pick at all (e.g.
                                       a good_customer is never offered legal
                                       pressure).
      escalation_walk_rung_N           same action label, delivered at a
                                       different rung by the escalation walk.
      exploration_sample               SIMULATOR exploration mode sampled a
                                       non-argmax action -- not a gate, labelled
                                       so it is not read as one.
    """
    if bandit_top == executed:
        if rung_overridden:
            return (f"law_ceiling_rung_{ceiling}" if law_capped
                    else f"escalation_walk_rung_{chosen_rung}")
        return None
    if selection == "explore":
        return "exploration_sample"
    if bandit_top in (negotiation.HUMAN_HANDOFF, negotiation.LEGAL_ESCALATION):
        if ceiling < HANDOFF_RUNG:
            return f"law_ceiling_rung_{ceiling}"
        if chosen_rung < HANDOFF_RUNG:
            return f"escalation_rung_{chosen_rung}_below_handoff"
        return "eligible_actions_policy"
    if bandit_top not in quadrant_menu:
        return "eligible_actions_policy"
    if law_capped:
        return f"law_ceiling_rung_{ceiling}"
    return "eligible_actions_policy"


def _learning_audit(
    evaluation: dict[str, Any],
    selection: str,
    *,
    quadrant: str,
    outstanding: int,
    broken_promises: int,
    chosen_rung: int,
    ceiling: int,
    law_capped: bool,
    rung_overridden: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    """The six learned-decision fields for the audit trail, added by decide()'s
    EV branches ONLY when config/rules.yaml's learning.enabled is true.

    bandit_top_choice is negotiation.rank_actions() over the FULL action space --
    what raw expected value would do with every gate removed. executed_action is
    what decide() actually committed to, after negotiation.eligible_actions and
    the law ceiling. gate_reason names the binding constraint when they differ,
    None when they do not -- so every case of the rules overriding the learner
    is a visible line in audit/audit_log.jsonl.

    learning_method / estimated_probability / observations describe the number
    the EV formula actually used for the executed action: the value from
    engine.negotiation.recovery_probability() (0-1), and where it came from and
    on how much data (engine.learning.audit_method() / .observations()).

    The rank_actions() call here runs AFTER _pick_negotiation_action() chose the
    executed action, so it cannot change the decision. Under online learning it
    draws from the same per-(seed, invoice, day) Thompson RNG, which is fresh per
    decision and discarded when engine.learning.online_sampling() exits --
    selection, the outcome ledger and the posterior updates are all unaffected,
    so a seeded run stays reproducible.
    """
    neg_action = evaluation["action"]
    cell_key = _learned_cell_key(neg_action)
    bandit_top = negotiation.rank_actions(
        quadrant, outstanding, broken_promises=broken_promises,
    )[0]["action"]
    return {
        "learning_method": learning.audit_method(quadrant, cell_key),
        "estimated_probability": round(evaluation["probability"] / 100, 4),
        "observations": learning.observations(quadrant, cell_key),
        "bandit_top_choice": bandit_top,
        "executed_action": neg_action,
        "gate_reason": _gate_reason(
            bandit_top=bandit_top, executed=neg_action, selection=selection,
            quadrant_menu=tuple(config["negotiation"]["eligible_actions"][quadrant]),
            chosen_rung=chosen_rung, ceiling=ceiling, law_capped=law_capped,
            rung_overridden=rung_overridden,
        ),
    }


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
    explore_rng: random.Random | None = None,
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
        explore_rng: SIMULATOR ONLY -- sim/run_sim.py's run_agent(explore=True).
            When supplied (and only then), the EV branches below sample
            uniformly from their already-gated candidate list instead of
            taking the top-EV action, so a learning run can see what happens
            after actions the current EV grid would never have picked. (The
            attribution ledger that records those results is named nowhere in
            this module, not even in prose: its own test suite greps this
            directory for the name and treats any hit as a violation. That
            tripwire is right to be that crude, and this module reads nothing
            the ledger writes.) It is a
            random.Random OBJECT rather than a config flag on purpose: there
            is no key in config/rules.yaml that turns exploration on, so no
            production entry point can reach it by editing config, and main.py
            -- which never constructs one, and passes a plain
            engine.score.score_buyer() dict with no "quadrant" so ev_mode_on
            is False for it regardless -- cannot trigger it at all.
            Exploration changes only WHICH action is chosen from the eligible
            list; every stop rule, spacing rule, rung gate and law ceiling
            above still runs first and unchanged.

    Returns:
        One Action, carrying the reason and everything needed to audit it.
    """
    config = config or rules()
    today = _as_date(legal_position["as_of"])
    ceiling = int(legal_position["available_rung"])
    stop_rules = config["stop_rules"]
    ladder = config["ladder"]
    # Read once, reused by both step 8 (which handoff flavor) and step 13
    # (which action generally) -- see eligible_negotiation_actions()'s own
    # docstring for what "quadrant present" does and does not unlock.
    quadrant = score.get("quadrant")
    ev_mode_on = bool(quadrant) and _ev_mode_selected(config)

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
    jump = int(ladder.get("broken_promise_rung_jump", 1)) * broken_promises(promises, today, grace)

    # A first-ever contact is not necessarily a fresh case: an invoice that
    # sat overdue and uncontacted before the watchdog ever saw it (P2) should
    # not open with the same soft nudge as a buyer who became overdue
    # yesterday. One cadence interval already having passed silently is
    # enough to say "this is not fresh" and open one rung higher -- but only
    # one. It deliberately does not keep counting cadence intervals for
    # however old the backlog is: a case old enough for the legal ceiling to
    # already sit at its maximum still gets one real message before ordinary
    # escalation (which already accounts for elapsed days via step_up above)
    # carries it the rest of the way, rather than opening on a stop with the
    # buyer never contacted at all.
    backlog_steps = 0
    if not history:
        if int(legal_position["days_overdue"]) >= int(pacing["days_between_rungs"]):
            backlog_steps = 1
        desired = base + backlog_steps
    else:
        desired = max(base, current + step_up + jump)
    chosen = min(desired, ceiling)                       # <-- the ceiling, applied

    # 7b. A rung with no room left escalates, if the law allows it.
    while chosen < ceiling and contacts_at_rung(history, chosen) >= int(
            rungs.rung(chosen)["max_messages"]):
        chosen += 1
    chosen = min(chosen, ceiling)                        # <-- re-applied after the walk

    base_detail.update({"scored_band": scored_band, "effective_band": effective_band})
    capped = desired > ceiling
    if not capped:
        cap_note = ""
    elif desired > HANDOFF_RUNG:
        # `desired` ran past the top of the ladder (rungs are 1-4) -- naming a
        # rung 5+ that does not exist reads as an off-by-one bug. Say what
        # actually happened: the walk wanted to keep escalating and the law
        # stopped it. A real, in-range desired rung keeps its exact wording
        # ("wanted rung 3 but the law supports at most 2").
        cap_note = f"; wanted to escalate further but the law supports at most {ceiling}"
    else:
        cap_note = f"; wanted rung {desired} but the law supports at most {ceiling}"
    if effective_band != scored_band:
        seen = int(score.get("history_count", 0) or 0)
        how_paced = (f"{scored_band} band, paced as {effective_band}: low confidence "
                     f"from {seen} settled invoice{'' if seen == 1 else 's'}")
    else:
        how_paced = f"{scored_band} band"
    backlog_note = (f"; first contact but already {legal_position['days_overdue']} days "
                    f"overdue, paced one rung ahead of the base for the backlog"
                    if backlog_steps else "")
    why = (f"score {score.get('score')} ({how_paced}) starts at rung {base}; "
           f"{legal_position['days_overdue']} days overdue; ceiling {ceiling}"
           f"{cap_note}{backlog_note}")

    # 8. Rung 4 is a stop, not a message. This MUST precede every send gate:
    #    rung 4 has max_messages of 0, so the exhaustion check below would
    #    otherwise swallow it into a wait and no draft would ever be produced.
    #
    #    WHETHER a handoff fires here is never affected by ev_mode -- chosen
    #    reaching HANDOFF_RUNG is decided entirely by the rung selection
    #    above, unconditionally, exactly as before this phase. What ev_mode
    #    CAN do, once a handoff is already certain, is pick WHICH KIND of
    #    handoff to record: human_handoff or legal_escalation, by EV, among
    #    whichever of those two config/rules.yaml's negotiation.
    #    eligible_actions[quadrant] actually offers (a can_pay_but_wont or
    #    high_risk buyer offers both; cash_flow_problem offers only
    #    human_handoff; good_customer offers neither). Reuses
    #    eligible_negotiation_actions() rather than reading the config table
    #    directly -- chosen is already >= HANDOFF_RUNG here, so that
    #    function's own reachability gate is a pass-through, and only the
    #    quadrant filter actually does anything. A quadrant offering neither
    #    falls straight through to the plain, undifferentiated HANDOFF below,
    #    exactly as it always has: rung 4 is a stop, not a message, whatever
    #    the quadrant, and no fallback action is invented here.
    if chosen >= HANDOFF_RUNG:
        draft = samadhaan.build_draft(invoice, buyer, legal_position, today)
        extra = {"samadhaan_draft": {
            "ready": draft["ready"],
            "blockers": draft["blockers"],
            "warnings": draft["warnings"],
        }}
        if ev_mode_on:
            handoff_candidates = tuple(
                a for a in eligible_negotiation_actions(quadrant, chosen, config)
                if a in (negotiation.HUMAN_HANDOFF, negotiation.LEGAL_ESCALATION)
            )
            if handoff_candidates:
                promise_count = int((score.get("signals") or {}).get("broken_promises", 0) or 0)
                # explore_rng samples the FLAVOR here, never whether a handoff
                # fires: this branch has already committed to HANDOFF above,
                # and handoff_candidates holds nothing but the two flavors.
                winner, selection = _pick_negotiation_action(
                    handoff_candidates, quadrant=quadrant,
                    outstanding=outstanding_paise(invoice),
                    broken_promises=promise_count, explore_rng=explore_rng,
                )
                handoff_extra = _negotiation_extra(winner, selection, HANDOFF_RUNG)
                extra.update(handoff_extra)
                if learning.enabled():
                    extra.update(_learning_audit(
                        winner, selection, quadrant=quadrant,
                        outstanding=outstanding_paise(invoice),
                        broken_promises=promise_count, chosen_rung=chosen,
                        ceiling=ceiling, law_capped=capped,
                        rung_overridden=handoff_extra["negotiation_gate_override"],
                        config=config,
                    ))
        return act(HANDOFF, HANDOFF_RUNG,
                   f"escalated to the final rung, so contact stops and a human takes over ({why})",
                   capped=capped, extra=extra)

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

    # 13. EV-informed action selection (Phase 3), replacing the unconditional
    #     send below -- only when ev_mode_on (config/rules.yaml's
    #     brain.ev_mode is "on" AND the caller supplied a two-axis score, one
    #     carrying "quadrant"; see engine.ability_willingness.
    #     two_axis_score()). A caller still passing a plain
    #     engine.score.score_buyer() dict has no "quadrant" key, so it always
    #     falls through to the unconditional send exactly as before -- this
    #     is what keeps ev_mode: off byte-for-byte identical to pre-Phase-3
    #     output (tests/test_brain.py's snapshot test), and what keeps this
    #     branch inert for every caller (main.py, sim/scenario_tc141.py)
    #     that has not opted into a two-axis score.
    #
    #     Candidates are gated by `chosen`, NOT `ceiling`: eligible_negotiation_
    #     actions() only admits human_handoff/legal_escalation once chosen has
    #     ALREADY reached HANDOFF_RUNG -- the identical condition step 8 above
    #     uses. Since step 8 unconditionally intercepts and returns a HANDOFF
    #     whenever that is true, execution only ever reaches THIS branch with
    #     chosen < HANDOFF_RUNG, so a handoff is never actually one of EV's
    #     live choices at this particular call site. That is intentional, not
    #     dead code left by accident: EV may choose a different KIND of
    #     action among whatever is already reachable today (a send vs. a
    #     payment plan vs. a wait), but must never become MORE willing to
    #     hand a case to a human than the existing escalation walk already
    #     is -- see eligible_negotiation_actions()'s own docstring for the
    #     full reasoning, and tests/test_brain.py's ceiling/chosen-rung tests
    #     for the proof. (Step 8 above calls the SAME helper with a
    #     DIFFERENT chosen -- one already >= HANDOFF_RUNG there by
    #     construction -- to pick which flavor of an already-certain handoff
    #     to record; that is a separate call site with its own precondition,
    #     not a contradiction of this one.)
    if ev_mode_on:
        promise_count = int((score.get("signals") or {}).get("broken_promises", 0) or 0)
        candidates = eligible_negotiation_actions(quadrant, chosen, config)
        winner, selection = _pick_negotiation_action(
            candidates, quadrant=quadrant, outstanding=outstanding_paise(invoice),
            broken_promises=promise_count, explore_rng=explore_rng,
        )
        neg_action = winner["action"]
        ev_extra = _negotiation_extra(winner, selection, chosen)
        if learning.enabled():
            ev_extra.update(_learning_audit(
                winner, selection, quadrant=quadrant,
                outstanding=outstanding_paise(invoice),
                broken_promises=promise_count, chosen_rung=chosen,
                ceiling=ceiling, law_capped=capped,
                rung_overridden=ev_extra["negotiation_gate_override"],
                config=config,
            ))
        how = (f"EV ranked {neg_action} highest" if selection == "argmax"
               else f"exploration sampled {neg_action} uniformly from "
                    f"{len(candidates)} eligible action(s)")
        ev_why = (f"{why}; {how} for a {quadrant} buyer "
                  f"({winner['probability']}% recover, EV {winner['ev_paise']} paise)")

        if neg_action == negotiation.WAIT:
            return act(WAIT, chosen, ev_why, capped=capped,
                       review=today + timedelta(days=CEILING_REVIEW_DAYS), extra=ev_extra)

        if neg_action in (negotiation.HUMAN_HANDOFF, negotiation.LEGAL_ESCALATION):
            # Unreachable via this call site -- see the comment above and
            # eligible_negotiation_actions()'s docstring. Kept (not collapsed
            # away) so the mapping stays correct if that invariant ever
            # changes, and so engine.negotiation's own action space still
            # maps onto a real Action.kind everywhere it is used.
            draft = samadhaan.build_draft(invoice, buyer, legal_position, today)
            return act(HANDOFF, HANDOFF_RUNG, ev_why, capped=capped,
                       extra={**ev_extra, "samadhaan_draft": {
                           "ready": draft["ready"], "blockers": draft["blockers"],
                           "warnings": draft["warnings"],
                       }})

        kind = {negotiation.PAYMENT_PLAN: PAYMENT_PLAN,
                negotiation.COUNTER_SETTLE: COUNTER_SETTLE}.get(neg_action, SEND)
        return act(kind, chosen, ev_why, capped=capped, skeleton=skeleton, extra=ev_extra)

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
