"""Inbound webhooks. No session: each one proves itself with a signature."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import payments
from app.db import get_db

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/razorpay")
async def razorpay(request: Request, db: Session = Depends(get_db)) -> dict:
    """Razorpay Dashboard -> Webhooks: <public URL>/api/webhooks/razorpay, event
    payment_link.paid, secret = RAZORPAY_WEBHOOK_SECRET. The HMAC is checked on
    the exact raw bytes before anything is parsed or trusted."""
    raw = await request.body()
    if not payments.verify_signature(raw, request.headers.get("X-Razorpay-Signature")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad signature")
    result = payments.handle_webhook(db, json.loads(raw))
    db.commit()
    return {"ok": True, "result": result}
