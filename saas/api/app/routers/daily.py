"""The morning digest, from inside a business: preview it, or email it to
yourself now. The daily run itself is app/digest.py."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status

from app import digest, mailer
from app.clock import today_for
from app.deps import TenantContext, tenant_context

router = APIRouter(prefix="/digest", tags=["digest"])


@router.get("")
def preview(ctx: TenantContext = Depends(tenant_context), as_of: date | None = None) -> dict:
    today = today_for(as_of)
    s = digest.summarize(ctx.db, ctx.tenant, today)
    return {"summary": s, "would_send": digest.worth_sending(s),
            "recipients": [u.email for u in digest.recipients(ctx.db, ctx.tenant)],
            "last_daily_run": ctx.tenant.last_daily_run.isoformat() if ctx.tenant.last_daily_run else None,
            "digest_hour": digest.digest_hour(), "me_opted_out": ctx.user.digest_opt_out}


@router.post("/send-me")
def send_me(ctx: TenantContext = Depends(tenant_context)) -> dict:
    """Today's digest to the caller only -- to see what the team gets."""
    if not ctx.user.email_verified:
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirm your email first")
    digest.run_for_tenant(ctx.db, ctx.tenant, today_for(), only=ctx.user)
    ctx.db.commit()
    mailer.deliver_pending(ctx.db)
    return {"ok": True, "message": f"Today's digest is on its way to {ctx.user.email}."}
