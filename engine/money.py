"""Money formatting and rounding, in one place.

Money is integer paise everywhere inside the system. It becomes rupees only at
the moment a human reads it, which is here.
"""

from __future__ import annotations

import math
import sys

RUPEE = "₹"


def enable_unicode_output() -> str:
    """Make stdout able to print the rupee sign, and return a symbol that works.

    A Windows console defaults to cp1252, which cannot encode U+20B9. Legal fact
    sentences carry the rupee sign because that is how they will appear in an
    email, so the console is switched to UTF-8 rather than the text downgraded.
    Where that is impossible, "Rs " is returned as the fallback.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        RUPEE.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return "Rs "
    return RUPEE


def round_paise(value: float) -> int:
    """Round to whole paise, half away from zero.

    Deliberately not the built-in round(), which rounds halves to even. On money
    that is surprising to anyone checking the arithmetic by hand, and every
    figure this engine produces is meant to be checkable by hand.
    """
    if value < 0:
        return -math.floor(-value + 0.5)
    return math.floor(value + 0.5)


def group_indian(digits: str) -> str:
    """Indian digit grouping: 12500000 -> 1,25,00,000 (last three, then pairs)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_inr(paise: int, symbol: str = RUPEE, decimals: bool = False) -> str:
    """Format paise as rupees.

    Args:
        paise: the amount, in integer paise.
        symbol: currency mark to prefix. Pass "Rs " where the console cannot
            encode the rupee sign.
        decimals: include the paise. Interest figures need them; invoice
            amounts, which are always whole rupees here, do not.

    Returns:
        e.g. 4269423 -> "Rs 42,694.23" with decimals, "Rs 42,694" without.
    """
    sign = "-" if paise < 0 else ""
    rupees, remainder = divmod(abs(paise), 100)
    text = group_indian(str(rupees))
    if decimals:
        text = f"{text}.{remainder:02d}"
    return f"{sign}{symbol}{text}"
