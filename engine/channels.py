"""Post office -- the one way out of the system.

Email is really sent, and only ever to TEST_INBOX_EMAIL from .env. WhatsApp and
SMS are stubs that log "would send": the WhatsApp Business API needs business
verification, which is a documented scope call, not an oversight.

No real person is ever contacted.

Day 7.
"""

from __future__ import annotations

from typing import Any


def send(channel: str, to: str, message: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send (email) or log a would-send (whatsapp, sms).

    Returns a delivery record for the audit trail.
    """
    raise NotImplementedError("step 7: post office")
