"""Tests for buyer-level message consolidation.

engine.consolidate never touches a brain.decide() decision -- it only groups
already-decided SEND actions by buyer, into homogeneous rung tiers, so a
buyer with several overdue invoices gets one envelope per tier instead of one
per invoice (CLAUDE.md W3, docs/winning_layer.md Enhancement 14). The
highest-risk invariant here is what must NEVER appear in a bundle: a
disputed invoice's HANDOFF action, or anything that is not a buyer-facing
SEND.
"""

from __future__ import annotations

from engine import brain, consolidate

_UNSET = object()


#: Kinds that default to a real skeleton below -- SEND and Phase 3's two new
#: buyer-facing kinds, which reuse the same skeleton construction (see
#: engine.brain.decide()'s EV branch).
_SEND_LIKE_KINDS = (brain.SEND, "payment_plan", "counter_settle")


def action(
    invoice_id: str, buyer_id: str, *, kind: str = brain.SEND, rung: int = 1,
    sends_to_buyer: bool = True, skeleton: dict | None | object = _UNSET,
) -> brain.Action:
    if skeleton is _UNSET:
        skeleton = ({"rung": rung, "sends_to_buyer": sends_to_buyer}
                    if kind in _SEND_LIKE_KINDS else None)
    return brain.Action(
        kind=kind, rung=rung, reason="test", source="rule",
        invoice_id=invoice_id, buyer_id=buyer_id, available_rung=4,
        skeleton=skeleton,
    )


# --- grouping ---------------------------------------------------------------

def test_two_sends_for_the_same_buyer_same_tier_are_bundled_together() -> None:
    actions = [action("INV-1", "BUY-01", rung=1), action("INV-2", "BUY-01", rung=1)]
    bundles = consolidate.bundle_sends(actions)
    assert len(bundles) == 1
    assert bundles[0]["buyer_id"] == "BUY-01"
    assert bundles[0]["tier"] == consolidate.COURTESY
    assert [a.invoice_id for a in bundles[0]["actions"]] == ["INV-1", "INV-2"]


def test_different_buyers_are_never_bundled_together() -> None:
    actions = [action("INV-1", "BUY-01", rung=1), action("INV-2", "BUY-02", rung=1)]
    bundles = consolidate.bundle_sends(actions)
    assert len(bundles) == 2
    assert {b["buyer_id"] for b in bundles} == {"BUY-01", "BUY-02"}


def test_rung_one_and_rung_two_for_the_same_buyer_are_never_bundled_together() -> None:
    """The tier partition (plan 2c): courtesy (rung<=1) and escalated (rung>=2)
    never share an envelope, so the rung-1 no-legal-content guardrail can stay
    a simple whole-message check instead of needing to be section-aware."""
    actions = [action("INV-1", "BUY-01", rung=1), action("INV-2", "BUY-01", rung=2)]
    bundles = consolidate.bundle_sends(actions)
    assert len(bundles) == 2
    tiers = {b["tier"] for b in bundles}
    assert tiers == {consolidate.COURTESY, consolidate.ESCALATED}
    for b in bundles:
        assert b["buyer_id"] == "BUY-01"


def test_rung_two_and_rung_three_for_the_same_buyer_are_bundled_together() -> None:
    """Both are 'escalated' -- only the rung<=1 boundary matters."""
    actions = [action("INV-1", "BUY-01", rung=2), action("INV-2", "BUY-01", rung=3)]
    bundles = consolidate.bundle_sends(actions)
    assert len(bundles) == 1
    assert bundles[0]["tier"] == consolidate.ESCALATED


# --- Phase 3's new kinds: buyer-facing sends, exactly like SEND -----------

def test_a_payment_plan_action_is_bundled_like_an_ordinary_send() -> None:
    actions = [action("INV-1", "BUY-01", kind="payment_plan", rung=2)]
    bundles = consolidate.bundle_sends(actions)
    assert len(bundles) == 1
    assert bundles[0]["tier"] == consolidate.ESCALATED
    assert [a.invoice_id for a in bundles[0]["actions"]] == ["INV-1"]


def test_a_counter_settle_action_is_bundled_like_an_ordinary_send() -> None:
    actions = [action("INV-1", "BUY-01", kind="counter_settle", rung=2)]
    bundles = consolidate.bundle_sends(actions)
    assert len(bundles) == 1
    assert bundles[0]["tier"] == consolidate.ESCALATED


def test_a_payment_plan_and_a_send_for_the_same_buyer_and_tier_are_bundled_together() -> None:
    actions = [action("INV-1", "BUY-01", kind=brain.SEND, rung=2),
               action("INV-2", "BUY-01", kind="payment_plan", rung=3)]
    bundles = consolidate.bundle_sends(actions)
    assert len(bundles) == 1
    assert [a.invoice_id for a in bundles[0]["actions"]] == ["INV-1", "INV-2"]


def test_a_payment_plan_with_no_skeleton_never_enters_a_bundle() -> None:
    """Defensive, mirroring the plain-send case: a payment_plan/counter_settle
    action is only eligible with a real buyer-facing skeleton attached."""
    actions = [action("INV-1", "BUY-01", kind="payment_plan", rung=2, skeleton=None)]
    bundles = consolidate.bundle_sends(actions)
    assert bundles == []


# --- what must never enter a bundle -----------------------------------------

def test_a_disputed_invoices_handoff_action_never_enters_a_bundle() -> None:
    """The highest-risk guardrail in this change: a dispute must never reach a
    buyer through a consolidated message. It reaches consolidate.py as a
    HANDOFF (brain.decide()'s rule 2 fires before any SEND could happen), so
    this proves the exclusion here too, even though it is also structurally
    impossible for a HANDOFF to carry a skeleton with sends_to_buyer=True."""
    actions = [
        action("INV-1", "BUY-01", rung=1),
        action("INV-2", "BUY-01", kind=brain.HANDOFF, rung=0, skeleton=None),
    ]
    bundles = consolidate.bundle_sends(actions)
    all_ids = [a.invoice_id for b in bundles for a in b["actions"]]
    assert "INV-2" not in all_ids
    assert all_ids == ["INV-1"]


def test_wait_and_stop_actions_never_enter_a_bundle() -> None:
    actions = [
        action("INV-1", "BUY-01", kind=brain.WAIT, rung=0, skeleton=None),
        action("INV-2", "BUY-01", kind=brain.STOP, rung=0, skeleton=None),
    ]
    bundles = consolidate.bundle_sends(actions)
    assert bundles == []


def test_a_send_with_no_skeleton_never_enters_a_bundle() -> None:
    """Defensive: brain.decide() always attaches a skeleton to a SEND, but
    consolidate.py must not assume that silently."""
    actions = [action("INV-1", "BUY-01", rung=1, skeleton=None)]
    bundles = consolidate.bundle_sends(actions)
    assert bundles == []


def test_a_send_whose_skeleton_does_not_send_to_buyer_never_enters_a_bundle() -> None:
    actions = [action("INV-1", "BUY-01", rung=4, sends_to_buyer=False)]
    bundles = consolidate.bundle_sends(actions)
    assert bundles == []


# --- the size cap -------------------------------------------------------------

def test_a_tier_larger_than_the_configured_cap_splits_into_multiple_bundles() -> None:
    actions = [action(f"INV-{i}", "BUY-01", rung=1) for i in range(5)]
    bundles = consolidate.bundle_sends(actions, max_invoices_per_message=2)
    assert [len(b["actions"]) for b in bundles] == [2, 2, 1]
    assert all(b["buyer_id"] == "BUY-01" and b["tier"] == consolidate.COURTESY for b in bundles)
    # order preserved, nothing dropped
    assert [a.invoice_id for b in bundles for a in b["actions"]] == [f"INV-{i}" for i in range(5)]


def test_the_default_cap_is_read_from_config() -> None:
    from engine.config import rules

    configured = int(rules()["consolidation"]["max_invoices_per_message"])
    actions = [action(f"INV-{i}", "BUY-01", rung=1) for i in range(configured + 1)]
    bundles = consolidate.bundle_sends(actions)
    assert len(bundles) == 2
    assert len(bundles[0]["actions"]) == configured
    assert len(bundles[1]["actions"]) == 1
