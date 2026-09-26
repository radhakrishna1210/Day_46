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

API settings live in `saas/api/.env` (copy `saas/api/.env.example`; git-ignored).

| Setting | Where | Default |
|---|---|---|
| `DATABASE_URL` | API env | `sqlite:///saas/api/recova.db` — any SQLAlchemy URL; Postgres in production |
| `RECOVA_SECRET_KEY` | API env | random per start (sessions end on restart) — **set it** outside local dev |
| `RECOVA_PUBLIC_URL` | API env | `http://localhost:3000` — builds the Google redirect URI and email links |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | API env | empty = no Google button |
| `SMTP_HOST` … `MAIL_FROM` | API env | empty host = emails (incl. sign-in codes) printed to the API log, not sent |
| `RECOVA_SUPER_ADMIN_EMAILS` | API env | empty = no platform admin. Comma-separated; counts only once that email is verified |
| `RECOVA_EMAIL_ALLOWLIST` | API env | empty = email anyone. Set in development: only these addresses / `@domains` get real email |
| `RECOVA_SCHEDULER` / `RECOVA_DIGEST_HOUR` | API env | `on` / `8` — the daily run + digest, once per business per day after that local hour |
| `RECOVA_TODAY` | API env | real date — pin a day for demos and tests |
| `RECOVA_API_URL` | web env | `http://127.0.0.1:8000` |
| `LLM_MODE` | repo `.env` | `mock` — canned, deterministic drafts; `live` uses Gemini via `engine/llm.py` |

Tests: `cd saas/api && python -m pytest tests` (77, including cross-tenant
isolation and every role rule). The engine's own suite still runs from the repo root.

## What is built

- **Sign-in three ways:** email + password, a one-time emailed code, or Google
  (OAuth code flow; ID token verified against Google's keys, state + nonce
  checked). Email verification and password reset by code. Codes: 6 digits,
  stored only as an HMAC, 10-minute expiry, single use, 5 guesses, rate-limited,
  and the request reply never reveals whether an account exists. Every email
  goes through an outbox table (queued → sent / retried / failed) over SMTP.
- **Accounts & tenancy.** Email + password sign-up creates a business and makes
  you its owner; a Google sign-up names its business on a welcome step. Users can belong to several businesses and switch between
  them. Every tenant query goes through one scoped path (`app/deps.py`); another
  business's ids read as *not found*, and tests try to break that.
- **Platform super admin.** Whoever runs the server, set only by
  `RECOVA_SUPER_ADMIN_EMAILS` (never by sign-up or invite) and only once that
  email is verified. They get a **Platform** page listing every business and
  user as counts (team, buyers, invoices, last activity) plus email-delivery
  health — no buyer, invoice or message from any business — and can
  **suspend / reactivate** a business or a user (recorded in the affected
  businesses' own audit trails) or run the daily run on demand. Everyone else
  gets a 404 there. A super admin needs no business of their own.
- **Team invites and four roles.** Settings → Team: invite by email as admin,
  member or viewer (7-day link, only the hash stored, one live invite per
  address; resend / cancel). **Owner** — everything, one per business, hands
  over by an explicit transfer. **Admin** — profile, deletes, the team (never
  the owner). **Member** — buyers, invoices, payments, promises, sends.
  **Viewer** — reads everything, changes nothing (refused on every non-read
  request in one place, `app/deps.py`). Nobody edits their own role; anyone but
  the owner can leave. `/invite/<token>` handles signed-in, wrong-account,
  existing-account, new-account and Google.
- **Daily run + morning digest.** Once a day per business after
  `RECOVA_DIGEST_HOUR`: today's decision queue (the same code as the Decisions
  page), one audit row, and an email to verified owners/admins/members who
  haven't switched it off — top actions, promises due, totals. Nothing to do →
  no email. Settings shows today's digest and can email it to you now.
- **Buyer replies.** Paste what a buyer said (English or Hinglish). With
  `LLM_MODE=live` the engine's own reader (`engine.promises.parse_reply`,
  Gemini) reads it; without a key a small keyword reader stands in, labelled
  as rules, using the engine's date resolver and sanity bounds. A person
  confirms or corrects before anything changes (a promise pauses reminders, a
  dispute goes to a person); both steps are audited.
- **Email allow-list.** `RECOVA_EMAIL_ALLOWLIST` (development): only listed
  addresses get real email, the rest are recorded as blocked — demo accounts
  carry made-up addresses.
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
- **Row-level security in Postgres**, as a second barrier behind the app-level
  scoping, once the database is Postgres — and Alembic migrations (today a
  startup step only ADDS missing columns).
- **Replies arriving by themselves.** Replies are pasted in; reading them from
  a WhatsApp or email inbox comes with those channels.
