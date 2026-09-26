"""Tables. Every tenant-owned row carries tenant_id; nothing is shared across
tenants except users (one person can belong to several businesses).

Money is integer paise, dates are datetime.date -- the same conventions the
engine uses, so rows map onto engine records without conversion loss.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, false)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    """One business (an MSME supplier) using the platform."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    legal_name: Mapped[str] = mapped_column(String(200))
    udyam_registration: Mapped[str | None] = mapped_column(String(40))
    enterprise_class: Mapped[str] = mapped_column(String(10), default="small")  # micro|small|medium
    gstin: Mapped[str | None] = mapped_column(String(20))
    pan: Mapped[str | None] = mapped_column(String(12))
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    pincode: Mapped[str | None] = mapped_column(String(10))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    contact_email: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Set by a platform super admin; the whole workspace goes read-nothing.
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The last day the daily run finished for this business (one run per day).
    last_daily_run: Mapped[date | None] = mapped_column(Date)

    memberships: Mapped[list[Membership]] = relationship(back_populates="tenant",
                                                          cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    #: None for someone who only ever signs in with Google or an email code.
    password_hash: Mapped[str | None] = mapped_column(String(300))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Google's stable account id ("sub"), once linked.
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Set by a platform super admin; a suspended user cannot sign in or act.
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The morning digest email, per person (owners/admins/members get it by default).
    digest_opt_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    memberships: Mapped[list[Membership]] = relationship(back_populates="user",
                                                          cascade="all, delete-orphan")


#: owner   -- everything, one per business; hands over with a transfer.
#: admin   -- the business profile, deletes, and the team (never the owner).
#: member  -- the day-to-day work: buyers, invoices, payments, promises, sends.
#: viewer  -- reads everything, changes nothing (an accountant, a CA).
ROLES = ("owner", "admin", "member", "viewer")
MANAGERS = ("owner", "admin")
WRITERS = ("owner", "admin", "member")


class Membership(Base):
    """A user's role inside one tenant."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")

    user: Mapped[User] = relationship(back_populates="memberships")
    tenant: Mapped[Tenant] = relationship(back_populates="memberships")


class Buyer(Base):
    """A customer of the tenant -- someone who owes them money."""

    __tablename__ = "buyers"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))            # tenant-visible id, e.g. BUY-07
    name: Mapped[str] = mapped_column(String(200))
    profile: Mapped[str] = mapped_column(String(20), default="corporate")  # corporate|small_trader
    sector: Mapped[str | None] = mapped_column(String(60))
    language_pref: Mapped[str] = mapped_column(String(20), default="english")  # english|hinglish
    contact_name: Mapped[str | None] = mapped_column(String(120))
    contact_email: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    gstin: Mapped[str | None] = mapped_column(String(20))
    preferred_channel: Mapped[str] = mapped_column(String(20), default="email")
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "invoice_number"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    buyer_id: Mapped[str] = mapped_column(ForeignKey("buyers.id", ondelete="CASCADE"), index=True)
    invoice_number: Mapped[str] = mapped_column(String(60))
    description: Mapped[str | None] = mapped_column(String(300))
    po_number: Mapped[str | None] = mapped_column(String(60))
    amount_paise: Mapped[int] = mapped_column(Integer)
    issue_date: Mapped[date] = mapped_column(Date)
    acceptance_date: Mapped[date] = mapped_column(Date)
    written_agreement: Mapped[bool] = mapped_column(Boolean, default=False)
    agreed_days: Mapped[int | None] = mapped_column(Integer)
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    dispute_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    buyer: Mapped[Buyer] = relationship()
    payments: Mapped[list[Payment]] = relationship(cascade="all, delete-orphan",
                                                    order_by="Payment.paid_on")
    promises: Mapped[list[Promise]] = relationship(cascade="all, delete-orphan",
                                                    order_by="Promise.recorded_on")
    contacts: Mapped[list[ContactLog]] = relationship(cascade="all, delete-orphan",
                                                       order_by="ContactLog.contacted_on")
    replies: Mapped[list[BuyerReply]] = relationship(cascade="all, delete-orphan",
                                                     order_by="BuyerReply.created_at")
    payment_links: Mapped[list[PaymentLink]] = relationship(cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    paid_on: Mapped[date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(300))


class Promise(Base):
    """A buyer's commitment to pay, logged by the tenant."""

    __tablename__ = "promises"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    promised_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[str] = mapped_column(String(10), default="full")   # full|partial
    status: Mapped[str] = mapped_column(String(10), default="open")    # open|kept|broken
    recorded_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(300))

    invoice: Mapped[Invoice] = relationship(viewonly=True)


class ContactLog(Base):
    """A reminder the tenant approved at a given rung. With channels not yet
    connected, "approved" means the owner sent it themselves -- the engine
    still needs the history to pace the next one."""

    __tablename__ = "contact_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    contacted_on: Mapped[date] = mapped_column(Date)
    rung: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(20), default="manual")
    outcome: Mapped[str] = mapped_column(String(30), default="no_reply")


class EmailCode(Base):
    """A one-time 6-digit code sent by email. Only its keyed hash is stored."""

    __tablename__ = "email_codes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    email: Mapped[str] = mapped_column(String(200), index=True)
    purpose: Mapped[str] = mapped_column(String(10))          # login | verify | reset
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class OutboxEmail(Base):
    """Every email the platform sends, queued first and then delivered, so a
    failure is retried and visible rather than silently lost. Sent to the
    platform's own users only (owners and teammates) -- never to buyers."""

    __tablename__ = "outbox_emails"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    to_email: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30))               # login_code | verify_email | ...
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    #: Buyer reminders go out as "<Business> via Recova", replies to the business.
    from_name: Mapped[str | None] = mapped_column(String(200))
    reply_to: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="queued")   # queued|sent|failed|blocked
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentLink(Base):
    """A Razorpay Payment Link for (part of) an invoice. Recova records the
    money only when Razorpay says it was paid -- by signed webhook or by asking
    Razorpay directly -- never because a buyer clicked something."""

    __tablename__ = "payment_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[str] = mapped_column(String(40), unique=True)       # plink_...
    short_url: Mapped[str] = mapped_column(String(300))
    amount_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="created")      # created|paid|cancelled|expired
    provider_payment_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(200))


class BuyerReply(Base):
    """What a buyer said back, as a person pasted it, and what was done with it.
    `suggested_*` is the reader's guess (AI or rules); `intent` is what the
    person confirmed -- both kept, so the gap between them stays visible."""

    __tablename__ = "buyer_replies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    received_on: Mapped[date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    suggested_intent: Mapped[str | None] = mapped_column(String(10))
    suggested_by: Mapped[str | None] = mapped_column(String(10))       # ai | rules
    intent: Mapped[str] = mapped_column(String(10))
    promised_date: Mapped[date | None] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Invite(Base):
    """An emailed invitation to join a business. Only a hash of the link's
    token is stored; the link itself exists only in the email."""

    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[str] = mapped_column(String(200))          # inviter's email
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship()


class AuditEntry(Base):
    """Append-only. The API exposes no update or delete for this table."""

    __tablename__ = "audit_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    actor: Mapped[str] = mapped_column(String(200))        # a user's email, or "agent"
    action: Mapped[str] = mapped_column(String(60))
    invoice_number: Mapped[str | None] = mapped_column(String(60))
    buyer_name: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="rule")   # rule|llm|user
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
