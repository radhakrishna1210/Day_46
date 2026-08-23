"""One place that reads config/, so no module ever embeds a tunable.

Both files are cached after the first read -- they are small, and the
simulation calls into them thousands of times. Tests that need to try a
different rule set call :func:`reload`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "rules.yaml"
LEGAL_PATH = ROOT / "config" / "legal.yaml"
SUPPLIER_PATH = ROOT / "config" / "supplier.yaml"


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    """Ladder timings, score weights, stopping limits."""
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def legal() -> dict[str, Any]:
    """MSMED Act and Income Tax figures. Simplified, as of Aug 2026, not legal advice."""
    return yaml.safe_load(LEGAL_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def supplier() -> dict[str, Any]:
    """Our own business identity. Needed to file anything in our own name."""
    return yaml.safe_load(SUPPLIER_PATH.read_text(encoding="utf-8"))


def reload() -> None:
    """Drop the cached config. For tests that edit the YAML on disk."""
    rules.cache_clear()
    legal.cache_clear()
    supplier.cache_clear()
