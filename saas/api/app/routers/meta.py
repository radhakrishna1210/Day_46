"""Public, non-tenant facts the UI quotes. Legal figures come from
config/legal.yaml through the engine's own loader -- the web app never types a
statutory number itself (CLAUDE.md non-negotiable #3)."""

from __future__ import annotations

from fastapi import APIRouter

from app.bridge import REPO_ROOT  # noqa: F401  -- engine on sys.path
from engine import law
from engine.config import legal

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/legal")
def legal_figures() -> dict:
    cfg = legal()
    return {
        "no_agreement_days": cfg["no_agreement_days"],
        "max_agreement_days": cfg["max_agreement_days"],
        "bank_rate_multiplier": cfg["bank_rate_multiplier"],
        "effective_annual_rate_pct": round(law.effective_annual_rate() * 100, 2),
        "as_of": cfg["as_of"],
        "disclaimer": cfg["disclaimer"],
    }
