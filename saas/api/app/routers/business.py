"""The active business: its legal profile, its team, and demo data."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import audit
from app.bridge import REPO_ROOT  # noqa: F401  -- puts the engine/data packages on sys.path
from app.clock import today_for
from app.deps import TenantContext, tenant_context
from app.models import Buyer, Invoice, Membership, Payment

router = APIRouter(prefix="/business", tags=["business"])

PROFILE_FIELDS = ("legal_name", "udyam_registration", "enterprise_class", "gstin", "pan",
                  "address_line1", "address_line2", "city", "state", "pincode",
                  "contact_name", "contact_email", "contact_phone")


class ProfileIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    udyam_registration: str | None = Field(default=None, max_length=40)
    enterprise_class: str = Field(default="small", pattern="^(micro|small|medium)$")
    gstin: str | None = None
    pan: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


def profile_out(ctx: TenantContext) -> dict:
    t = ctx.tenant
    udyam = t.udyam_registration or ""
    return {"id": t.id, **{k: getattr(t, k) for k in PROFILE_FIELDS},
            "role": ctx.role,
            # MSMED Act s.15-16 protection needs a real Udyam registration;
            # the engine keeps Samadhaan drafts BLOCKED until one is on file.
            "udyam_on_file": bool(udyam) and not udyam.upper().startswith("UDYAM-XX"),
            "msmed_covered": t.enterprise_class in ("micro", "small")}


@router.get("")
def get_profile(ctx: TenantContext = Depends(tenant_context)) -> dict:
    return profile_out(ctx)


@router.put("")
def update_profile(body: ProfileIn, ctx: TenantContext = Depends(tenant_context)) -> dict:
    ctx.require("owner", "admin")
    for key, value in body.model_dump().items():
        setattr(ctx.tenant, key, value)
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email,
                 action="business_updated", reason="business profile updated")
    ctx.db.commit()
    return profile_out(ctx)


@router.get("/members")
def members(ctx: TenantContext = Depends(tenant_context)) -> list[dict]:
    rows = ctx.db.scalars(select(Membership).where(Membership.tenant_id == ctx.tenant.id)).all()
    return [{"id": m.id, "name": m.user.name, "email": m.user.email, "role": m.role}
            for m in rows]


@router.post("/demo-data", status_code=status.HTTP_201_CREATED)
def load_demo_data(ctx: TenantContext = Depends(tenant_context)) -> dict:
    """Fill an EMPTY business with the engine's synthetic world (seed 7):
    20 buyers with payment history and ~100 open invoices, dates shifted so the
    book reads as of today. Buyers and invoices only -- the simulator's hidden
    buyer personas are never copied, the same isolation the engine keeps."""
    ctx.require("owner", "admin")
    if ctx.db.scalars(ctx.scoped(Invoice)).first() or ctx.db.scalars(ctx.scoped(Buyer)).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Demo data only loads into an empty business")
    from data.generate import SIMULATION_START, generate

    world = generate(7)
    shift = today_for() - SIMULATION_START

    def moved(value: str | None) -> date | None:
        return date.fromisoformat(value) + shift if value else None

    ids: dict[str, str] = {}
    for b in world["buyers"]["buyers"]:
        buyer = Buyer(tenant_id=ctx.tenant.id, code=b["buyer_id"], name=b["name"],
                      profile=b["profile"], sector=b.get("sector"),
                      language_pref=b.get("language_pref", "english"),
                      contact_name=b.get("contact_name"), contact_email=b.get("contact_email"),
                      contact_phone=b.get("contact_phone"), city=b.get("city"),
                      state=b.get("state"), gstin=b.get("gstin"),
                      preferred_channel=b.get("preferred_channel", "email"),
                      opted_out=b.get("opted_out", False))
        ctx.db.add(buyer)
        ctx.db.flush()
        ids[b["buyer_id"]] = buyer.id

    count = 0
    for i in world["invoices"]["invoices"]:
        inv = Invoice(tenant_id=ctx.tenant.id, buyer_id=ids[i["buyer_id"]],
                      invoice_number=i["invoice_id"], description=i.get("description"),
                      po_number=i.get("po_number"), amount_paise=i["amount_paise"],
                      issue_date=moved(i["issue_date"]),
                      acceptance_date=moved(i["acceptance_date"]),
                      written_agreement=bool(i.get("written_agreement")),
                      agreed_days=i.get("agreed_days"), disputed=bool(i.get("disputed")),
                      dispute_note=i.get("dispute_note"))
        ctx.db.add(inv)
        ctx.db.flush()
        for p in i.get("partial_payments") or []:
            ctx.db.add(Payment(tenant_id=ctx.tenant.id, invoice_id=inv.id,
                               paid_on=moved(p["date"]), amount_paise=p["amount_paise"]))
        count += 1
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="demo_data_loaded",
                 reason=f"loaded demo data: {len(ids)} buyers, {count} invoices "
                        f"(synthetic, seed 7, dates shifted by {shift.days} days)")
    ctx.db.commit()
    return {"buyers": len(ids), "invoices": count}
