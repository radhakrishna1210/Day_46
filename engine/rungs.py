"""Rung fact-skeletons -- the contract between the law engine and the writer.

A skeleton says: at this rung, these are the numbers you may quote, these are
the sentences you may state, and here is what you must not do. The message
writer (Day 6, an LLM) may rephrase and set the tone. It may not add a fact,
drop a citation, or move a digit.

No LLM here, and no creativity. Everything is assembled from config:
the ladder in config/rules.yaml decides which fact keys a rung may use, and
config/legal.yaml holds the sentences themselves. The numbers all come from
engine.law, which stays the single source of truth for money.

Two rules the skeleton enforces rather than merely documents:

  * rung 1 carries no legal facts AND no legal numbers. A courtesy nudge that
    quietly hands the writer an interest figure is not a courtesy nudge.
  * a rung above the legal ceiling is refused outright. Asking for the tax
    argument on an invoice that is not yet due is a bug, not a style choice.
"""

from __future__ import annotations

from typing import Any

from engine.config import legal, rules

#: Rungs at which we speak to the buyer at all. 0 is silence by definition and
#: 4 is a stop: the facts assembled there are for the draft and the human.
BUYER_FACING_RUNGS = (1, 2, 3)

#: Numbers a rung may see. Rung 1 gets only what it needs to ask politely --
#: withholding the rest is what keeps a nudge a nudge.
NEUTRAL_NUMBER_KEYS = (
    "invoice_id", "buyer_id", "description", "po_number",
    "outstanding_paise", "issue_date", "agreed_due_date",
)
LEGAL_NUMBER_KEYS = (
    "statutory_due_date", "interest_from", "days_overdue", "principal_paise",
    "interest_paise", "total_payable_paise", "interest_per_day_paise",
    "cost_of_waiting_paise", "waiting_horizon_days", "tax_exposure_paise",
    "interest_per_day_average_paise",
    "fy_end", "days_gained_by_law",
)

#: Handed to the writer verbatim. The first two are non-negotiable #3.
UNIVERSAL_RULES = (
    "State facts only. Never threaten, and never imply a consequence we have "
    "not stated as a fact with a citation.",
    "Every number must match the numbers block exactly. Do not round, "
    "recompute, estimate or infer any figure.",
    "Do not invent a fact, a section reference, a date or a deadline that is "
    "not in the facts list.",
    "Do not promise anything on our behalf, including discounts, waivers or "
    "extensions.",
)

RUNG_ONE_RULES = (
    "Do not mention interest, the statutory position, tax, any Act or section, "
    "or any legal consequence. This is a courtesy reminder only.",
)


class RungNotAvailable(ValueError):
    """Raised when a rung is requested that the law does not yet support."""


def all_rungs() -> list[dict[str, Any]]:
    """The ladder as data, in id order."""
    return sorted(rules()["ladder"]["rungs"], key=lambda entry: entry["id"])


def rung(rung_id: int) -> dict[str, Any]:
    """One rung's configuration."""
    for entry in all_rungs():
        if entry["id"] == rung_id:
            return entry
    raise ValueError(f"no such rung: {rung_id!r}")


def _numbers(rung_id: int, position: dict[str, Any], invoice: dict[str, Any]) -> dict[str, Any]:
    """The figures this rung is allowed to put in front of the writer."""
    available = {
        "invoice_id": invoice.get("invoice_id"),
        "buyer_id": invoice.get("buyer_id"),
        "description": invoice.get("description"),
        "po_number": invoice.get("po_number"),
        "issue_date": invoice.get("issue_date"),
        "agreed_due_date": invoice.get("agreed_due_date"),
        "outstanding_paise": position["principal_paise"],
        **{key: position[key] for key in LEGAL_NUMBER_KEYS if key in position},
    }
    permitted = NEUTRAL_NUMBER_KEYS if rung_id <= 1 else NEUTRAL_NUMBER_KEYS + LEGAL_NUMBER_KEYS
    return {key: available[key] for key in permitted if key in available}


def fact_skeleton(
    rung_id: int,
    position: dict[str, Any],
    invoice: dict[str, Any],
    buyer: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the factual skeleton for one invoice at one rung.

    Args:
        rung_id: the rung the brain chose.
        position: the output of engine.law.legal_position for this invoice.
        invoice: the invoice record.
        buyer: the buyer record, for tone hints only -- no numbers come from it.

    Returns:
        The rung's identity and limits, the permitted numbers, the permitted
        sentences, and the rules the writer must not break.

    Raises:
        RungNotAvailable: if the rung exceeds what the law currently supports.
        ValueError: if the rung id is unknown.
    """
    config = rung(rung_id)
    ceiling = position["available_rung"]
    if rung_id > 0 and rung_id > ceiling:
        raise RungNotAvailable(
            f"rung {rung_id} requested but the law supports at most {ceiling} "
            f"for {invoice.get('invoice_id')} as at {position['as_of']}"
        )

    held = bool(position.get("dispute_hold"))
    allowed_keys = [] if held else list(config["allowed_facts"])
    by_key = position.get("facts_by_key", {})
    facts = [by_key[key] for key in allowed_keys if key in by_key]

    forbidden = list(UNIVERSAL_RULES)
    if rung_id <= 1:
        forbidden.extend(RUNG_ONE_RULES)
    if held:
        forbidden.append(
            "This invoice is under dispute. Do not send anything; it belongs "
            "with a human."
        )

    sends = rung_id in BUYER_FACING_RUNGS and not held and config["max_messages"] > 0

    return {
        "rung": rung_id,
        "name": config["name"],
        "intent": config["intent"],
        "audience": "buyer" if rung_id in BUYER_FACING_RUNGS else "internal",
        "sends_to_buyer": sends,
        "max_messages": config["max_messages"],
        "min_days_between_contacts": config["min_days_between_contacts"],
        "available_rung": ceiling,
        "dispute_hold": held,
        "buyer": {
            "name": buyer.get("name"),
            "profile": buyer.get("profile"),
            "language_pref": buyer.get("language_pref"),
            "contact_name": buyer.get("contact_name"),
        },
        "numbers": _numbers(rung_id, position, invoice) if not held else {},
        "allowed_fact_keys": allowed_keys,
        "facts": facts,
        # Keyed as well as listed, so a message template can address one
        # sentence by name and interpolate it rather than restating it.
        "facts_by_key": {key: by_key[key] for key in allowed_keys if key in by_key},
        "forbidden": forbidden,
        "basis": {
            "as_of": position["as_of"],
            "config_version": legal()["version"],
            "config_as_of": legal()["as_of"],
        },
    }
