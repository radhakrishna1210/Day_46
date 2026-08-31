# Revenue Recovery Agent

**Razorpay AI Buildathon 2026 -- Track 3: AI Revenue Recovery**

> Razorpay has the payment rails and sends reminders. This is the brain that
> decides *who* to chase, *how hard*, with *what legal leverage*, and *when to
> stop* -- and it can prove it recovers more money than a fixed reminder bot.

An AI agent that helps Indian MSMEs collect overdue B2B invoices. It watches
invoices, scores the buyer from payment history, computes the supplier's real
legal position under the MSMED Act, picks an escalation rung, writes the
message (English or Hinglish), remembers every promise a buyer makes,
consolidates a buyer's invoices into one envelope instead of five, and stops
before it becomes spam. Every money-related action is written to an audit
trail with the reason behind it.

**Status:** the original 12-day MVP, four rounds of edge-case hardening
(E1-E4), four post-MVP additions (W1-W4: early warning, buyer panel,
buyer-level message consolidation, experiment refresh), and three
negotiation-model phases (ability/willingness split, recovery-probability +
EV ranking, and wiring that ranking into the Brain behind a config flag) are
all done -- 844 tests passing, agent beats baseline on rupees recovered on
6/6 tested seeds. Demo video and final submission polish are what's left
(see [Honest scope](#honest-scope-built-vs-future-work) below).

**At a glance:**
- Recovers **₹22,98,757 more** than a fixed-reminder baseline bot on the
  same seeded invoices (seed 42) -- and wins on rupees recovered in **6/6**
  tested seeds.
- Every money-related decision is **explainable and audit-logged** -- no
  silent actions, no invented legal numbers.
- **844 tests passing**; of 143 documented edge cases, 62 have a dedicated
  test, the rest are HANDLED or explicitly OUT OF SCOPE -- never left vague.
- Full numbers in [Results](#results); what's built vs. not in
  [Honest scope](#honest-scope-built-vs-future-work).

**Contents:** [The problem](#the-problem) -- [What it does](#what-it-does)
-- [Quickstart](#quickstart) -- [Results](#results) --
[Architecture](#architecture) -- [Early warning (W1)](#early-warning-w1) --
[Buyer / trader view (W2)](#buyer--trader-view-w2) --
[Ability vs. willingness (Phase 1)](#ability-vs-willingness-the-two-axis-score-phase-1) --
[Recovery probability + EV (Phase 2)](#recovery-probability--expected-value-phase-2) --
[Wiring EV into the Brain (Phase 3)](#wiring-the-ev-ranking-into-the-brain-phase-3) --
[Buyer-level message consolidation (W3)](#buyer-level-message-consolidation-w3)
-- [Scope (deliberate)](#scope-deliberate) -- [Edge cases](#edge-cases) --
[Honest scope: built vs. future work](#honest-scope-built-vs-future-work) --
[Where this goes next](#where-this-goes-next) --
[Legal disclaimer](#legal-disclaimer)

---

## The problem

- **73 days** -- average time an Indian MSME waits to get paid, against a
  **45-day** legal ceiling under the MSMED Act.
- **₹3.83 crore** -- what the average SME has stuck 360+ days
  (Recordent's 2026 SME Receivables Report).
- **82.8/100** -- Razorpay's own Fix My Itch data scores this as a problem
  worth solving.

Almost nobody realizes the law is heavily on the small supplier's side. This
agent turns that law into automatic, polite, factual pressure -- and proves
it recovers measurably more money than a fixed reminder bot, on the same
seeded data.

---

## What it does

1. **Explainable decisions** -- every money-related action carries a reason
   and is written to an append-only audit trail (`audit/audit_log.jsonl`):
   timestamp, invoice, action, reason, and whether a rule or the AI decided
   it. No silent actions.
2. **Strategy simulation** -- a baseline bot (3 fixed reminders, same
   message for everyone) and this agent run over the same seeded invoices
   and buyers, so the uplift is measured, not asserted.
3. **Closed-loop learning** -- promise outcomes feed back into the buyer
   score and the escalation ladder: a kept promise builds the score, a
   broken one escalates the next message and references it by date.
4. **Next-best-action selection** -- the Brain picks exactly one action per
   invoice per day: send at a given rung, wait (a promise is active), hand
   off to a human (dispute or final rung), or stop (opt-out or exhausted
   attempts).

---

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env                          # LLM_MODE=mock by default; no API key needed
python data/generate.py --seed 42              # generates buyers.json, invoices.json (gitignored)
python sim/run_sim.py --compare --seed 42 --days 120   # baseline vs agent -> report/out/results.json
python report/build_report.py                  # -> report/out/report.html
pytest -q
```

Separately, `python main.py --seed 42` runs the live single-pass agent
pipeline (watchdog -> score -> law -> brain -> writer -> channels ->
promises) with a real audit trail, and `--send-email` sends a real message
to your own test inbox. It does **not** run the baseline comparison or
build the report -- that's what the two commands above are for.

`LLM_MODE=mock` (the default) gives deterministic canned responses, so the
project runs end to end on a fresh clone with no API key. `LLM_MODE=live`
calls the real Gemini API using `GEMINI_API_KEY` from `.env`.

---

## Results

Baseline vs agent, seed 42, 120-day simulation window
(`report/out/results.json`, generated 2026-08-27):

| Metric | Baseline | Agent |
|---|---|---|
| Recovered | ₹1,36,80,472 (₹1.37 Cr) | ₹1,59,79,229 (₹1.60 Cr) |
| Outstanding at end of window | ₹1,69,13,928 (₹1.69 Cr) | ₹1,46,15,171 (₹1.46 Cr) |
| Invoices fully paid | 36 | 41 |
| Messages sent | 252 | 73 envelopes (141 invoice-level contacts before W3 consolidation) |
| Avg days to pay (all paid invoices) | 93.3 | 95.5 |
| Avg days to pay (matched set -- same 25 invoices both sides recovered) | 101.2 | 97.7 |
| Handoffs to a human | 0 | 42 (17 disputed, 25 rung-4 escalation) |
| Stops | 0 | 6 (opted out) |
| Disputed invoices (current, at end of window) | 12 | 17 |
| Not recovered in the window (exceptions) | 64 | 59 |

**A note on the disputed-invoices row:** `results.json` also has a second,
related field, `disputes`, that counts something different -- invoices that
*became* disputed via a live simulated reply during the run, not a snapshot
of who's disputed right now. The two agree for the baseline (it messages
every overdue invoice regardless of dispute status, so it always gets the
chance to see a live dispute reply) but not for the agent (which correctly
never messages an invoice it already knows is disputed, so a buyer disputed
from the very start of the simulated world can never show up in that
event-counted field even though it's still disputed today) -- if you
cross-reference the raw JSON, don't expect these two numbers to match.

**Why the "avg days to pay" row needs the matched-set caveat:** the raw
figure compares two *different* sets of paid invoices -- the agent
recovers a harder set than baseline gives up on, which drags its raw
average up even though it's the stronger performer. The matched-set row
(the 25 invoices *both* sides actually recovered) is the fair comparison,
and there the agent wins: 97.7 days vs 101.2. Reporting both, not just the
flattering one, is the point.

**Across all 6 tested seeds** (42, 7, 13, 99, 2024, 555): agent wins on
rupees recovered **6/6**, and on matched-set days-to-pay **6/6**.

| Seed | Baseline recovered | Agent recovered | Matched N | Baseline days | Agent days | Malformed invoices | Superseded promises |
|---|---|---|---|---|---|---|---|
| 42 | ₹1.37 Cr | ₹1.60 Cr | 25 | 101.2 | 97.7 | 0 | 0 |
| 7 | ₹0.88 Cr | ₹1.45 Cr | 21 | 99.4 | 95.4 | 0 | 6 |
| 13 | ₹1.32 Cr | ₹1.70 Cr | 25 | 91.5 | 87.2 | 2 | 2 |
| 99 | ₹1.01 Cr | ₹1.45 Cr | 23 | 101.5 | 98.3 | 0 | 5 |
| 2024 | ₹1.00 Cr | ₹1.47 Cr | 26 | 89.1 | 83.4 | 0 | 4 |
| 555 | ₹0.96 Cr | ₹1.17 Cr | 28 | 96.7 | 95.0 | 1 | 5 |

The last two columns exist so a "6/6" win doesn't read as "these edge cases
never came up": 5 of the 6 seeds contain a buyer who renegotiated a promise
before it fell due, and 2 of the 6 contain a structurally malformed invoice
-- the win happens *despite* those, not in their absence.

The full exceptions list (every invoice not recovered in the window, and
why) is in `report/out/report.html` after you run the Quickstart above.

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the flow diagram and a
description of every block.

### Rules vs AI

Rules where mistakes are expensive, AI where language is messy:

| Job | Rules or AI |
|---|---|
| Detect overdue invoices, score buyers, law/interest/tax math, escalation ladder, stopping rules, buyer-level message grouping | Rules |
| Reading buyer replies into structured intent, drafting messages, ambiguous judgment calls | AI -- Gemini (Flash-tier model), via `engine/llm.py`, logged to the audit trail |

`engine/llm.py` is the *only* caller of the Gemini API. `draft_message` and
`judgment_call` currently share one Flash-tier model rather than a
cheap/strong split, because this key's free tier has zero Pro-tier quota and
billing isn't available for it -- a cost tradeoff, not a design choice (see
Future Work).

---

## Early warning (W1)

Risk is surfaced **before** an invoice's statutory due date, not just after:
`engine/watchdog.py::early_warnings()` puts every not-yet-overdue invoice
into a low/watch/high band based on how close it is to falling overdue.

- **Human-facing only** -- surfaced in the buyer panel and report, never
  sent to a buyer. No pre-due message goes out because of an early warning.
- The band is a fixed rule (days remaining until the due date), not a
  prediction. There is no probability score, no cash-flow signal, and no
  claim of predictive intelligence -- that's future work (see below), not
  what's built today.

---

## Buyer / trader view (W2)

`engine/buyer_panel.py` rolls invoice-level facts up to a per-buyer
relationship view, surfaced in the report:

- Total outstanding and overdue-invoice count
- Oldest overdue invoice (days)
- Buyer score, confidence (low/medium/high), and a trend (score N days ago
  vs now)
- Promise reliability: made / kept / broken / in-flight, reliability %,
  average days late
- Response rate: messages sent, replies received, response rate %
- Recovery state: not yet due / in ladder / handed off / stopped

Every field above is real and populated in `report/out/results.json`'s
`buyer_panel` array -- nothing here is aspirational.

---

## Ability vs. willingness: the two-axis score (Phase 1)

**The problem:** the buyer score is one number, and one number cannot answer
two different questions. Two buyers both pay 40 days late and both break
promises, so they score the same -- but one is **broke** and one is
**stalling**. Chasing the first one harder just burns a relationship the
money was never behind; offering the second one a payment plan is a gift.

`engine/ability_willingness.py` asks the two questions separately:

- **Willingness -- "will they pay?"** The existing formula, relabelled:
  delay, broken promises, disputes, on-time streak. Nothing new -- with the
  shipped weights it equals the legacy score exactly, and a test pins that.
- **Ability -- "can they pay?"** The new axis, read off the buyer's money
  coming *in*: inflow trend, how lumpy that inflow is, failed payments, and
  how big the specific invoice is against a typical month for that buyer.
  The same Rs 5 lakh invoice is routine for a corporate and a hard ask for a
  small trader; the ratio says so.

Crossing them gives four buyers instead of one:

| | **Low willingness** | **High willingness** |
|---|---|---|
| **High ability** | `can_pay_but_wont` -- has the money, choosing not to | `good_customer` -- can pay and does |
| **Low ability** | `high_risk` -- neither means nor intent | `cash_flow_problem` -- wants to pay, money isn't there |

Every weight and both quadrant boundaries live in `config/rules.yaml`
(`score.ability`, `score.willingness`, `score.quadrant`), and both axes carry
a full breakdown -- `explain_ability()` and `explain_willingness()` print the
arithmetic in plain English, the same way `explain()` already does:

```
python engine/ability_willingness.py --explain BUY-07
```

> **Computed and explained as shipped in Phase 1.** As of Phase 1 the Brain
> did not read any of this: no message changes, no escalation changes, and
> the whole baseline-vs-agent experiment produced byte-identical numbers to
> before. It shipped inert first so the numbers could be argued with before
> they were allowed to move money. Phase 3 (below) is what wires a quadrant
> into an actual decision, behind a config flag shipped off by default.

**Honest about the data:** the inflow signals are *synthetic*, generated per
buyer and correlated with the simulator's hidden persona (a cash-strapped
buyer's inflow declines and bounces; a habitual delayer's stays healthy
because they are slow, not broke). No real transaction feed is wired in --
that is what a Razorpay integration would supply. The correlation runs one
way only: the persona shapes the numbers, and no module under `engine/` ever
sees the tag, which `tests/test_sim_isolation.py` enforces automatically.

**Honest about the formula:** `average_delay_days` is not a pure willingness
signal -- a buyer who pays late because they are broke loses willingness
points too. Separating that cleanly needs per-invoice attribution we do not
have; the ability axis is what keeps that conflation from driving a decision
on its own.

---

## Recovery probability + expected value (Phase 2)

**The problem:** knowing a buyer's quadrant still leaves "so which action do
I actually take?" unanswered. `engine/negotiation.py` scores a fixed set of
candidate recovery actions -- `wait`, `soft_nudge`, `firm`, `legal_facts`
(sharing names with the ladder's rungs on purpose), plus `payment_plan` and
`counter_settle` (genuinely new: a schedule for the full amount, versus the
buyer proposing "70% now, waive the rest"), plus `human_handoff` and
`legal_escalation` (both correspond to today's final rung, scored separately
because a phone call and the Samadhaan reference path have different costs
and different odds) -- by expected value:

```
EV = P(recover) x expected_recovery_paise - cost_paise
```

`P(recover)` is a flat, assumed percentage per `(quadrant, action)` pair in
`config/rules.yaml`, not a weighted formula: unlike ability/willingness's
per-signal weights, there is no measured recovery-rate data behind any of
these numbers, so a visibly-a-guess grid is more honest than dressing a guess
up as arithmetic. `expected_recovery_paise` scales the outstanding amount by
how much of it the action collects *when it succeeds* -- full value for a
message or a payment plan, a partial settlement for `counter_settle` /
`human_handoff` / `legal_escalation`. `cost_paise` is one LLM draft call, or
minutes of the MSME owner's own time for a handoff, priced from real Gemini
3.7 Flash pricing and a stated USD-INR assumption (see the config file's
comments for both citations).

```
python engine/negotiation.py --explain INVOICE_ID
```

On the seeded data, the ranking behaves the way the quadrant split promises:
a `cash_flow_problem` buyer's top action is `payment_plan`, ahead of every
message action and of `legal_escalation`; a `can_pay_but_wont` buyer's top
action is `legal_facts`, ahead of `payment_plan`; a `high_risk` buyer's top
action is `legal_escalation` (stop early, escalate -- matching the
simulator's own deadbeat persona reasoning).

**A result worth stating rather than quietly re-tuning away:** for a
`good_customer` -- the best-paying buyer -- the same grid ranks `legal_facts`
above `soft_nudge`. The model has no term for the relationship cost of
over-escalating a good payer, only `P(recover)`, and more assertive contact
is modelled as at least as likely to work, at essentially the same near-zero
cost as a gentle one. Left as an honest, visible result rather than patched
by hand-tuning one row -- a candidate for Phase 3, which is also where the
Brain would need to weigh relationship cost at all.

> **Computed and ranked as shipped in Phase 2.** Phase 3, directly below,
> is what wires a chosen action into the Brain and fixes
> `engine/consolidate.py` / `engine/buyer_panel.py`'s two `Action.kind`
> consumers for the day `payment_plan`/`counter_settle` actually appear.

---

## Wiring the EV ranking into the Brain (Phase 3)

**The problem:** Phase 2's ranking sat inert -- `engine/brain.py` never
imported it, and every decision still ended in one unconditional `SEND` at
whatever rung the escalation walk picked.

**What Phase 3 does:** once every hard stopping rule and rung gate has
cleared -- opt-out, dispute, settlement, not-yet-due, max-contacts, an
active promise, the legal ceiling, rung exhaustion, weekends, message
spacing, all unchanged and all still running first -- `engine/brain.py`'s
`decide()` can replace its unconditional send with an EV-informed choice,
behind a new config flag:

```yaml
brain:
  ev_mode: off   # shipped default: decide() is byte-for-byte unchanged
```

With it off, nothing about the agent's behaviour changes -- proven by a
snapshot-diff regression test pinning the seed-42 headline numbers from
immediately before this phase, not just "tests still pass." With it on, and
only for a caller that supplies a two-axis score (a `quadrant` key --
`main.py` and `sim/scenario_tc141.py` still pass a plain score and are
unaffected either way), `decide()` ranks a candidate list built from two
independent gates -- the same two-gate shape the ladder itself already
uses:

- **What is ever appropriate for this buyer's profile:** a new
  `config/rules.yaml` `negotiation.eligible_actions` table, one list per
  quadrant. This is the fix for the `good_customer` finding Phase 2's report
  flagged: `good_customer`/`cash_flow_problem` never see `legal_facts`/
  `legal_escalation`/`counter_settle` as candidates at all, and
  `can_pay_but_wont`/`high_risk` never see `payment_plan`. `wait` is
  eligible everywhere.
- **What is reachable today:** `human_handoff`/`legal_escalation` are
  dropped from this candidate list -- the one used for choosing the general
  action -- unless the escalation walk's own `chosen` rung has ALREADY
  reached rung 4, the *identical* condition the non-EV rung-4 step already
  uses, deliberately **not** "is the legal ceiling open." Those two are not
  the same thing: the ceiling opening only means the law would *permit*
  rung 4 today, not that this invoice's own contact history has organically
  escalated there. A first-ever contact, for instance, can have a wide-open
  ceiling while `chosen` sits at rung 1 or 2, because `decide()`'s backlog
  formula for a first contact never desires more than `base + 1`. Gating on
  the ceiling alone (an earlier version of this gate did exactly that,
  caught on review) would have let EV send that case to a human handoff
  sooner than the ordinary escalation walk ever would have. Gating on
  `chosen` instead means those two actions are only candidates, for choosing
  the *general* action, once the non-EV rung-4 step's own condition already
  holds -- and since that step intercepts unconditionally before the general
  choice ever runs, whenever it's true, `human_handoff`/`legal_escalation`
  end up permanently excluded from *that* choice, by construction: EV may
  choose a different *kind* of action among what is already reachable
  today, never make *more* reachable than the existing walk already allows.
  Proven with two dedicated tests: a `high_risk` buyer whose unrestricted
  top action is `legal_escalation` falls back to the next-eligible candidate
  both when the legal ceiling is closed, and -- the sharper case -- when the
  ceiling is wide open but a first-ever contact means `chosen` hasn't gotten
  there yet either way.

The winner maps onto `Action`: `wait` stays a `wait`; `soft_nudge`/`firm`/
`legal_facts` stay a `send` at the already-chosen rung, unchanged;
`payment_plan`/`counter_settle` are two genuinely new `Action.kind` values,
buyer-facing sends at the already-chosen rung. Every mapped action carries
the EV reasoning in its audit detail. `engine/writer.py` is untouched, so
`soft_nudge`/`firm`/`legal_facts` all still draft through the identical
rung-based skeleton -- choosing one over another changes the stated
reasoning in the audit trail, not what the buyer reads. That is also why the
residual half of the `good_customer` finding (`firm` still edges out
`soft_nudge` on raw probability) is left alone rather than patched: with no
message-content difference between them yet, it has no effect on what is
actually sent.

**Where `human_handoff`/`legal_escalation` actually get to matter.** The
general-action choice above can never select them, but a handoff is very
often reached directly -- from real per-invoice history, not through that
choice at all. Once `chosen >= HANDOFF_RUNG` is already true -- a handoff
will happen regardless, `ev_mode` or not -- the *existing, unchanged*
rung-4 step additionally asks, only with `ev_mode: on` and a quadrant
present, *which flavor* the audit trail should record: it intersects
`human_handoff`/`legal_escalation` with whatever `eligible_actions[quadrant]`
offers (`cash_flow_problem` offers only `human_handoff`;
`can_pay_but_wont`/`high_risk` offer both; `good_customer` offers neither
and falls straight through to the exact same plain, undifferentiated
`HANDOFF` it always produced) and ranks the survivors by EV. This never
changes *whether* a handoff fires, the rung, the reason text, or the
Samadhaan draft -- only whether the audit detail additionally distinguishes
"handed to a human" from "flagged for legal escalation."

> **PHASE 3 SCOPE.** No message-content differentiation by action. No new
> persona reaction behaviour. No reactive "buyer proposed a settlement,
> evaluate accepting it" path -- `rank_actions()` takes buyer-profile
> inputs, not buyer-reply text. No CLI flag or third experiment arm for
> `ev_mode` -- a config-file switch only, for now.

---

## Buyer-level message consolidation (W3)

**The problem:** a buyer with five overdue invoices shouldn't necessarily
get five separate emails on the same day.

**What W3 does:** `engine/consolidate.py` groups a day's already-decided
SEND actions for one buyer into rung tiers -- courtesy (rung ≤ 1) and
escalated (rung ≥ 2), never mixed in one envelope, since a rung-1 message
carries no legal content and an escalated one does. A buyer gets at most
two envelopes on a given day instead of one per invoice. A tier with more
than `config/rules.yaml`'s `consolidation.max_invoices_per_message` (6)
invoices for one buyer splits into multiple envelopes rather than one
oversized draft.

`engine.brain.decide()` and its per-invoice stopping rules (max 3 messages
per rung, max 5 total, quiet hours, opt-out) are completely unchanged --
consolidation only changes how many envelopes carry those decisions, never
how a single invoice is escalated.

**Disputed invoices are excluded from consolidation.** A dispute triggers
an immediate human handoff before the Brain ever chooses a SEND action, so
a disputed invoice is never eligible to enter a bundle in the first place.

On seed 42, this dropped 141 invoice-level contacts to 73 outbound
envelopes with zero change to which invoices got contacted, on which days,
or to any final recovery number -- see `docs/edge_cases.md`'s TC-070 for
the full test evidence.

---

## Scope (deliberate)

- Email is really sent, and only to the owner's own test inbox. **No real
  person is ever contacted.**
- WhatsApp and SMS log "would send". The WhatsApp Business API needs
  business verification, which does not fit in a 12-day build.
- All data is synthetic and generated from a seed, so any run is
  reproducible.

---

## Edge cases

`docs/edge_cases.md` documents 141 edge cases the agent could encounter,
each honestly marked:

- **60 TESTED** -- has a passing test, named.
- **44 HANDLED** -- correct in the code, no dedicated test, named.
- **37 OUT OF SCOPE** -- neither, with the specific integration or data it
  would need, named -- never left vague.

---

## Honest scope: built vs. future work

**CURRENTLY BUILT:**

- The original MVP: data factory, score engine, watchdog, law engine
  (MSMED Act interest/tax math), brain, message writer (English/Hinglish),
  channels (real email, stubbed WhatsApp/SMS), promise tracker, simulator,
  and report.
- **E1-E4** -- edge-case hardening: promise sanity bounds, invoice
  validation, regression tests, and an end-to-end scripted scenario
  (TC-141).
- **W1-W4** -- early warning, buyer panel + promise reliability,
  buyer-level message consolidation, and a refreshed 6-seed experiment (all
  described above).
- **Phase 1-3** -- the ability/willingness two-axis score + quadrant,
  recovery probability + expected value per candidate action, and wiring
  that ranking into the Brain's `decide()` (all described above). The
  wiring ships behind `config/rules.yaml`'s `brain.ev_mode`, off by
  default, so the seeded demo result is unaffected unless it is switched
  on; even on, no message content yet differs by the chosen action -- see
  the Phase 3 section's own scope note.

**FUTURE WORK:** see the list below, and `docs/winning_layer.md` for the
larger roadmap (predictive risk, cash-flow intelligence, payment
propensity, and more) that needs real transaction data this standalone
project doesn't have.

### Future Work

Ideas that came up during the build and were deliberately **not** built:

- Real WhatsApp Business API channel
- Voice calls with Hinglish TTS
- Live RBI bank-rate feed instead of a config value
- Tally / Zoho invoice import
- TReDS invoice-discounting suggestion for stuck invoices
- Network-level buyer score across many vendors (the Razorpay-scale
  version -- see "Where this goes next" below)
- Dispute-resolution assistant
- Financial-year seasonality in the synthetic data: a visible cluster of
  buyers settling just before March 31, so the Section 43B(h) tax-deduction
  cliff can be shown landing rather than asserted. Parked on Day 2 because
  the simulation window (starts 2026-08-24, runs 120 days) never crosses
  March 31 -- revisit if the window changes.
- `draft_message` and `judgment_call` run on a Flash-tier Gemini model
  rather than Pro, because this key's free tier has zero pro-tier quota
  and billing isn't available for it -- a known quality-vs-cost tradeoff,
  not a design choice.
- Simulator reply lag: a persona's reaction lands the same simulated day a
  message is sent. A real buyer takes a day or three, and the "days to
  pay" numbers above would mean more with that modelled.
- Simulator fallback-message penalty: when the writer's guardrail rejects
  a draft and falls back to the plain skeleton, the persona reacts
  identically to a full LLM-drafted message today. A small penalty on the
  fallback path would give the guardrail work a measurable effect on
  outcomes, not just on audit-trail honesty.
- Simulator partial-payment realism: every `pay_partial` reaction is
  tagged as an unexplained, ambiguous reply (to exercise the brain's one
  LLM judgment-call path) rather than sometimes arriving with a normal
  explanation. Splitting some partial payments into a clean "partial,
  explained" case would stop that path from over-firing on every partial
  payment in the simulated world.
- Ablation experiment: a third arm with the baseline's fixed 3-message
  schedule but score-aware timing and no legal/tax content, to isolate how
  much of the agent's win over the baseline comes from smarter timing
  versus the law engine's leverage. The most direct answer to "how much of
  this is really the legal argument" a skeptical judge could ask -- not
  built because it's a third full pipeline variant, not a report tweak.
- The consolidated message's subject line doesn't total the ₹ amount
  across bundled invoices yet -- deferred from W3 to W4, and still not
  built.
- A reply that arrives during an LLM outage (`engine.promises.parse_reply`
  catching `LLMError`) is safe -- nothing is fabricated -- but is silently
  recorded as noise, so a genuine promise or dispute said while the model
  was down is lost rather than merely delayed. Found while building the E1
  promise sanity bounds (TC-135, `docs/edge_cases.md`); out of scope for
  that round because retrying or queuing the reply for a later pass is a
  different kind of fix from a rule-based sanity bound.

---

## Where this goes next

`docs/winning_layer.md` is the full roadmap beyond W1-W4: a dynamic trader
financial profile, cash-flow intelligence, payment propensity prediction,
next-best-action beyond the current rung ladder, expected-recovery/cost
optimization, a strategy simulator, and closed-loop learning at a deeper
level than promise tracking. None of it is built -- it's explicitly future
work, not a claim of current capability.

The honest reason it isn't built here: most of it needs real
transaction/payment data that a standalone tool like this one structurally
cannot see. This prototype demonstrates the *intelligence layer* -- the
scoring, the legal-leverage math, the escalation logic, the closed loop
between promises and score. Razorpay's payment rails and ecosystem access
could, subject to appropriate permissions, privacy, and compliance,
potentially supply what a standalone tool can't: buyer-side inflows,
cross-supplier payment behavior, a network-level buyer score built from
many vendors' data at once (the concrete example above). To be clear: this
project does not have access to Razorpay's private transaction data today
-- this is a description of the opportunity, not a claim about what this
prototype already does.

---

## Legal disclaimer

The legal calculations here are **simplified for a demonstration, current
as of Aug 2026, and are not legal advice.** All figures live in
`config/legal.yaml` and should be verified against the current RBI bank
rate and the prevailing text of the MSMED Act 2006 and the Income Tax Act
before being relied on.
