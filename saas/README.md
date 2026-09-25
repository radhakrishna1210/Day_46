# Recova — the multi-tenant web app

A SaaS product built on this repo's recovery engine (`engine/`). Each business
(an MSME supplier) signs up, gets its own private workspace, adds the buyers
who owe it money and their invoices, and every day sees **what the rules say
to do about each overdue invoice, and why** — with a drafted message, the
MSMED Act position worked out to the rupee, and every action on an audit trail.

"Recova" is a working name.

```
saas/web  Next.js 16 + React 19 + Tailwind v4 + motion + recharts   (the UI)
   │  /api/*  (rewrite, same origin → first-party session cookie)
saas/api  FastAPI + SQLAlchemy                                      (the service)
   │  imports, unchanged
engine/   watchdog · score · law · brain · writer                   (the rules)
```

## Run it locally

Two terminals, from the repo root. Python 3.11+ and Node 20+.

```bash
# 1. API  (http://127.0.0.1:8000, docs at /docs)
pip install -r requirements.txt -r saas/api/requirements.txt
cd saas/api
RECOVA_SECRET_KEY=change-me python -m uvicorn app.main:app --port 8000

# 2. Web  (http://localhost:3000)
cd saas/web
npm install
npm run dev          # or: npm run build && npm start
```

Open http://localhost:3000, create an account, and on the overview choose
**Load a demo book** (20 synthetic buyers, ~100 open invoices, dated to today)
— or import a CSV, or add a buyer by hand.

| Setting | Where | Default |
|---|---|---|
| `DATABASE_URL` | API env | `sqlite:///saas/api/recova.db` — any SQLAlchemy URL; Postgres in production |
| `RECOVA_SECRET_KEY` | API env | random per start (sessions end on restart) — **set it** outside local dev |
| `RECOVA_TODAY` | API env | real date — pin a day for demos and tests |
| `RECOVA_API_URL` | web env | `http://127.0.0.1:8000` |
| `LLM_MODE` | repo `.env` | `mock` — canned, deterministic drafts; `live` uses Gemini via `engine/llm.py` |

Tests: `cd saas/api && python -m pytest tests` (20, including cross-tenant
isolation). The engine's own suite still runs from the repo root.

## What is built

- **Accounts & tenancy.** Email + password sign-up creates a business and makes
  you its owner. Users can belong to several businesses and switch between
  them. Every tenant query goes through one scoped path (`app/deps.py`); another
  business's ids read as *not found*, and tests try to break that.
- **Screens.** Landing page · sign-in / sign-up · Overview (receivable, overdue,
  statutory interest, aging chart, 12-week collections, who owes most, coming
  due, early warnings) · Today's decisions (per invoice: the decision, its
  reason, the escalation ladder against the legal ceiling, the drafted message,
  the legal position, the buyer's score arithmetic) · Invoices + detail
  (timeline, payments, promises, disputes) · Buyers + detail · CSV import ·
  Audit trail · Settings (business profile incl. Udyam status, team, legal
  figures in use).
- **Motion.** Page transitions, staggered reveals, counting KPIs, animated
  ladders and score rings, a gliding nav indicator, slide-over drawers, toasts —
  all switched off by the OS "reduce motion" setting. Light and dark themes.
- **Rules stay rules.** The API never overrides the engine. "Mark as sent"
  re-decides first and refuses if today's decision is not a message. Promises
  pause chasing; a dispute hands the invoice to a person; opt-out stops
  everything. Legal numbers on every screen come from `config/legal.yaml`
  (`GET /meta/legal`) — the UI never types one.

## Not connected yet — on purpose

- **Sending.** Email, WhatsApp and payment links are not wired in. Recova drafts
  the message; the owner sends it themselves and marks it sent (with the
  channel), which the engine needs as history to pace the next one.
- **Google sign-in, email OTP, team invites.** They slot into the same session
  issue path (`app/security.py`).
- **Row-level security in Postgres**, as a second barrier behind the app-level
  scoping, once the database is Postgres.
- **Background daily runs / notifications.** Decisions are computed on demand
  when the page loads.
