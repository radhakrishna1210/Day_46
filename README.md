<!-- ┌─────────────────────────────────────────────────────────────────────────┐ -->
<!-- │  PASTE THE LIVE DASHBOARD URL BELOW once GitHub Pages is enabled.         │ -->
<!-- │  Settings ▸ Pages ▸ Source: "Deploy from a branch" ▸ Branch: main /docs   │ -->
<!-- │  Expected URL:  https://radhakrishna1210.github.io/Day_46/                │ -->
<!-- └─────────────────────────────────────────────────────────────────────────┘ -->
**▶ Live dashboard:** `https://radhakrishna1210.github.io/Day_46/`  _(placeholder — confirm live after enabling Pages; steps at the bottom of this file)_

The dashboard is one self-contained HTML file with its data embedded, also
committed at `report/out/dashboard.html` — open it directly, no server needed.

---

# Revenue Recovery Agent

**Razorpay AI Buildathon 2026 — Track 03: AI Revenue Recovery**

An AI agent for Indian MSMEs that **detects revenue at risk** (invoices past
their *statutory* due date under the MSMED Act, not the date the contract
claimed), **determines the right intervention** (score the buyer from real
payment history, compute the exact legal leverage — compounding penalty
interest and the buyer's own deferred-tax cost — and pick one escalation step:
wait, nudge, send the statutory facts, offer a payment plan, hand to a human,
or stop), and **executes a bounded recovery workflow** that drafts the actual
message (English or Hinglish), tracks every promise, consolidates a buyer's
invoices into one envelope, and halts before it becomes spam. Every
money-related decision is deterministic code with a sourced reason, written to
an append-only audit trail. It is proven against a fixed-reminder baseline on
the same seeded data across six random worlds.

---

## Track 03 requirements → the artifact that satisfies each

> The track asks for: *"measured money recovered across a batch, with compliant
> escalation, stopping rules, and an audit trail."*

| Requirement | Artifact | Where |
|---|---|---|
| **Measured money recovered across a batch** | `report/out/results.json` — baseline vs agent vs agent+EV vs agent+EV+learned, over 6 seeds × 120 simulated days, with a money-conservation invariant and a matched-set fair comparison | `sim/run_sim.py --compare`; rendered in `report/out/report.html` and `report/out/dashboard.html` |
| **Compliant escalation** | a 4-rung ladder capped by `engine/law.py`'s statutory ceiling, enforced **twice** — `min(chosen, available_rung)` in `engine/brain.py` and an independent `RungNotAvailable` in `engine/rungs.py`. No message can state a legal fact the invoice does not yet support. | `engine/brain.py` steps 7–8, `engine/rungs.py`, `ARCHITECTURE.md` §5 |
| **Stopping rules** | hard limits enforced in code, evaluated *before* any expected-value logic: opt-out, dispute → immediate human handoff, max 5 contacts/invoice, per-rung message caps, quiet hours, no weekend sends, never a threat | `engine/brain.py::decide()` steps 1–11, `tests/test_brain.py` |
| **An audit trail** | `audit/audit_log.jsonl` — append-only, one JSON object per action, timestamped on the simulation clock, with the plain-English reason and `source: rule|llm` for every decision, draft, delivery, and parsed reply | `engine/audit.py` |

Full architecture (the 13-step decision flow, the double-enforced ceiling, the
rules-first / narrow-AI boundary, and the exact seam where a payments API would
plug in): **[ARCHITECTURE.md](ARCHITECTURE.md)**. Module-by-module walkthrough:
**[PROJECT_WALKTHROUGH.md](PROJECT_WALKTHROUGH.md)**.

---

## Headline numbers

Seed 7 (the primary benchmark seed), 120-day window. Every figure below is read
directly from `report/out/results.json`.

| Metric | Baseline | Agent | Agent + EV | Agent + EV + learned |
|---|---|---|---|---|
| Recovered | ₹88,38,375 | **₹1,44,80,534** | ₹1,48,33,614 | ₹1,16,76,702 |
| Invoices fully paid | 28 | 42 | 44 | 33 |
| Messages sent (envelopes) | 259 | 63 | 59 | 53 |
| Avg days to pay (matched set — 21 invoices baseline + agent both recovered) | 99.4 | **95.4** | — | — |
| Escalated to a human | 0 | 47 (18 disputed, 29 rung-4) | 46 | 43 |
| Not recovered (full exceptions list, each with a reason) | 72 | 58 | 56 | 67 |

- **The agent recovered ₹56,42,158 more than the baseline** while sending **196
  fewer messages** — and wins on rupees recovered in **6 of 6** seeds
  (7, 42, 13, 99, 2024, 555) and on matched-set days-to-pay in **6 of 6**.
- **The expected-value negotiation layer** (`brain.ev_mode`, off by default)
  adds a further **₹3,53,079** on seed 7 and wins on **5 of 6** seeds — seed
  2024 loses −₹51,765, reported alongside the five wins.
- **The learned layer** (`learning.enabled`, off by default) — a contextual
  bandit fit on simulated data — was wired into a fourth ablation arm and
  **loses** to the hand-typed EV grid on rupees recovered in **0 of 6** seeds:
  seed 7 −₹31,56,911, mean **−₹22,53,175** across all six (range −₹31,56,911 to
  −₹5,16,048). This is a disclosed negative result. It is built, fitted,
  root-caused to one specific mechanism (`docs/learning_findings.md`), and
  reported exactly as it came out — not smoothed into a partial win. **The
  arm ships off.**

**Per-rung effectiveness (agent, seed 7):** rung 1 (soft nudge) 33.3% · rung 2
(firm) 50.0% · rung 3 (legal facts) 17.5%. Rung 3 recovers a smaller share than
rung 2 only because the invoices that reach it are the ones that already failed
rungs 1–2 — the baseline's three identical reminders decay the same way (16.0%
→ 10.7% → 4.0%), which is the control that isolates that selection effect.

**1032 tests passing.** `docs/edge_cases.md` triages 147 edge cases: 66 with a
named passing test, 44 correct-in-code, 37 explicitly out of scope with the
specific data or integration each would need named.

---

## Quickstart

Runs end to end on a fresh clone with no API key — `LLM_MODE=mock` (the default)
gives deterministic canned model responses.

```bash
pip install -r requirements.txt
cp .env.example .env

python data/generate.py --seed 7                                                  # buyers.json + invoices.json (gitignored)
python sim/run_sim.py --compare --seed 7 --extra-seeds 42,13,99,2024,555 --days 120  # -> report/out/results.json  (~3.5 min)
python report/build_report.py                                                     # -> report/out/report.html
python scripts/build_dashboard.py --seed 7                                        # -> report/out/dashboard.html  (single self-contained file)
pytest -q                                                                         # 1032 passed
```

The built `report/out/results.json`, `report.html`, `dashboard.html` and
`dashboard.json` are **committed to this repo** so a reviewer can see the
measured result without waiting for the simulation — the commands above
regenerate them byte-for-byte (deterministic; only the `generated` timestamp
changes).

Separately, `python main.py --seed 42` runs one real-clock pass of the live
agent pipeline (watchdog → score → law → brain → writer → channels → promises)
with a full audit trail; `--send-email` sends a real message to
`TEST_INBOX_EMAIL` only. `main.py` does not run the comparison or build the
report.

`LLM_MODE=live` calls the real Gemini API with `GEMINI_API_KEY` from `.env`.

---

## What this does **not** do yet

Stated up front, because a track judged on honest measurement should be able to
trust the doc.

**No Razorpay integration, and no live network call to any Razorpay API.**
`engine/channels.py::send()` is the single seam where Razorpay Payment Links /
Invoices would connect (see `ARCHITECTURE.md` §7); it is not implemented. The
invoice and payment-history feed is **synthetic** — `data/generate.py` from a
seed — not a real transaction ledger. The buyer inflow signals behind the
ability axis are synthetic too, correlated with the simulator's hidden persona;
no real cash-flow feed exists.

**The learned bandit underperforms.** Fit entirely on simulated exploration
data, it loses the four-arm ablation 0/6 (see Headline numbers). It ships off;
a fresh clone reproduces the pre-learning agent exactly.

**Channels are partly stubbed.** Email is real (test inbox only). WhatsApp and
SMS log `would send` — the WhatsApp Business API needs business verification.

**One LLM model, not a cheap/strong split.** `parse_reply`, `draft_message` and
`judgment_call` all run on one Flash-tier Gemini model — this key's free tier
has zero Pro-tier quota and billing isn't available for it. A cost tradeoff,
disclosed.

**The live path has no persisted promise store.** `main.py` starts each run
with `promises = []`; promise memory only spans a run inside the simulator. A
real deployment would persist promises across pipeline runs.

**Simulator simplifications** (all would make the numbers *more* conservative,
not less): a persona's reaction lands the same simulated day the message is
sent (real buyers take longer, so "days to pay" is optimistic); a guardrail
fallback message is reacted to identically to a full LLM draft; every partial
payment is tagged as an ambiguous reply. No ablation isolates "score-aware
timing" from "the legal argument" as two separately toggleable things.

**Legal figures are a manually maintained snapshot** (`config/legal.yaml`, RBI
Bank Rate re-verified 2026-09-03, next MPC 2026-10-05/07), not a live feed.

**Negotiation layer, remaining gaps:** message *content* does not yet differ by
the chosen action (`payment_plan` drafts through the same skeleton as a plain
send); `counter_settle` is implemented and tested but never wins a live EV
ranking under the shipped grid; there is no reactive "buyer proposed terms
mid-conversation, evaluate accepting" path; online learning runs only as a
standalone demo mode, not inside `--compare`.

**Roadmap** (`docs/winning_layer.md`): network-level buyer score across many
vendors, payment-propensity prediction on real data, voice/Hinglish TTS,
Tally/Zoho import, TReDS invoice-discounting suggestions, a dispute-resolution
assistant. Each needs data or rails a standalone tool cannot supply — which is
the case for the platform integration, not a gap in the intelligence layer.

---

## Enabling the live dashboard (GitHub Pages)

`docs/index.html` is a copy of `report/out/dashboard.html`; `docs/.nojekyll`
tells Pages to serve the folder as-is. One-time setup in the GitHub UI:

1. Repo **Settings** → **Pages**.
2. **Build and deployment** → **Source**: *Deploy from a branch*.
3. **Branch**: `main`, folder `/docs`. **Save**.
4. Wait ~1 minute; the page publishes at `https://radhakrishna1210.github.io/Day_46/`.
5. Paste that URL into the **▶ Live dashboard** line at the top of this file and commit.

To refresh the dashboard later: `python scripts/build_dashboard.py --seed 7 && cp report/out/dashboard.html docs/index.html`, then commit.

---

## Legal disclaimer

The legal calculations here are **simplified for a demonstration, current as of
Aug 2026 (RBI Bank Rate re-verified 2026-09-03), and are not legal advice.**
All figures live in `config/legal.yaml`, each dated and sourced, and should be
verified against the current RBI Bank Rate and the prevailing text of the MSMED
Act 2006 and the Income-tax Act 2025 before being relied on.
