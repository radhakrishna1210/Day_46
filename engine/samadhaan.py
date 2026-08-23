"""MSME Samadhaan complaint draft generator.

The last rung. When an invoice has gone far enough that messaging has stopped,
this assembles everything a human would need to make a reference under Section
18 of the MSMED Act 2006 -- the parties, the invoice facts, the statutory
position, the interest arithmetic shown step by step, and the relief sought --
and writes it to audit/drafts/ as markdown.

No LLM, no persuasion. It is a form, filled from recorded data.

Two things it will not do:

  * mark a draft READY TO FILE while anything required is missing. The Udyam
    registration in config/supplier.yaml ships as a placeholder, so every draft
    is BLOCKED until a real one is supplied. A draft that invented a
    registration number would be far worse than one that says it has none.
  * invent a figure. Every rupee comes from engine.law, and the interest table
    reproduces the inputs so the arithmetic can be checked by hand.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from engine.config import legal, rules, supplier
from engine.law import (
    _as_date,
    agreed_term_is_void,
    effective_annual_rate,
    financial_year_end,
    interest_start_date,
    legal_position,
    outstanding_paise,
    statutory_due_date,
    statutory_term_days,
)
from engine.money import format_inr

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRAFT_DIR = ROOT / "audit" / "drafts"

#: What a real filing needs attached. Rendered as a checklist for the human.
SUPPORTING_DOCUMENTS = (
    "Copy of the invoice",
    "Proof of delivery and acceptance (challan, gate entry or written acceptance)",
    "Udyam registration certificate of the Applicant",
    "Purchase order or written agreement, if any",
    "Ledger extract showing the outstanding balance",
    "Record of reminders and correspondence",
)


def _blockers_and_warnings(
    invoice: dict[str, Any],
    buyer: dict[str, Any],
    position: dict[str, Any] | None,
    today: date,
) -> tuple[list[str], list[str]]:
    """Check whether this could actually be filed. Blockers stop it; warnings do not."""
    config = supplier()
    profile = config["supplier"]
    blockers: list[str] = []
    warnings: list[str] = []

    udyam = str(profile.get("udyam_registration") or "")
    if not udyam:
        blockers.append("No Udyam registration number on file for the Applicant.")
    elif udyam.startswith(str(config["placeholder_udyam_prefix"])):
        blockers.append(
            f"The Udyam registration number on file ({udyam}) is the placeholder "
            f"shipped in config/supplier.yaml, not a real registration."
        )

    if not invoice.get("acceptance_date"):
        blockers.append(" ".join(legal()["reference_text"]["no_acceptance_date"].split()))

    if position is None:
        return blockers, warnings

    if position["dispute_hold"]:
        blockers.append(
            "The invoice is under dispute. A disputed claim belongs with a human "
            "before any reference is made."
        )

    minimum = int(rules()["law_gates"]["samadhaan_after_days"])
    if position["days_overdue"] < minimum:
        blockers.append(
            f"The invoice is {position['days_overdue']} days overdue; our own "
            f"threshold for a reference is {minimum} days."
        )

    if position["principal_paise"] <= 0:
        blockers.append("Nothing is outstanding on this invoice.")

    recorded = int(invoice.get("amount_paid_paise") or 0)
    itemised = sum(int(p["amount_paise"]) for p in invoice.get("partial_payments") or [])
    if recorded != itemised:
        blockers.append(
            f"Payment records do not reconcile: the invoice records "
            f"{format_inr(recorded)} received, the itemised payments total "
            f"{format_inr(itemised)}."
        )

    if not buyer.get("name") or not buyer.get("state"):
        blockers.append("The Respondent's name and State are required.")

    if not invoice.get("po_number") and not invoice.get("written_agreement"):
        warnings.append(
            "No purchase order and no written agreement on record. Acceptance "
            "will have to be evidenced some other way."
        )
    elif not invoice.get("po_number"):
        warnings.append("No purchase order reference on record.")

    if not buyer.get("gstin"):
        warnings.append("No GSTIN on record for the Respondent.")

    return blockers, warnings


def _interest_rows(invoice: dict[str, Any], position: dict[str, Any], today: date) -> list[str]:
    """The interest arithmetic as markdown table rows, with its inputs shown."""
    basis = position["basis"]
    payments = sorted(
        (p for p in invoice.get("partial_payments") or [] if _as_date(p["date"]) <= today),
        key=lambda p: _as_date(p["date"]),
    )
    rows = [
        f"| Principal outstanding | {format_inr(position['principal_paise'])} |",
        f"| Interest runs from | {position['interest_from']} "
        f"(the day after the statutory due date) |",
        f"| Valued as at | {position['as_of']} |",
        f"| RBI Bank Rate | {basis['bank_rate'] * 100:.2f}% per annum |",
        f"| Statutory multiplier | "
        f"{legal()['reference_text']['multiplier_label'].format(multiplier=basis['multiplier'])} |",
        f"| Rate applied | {basis['effective_annual_rate'] * 100:.2f}% per annum, "
        f"compounded with monthly rests |",
        f"| Monthly rest rate | {basis['monthly_rate']:.6f} |",
        f"| Complete monthly rests | {basis['complete_months']} |",
        f"| Remaining days | {basis['stub_days']} "
        f"(simple, on a {basis['day_basis']}-day basis) |",
    ]
    for payment in payments:
        rows.append(
            f"| Part payment received | {format_inr(int(payment['amount_paise']))} "
            f"on {payment['date']}, reducing the principal from that date |"
        )
    rows.append(
        f"| **Interest accrued** | **{format_inr(position['interest_paise'], decimals=True)}** |"
    )
    return rows


def build_draft(
    invoice: dict[str, Any],
    buyer: dict[str, Any],
    position: dict[str, Any] | None,
    today: date,
) -> dict[str, Any]:
    """Assemble the draft and its readiness verdict.

    Args:
        invoice: the invoice record.
        buyer: the buyer record.
        position: engine.law.legal_position output, or None when it could not
            be computed (a missing acceptance date, for instance).
        today: the simulation clock.

    Returns:
        The rendered markdown, the readiness flag, and the reasons behind it.
    """
    config = legal()
    profile = supplier()["supplier"]
    council = supplier()["facilitation_council"]
    wording = config["reference_text"]

    blockers, warnings = _blockers_and_warnings(invoice, buyer, position, today)
    ready = not blockers

    lines: list[str] = []
    add = lines.append

    add(f"# {wording['title']}")
    add("")
    add(f"> **{'READY TO FILE' if ready else 'BLOCKED — NOT READY TO FILE'}** · "
        f"drafted {today.isoformat()} · invoice {invoice.get('invoice_id')}")
    add("")
    add(f"{' '.join(wording['draft_warning'].split())}")
    add("")
    add("---")
    add("")

    # 1. Applicant
    add("## 1. Applicant (supplier)")
    add("")
    add(f"| Field | Value |")
    add(f"|---|---|")
    add(f"| Legal name | {profile['legal_name']} |")
    add(f"| Udyam registration | {profile['udyam_registration']} |")
    add(f"| Enterprise class | {profile['enterprise_class']} |")
    add(f"| Address | {profile['address_line1']}, {profile['address_line2']}, "
        f"{profile['city']}, {profile['state']} {profile['pincode']} |")
    add(f"| GSTIN | {profile['gstin']} |")
    add(f"| PAN | {profile['pan']} |")
    add(f"| Contact | {profile['contact_name']}, {profile['contact_email']}, "
        f"{profile['contact_phone']} |")
    add("")

    # 2. Respondent
    add("## 2. Respondent (buyer)")
    add("")
    add(f"| Field | Value |")
    add(f"|---|---|")
    add(f"| Name | {buyer.get('name') or 'not on record'} |")
    add(f"| Address | {buyer.get('city') or 'not on record'}, "
        f"{buyer.get('state') or 'not on record'} |")
    add(f"| GSTIN | {buyer.get('gstin') or 'not on record'} |")
    add(f"| Contact | {buyer.get('contact_name') or 'not on record'}, "
        f"{buyer.get('contact_email') or 'not on record'} |")
    add("")

    # 3. Invoice
    add("## 3. Invoice particulars")
    add("")
    add(f"| Field | Value |")
    add(f"|---|---|")
    add(f"| Invoice number | {invoice.get('invoice_id')} |")
    add(f"| Date of invoice | {invoice.get('issue_date') or 'not on record'} |")
    add(f"| Date of acceptance | {invoice.get('acceptance_date') or 'NOT ON RECORD'} |")
    add(f"| Goods supplied | {invoice.get('description') or 'not on record'} |")
    add(f"| Purchase order | {invoice.get('po_number') or 'none on record'} |")
    add(f"| Invoice amount | {format_inr(int(invoice['amount_paise']))} |")
    payments = invoice.get("partial_payments") or []
    if payments:
        for payment in payments:
            add(f"| Payment received | {format_inr(int(payment['amount_paise']))} "
                f"on {payment['date']} |")
    else:
        add(f"| Payments received | none |")
    if position:
        add(f"| Amount outstanding | **{format_inr(position['principal_paise'])}** |")
    add("")

    # 4. Statutory position
    add("## 4. Statutory position")
    add("")
    if position:
        term = statutory_term_days(invoice)
        add(f"| Field | Value |")
        add(f"|---|---|")
        add(f"| Written agreement | {'yes' if invoice.get('written_agreement') else 'no'} |")
        add(f"| Term stated in the agreement | "
            f"{invoice.get('agreed_days') if invoice.get('agreed_days') else 'none stated'} days |")
        add(f"| Statutory term applied | {term} days from acceptance |")
        if agreed_term_is_void(invoice):
            note = " ".join(wording["void_term_note"].format(
                agreed_days=invoice["agreed_days"],
                ceiling=config["max_agreement_days"],
            ).split())
            add(f"| Note | {note} |")
        add(f"| Statutory due date | **{position['statutory_due_date']}** |")
        add(f"| Days overdue as at {position['as_of']} | **{position['days_overdue']}** |")
    else:
        add("Cannot be established: the invoice has no acceptance date.")
    add("")

    # 5. Interest
    add(f"## 5. {wording['heading_interest']}")
    add("")
    if position:
        add(" ".join(wording["interest_preamble"].split()))
        add("")
        add("| Input | Value |")
        add("|---|---|")
        for row in _interest_rows(invoice, position, today):
            add(row)
    else:
        add("Not computable without an acceptance date.")
    add("")

    # 6. Total
    add("## 6. Total claimed")
    add("")
    if position:
        add("| Component | Amount |")
        add("|---|---|")
        add(f"| Principal | {format_inr(position['principal_paise'])} |")
        add(f"| Interest to {position['as_of']} | "
            f"{format_inr(position['interest_paise'], decimals=True)} |")
        add(f"| **Total** | **{format_inr(position['total_payable_paise'], decimals=True)}** |")
        add("")
        add(f"Interest continues to accrue at approximately "
            f"{format_inr(position['interest_per_day_paise'], decimals=True)} per day.")
    else:
        add("Not computable.")
    add("")

    # 7. Relief
    add("## 7. Relief sought")
    add("")
    if position:
        values = {
            "principal": format_inr(position["principal_paise"]),
            "interest": format_inr(position["interest_paise"], decimals=True),
            "total": format_inr(position["total_payable_paise"], decimals=True),
            "effective_rate_pct": f"{effective_annual_rate() * 100:.2f}",
            "bank_rate_pct": f"{float(config['rbi_bank_rate']) * 100:.2f}",
            "as_of": position["as_of"],
        }
        add(" ".join(wording["relief_sought"].format(**values).split()))
        add("")
        add(" ".join(wording["jurisdiction"].format(council_name=council["name"]).split()))
        add("")
        add(" ".join(wording["predeposit_note"].format(
            predeposit_pct=f"{float(config['samadhaan']['challenge_predeposit_share']) * 100:.0f}"
        ).split()))
    else:
        add("Not computable.")
    add("")

    # 8. Documents
    add("## 8. Supporting documents to attach")
    add("")
    for document in SUPPORTING_DOCUMENTS:
        add(f"- [ ] {document}")
    add("")

    # 9. Readiness
    add("## 9. Readiness")
    add("")
    add(f"**{'READY TO FILE' if ready else 'BLOCKED — NOT READY TO FILE'}**")
    add("")
    if blockers:
        add("Blocking issues:")
        add("")
        for reason in blockers:
            add(f"- {reason}")
        add("")
    if warnings:
        add("Warnings (do not block filing):")
        add("")
        for warning in warnings:
            add(f"- {warning}")
        add("")
    if not blockers and not warnings:
        add("No issues found.")
        add("")

    # 10. Declaration
    add("## 10. Declaration")
    add("")
    add(" ".join(wording["declaration"].split()))
    add("")
    add("Signature: ______________________    Date: ______________")
    add("")
    add(f"Name: {profile['contact_name']}, for and on behalf of {profile['legal_name']}")
    add("")
    add("---")
    add("")
    add(f"_Generated from recorded invoice data on {today.isoformat()}. "
        f"Bank Rate {float(config['rbi_bank_rate']) * 100:.2f}% as retrieved on "
        f"{config['retrieved_on']}; legal config version {config['version']}. "
        f"{' '.join(config['disclaimer'].split())}_")

    return {
        "invoice_id": invoice.get("invoice_id"),
        "ready": ready,
        "blockers": blockers,
        "warnings": warnings,
        "markdown": "\n".join(lines) + "\n",
    }


def write_draft(
    invoice: dict[str, Any],
    buyer: dict[str, Any],
    position: dict[str, Any] | None,
    today: date,
    out_dir: Path | None = None,
) -> Path:
    """Render the draft and write it to disk. Returns the path written."""
    draft = build_draft(invoice, buyer, position, today)
    directory = Path(out_dir) if out_dir else DEFAULT_DRAFT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"samadhaan-{invoice.get('invoice_id')}.md"
    path.write_text(draft["markdown"], encoding="utf-8", newline="\n")
    return path
