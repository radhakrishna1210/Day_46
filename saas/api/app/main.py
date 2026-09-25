"""Recova API -- the multi-tenant web service around the recovery engine.

    cd saas/api && uvicorn app.main:app --reload --port 8000

The web app (saas/web) proxies /api/* here, so the browser sees one origin and
the session cookie stays first-party.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import create_all
from app.routers import audit_log, auth, business, buyers, dashboard, decisions, invoices, meta


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_all()
    yield


app = FastAPI(title="Recova API", version="0.1.0", lifespan=lifespan,
              description="Receivables recovery for Indian MSMEs -- rules decide, AI writes, "
                          "every action audited.")

for module in (auth, business, buyers, invoices, dashboard, decisions, audit_log, meta):
    app.include_router(module.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
