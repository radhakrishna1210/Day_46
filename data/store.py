"""Reading the generated world back off disk.

The dataset is generated, not committed, so every entry point has to cope with
it being absent and say something useful rather than throwing a traceback at a
judge who has just cloned the repo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "data" / "seed"
BUYERS_PATH = SEED_DIR / "buyers.json"
INVOICES_PATH = SEED_DIR / "invoices.json"

REGENERATE_HINT = "run: python data/generate.py --seed 42"


class DatasetMissing(FileNotFoundError):
    """Raised when the seed data has not been generated yet."""


def _read(path: Path, key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.exists():
        raise DatasetMissing(f"{path.name} not found -- {REGENERATE_HINT}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["meta"], payload[key]


def load_buyers() -> list[dict[str, Any]]:
    """Every buyer, in buyer_id order."""
    return _read(BUYERS_PATH, "buyers")[1]


def load_invoices() -> list[dict[str, Any]]:
    """Every invoice, history and current, oldest first."""
    return _read(INVOICES_PATH, "invoices")[1]


def load_meta() -> dict[str, Any]:
    """The invoice file header: seed, counts, simulation start date."""
    return _read(INVOICES_PATH, "invoices")[0]


def invoices_by_buyer(invoices: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group invoices by buyer, preserving order within each buyer."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for invoice in invoices:
        grouped.setdefault(invoice["buyer_id"], []).append(invoice)
    return grouped


def dataset_exists() -> bool:
    return BUYERS_PATH.exists() and INVOICES_PATH.exists()
