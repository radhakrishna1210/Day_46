# Project Blueprint: The Revenue Recovery Agent
### Razorpay AI Buildathon 2026 — Track 3 (AI Revenue Recovery)

**Deadline: September 5, 2026 · Time available: ~12 days · Team: You + Claude Code**

---

## 1. What are we building? (One paragraph)

We are building an AI agent that helps a small Indian business (an MSME) get back money that is stuck with buyers who pay late. The agent watches all invoices, notices when one goes overdue, checks **who** the buyer is (using a payment score built from their history), checks **what the law says** (India's MSMED Act gives small suppliers real legal power), decides the **right way** to chase (gentle, firm, or legal), writes the message itself (even in Hinglish), remembers every **promise** the buyer makes and catches them when they break it, and **stops** at the right time so it never spams anyone. Finally, it **proves** it works by recovering measurably more money than a dumb reminder bot in a simulated test.

**One line:** *"Razorpay has the payment rails and sends reminders. We built the brain that decides who to chase, how hard, with what legal leverage, and when to stop — and we can prove it recovers more money."*

---

## 2. Why this project can win

Razorpay judges on 4 things. Here is how each part of our project scores on them:

| Their criteria | What it means | How we score it |
|---|---|---|
| **Problem Taste** | Did you pick a real problem? | Razorpay's own Fix My Itch data scores this problem 82.8. Recordent's 2026 report: the average SME has ₹3.83 crore stuck 360+ days and buyers take 73 days to pay against a 45-day legal limit. |
| **Build Quality** | Clean code, works reliably | Small modular blocks, config files, tests, honest README |
| **AI Judgment** | Used AI where it helps, rules where they're enough | Rules do the math and dates. AI reads replies, writes messages, and makes judgment calls. We say this out loud in the pitch. |
| **Failure Recovery** | What happens when things break | Stopping rules, dispute detection hands off to a human, exception list shows every case we could NOT recover and why |

The track's bar says: *"Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail."* Every word of that bar has a matching block below.

---

## 3. The problem (in 60 seconds)

A small factory sells ₹5,00,000 of goods to a big buyer. The buyer says "we pay in 90 days." The factory already spent ₹3,50,000 on materials and wages. So the factory is giving the big company a **free loan** — and often the buyer pays even later than 90 days.

Why the factory can't fight back:
1. **Power gap** — the buyer has 50 suppliers; the factory has 2 big buyers.
2. **No data** — the owner doesn't know if this buyer delays everyone or just him.
3. **No number** — he *feels* the pain but can't show what the delay *costs*. Feelings lose negotiations. Numbers win them.

What almost nobody knows: **the law is heavily on the small supplier's side.** Our agent turns that law into automatic, polite, factual pressure.

---

## 4. The big picture — how the whole system flows

```
                        ┌─────────────────────┐
                        │  FAKE DATA FACTORY   │  (creates 100 invoices,
                        │  invoices + buyers   │   20 buyers, on purpose messy)
                        └──────────┬──────────┘
                                   │
                                   ▼
   ┌───────────────┐    ┌─────────────────────┐
   │  SCORE ENGINE  │◄───│      WATCHDOG        │  (runs daily: finds which
   │ buyer history  │    │  who is overdue?     │   invoices crossed due date)
   │  → score 0-100 │    └──────────┬──────────┘
   └───────┬───────┘               │
           │                        ▼
           │             ┌─────────────────────┐
           └────────────►│      LAW ENGINE      │  (15/45 day rule, interest
                         │  what leverage do    │   owed, buyer's tax cost,
                         │  we legally have?    │   Samadhaan draft if needed)
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │       BRAIN          │  (decides: nudge? firm?
                         │  score + law + past  │   legal facts? wait? stop?
                         │  promises → action   │   or hand to human?)
                         └──────────┬──────────┘
                                    │
                        ┌───────────┴───────────┐
                        ▼                       ▼
             ┌──────────────────┐    ┌──────────────────┐
             │  MESSAGE WRITER   │    │  PROMISE TRACKER  │
             │  AI writes the    │    │  "will pay Friday"│
             │  right words in   │    │  → remembered →   │
             │  right tone/lang  │    │  caught if broken │
             └────────┬─────────┘    └──────────────────┘
                      ▼
             ┌──────────────────┐
             │    POST OFFICE    │  (email = real,
             │  sends / logs it  │   WhatsApp & SMS = stubbed)
             └────────┬─────────┘
                      ▼
             ┌──────────────────┐
             │  BUYER SIMULATOR  │  (fake buyers with personalities
             │  reacts like a    │   react to our messages —
             │  real buyer would │   this is our test world)
             └────────┬─────────┘
                      ▼
             ┌──────────────────┐
             │    SCOREBOARD     │  (₹ recovered vs baseline bot,
             │  audit trail +    │   days saved, honest failures)
             │  final report     │
             └──────────────────┘
```

---

## 5. A day in the life of Invoice #204 (story version)

This is the easiest way to understand the whole system. Follow one invoice:

1. **Day 0** — Invoice #204 for ₹5,00,000 is raised on buyer "ABC Traders". Written agreement says 45 days.
2. **Day 46** — The **Watchdog** wakes up on its daily run and sees #204 is now 1 day overdue.
3. The **Score Engine** says: ABC Traders scores **48/100** — they've been late on 7 of their last 9 invoices, average delay 26 days. This is a habitual delayer, not a forgetful friend.
4. The **Law Engine** calculates: statutory due date passed, interest is now legally accruing at 3× the RBI bank rate compounded monthly, and if ABC pays after March 31 they lose the tax deduction on ₹5,00,000 this year (~₹1,50,000 tax hit for them at 30%).
5. The **Brain** decides: score is low, so skip the soft first reminder. Go straight to **firm but polite**, mention the interest is accruing. Do NOT mention Samadhaan yet — that's rung 3.
6. The **Message Writer** (AI) drafts it in professional English (ABC's profile says they're a corporate buyer, so no Hinglish here), states facts, no threats.
7. The **Post Office** sends it by email (real) and logs "WhatsApp: would send" (stubbed).
8. **Day 49** — ABC replies: *"Cash flow tight, will clear by 5th September."* The **Promise Tracker** (AI) reads this, extracts: `{promise_date: 2026-09-05, amount: full}`. The Brain says: go silent. Chasing a person who just promised is rude and hurts the relationship.
9. **Day 53 (Sept 6)** — Promise date passed, no payment. The Brain escalates: the next message opens with *"On 26 Aug you committed to clearing this by 5 Sept."* and now includes the exact interest amount owed to date and a line about Section 43B(h) tax impact.
10. **Day 58** — Still nothing. Attempt 3 of 3 reached. The Brain **stops messaging** (stopping rule) and does two things: generates a ready-to-file **MSME Samadhaan complaint draft** and flags the case to the human owner with the full history.
11. Every one of these steps was written to the **audit trail**: timestamp, what was decided, *why* it was decided, which rule or AI reasoning triggered it.
12. In the final **Scoreboard**, #204 appears in the exceptions list: *"Not recovered in simulation window. Escalated to Samadhaan draft. Buyer persona: habitual delayer, ignored 3 contacts."* — because honesty about failures is literally in the judging bar.

---

## 6. The building blocks (one by one, in easy words)

### Block 1 — Fake Data Factory (`data/generate.py`)
**What:** A script that creates our test world: ~20 buyers and ~100 invoices.
**Why fake?** The track allows synthetic data, and fake data means nothing can block us — no API access, no privacy issues.
**Important:** Make it messy ON PURPOSE. Include: invoices with no written agreement (15-day rule applies!), partial payments, one disputed invoice, buyers with only 1-2 invoices of history (low confidence!), amounts from ₹8,000 to ₹12,00,000.
**Output:** `buyers.json`, `invoices.json` (or SQLite tables).

### Block 2 — Buyer Score Engine (`engine/score.py`)
**What:** Turns a buyer's payment history into one number (0–100) plus a confidence level.
**Simple formula to start (tune later):**
```
score = 100
        − (average_delay_days × 1.2)
        − (broken_promises × 8)
        − (disputes_raised × 5)
        + (on_time_streak × 2)
clamp between 0 and 100
confidence = low (<3 invoices) / medium (3–9) / high (10+)
```
**Also produces:** a breakdown (why the score is what it is) and a trend arrow (score 6 months ago vs now).
**Rule, not AI.** A score must be explainable — Razorpay's bar says "every money action explainable."

#### Block 2b — Ability and Willingness (`engine/ability_willingness.py`)

The one number above hides a question it cannot answer. Two buyers both pay
40 days late and both break promises. The score gives them the same number —
but one is **broke** and one is **stalling**, and the right thing to do with
them is opposite. Chasing a buyer harder when the money genuinely is not
there just burns the relationship; offering a payment plan to a buyer who is
simply choosing not to pay is a gift.

So the score is split into two questions asked separately:

- **Willingness — "will they pay?"** The old formula, relabelled: delay,
  broken promises, disputes, on-time streak. Nothing new; this is what the
  score was always really measuring.
- **Ability — "can they pay?"** The genuinely new axis, read off the buyer's
  money coming *in*: whether their monthly inflow is rising or falling, how
  lumpy it is, how many payments have bounced, and — when we are judging one
  specific invoice — how big that invoice is against a typical month for
  them. The same ₹5 lakh invoice is pocket change to a corporate and a
  serious ask for a small trader, and the ratio says so.

Put the two on a grid and you get four buyers, not one:

```
                         WILLINGNESS
                    low                 high
              +-------------------+-------------------+
       high   | can_pay_but_wont  |  good_customer    |
              | has the money,    |  can pay and      |
              | chooses not to    |  does pay         |
  ABILITY     +-------------------+-------------------+
       low    | high_risk         |  cash_flow_problem|
              | neither means     |  wants to pay,    |
              | nor intent        |  money isn't there|
              +-------------------+-------------------+
```

**Where the evidence comes from.** `data/generate.py` puts two new fields on
each buyer — `monthly_inflow_paise` (6–12 months of money in, most recent
last) and `failed_payment_count`. They are correlated with the hidden
simulator persona the same way payment delays already are: a `cash_tight`
buyer's inflow declines and bounces, a `habitual_delayer`'s stays flat and
healthy because they are not short of money, they are just slow. **The
correlation runs one way only.** The persona shapes the numbers; only the
numbers reach the buyer record, and no module under `engine/` ever sees the
tag — the same one-way street the delay pattern already travels down, and
`tests/test_sim_isolation.py` enforces it for this module automatically.

**Rule, not AI**, like the rest of the scoring, and every weight and boundary
lives in `config/rules.yaml` under `score.ability`, `score.willingness` and
`score.quadrant`. Both axes carry a full breakdown, and
`explain_ability()` / `explain_willingness()` print the arithmetic in plain
English exactly as `explain()` does for the legacy score.

> **Nothing acts on this yet.** As of Phase 1 the two axes and the quadrant
> are *computed and explained only*. `engine/brain.py` does not import this
> module, no message changes, and no escalation changes — the agent behaves
> exactly as it did before. Wiring the quadrant into decisions (a payment-plan
> conversation for `cash_flow_problem`, firmer escalation for
> `can_pay_but_wont`) is **Phase 2**. Shipping it inert first means the
> numbers can be argued with before they are allowed to move money.
>
> The legacy `score` is untouched by all of this: `score_buyer()` returns
> exactly the record it always did, and the two-axis view is a separate
> `two_axis_score()` composed on top of it. Every existing reader — brain,
> writer, watchdog, buyer panel, the simulator — is unaffected.

**Known limitation, stated rather than hidden:** `average_delay_days` is not
a pure willingness signal — a buyer who pays late *because* they are broke is
penalised on the willingness axis too. Separating that properly needs
per-invoice attribution we do not have. The ability axis is what stops that
conflation from reaching a decision on its own.

#### Block 2c — Recovery Probability + EV (`engine/negotiation.py`)

Ability and willingness (Block 2b) place a buyer in a quadrant. This block
asks the next question: for a buyer in that quadrant, which of a fixed set
of candidate recovery actions is actually worth taking?

```
P(recover)               per (quadrant, action), from config -- an assumption,
                          not a measured outcome.
expected_recovery_paise   outstanding_paise scaled by how much of it the
                          action collects WHEN it succeeds (full value for a
                          message or a payment plan; a partial settlement for
                          counter_settle / human_handoff / legal_escalation).
cost_paise                one LLM draft call, or minutes of the MSME owner's
                          own time for a handoff.
ev_paise = round(probability/100 * expected_recovery_paise) - cost_paise
```

The action space: `wait`, `soft_nudge`, `firm`, `legal_facts` (sharing names
with the ladder's rungs 0-3 on purpose -- same real-world action), plus two
genuinely new ones, `payment_plan` (a schedule for the full amount) and
`counter_settle` (the buyer proposes a partial settlement), plus
`human_handoff` and `legal_escalation` (both correspond to today's rung 4,
scored separately because a phone call and the Samadhaan reference path have
different costs and different odds).

**Design call:** `recovery_probability` is a flat grid, one assumed
percentage per `(quadrant, action)` pair, not a weighted formula like
`ability()`/`willingness()`. Those two decompose into weighted per-signal
terms because the terms have plausible units (percent inflow decline maps to
score points); a probability weight here would have no such unit -- there is
no measured recovery-rate data behind any of these numbers, so a flat,
visibly-a-guess grid is more honest than dressing a guess up as arithmetic.

**A result worth stating rather than quietly re-tuning away:** with the
shipped grid, the model ranks `legal_facts` (or `legal_escalation`) above
`soft_nudge` even for a `good_customer` -- the best-paying buyer. The model
has no term for the relationship cost of over-escalating a good payer, only
`P(recover)`, and more assertive contact is modelled as at least as likely to
work, at essentially the same near-zero cost as a gentle one. This is exactly
what shipping the reasoning inert first is for: the number is visible and
arguable before it is allowed to move money. A candidate for Phase 3, not
patched here by hand-tuning one row to hide it.

> **PHASE 2 SCOPE.** Computed and ranked only. `engine/brain.py` does not
> import this module, `Action.kind` stays exactly the four strings it is
> today, and the two silent-failure `Action.kind` consumers
> (`engine/consolidate.py`'s `_eligible()`, `engine/buyer_panel.py`'s
> `_LADDER_KINDS`) are untouched. Wiring a chosen action into the Brain,
> adding `payment_plan`/`counter_settle` to `Action.kind`, and fixing those
> two consumers is Phase 3.
>
> **Money-safety note:** every number this module produces is advisory
> arithmetic over a hypothetical action, not a real transaction. `ev_paise`
> never touches `amount_paid_paise` or any other ledger state -- it is
> evaluated for comparison only, the same spirit as `section_16_running`'s
> "cost of waiting" figure in `engine/law.py`.

Every weight and cost figure lives in `config/rules.yaml`'s `negotiation`
block, and every function carries its own breakdown, exactly like
`engine/ability_willingness.py`:

```
python engine/negotiation.py --explain INVOICE_ID
```

### Block 3 — Watchdog (`engine/watchdog.py`)
**What:** Runs once per simulated day. Compares today's date with every unpaid invoice's due date. Anything overdue goes into the work queue.
**Pure rule.** Just date math.

### Block 4 — Law Engine (`engine/law.py` + `config/legal.yaml`) ⭐ our biggest differentiator
**What:** For any overdue invoice, computes the supplier's full legal position under Indian law:

| Legal fact | What our engine does with it |
|---|---|
| **Section 15, MSMED Act:** payment due in 15 days (no written agreement) or max 45 days (with one). Any written term beyond 45 days is void — 45 is an absolute ceiling. | Compute the TRUE statutory due date, even when the "agreed" terms said 90 days. |
| **Section 16:** on delay, buyer owes compound interest with monthly rests at 3× the RBI **bank rate** — automatically, "notwithstanding anything contained in any agreement", so a no-interest clause does not survive it. Interest runs from the day *immediately following* the due date. | Compute exact ₹ interest owed to date. (Verified 23 Aug 2026: bank rate **5.50%** → **16.50% p.a.** compounded monthly. Note this is the Bank Rate, not the repo rate of 5.25%. The rate lives in `config/legal.yaml` — re-verify before submitting, the MPC meets roughly every two months.) |
| **Section 37(2)(g), Income-tax Act 2025** (formerly Section 43B(h) of the 1961 Act — renumbered when the 2025 Act took effect on 1 Apr 2026): if the buyer pays a registered MSME late, the deduction moves to the year of actual payment. No pay-before-filing relief. | Compute the buyer's OWN tax cost of delaying. This flips the message from "please pay me" to "delaying costs YOU money." Both citations are in `config/legal.yaml`; messages quote the current one. |
| **Section 22:** buyers must disclose unpaid MSME dues in their annual financial statements. | One factual line in the firm message: "this outstanding is disclosable in your annual accounts." |
| **Section 23:** the penalty interest the buyer pays is NOT tax-deductible for them. | Strengthens the cost math in escalated messages. |
| **MSME Samadhaan portal:** the official complaint route; a buyer challenging an award must first deposit 75% of it. | Final rung: auto-generate a ready-to-file complaint draft with all facts filled in. |

**Output per invoice:** statutory due date, days overdue, interest owed (₹), buyer's tax exposure (₹), which escalation rung is legally available.
**Pure rules + config file.** No AI needed — law is deterministic. This is exactly the kind of "used AI only where it helps" judgment they score.
**Doc note for the repo:** mark all of this as "simplified, as of Aug 2026, not legal advice" — that honesty earns points.

### Block 5 — The Brain (`engine/brain.py`)
**What:** The decision maker. Inputs: score + law position + promise status + attempt count. Output: ONE action.
**The escalation ladder (bounded — this IS the "compliant escalation"):**
```
Rung 0  WAIT        (promise is active and not yet broken)
Rung 1  SOFT NUDGE  (polite reminder, no law talk)
Rung 2  FIRM        (interest accruing + amount, still polite)
Rung 3  LEGAL FACTS (43B(h) tax cost + Section 22 disclosure line)
Rung 4  STOP + HANDOFF (Samadhaan draft + flag to human)
ANY TIME: dispute detected → jump to human handoff immediately
```
**How score changes the path:** score 80+ starts at Rung 1 and waits 7 days between rungs. Score below 50 starts at Rung 2 and waits 4 days. Broken promise = jump one rung.
**Hard stopping rules (never violated):** max 3 messages per rung, max 5 total; no messages in quiet hours; opt-out respected instantly; NEVER threaten — only state facts with sources.
**Mostly rules; AI is consulted only for genuinely ambiguous cases** (e.g., "buyer partially paid and sent a confusing reply — what now?") and its reasoning is saved to the audit trail.

### Block 6 — Message Writer (`engine/writer.py`)
**What:** The AI (Gemini, via `engine/llm.py`) writes the actual message for the chosen rung.
**It receives:** rung, buyer profile (corporate vs small trader), language preference, all the law numbers, promise history.
**It adapts tone:** warm for a good buyer having a bad month, businesslike for a corporate, firm for a habitual delayer.
**Hinglish mode:** for small-trader profiles, it drafts WhatsApp-style Hinglish: *"Sir, invoice #204 ka payment 12 din se pending hai. Request hai ki is week clear kar dein…"* — this is literally an example direction in the track, and no enterprise tool does it.
**Guardrail:** a checklist the AI output must pass — no threats, no invented facts, numbers must match the Law Engine exactly.

### Block 7 — Post Office (`engine/channels.py`)
**What:** One interface: `send(channel, to, message)`.
- **Email = REAL** (Gmail SMTP + app password, sent to your own test inbox — great video moment).
- **WhatsApp & SMS = STUBBED** (they log "would send"). README says why: WhatsApp Business API needs business verification — a deliberate, documented scope call.
**Never contacts any real person except your own test inbox.**

### Block 8 — Promise Tracker (`engine/promises.py`)
**What:** When a (simulated) buyer replies, the AI reads the free text and extracts structure:
`"boss thoda time do, 5 tarikh tak ho jayega"` → `{intent: promise, date: 2026-09-05, amount: full}`
It also classifies: promise / dispute / refusal / question / noise.
**Then:** promises are stored; the Watchdog checks them daily; a broken promise escalates the Brain and the next message references it. A detected **dispute** immediately stops chasing and hands off to a human — chasing a disputed invoice is how you lose a customer.
**This is real AI work** — intent extraction from messy Hinglish/English text is exactly what LLMs are for.

### Block 9 — Buyer Simulator (`sim/personas.py`, `sim/run_sim.py`)
**What:** Our test world. Each fake buyer gets a hidden personality that decides how they react to each message type:

| Persona | Behavior |
|---|---|
| The Forgetful | 80% pays within 3 days of ANY reminder |
| The Cash-Tight | Ignores soft nudges; makes a promise when pushed; usually keeps it (70%) |
| The Habitual Delayer | Only moves when interest/tax numbers appear (60% then) |
| The Disputer | Replies with a complaint about the goods; needs a human |
| The Deadbeat | 10% ever pays; the right answer is to stop early and escalate |

**Why this matters:** the bar demands *measured* money recovered. A simulator lets us run a fair experiment (below) instead of showing one cherry-picked success — which the track explicitly mocks ("one cherry-picked match proves nothing" is their phrase for Track 4, same spirit here).

### Block 10 — Scoreboard (`report/build_report.py`)
**The experiment:** run TWO agents on the SAME 100 invoices, same random seed:
- **Baseline:** 3 fixed reminders, same message for everyone (≈ what Razorpay Payment Links reminders do today)
- **Our agent:** everything above
**Report (also the star slide of the video):**
```
                     Baseline      Our Agent
₹ recovered          ₹6.1L         ₹8.4L        (+₹2.3L)
Avg days to pay      71            52           (−19 days)
Messages sent        300           187          (fewer = less annoying)
Escalated to human   0             6            (correctly!)
Not recovered        11 invoices   4 invoices   → full exceptions list
```
**Plus the audit trail:** every action ever taken, with timestamp, reason, and rule/AI reasoning — exportable, viewable, honest.

### Block 11 — The Winning Layer (built after Day 9, labels W1-W4)

**What:** four post-MVP additions, built after Blocks 1-10 above and the
E1-E4 edge-case hardening pass. `docs/winning_layer.md` is the full roadmap
these only partially implement — see it for what's still future work.

- **W1 Early Warning** (`engine/watchdog.py::early_warnings()`) — a
  rule-based low/watch/high risk band on invoices approaching (not yet past)
  their due date. Human-facing only, surfaced in the buyer panel/report; no
  pre-due message is ever sent to a buyer. No predictive probability, no
  cash-flow signal — just a fixed date-based band.
- **W2 Buyer/Trader Panel** (`engine/buyer_panel.py`) — rolls invoice-level
  facts up to a per-buyer view: outstanding amount, overdue count, oldest
  overdue, score/confidence/trend, promise reliability % + average days
  late, response rate, recovery state.
- **W3 Buyer-Level Message Consolidation** (`engine/consolidate.py`) —
  groups a day's already-decided SEND actions for one buyer into rung
  tiers (courtesy: rung ≤ 1, escalated: rung ≥ 2), so a buyer gets at most
  two envelopes/day instead of one email per invoice. `engine/brain.py`'s
  per-invoice decisions and stopping rules are unchanged; this only changes
  how many envelopes carry them. A disputed invoice's handoff never enters
  a bundle — it's routed to a human before consolidation sees it.
- **W4 Experiment Refresh** — re-ran the full 6-seed baseline-vs-agent
  comparison at HEAD and added per-seed edge-case-count transparency (how
  many seeds actually exercise the malformed-invoice/superseded-promise
  fixes) to the multi-seed report table.

**Rules, not AI** — same split as every earlier block: these are
deterministic rollups and groupings over data the rest of the pipeline
already produces, not new model calls.

---

## 7. Where AI is used vs plain code (say this in the pitch!)

| Job | Rules or AI? | Why |
|---|---|---|
| Detect overdue | Rules | Date math. Using AI here would look naive. |
| Buyer score | Rules | Must be explainable and auditable. |
| Law calculations | Rules + config | Law is deterministic. |
| Escalation ladder | Rules | Safety-critical → must be predictable. |
| Reading buyer replies | **AI** | Messy Hinglish free text → structure. |
| Writing messages | **AI** | Tone, language, context — LLM's home turf. |
| Ambiguous judgment calls | **AI** (logged) | Partial payment + confusing reply etc. |
| Deciding when to STOP | Rules | Never let an LLM decide to keep pushing. |

One pitch line: *"Rules where mistakes are expensive, AI where language is messy."*

---

## 8. Tech stack (all free / near-free, no GPU needed)

- **Python 3.11+** — the whole project
- **Gemini API** (Google, via `engine/llm.py`) — the AI parts: reply parsing, message drafting, ambiguous judgment calls. All three purposes currently run on a single Flash-tier model (`gemini-3.7-flash`, set in `config/rules.yaml`) rather than a cheap/strong split — this key's free tier has zero Pro-tier quota and billing isn't available for it, a known quality-vs-cost tradeoff (see README's Future Work). `engine/llm.py` reads `LLM_MODE` from `.env`: `mock` (default) gives deterministic canned responses, no key needed; `live` calls the real Gemini API with `GEMINI_API_KEY`. Your Claude Pro plan separately gives you **Claude Code** as your coding assistant — use it heavily; it's unrelated to which LLM the shipped app itself calls.
- **SQLite** (built into Python) or plain JSON files — no database server needed
- **smtplib** (built in) — real email sending
- **Jinja2** — turn results into a clean HTML report
- **pytest** — a few tests on the Law Engine math (judges love tested money-math)
- Everything runs on a normal laptop. The heavy AI lifting happens on Google's servers (or nowhere, in mock mode).

---

## 9. Repo structure (what the judges will open)

This is the original Day-1 design. The actual repo (post E1-E4, W1-W4) has
grown beyond it — see below for what's real today.

```
revenue-recovery-agent/
├── README.md              ← the pitch in text: problem, demo, results table, honest scope
├── ARCHITECTURE.md        ← the flow diagram + block descriptions (from this doc)
├── main.py                ← runs the live single-pass agent pipeline (not the comparison/report — see below)
├── config/
│   ├── rules.yaml         ← ladder timings, stop rules, score weights, consolidation cap, LLM model names
│   ├── legal.yaml         ← 15/45 days, bank rate, tax rate (marked "as of Aug 2026")
│   ├── messages.yaml      ← message/subject-line templates per rung and per consolidated bundle
│   ├── replies.yaml       ← mock-mode canned reply fixtures
│   └── supplier.yaml      ← the one supplier identity used across all invoices
├── data/
│   ├── generate.py        ← --seed, --out-dir, --persona-out
│   ├── store.py           ← loads the generated seed data back off disk
│   └── seed/              ← buyers.json, invoices.json (generated, gitignored)
├── engine/
│   ├── config.py          ← the only reader of config/*.yaml
│   ├── llm.py              ← the only caller of the Gemini API (mock/live modes)
│   ├── money.py            ← integer-paise formatting, one source of truth
│   ├── score.py  watchdog.py  law.py  rungs.py  brain.py
│   ├── ability_willingness.py ← Phase 1: the two-axis score + quadrant (computed, not yet acted on)
│   ├── negotiation.py      ← Phase 2: recovery probability + EV per action (computed, not yet acted on)
│   ├── validate.py         ← catches structurally malformed invoices before law/brain see them (E2)
│   ├── samadhaan.py        ← the real ready-to-file Samadhaan complaint draft
│   ├── buyer_panel.py      ← W2: per-buyer rollup (outstanding, score, promise reliability, recovery state)
│   ├── consolidate.py      ← W3: groups a day's SEND decisions by buyer into rung-tier envelopes
│   ├── writer.py  channels.py  promises.py  audit.py
├── sim/
│   ├── personas.py  run_sim.py        ← --compare, --seed, --days, --extra-seeds, --scenario
│   ├── scenario_tc141.py              ← the E4 end-to-end scripted scenario fixture
│   └── hidden_personas.json           ← generated by data/generate.py --persona-out; gitignored; engine/ must never read it
├── docs/
│   ├── edge_cases.md      ← 141 documented edge cases, each TESTED / HANDLED / OUT OF SCOPE
│   └── winning_layer.md   ← the Winning Layer roadmap: what's built (W1-W4) vs still future
├── report/
│   ├── build_report.py    ← baseline-vs-agent HTML report + exceptions list + buyer panel + multi-seed table
│   ├── templates/         ← the Jinja2 template(s) build_report.py renders
│   └── out/                ← generated, gitignored: results.json, report.html
├── audit/                 ← generated audit logs land here (append-only JSONL); audit/drafts/ holds Samadhaan drafts
└── tests/                 ← 24 files, 829 tests. Beyond the obvious per-module tests, three are
                              structural guards worth naming: test_sim_isolation.py (AST-scans
                              engine/ + main.py to prove the agent never reads sim/hidden_personas.json),
                              test_no_legal_constants.py (AST-scans for hardcoded legal numbers/citations
                              outside config/legal.yaml), and test_run_sim.py (money-conservation
                              invariant + a permanent guard that every SEND decision has exactly one
                              matching writer audit entry, forever).
```

`main.py --seed 42` runs the real per-invoice pipeline (watchdog → score →
law → brain → writer → channels → promises) with a full audit trail, and
`--send-email` sends a real message to the test inbox. It does **not** run
the baseline-vs-agent comparison or build the report — those are
`sim/run_sim.py --compare` and `report/build_report.py`, run separately (see
README's Quickstart). The original "one command runs the whole simulation"
plan for `main.py` was deliberately dropped once `sim/run_sim.py` and
`report/build_report.py` were built as their own scripts instead: its last
two pipeline stages print the command that does the work ("run separately:
python sim/run_sim.py --compare …" / "… report/build_report.py") rather than
running it, so the pipeline never pretends to have produced results it did
not.

### Support modules (not blocks — plumbing added during the build)

These carry no business logic. They exist so the rules above have exactly one
place each to live, rather than being copy-pasted across blocks.

| Module | Why it exists |
|---|---|
| `engine/config.py` | The single reader of `config/rules.yaml` and `config/legal.yaml`, cached. The rule "code reads config, code never embeds these numbers" needs one door, or every block grows its own YAML loader and they drift. `reload()` lets tests swap a rule set. |
| `data/store.py` | Reads `data/seed/*.json` back. The dataset is generated rather than committed, so every entry point needs the same "not generated yet, here is the command" answer instead of its own traceback. Also groups invoices by buyer, which the score engine and the simulator both need. |
| `engine/audit.py` | Named in the non-negotiables but not in the original block list. Every money-related action is appended here with timestamp, invoice, action, reason, and whether the reason came from a rule or the AI. |
| `engine/money.py` | One source of truth for turning integer paise into a ₹ string. Added so "money is stored in paise, formatted as ₹ only for display" (CLAUDE.md's own rule) has one door instead of drifting rounding logic across every block that prints an amount. |
| `engine/rungs.py` | The fact-skeleton contract between the Law Engine and the Message Writer — which numbers/sentences a given rung is allowed to state, so the writer's guardrail has something concrete to check against. |

---

## 10. The 12-day plan (Aug 24 → Sept 4, buffer on Sept 5)

| Day | Build | Done when… |
|---|---|---|
| 1 | Repo, API key, data models, config files | `main.py` runs end-to-end doing nothing |
| 2 | Fake Data Factory | 100 messy invoices, 20 buyers generated |
| 3 | Score Engine + Watchdog | Scores print with breakdown + confidence |
| 4 | Law Engine math | Tests pass: interest, 45-cap, 15-default, 43B(h) |
| 5 | Law messages + Samadhaan draft generator | Rung 2–4 content generates correctly |
| 6 | Brain + Message Writer | One invoice walks the full ladder in logs |
| 7 | Post Office (real email!) + Promise Tracker | Reminder lands in your inbox; promises extracted |
| 8 | Simulator personas | Buyers "reply" and sometimes pay |
| 9 | Full experiment: baseline vs agent | The results table exists with real numbers |
| 10 | Report, audit viewer, README, ARCHITECTURE.md | A stranger can run it in 3 commands |
| 11 | Record + edit the 5-min video | Video done |
| 12 | Polish repo, final test on fresh machine, **submit the form** | Submitted ✅ (deadline Sept 5) |

**Falling behind? Cut in this order:** Hinglish → trend arrows → HTML prettiness. **Never cut:** Law Engine, simulator experiment, audit trail, stopping rules — those ARE the bar.

---

## 11. The 5-minute video script

> Status: script only, not yet recorded (CLAUDE.md's checklist item 11 is
> still open). Every beat named below corresponds to real, working code —
> confirmed during the Phase 10 documentation audit (Brain rung selection,
> Hinglish drafting, Law Engine interest/tax math, promise tracking, a real
> email send, and the Samadhaan draft are all live) — this is a script to
> record, not a description of a video that exists.

- **0:00–0:30 — The problem, with numbers.** "Indian SMEs wait 73 days to get paid against a 45-day legal limit. The average SME has ₹3.83 crore stuck over a year. And 40% of India's B2B sales run on credit — that stat is from Razorpay's own blog."
- **0:30–1:00 — What I built.** The one-liner + the architecture diagram, 20 seconds on the ladder.
- **1:00–3:30 — Live demo.** Run the batch. Show: the Brain choosing different paths for a 90-score buyer vs a 45-score buyer → one Hinglish message → the Law Engine's interest + tax-cost math on screen → a promise being made, broken, and caught → the REAL email arriving in your inbox → the Samadhaan draft for the deadbeat.
- **3:30–4:30 — Proof.** The baseline-vs-agent table. Then the exceptions list: "It failed on these 4 — here's why each one." (Honesty is a feature.)
- **4:30–5:00 — Why Razorpay.** "A single vendor can't build the buyer score — Razorpay's network can. Razorpay has the rails and the reminders; this is the brain. Future work: real WhatsApp channel, live RBI rate feed, TReDS integration."

---

## 12. Submission checklist

- [ ] Public GitHub repo (clean history, no API keys committed — use `.env`)
- [ ] README with: problem → demo → results table → how to run in 3 commands → honest scope/Future Work
- [ ] ARCHITECTURE.md with the diagram
- [ ] 5-minute pitch video (unlisted YouTube link works)
- [ ] `pytest` passing
- [ ] Fresh-machine test (clone → install → run)
- [ ] Application form submitted **before September 5**
- [ ] Legal disclaimer line in README ("simplified, as of Aug 2026, not legal advice")

---

## 13. Future Work section (write these in README — do NOT build them)

Real WhatsApp Business API channel · voice calls (Hinglish TTS) · live RBI bank-rate feed · Tally/Zoho invoice import · TReDS invoice-discounting suggestion for stuck invoices · network-level score across many vendors (the Razorpay-scale version) · dispute-resolution assistant.

Each line here signals "I knew about this and chose focus" — which is worth more than a half-built feature.

---

## 14. Glossary (easy words)

- **MSME** — Micro, Small & Medium Enterprise; a small business, officially registered (Udyam registration).
- **Invoice** — the bill you send after delivering goods: "you owe me ₹X by date Y."
- **Receivable** — money others owe you that hasn't arrived yet.
- **DSO (Days Sales Outstanding)** — average days it takes to actually get paid. Lower = healthier.
- **Dunning** — the polite word for systematically chasing payments.
- **Escalation ladder** — the fixed sequence of increasingly firm steps.
- **Stopping rule** — a hard limit that makes the agent stop (max attempts, dispute, opt-out).
- **Audit trail** — a log of every action with the reason — so a human can check the agent later.
- **Synthetic data** — realistic fake data we generate ourselves for safe testing.
- **Persona** — a fake buyer's hidden personality in the simulator.
- **LLM** — Large Language Model (the AI; this project calls Gemini) — great with messy language, not for math.
- **Baseline** — the dumb version we compare against to prove ours is better.
- **MSME Samadhaan** — the government's online portal where MSMEs file delayed-payment complaints.

---

## 15. Sources behind the key facts

- Razorpay Buildathon tracks & bar: razorpay.com/buildathon
- Fix My Itch problem list & itch scores: razorpay.com/m/fix-my-itch
- Recordent Indian SME Receivables Report 2026 (73 days, ₹3.83 cr figures)
- MSMED Act 2006, Sections 15–24 (15/45 days; 3× bank rate compound monthly; Section 22 disclosure; Section 23 interest non-deductible; 75% pre-deposit on challenge)
- Income Tax Act Section 43B(h) (late payment to MSME → deduction only in year of actual payment; carried into the new Income Tax Act 2025)
- Razorpay Payment Links reminders docs (the "baseline" behavior: max 3 fixed reminders)

*Legal figures are simplified for a demo, current as of Aug 2026, and are not legal advice. Verify the current RBI bank rate before final submission.*
