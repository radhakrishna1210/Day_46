"""The team inside one business: members, roles, and emailed invitations.

Rules (all enforced here, on the server):
  * owners and admins manage the team; members and viewers only see it;
  * there is exactly one owner -- nobody changes or removes the owner, and the
    owner hands over only by an explicit transfer (they become an admin);
  * an invite can grant admin, member or viewer, never owner;
  * nobody changes their own role (leaving is its own action);
  * an invitation is accepted only by an account with the invited email.
Every change is written to the business's audit trail.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, mailer, settings
from app.db import get_db
from app.deps import TenantContext, current_user, tenant_context
from app.models import MANAGERS, Invite, Membership, User
from app.security import SESSION_COOKIE, issue_session, read_session

router = APIRouter(tags=["team"])

INVITE_DAYS = 7
InviteRole = Literal["admin", "member", "viewer"]


class InviteIn(BaseModel):
    email: EmailStr
    role: InviteRole = "member"


class RoleIn(BaseModel):
    role: InviteRole


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    """SQLite hands datetimes back naive; they were written in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def invite_status(invite: Invite) -> str:
    if invite.accepted_at:
        return "accepted"
    if invite.revoked_at:
        return "revoked"
    if _aware(invite.expires_at) < _now():
        return "expired"
    return "pending"


def find_invite(db: Session, token: str) -> Invite | None:
    return db.scalar(select(Invite).where(Invite.token_hash == _hash(token)))


def _send_invite(db: Session, invite: Invite, inviter_name: str) -> None:
    """Give the invite a fresh token + expiry and email the link. Only the hash
    is stored, so a resend is the only way to get a working link again."""
    token = secrets.token_urlsafe(32)
    invite.token_hash = _hash(token)
    invite.expires_at = _now() + timedelta(days=INVITE_DAYS)
    link = f"{settings.public_url()}/invite/{token}"
    subject, text, html_body = mailer.invite_email(invite.tenant.legal_name, inviter_name,
                                                   invite.role, link, INVITE_DAYS)
    mailer.queue(db, to=invite.email, kind="invite", subject=subject, text=text,
                 html_body=html_body, tenant_id=invite.tenant_id)


def accept_invite(db: Session, invite: Invite, user: User) -> Membership:
    """Join the invite's business. The caller has checked the email matches;
    following the emailed link also proves they read that inbox."""
    if invite_status(invite) != "pending":
        raise HTTPException(status.HTTP_410_GONE, f"This invitation is {invite_status(invite)}")
    if user.email.lower() != invite.email.lower():
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"This invitation is for {invite.email}. Sign in with that email.")
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id,
                                                    Membership.tenant_id == invite.tenant_id))
    if membership is None:
        membership = Membership(user_id=user.id, tenant_id=invite.tenant_id, role=invite.role)
        db.add(membership)
    invite.accepted_at = _now()
    user.email_verified = True
    audit.record(db, tenant_id=invite.tenant_id, actor=user.email, action="member_joined",
                 reason=f"{user.name} accepted {invite.invited_by}'s invitation as {invite.role}")
    return membership


def _member(ctx: TenantContext, membership_id: str) -> Membership:
    m = ctx.db.get(Membership, membership_id)
    if m is None or m.tenant_id != ctx.tenant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return m


def _manageable(ctx: TenantContext, m: Membership) -> None:
    ctx.require(*MANAGERS)
    if m.role == "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The owner's access can't be changed")
    if m.user_id == ctx.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change your own role")


# --------------------------------------------------------------------------
# the team, from inside the business
# --------------------------------------------------------------------------

@router.get("/team")
def team(ctx: TenantContext = Depends(tenant_context)) -> dict:
    members = ctx.db.scalars(ctx.scoped(Membership)).all()
    order = {"owner": 0, "admin": 1, "member": 2, "viewer": 3}
    invites = [] if ctx.role not in MANAGERS else [
        {"id": i.id, "email": i.email, "role": i.role, "invited_by": i.invited_by,
         "created_at": i.created_at.isoformat(), "expires_at": _aware(i.expires_at).isoformat(),
         "status": invite_status(i)}
        for i in ctx.db.scalars(ctx.scoped(Invite).where(
            Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))
            .order_by(Invite.created_at.desc())).all()]
    return {
        "my_role": ctx.role,
        "can_manage": ctx.role in MANAGERS,
        "members": sorted(({"id": m.id, "user_id": m.user_id, "name": m.user.name,
                            "email": m.user.email, "role": m.role,
                            "is_me": m.user_id == ctx.user.id} for m in members),
                          key=lambda r: (order.get(r["role"], 9), r["name"].lower())),
        "invites": invites,
    }


@router.post("/team/invites", status_code=status.HTTP_201_CREATED)
def invite(body: InviteIn, ctx: TenantContext = Depends(tenant_context)) -> dict:
    ctx.require(*MANAGERS)
    email = body.email.lower()
    already = ctx.db.scalar(select(Membership).join(User).where(
        Membership.tenant_id == ctx.tenant.id, func.lower(User.email) == email))
    if already:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{email} is already on the team")
    # One live invite per email: inviting again re-sends it with the new role.
    row = ctx.db.scalar(ctx.scoped(Invite).where(
        Invite.email == email, Invite.accepted_at.is_(None), Invite.revoked_at.is_(None)))
    if row is None:
        row = Invite(tenant_id=ctx.tenant.id, email=email, role=body.role,
                     invited_by=ctx.user.email, token_hash="", expires_at=_now())
        row.tenant = ctx.tenant
        ctx.db.add(row)
    row.role, row.invited_by = body.role, ctx.user.email
    _send_invite(ctx.db, row, ctx.user.name)
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="member_invited",
                 reason=f"invited {email} as {body.role}")
    ctx.db.commit()
    mailer.deliver_pending(ctx.db)
    return {"id": row.id, "email": email, "role": row.role, "status": invite_status(row)}


@router.post("/team/invites/{invite_id}/resend")
def resend(invite_id: str, ctx: TenantContext = Depends(tenant_context)) -> dict:
    ctx.require(*MANAGERS)
    row = ctx.get(Invite, invite_id)
    if row.accepted_at or row.revoked_at:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This invitation is {invite_status(row)}")
    _send_invite(ctx.db, row, ctx.user.name)
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="invite_resent",
                 reason=f"re-sent the invitation to {row.email}")
    ctx.db.commit()
    mailer.deliver_pending(ctx.db)
    return {"ok": True}


@router.delete("/team/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(invite_id: str, ctx: TenantContext = Depends(tenant_context)) -> None:
    ctx.require(*MANAGERS)
    row = ctx.get(Invite, invite_id)
    if row.accepted_at is None and row.revoked_at is None:
        row.revoked_at = _now()
        audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email,
                     action="invite_revoked", reason=f"cancelled the invitation to {row.email}")
        ctx.db.commit()


@router.put("/team/members/{membership_id}")
def change_role(membership_id: str, body: RoleIn,
                ctx: TenantContext = Depends(tenant_context)) -> dict:
    m = _member(ctx, membership_id)
    _manageable(ctx, m)
    if m.role != body.role:
        audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="role_changed",
                     reason=f"{m.user.email}: {m.role} -> {body.role}")
        m.role = body.role
        ctx.db.commit()
    return {"id": m.id, "role": m.role}


@router.delete("/team/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(membership_id: str, ctx: TenantContext = Depends(tenant_context)) -> None:
    m = _member(ctx, membership_id)
    _manageable(ctx, m)
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="member_removed",
                 reason=f"removed {m.user.email} ({m.role}) from the team")
    ctx.db.delete(m)
    ctx.db.commit()


@router.post("/team/members/{membership_id}/make-owner")
def transfer_ownership(membership_id: str, ctx: TenantContext = Depends(tenant_context)) -> dict:
    ctx.require("owner")
    m = _member(ctx, membership_id)
    if m.user_id == ctx.user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are already the owner")
    me = _member_of(ctx.db, ctx.user.id, ctx.tenant.id)
    me.role, m.role = "admin", "owner"
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email,
                 action="ownership_transferred",
                 reason=f"{ctx.user.email} made {m.user.email} the owner and is now an admin")
    ctx.db.commit()
    return {"ok": True}


def _member_of(db: Session, user_id: str, tenant_id: str) -> Membership:
    return db.scalar(select(Membership).where(Membership.user_id == user_id,
                                              Membership.tenant_id == tenant_id))


@router.post("/team/leave")
def leave(response: Response, db: Session = Depends(get_db), user: User = Depends(current_user),
          session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    """Leave the active business. Any role but the owner, viewers included --
    so this reads the session itself instead of the write-guarded context."""
    tenant_id = (read_session(session) or {}).get("tid") if session else None
    m = _member_of(db, user.id, tenant_id) if tenant_id else None
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active business to leave")
    if m.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The owner can't leave. Make someone else the owner first.")
    audit.record(db, tenant_id=tenant_id, actor=user.email, action="member_left",
                 reason=f"{user.name} ({m.role}) left the team")
    db.delete(m)
    db.commit()
    nxt = db.scalar(select(Membership.tenant_id).where(Membership.user_id == user.id))
    from app.routers.auth import _me, _set_cookie
    _set_cookie(response, issue_session(user.id, nxt))
    return _me(db, user, nxt)


# --------------------------------------------------------------------------
# the invitation link, from outside the business
# --------------------------------------------------------------------------

@router.get("/invites/{token}")
def lookup(token: str, db: Session = Depends(get_db)) -> dict:
    """What an invitation link is for -- shown before signing in, so it reveals
    only the business name, the inviter, the role and the invited address."""
    row = find_invite(db, token)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invitation link isn't valid")
    has_account = db.scalar(select(User.id).where(func.lower(User.email) == row.email)) is not None
    return {"business": row.tenant.legal_name, "invited_by": row.invited_by, "role": row.role,
            "email": row.email, "status": invite_status(row), "has_account": has_account}


@router.post("/invites/{token}/accept")
def accept(token: str, response: Response, db: Session = Depends(get_db),
           user: User = Depends(current_user)) -> dict:
    row = find_invite(db, token)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invitation link isn't valid")
    membership = accept_invite(db, row, user)
    db.commit()
    from app.routers.auth import _me, _set_cookie   # the session now points at the new business
    _set_cookie(response, issue_session(user.id, membership.tenant_id))
    return _me(db, user, membership.tenant_id)
