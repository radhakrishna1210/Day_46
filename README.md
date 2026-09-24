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

Seed 7 (the primary benchmark seed), 120-day window, **buyer reactions
delayed** — a reply lands 0–2 days and a payment 1–5 days after the message
(Phase R1; stated assumptions in `sim/personas.py`, identical for every arm).
Every figure below is read directly from `report/out/results.json`.

| Metric | Baseline | Agent | Agent + EV | Agent + EV + learned |
|---|---|---|---|---|
| Recovered | ₹88,46,422 | **₹1,46,64,306** | ₹1,48,08,810 | ₹1,54,56,949 |
| Invoices fully paid | 28 | 42 | 42 | 45 |
| Messages sent (envelopes) | 259 | 67 | 61 | 61 |
| Avg days to pay (matched set — 21 invoices baseline + agent both recovered) | 98.8 | **95.5** | — | — |
| Escalated to a human | 0 | 48 (18 disputed, 29 rung-4, 1 contact cap) | 51 | 47 |
| Not recovered (full exceptions list, each with a reason) | 72 | 58 | 58 | 55 |

- **The agent recovered ₹58,17,884 more than the baseline** while sending **192
  fewer messages** — and wins on rupees recovered in **6 of 6** seeds
  (7, 42, 13, 99, 2024, 555) and on matched-set days-to-pay in **6 of 6**.
- **The expected-value negotiation layer** (`brain.ev_mode`, off by default)
  matches or beats the plain agent on **4 of 6** seeds — **3 wins, 1 exact
  tie, 2 losses** (seed 13 −₹10,46,786, seed 2024 −₹2,36,370). It was 5 of 6
  before Phase E1 made EV's chosen tier the rung actually sent (see below).
- **The learned layer** (`learning.enabled`, off by default) — a contextual
  bandit fit on simulated data — matches or beats the hand-typed EV grid on
  **6 of 6** seeds — **4 wins, 2 exact ties** — mean **+₹3,80,496**, range
  ₹0 to +₹8,24,068. Until Phase E2 it **lost 0 of 6** (mean −₹22,53,175),
  root-caused to `wait`'s hand-typed 60% never having been measured; E2
  measured it, and in this simulator a chosen wait recovered nothing in 1,513
  episodes. **Read it with its caveats:** part of that turnaround is Phase E1
  lowering the hand-typed EV arm (the same untested-`wait` flaw, no longer
  hidden by a mislabelled send); the learned-vs-EV result moved from 4/6 to
  6/6 on the reaction-delay timing change alone, so it is sensitive to the
  fake world's assumptions; and six seeds is a small sample. Full write-up:
  `docs/learning_findings.md`. **Both arms ship off.**

**Same-day reactions, for comparison** (`--no-reaction-delays`, how every
figure was measured before Phase R1): agent vs baseline is still **6/6** on
rupees and on matched days (seed 7: +₹56,42,158); agent+EV vs agent **3/6**;
learned vs EV **4/6** (3 wins, 1 tie). With delays, days to pay rise about 1–3
days, as expected, but recovered rupees rose slightly in most arms. Traced on
seed 7: the agent cannot see a payment still in transit and sometimes messages
again, and the buyer pays the rest (+~₹4.8 lakh); a reply arriving a day later
dates its promise a day later, which re-rolls whether it is kept (−~₹3.0
lakh). An earlier version of R1 also re-rolled partial-payment *amounts* on
the landing day — a modelling bug, fixed before these numbers were produced.

**Per-rung effectiveness (agent, seed 7):** rung 1 (soft nudge) 33.3% · rung 2
(firm) 54.4% · rung 3 (legal facts) 10.8%. Rung 3 recovers a smaller share than
rung 2 only because the invoices that reach it are the ones that already failed
rungs 1–2 — the baseline's three identical reminders decay the same way (16.0%
→ 10.7% → 4.0%), which is the control that isolates that selection effect.

**1053 tests passing.** `docs/edge_cases.md` triages 147 edge cases: 66 with a
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
pytest -q                                                                         # 1053 passed
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

**The learned bandit is fit on a simulator with no unprompted payments.** Fit
entirely on simulated exploration data, it now matches or beats the hand-typed
EV grid on 6 of 6 seeds (4 wins, 2 ties; 4 of 6 with same-day reactions), but
its measured `wait` of ~0% is
true of this simulator by construction, not of real buyers (see Headline
numbers). It ships off; a fresh clone reproduces the pre-learning agent
exactly.

**Channels are partly stubbed.** Email is real (test inbox only). WhatsApp and
SMS log `would send` — the WhatsApp Business API needs business verification.

**One LLM model, not a cheap/strong split.** `parse_reply`, `draft_message` and
`judgment_call` all run on one Flash-tier Gemini model — this key's free tier
has zero Pro-tier quota and billing isn't available for it. A cost tradeoff,
disclosed.

**The live path has no persisted promise store.** `main.py` starts each run
with `promises = []`; promise memory only spans a run inside the simulator. A
real deployment would persist promises across pipeline runs.

**Simulator simplifications:** reply and payment delays are fixed, stated
ranges (0–2 and 1–5 days), not measured from real buyers, and modelling them
did not make recovery more conservative (see Headline numbers); the simulator has no
unprompted payments, so waiting never recovers anything here; a guardrail
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
