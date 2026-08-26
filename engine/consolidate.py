"""Buyer-level consolidation -- groups a day's SEND decisions by buyer.

engine.brain.decide() is untouched by this module and stays entirely
per-invoice: it is called exactly as before, in a loop, once per invoice,
and its stop rules (max_total, per-rung max_messages), promise grace,
escalation ceiling and dispute handoff are computed exactly as if this
module did not exist. What this module changes is purely how many outbound
ENVELOPES carry those decisions, never how many times any invoice may be
escalated.

Two invoices at different rungs for the same buyer keep their own
independently-decided rung and skeleton -- a bundle is one envelope, one
section per invoice, never "the highest/lowest rung applied to everyone" (see
CLAUDE.md's W3 plan, point c). A rung-1 invoice (no legal content permitted,
by rungs.RUNG_ONE_RULES) is never bundled with a rung >= 2 invoice (legal/tax
content) for the same reason: mixing them would either falsely trip the
rung-1 guardrail's whole-message check or require a section-aware guardrail
that depends on reliably parsing LLM output -- see engine/writer.py's
passes_guardrail_multi(). So a buyer may receive up to TWO envelopes on a
given day (one per tier), never a hard guarantee of exactly one.

Only a genuine buyer-facing SEND -- kind=="send" and a skeleton whose
sends_to_buyer is true -- is eligible for a bundle. Everything else (wait,
handoff, stop; or a send whose skeleton refuses to send, which should never
happen but is checked anyway) is dropped here. This is what keeps a disputed
invoice out of a bundle: engine.brain.decide()'s dispute rule fires before
any SEND could be chosen, so a disputed invoice only ever reaches this module
as a HANDOFF, which this filter already excludes -- see
tests/test_consolidate.py for the explicit proof, not just the argument.
"""

from __future__ import annotations

from typing import Any

from engine.config import rules

#: Rung 1 carries no legal content; rung 2/3 do. Never mixed in one envelope.
COURTESY, ESCALATED = "courtesy", "escalated"


def _tier(rung: int) -> str:
    return COURTESY if rung <= 1 else ESCALATED


def _eligible(action: Any) -> bool:
    """True only for a genuine buyer-facing send.

    kind is checked against the string "send" (engine.brain.SEND) rather than
    importing engine.brain, so this module has no dependency on the brain --
    consolidate.py groups whatever Action-shaped objects it is given.
    """
    return (
        getattr(action, "kind", None) == "send"
        and getattr(action, "skeleton", None) is not None
        and bool(action.skeleton.get("sends_to_buyer"))
    )


def bundle_sends(
    actions: list[Any], max_invoices_per_message: int | None = None,
) -> list[dict[str, Any]]:
    """Group today's eligible SEND actions into buyer/tier envelopes.

    Args:
        actions: one engine.brain.Action per invoice for today (any kind --
            ineligible ones are filtered out here, not by the caller).
        max_invoices_per_message: caps how many invoices share one envelope;
            defaults to config/rules.yaml's consolidation.max_invoices_per_message.
            A tier with more than this for one buyer splits into several
            envelopes, in the same invoice order, rather than one oversized
            draft that risks truncating against the writer's token budget.

    Returns:
        A list of bundles, each ``{"buyer_id": ..., "tier": ..., "actions": [...]}``,
        in first-seen order. A bundle's actions are guaranteed to share one
        buyer_id and one tier (courtesy or escalated) -- never mixed.
    """
    cap = (int(max_invoices_per_message) if max_invoices_per_message is not None
           else int(rules()["consolidation"]["max_invoices_per_message"]))

    groups: dict[tuple[str, str], list[Any]] = {}
    order: list[tuple[str, str]] = []
    for action in actions:
        if not _eligible(action):
            continue
        key = (action.buyer_id, _tier(action.rung))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(action)

    bundles: list[dict[str, Any]] = []
    for buyer_id, tier in order:
        group = groups[(buyer_id, tier)]
        for start in range(0, len(group), cap):
            bundles.append({
                "buyer_id": buyer_id, "tier": tier, "actions": group[start:start + cap],
            })
    return bundles
