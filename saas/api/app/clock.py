"""The API's clock. The engine never reads "now" itself (CLAUDE.md: the clock is
passed in); this is the one place the API decides what today is.

RECOVA_TODAY (YYYY-MM-DD) pins it for demos and tests; any request may also
pass ?as_of= to look at the book as of another day.
"""

from __future__ import annotations

import os
from datetime import date


def today_for(as_of: date | None = None) -> date:
    if as_of is not None:
        return as_of
    pinned = os.environ.get("RECOVA_TODAY")
    return date.fromisoformat(pinned) if pinned else date.today()
