# Revenue Recovery Agent — Winning Layer Enhancement Plan

## Purpose

This document defines the enhancement layer to be added **after the original 12-day MVP is fully completed and stable**.

The goal is not to replace the existing Revenue Recovery Agent. The goal is to extend it from a primarily reactive overdue-payment recovery system into a more intelligent **predictive, cash-flow-aware, next-best-action recovery system**.

The existing MVP remains the foundation.

The Winning Layer adds:

1. Dynamic Trader Financial Profile
2. Early Payment Risk Prediction
3. Cash-Flow Intelligence
4. Payment Propensity Prediction
5. Next-Best Recovery Action
6. Expected Recovery / Recovery Cost Intelligence
7. Promise Reliability Intelligence
8. Recovery Explanation
9. Recovery Strategy Simulation
10. Closed-Loop Learning from Payment Outcomes

The recommended implementation priority is deliberately limited. The strongest version should be built around the first five core capabilities rather than adding many disconnected features.

---

## Implementation Status (added Phase 10)

This document was written before any Winning Layer work started, and reads
end to end as a forward-looking plan. Eight slices of it have since actually
been built: four under the project's own W1-W4 labels, and four more under
this project's separate "Phase 1-4" negotiation-model track (ability/
willingness scoring, recovery-probability + EV ranking, wiring that ranking
into the Brain, and persona differentiation + an EV ablation) -- see
CLAUDE.md's Current status for both. **Neither label set maps 1:1 onto this
document's own Enhancement/Phase numbering above** -- they were renamed,
reordered, and in most cases built as a deliberately partial first slice
rather than the full capability originally planned here. The PARTIALLY BUILT
paragraphs below say exactly how much of each Enhancement that first slice
covers; anything not named in this section at all is genuinely untouched.

**CURRENTLY BUILT:**

- **W1 -- Early Warning**: rule-based pre-overdue risk surfacing
  (`engine/watchdog.py::early_warnings()`), a low/watch/high band on
  invoices approaching their due date. Human-facing only (report/buyer
  panel) -- no pre-due message is ever sent to a buyer.
- **W2 -- Buyer/Trader-Level Panel + Promise Reliability**: a per-buyer
  rollup (`engine/buyer_panel.py`) of outstanding amount, overdue count,
  oldest overdue, score/confidence/trend, promise reliability %, response
  rate, and recovery state.
- **W3 -- Buyer-Level Message Consolidation**: `engine/consolidate.py`
  groups a day's already-decided SEND actions by buyer into rung tiers, so
  one buyer gets at most two envelopes/day instead of one per invoice.
- **W4 -- Experiment Refresh**: re-ran the full 6-seed baseline-vs-agent
  comparison at HEAD and added per-seed edge-case-count transparency to the
  multi-seed report table.
- **Phase 1 -- Ability/Willingness Split**: `engine/ability_willingness.py`
  scores "can they pay?" and "will they pay?" separately and places the
  buyer in a four-way quadrant (`good_customer`, `cash_flow_problem`,
  `can_pay_but_wont`, `high_risk`). A **first slice** of Cash-Flow
  Intelligence (Enhancement 3) -- see the honest scoping below.
- **Phase 2 -- Recovery Probability + Expected Value**:
  `engine/negotiation.py` scores a fixed set of candidate recovery actions
  (`wait`, `soft_nudge`, `firm`, `legal_facts`, plus the new `payment_plan`
  and `counter_settle`, plus `human_handoff` and `legal_escalation`) by
  `EV = P(recover) x expected_recovery_paise - cost_paise`, ranked best
  first. A **first slice** of Next-Best Recovery Action (Enhancement 6) and
  Expected Recovery and Cost (Enhancement 7) -- reasoning only, see the
  honest scoping below.
- **Phase 3 -- Wiring the EV Ranking into the Brain**: `engine/brain.py`'s
  `decide()` can replace its unconditional "send at the chosen rung" with an
  EV-informed choice among Phase 2's ranking, narrowed by a new
  `config/rules.yaml` `negotiation.eligible_actions` table (what is ever
  appropriate for this buyer's quadrant) and by whether the escalation
  walk's own chosen rung has already reached rung 4 (what is reachable
  today for a handoff specifically -- deliberately not just whether the
  legal ceiling is open; see the honest scoping below for why that
  distinction mattered). Separately, once a handoff is already certain via
  the existing rung-4 step, EV can choose WHICH FLAVOR -- `human_handoff` or
  `legal_escalation` -- gets recorded, without ever changing whether the
  handoff itself fires. Behind a config flag, `brain.ev_mode`, shipped
  **off** by default.
- **Phase 4 -- Persona Differentiation + the EV Ablation**: `sim/personas.py`'s
  `react()` gained an `action_kind` parameter so a buyer correlated with
  genuine cash-flow constraint (`cash_tight`) actually engages more when
  offered a `payment_plan`, and one correlated with unwillingness
  (`habitual_delayer`) lowballs a `counter_settle` offer rather than
  accepting it in full -- reusing existing promise mechanics, not a new
  outcome category. `run_agent(..., ev_mode=True)` is the long-deferred
  third experiment arm this makes meaningful: measured across the same
  6-seed comparison, agent+EV beats the plain agent on rupees recovered in
  **5/6** seeds -- reported honestly, loss included.

**PARTIALLY BUILT -- Cash-Flow Intelligence (Enhancement 3):** Phase 1 built
the *reasoning* half and deliberately not the *data* half. What exists: a
real inflow-trend calculation, a volatility measure, a failed-payment
signal, an invoice-size-against-typical-month ratio, a 0-100 ability score
with full breakdown, and a plain-English explanation -- all rule-based, all
config-driven, all tested. What does **not** exist: any real transaction
feed. The inflow series is *synthetic*, generated per buyer by
`data/generate.py` and correlated with the simulator's hidden persona. Point
a real payment/banking feed at `monthly_inflow_paise` and the scoring above
works unchanged; until then this is a demonstration of the reasoning on
plausible fake data, not a cash-flow product. Two further caveats worth
stating: as shipped in Phase 1, nothing *acted* on the score yet
(`engine/brain.py` did not read it -- that took until Phase 3, and even then
only behind `brain.ev_mode`, off by default), and there is still no
probability *model* anywhere -- `P(recover)` (Phase 2) is a stated
assumption grid, not a learned one -- so Payment Propensity Prediction
(Enhancement 4) remains unbuilt.

**PARTIALLY BUILT -- Next-Best Recovery Action (Enhancement 6) and Expected
Recovery and Cost (Enhancement 7):** Phase 2 built the *ranking* half; Phase 3
built the *acting* half, but shipped it switched off. What exists as of
Phase 3: everything Phase 2 built, plus `engine/brain.py`'s `decide()`
choosing an action by EV -- among a `config/rules.yaml`
`negotiation.eligible_actions` table that withholds legal pressure from
`good_customer`/`cash_flow_problem` and a payment plan from
`can_pay_but_wont`/`high_risk`, and further narrowed to whether a handoff is
reachable TODAY -- behind `config/rules.yaml`'s `brain.ev_mode` flag, shipped
`off`. With it off (the default the seeded demo runs with), `decide()` is
byte-for-byte what it was before this phase.

**A correction made during this phase's own review, worth stating rather
than quietly fixing:** the handoff-reachability gate was first written as
"the legal ceiling is at rung 4" (`available_rung == 4`). That is NOT the
same condition `decide()`'s existing non-EV rung-4 step uses, and is
strictly more permissive -- the ceiling opening means the law would *permit*
rung 4 today, not that this invoice's own contact history has organically
escalated there (a broken-promise jump, a rung fully exhausted, enough
elapsed time at the top rung already used). A first-ever contact can have a
wide-open ceiling while the escalation walk's own `chosen` rung sits at 1 or
2, because the backlog formula for a first contact never desires more than
`base + 1`. Left as originally written, EV mode could have sent such a case
straight to a human handoff sooner than the ordinary escalation walk ever
would have. The fix gates on `chosen` reaching `HANDOFF_RUNG` instead -- the
identical condition the non-EV step already uses -- which, because that step
intercepts and returns a handoff unconditionally whenever it's true, before
the general-action choice ever runs, means `human_handoff`/`legal_escalation`
can never be selected as the *general* action. That is the correct,
conservative behaviour: EV may choose a different *kind* of action among
what the existing walk already makes reachable, never make *more* reachable
than it already does.

**The other half, added in a follow-up so those two actions are not simply
dead weight in the config:** a handoff is very often reached directly, from
real per-invoice history, with no general-action choice ever running at
all. Once `chosen >= HANDOFF_RUNG` is already true -- a handoff will happen
regardless, `ev_mode` or not -- the existing, unchanged rung-4 step now also
asks, only with `ev_mode: on` and a quadrant present, *which flavor* the
audit trail should record, by intersecting `human_handoff`/`legal_escalation`
with whatever `eligible_actions[quadrant]` offers and ranking the survivors
by EV. `good_customer` offers neither and falls straight through to the
same plain, undifferentiated `HANDOFF` it always produced -- no fallback
action is invented. This never changes *whether* a handoff fires, the rung,
the reason text, or the Samadhaan draft; only whether the audit detail
distinguishes "handed to a human" from "flagged for legal escalation."

What does **not** exist even with the flag on: any
message-content differentiation by the chosen action -- `soft_nudge`,
`firm` and `legal_facts` all still draft through the identical rung-based
skeleton (`engine/writer.py` untouched), so choosing one over another only
changes the audit trail's stated reasoning, not what the buyer reads; that,
and any reactive "buyer proposed a settlement, evaluate it" path, are UNBUILT.
There is also still no probability *model* underneath `P(recover)` -- it
remains a flat, stated assumption grid, the same honesty as Enhancement 3's
synthetic data caveat, not a measured recovery rate. The `good_customer`
relationship-cost finding Phase 2 surfaced is addressed by the
`eligible_actions` gate above, not by a new EV term -- `firm` still edges out
`soft_nudge` on raw probability for a `good_customer`, but since both map to
the identical `send` at the identical already-chosen rung in this phase,
that residual has no effect on what is actually sent.

**PARTIALLY BUILT -- Phase 4, the missing measurement:** Phase 3 shipped the
wiring inert (`ev_mode: off`); Phase 4 is what actually measured its effect.
`sim/personas.py::react()` now reacts differently to a `payment_plan`
(`cash_tight`, the `cash_flow_problem` persona, promises 20-27 points more
often -- a real, 1000-trial-verified effect) and to a `counter_settle`
(`habitual_delayer` lowballs via the existing partial-promise fixture rather
than accepting in full). **A finding worth stating plainly:** `counter_settle`
itself never actually gets chosen by a live `decide()` ranking under the
shipped `recovery_probability`/`recovery_fraction` grid -- `legal_facts`'s
100% recovery fraction beats it at every outstanding amount and
broken-promise count for `can_pay_but_wont`. Its persona behaviour is real
and tested, but currently inert in practice, the same "visible, not hidden"
treatment Phase 2 gave the `good_customer` finding. `run_agent(seed, days,
ev_mode=True)` and a `results.json` `agent_ev` section are the third
experiment arm this differentiation makes meaningful: on the identical
6-seed comparison, agent+EV beats plain agent on rupees recovered in **5/6**
seeds (seed 2024 is the one loss), and the gain traces almost entirely to
`payment_plan` -- every other EV-driven relabeling (`firm` vs. `soft_nudge`,
`human_handoff` vs. `legal_escalation`) maps to an identical `Action.kind`/
rung/skeleton either way, so it cannot itself move simulated behaviour.

**FUTURE / UNBUILT** (everything else in this document): Enhancement 1
*as originally scoped* -- a single unified "Dynamic Trader Financial
Profile" object -- was never built as its own thing, but several of the
dimensions it envisioned now exist in scattered form: cash-flow health
(Phase 1's ability axis), promise reliability (W2's buyer panel), and a
recovery-worthiness view (the quadrant + EV ranking). What's still genuinely
absent is any of it unified into one profile object or endpoint. Enhancements
4, 8-12 (Payment Propensity Prediction, Recovery Explanation beyond the
existing audit trail, Recovery Strategy Simulation, Closed-Loop Learning),
and the UI Screens 1-5 remain fully unbuilt with no code behind them -- no
payment-propensity score, no dashboard, no what-if simulator. They require
either real transaction/payment data this standalone project doesn't have,
or are explicitly out of the 12-day build's scope (dashboards, ML models --
see CLAUDE.md's scope guard).

**Reconciliation -- actual W-label vs. this document's original plan:**

| Actually built | Closest match in this document's original numbering |
|---|---|
| W1 Early Warning (low/watch/high band, pre-due-date) | A narrower slice of Enhancement 2 (Early Payment Risk Prediction) -- no probability score, just a fixed band |
| W2 Buyer panel + promise reliability | Enhancement 5 (Promise Reliability) + Enhancement 13 (Trader-Level Recovery Intelligence) |
| W3 Buyer-level message consolidation | Enhancement 14 / "Buyer-Level Communication Awareness" -- this document's own lowest-priority item (see Section 27, Priority Ranking, below) |
| W4 Experiment refresh + edge-case transparency | Not one of this document's original Enhancements -- an experiment-honesty hardening pass, not a new product capability |
| Phase 1 Ability/willingness split + quadrant | A first slice of Enhancement 3 (Cash-Flow Intelligence) on synthetic inflow data -- the reasoning, not the data feed. Not Enhancement 4: there is no probability model. |
| Phase 2 Recovery probability + EV ranking | A first slice of Enhancement 6 (Next-Best Recovery Action) + Enhancement 7 (Expected Recovery and Cost) -- the ranking, not the acting. `P(recover)` is a stated assumption grid, not a measured or learned probability. |
| Phase 3 Wiring EV into the Brain | The rest of that same slice of Enhancement 6/7 -- the acting, behind `brain.ev_mode`, shipped off. No message-content differentiation by action (that needs `engine/writer.py`), no reactive settlement-offer handling. |
| Phase 4 Persona differentiation + the EV ablation | Measures what Phase 3 wired: `payment_plan`/`counter_settle` persona reactions, and a third `run_agent(ev_mode=True)` experiment arm. Still no message-content differentiation and no reactive settlement handling. `counter_settle`'s own effect is unmeasurable in practice -- it never wins a live EV ranking under the shipped grid. |

---

# 1. Starting Point After Day 12

The original MVP should already contain the following working flow:

```text
Invoice / Buyer Data
        |
        v
Watchdog
        |
        v
Trader / Buyer Score
        |
        v
Law Engine
        |
        v
Brain
        |
        v
Message Writer
        |
        v
Communication
        |
        v
Buyer Response
        |
        v
Promise Tracker
        |
        v
Escalation / Human Handoff
        |
        v
Payment Outcome
        |
        v
Audit Trail + Report
```

The Winning Layer should be placed around this flow rather than rebuilding it.

---

# 2. New Product Positioning

## Current MVP Positioning

The current system primarily answers:

> "This invoice is overdue. What should we do now?"

## Winning Layer Positioning

The enhanced system should answer:

> "Which trader is likely to delay payment, why is the payment likely to be delayed, and what is the most effective and least expensive action we can take to recover the money?"

The complete product story becomes:

```text
PREDICT
    |
    v
UNDERSTAND
    |
    v
PRIORITIZE
    |
    v
CHOOSE NEXT-BEST ACTION
    |
    v
RECOVER
    |
    v
LEARN FROM OUTCOME
```

---

# 3. Core Winning Architecture

```text
                    TRANSACTION DATA
                           |
                    INVOICE DATA
                           |
                    PAYMENT HISTORY
                           |
                           v
                +----------------------+
                | TRADER PROFILE       |
                |                      |
                | Payment Score        |
                | Cash-Flow Health     |
                | Promise Reliability  |
                | Payment History      |
                | Recent Activity      |
                +----------+-----------+
                           |
                           v
                +----------------------+
                | PAYMENT RISK ENGINE  |
                |                      |
                | Will payment be late?|
                | How likely is delay? |
                +----------+-----------+
                           |
              +------------+------------+
              |                         |
              v                         v
       BEFORE DEFAULT             AFTER DEFAULT
              |                         |
              v                         v
       EARLY WARNING               WATCHDOG
              |                         |
              +------------+------------+
                           |
                           v
                +----------------------+
                | RECOVERY BRAIN       |
                |                      |
                | Next Best Action     |
                +----------+-----------+
                           |
       +-------------------+-------------------+
       |                   |                   |
       v                   v                   v
   Message             Promise             Human
    Writer              Tracker           Handoff
       |                   |                   |
       +-------------------+-------------------+
                           |
                           v
                    BUYER RESPONSE
                           |
                           v
                    PAYMENT OUTCOME
                           |
                           v
                UPDATE TRADER PROFILE
                           |
                           v
                    LEARNING LOOP
```

---

# 4. Enhancement 1 — Dynamic Trader Financial Profile

## Objective

Upgrade the existing trader score into a broader financial and behavioral profile.

Instead of showing only:

```text
Trader Score = 78
```

the system should be able to present multiple dimensions.

Example:

```text
ABC Traders

Recovery Score       78/100
Payment Reliability  84/100
Cash-Flow Health     62/100
Promise Reliability  72/100
Recent Activity      48/100
Recovery Risk        76/100
```

## Why This Is Important

A single score hides the reason behind the score.

Two traders can have the same score but very different situations.

Example:

```text
Trader A
High payment reliability
Temporary cash-flow decline

Trader B
Normal cash flow
Repeated broken promises
```

Both may have the same overall recovery risk, but their recovery situations are different.

## Data Used

Potential inputs:

```text
Transaction history
Invoice history
Payment history
Days-to-pay history
Overdue history
Promise history
Broken promises
Partial payments
Recent transaction volume
Recent inflow/outflow trends
Disputes
Communication responses
```

## Output

```json
{
  "trader_id": "TRADER_001",
  "recovery_score": 78,
  "payment_reliability": 84,
  "cash_flow_health": 62,
  "promise_reliability": 72,
  "recent_activity": 48,
  "recovery_risk": 76
}
```

## Demo Value

The judge should immediately understand:

> "This system knows the trader, not just the invoice."

---

# 5. Enhancement 2 — Early Payment Risk Prediction

## Objective

Move the system from reactive recovery to proactive recovery.

Current behavior:

```text
Invoice becomes overdue
        |
        v
Recovery starts
```

New behavior:

```text
Invoice approaching due date
        |
        v
Trader behavior analyzed
        |
        v
Payment risk predicted
        |
        v
Early warning
```

## Example

```text
Invoice:
₹5,00,000

Due in:
9 days

Predicted delayed-payment risk:
78%

Risk level:
HIGH
```

## Supporting Signals

Example signals:

```text
Recent transaction volume declining
Historical payment delays increasing
Recent inflows declining
Promise reliability decreasing
Previous overdue invoices increasing
Payment activity changing
```

## Important Product Difference

The system is no longer waiting for:

> "The customer already failed."

It is identifying:

> "This customer is showing signs that they may fail."

## Demo

Show two invoices:

```text
Invoice A
Due in 10 days
Risk = 18%

Invoice B
Due in 10 days
Risk = 78%
```

Then explain why the second one is higher risk.

---

# 6. Enhancement 3 — Cash-Flow Intelligence

## Objective

Understand whether a payment delay may be associated with a change in the trader's financial activity.

Example:

```text
ABC Traders

Last 30 Days
Money Received: ₹8.2L
Money Paid:     ₹6.7L
Net Flow:       +₹1.5L

Last 7 Days
Money Received: ₹0.4L
Money Paid:     ₹2.1L
Net Flow:       -₹1.7L

Cash-Flow Trend:
Deteriorating
```

## Why This Matters

Two traders can both be 30 days overdue.

### Trader A

```text
Strong recent inflows
Good payment history
No disputes
Usually keeps promises
```

### Trader B

```text
Recent inflows declining
Payment delays increasing
Broken promises increasing
Transaction activity declining
```

The system should understand that the situations are different.

## Important Concept

The feature is not intended to make a definitive statement about the trader's financial condition.

It should provide a **behavioral cash-flow signal based on available transaction data**.

Example:

```text
Recent cash-flow signal:
Deteriorating

Confidence:
High

Evidence:
Recent inflows declined 38%
```

---

# 7. Enhancement 4 — Payment Propensity

## Objective

Predict the likelihood that an invoice will be paid within different future periods.

Example:

```text
Invoice #204
Outstanding: ₹4,80,000

Probability of payment:

Within 7 days     73%
Within 14 days    86%
Within 30 days    94%
```

## Why This Matters

The current system primarily knows:

```text
paid
or
unpaid
```

Payment propensity adds:

```text
How likely is payment soon?
```

This helps the Recovery Brain decide how aggressively the situation needs attention.

## Example Factors

```text
Historical days-to-pay
Trader score
Recent transaction activity
Cash-flow trend
Promise reliability
Invoice age
Previous recovery response
```

---

# 8. Enhancement 5 — Promise Reliability

The current Promise Tracker should become a long-term behavioral signal.

Example:

```text
Promise Reliability

Promises made:       12
Promises fulfilled:   8
Promises broken:      4

Reliability:
67%

Average promise delay:
3.8 days
```

## Why This Is Powerful

Suppose two buyers say:

> "I'll pay Friday."

Trader A:

```text
Promise reliability = 94%
```

Trader B:

```text
Promise reliability = 31%
```

The same sentence has different historical context.

## Profile Update

Every completed promise becomes new trader data.

```text
Promise made
     |
     v
Promise outcome
     |
     v
Trader profile updated
```

This creates the first important feedback loop.

---

# 9. Enhancement 6 — Next-Best Recovery Action

This should become the central feature of the Winning Layer.

Instead of:

```text
Invoice overdue
    |
    v
Send reminder
```

the system evaluates the current situation and produces:

```text
NEXT BEST ACTION
```

Example:

```text
Invoice #204
Outstanding: ₹5,00,000
Overdue: 17 days

Recommended action:
Soft payment reminder

Reason:
- Buyer historically responds to reminders
- No active dispute
- Recent cash-flow pressure detected
- Promise reliability is high
- Legal escalation is not currently necessary
```

Another buyer could produce:

```text
NEXT BEST ACTION

Human review

Reason:
- High-value invoice
- Multiple broken promises
- Active dispute
- Low confidence in automated recovery
```

## Key Idea

The Brain is no longer only selecting an escalation rung.

It is selecting the **next best recovery action based on the complete trader and invoice context**.

---

# 10. Recovery Action Types

The system can conceptually evaluate actions such as:

```text
WAIT
SOFT REMINDER
FIRM REMINDER
PAYMENT-LINK MESSAGE
PROMISE FOLLOW-UP
PARTIAL-PAYMENT DISCUSSION
LEGAL-FACT MESSAGE
HUMAN REVIEW
STOP AUTOMATED RECOVERY
```

The exact implementation policy remains part of the Brain design.

The important enhancement is that the Brain can compare the current context before selecting the next action.

---

# 11. Enhancement 7 — Expected Recovery and Cost

## Objective

Don't optimize only for:

> "Recover the money."

Optimize for:

> "Recover the money efficiently."

Example:

```text
Invoice:
₹50,000

Option A
Email

Expected recovery:
₹32,000

Estimated communication cost:
Low
```

Another option:

```text
Option B
Human intervention

Expected recovery:
₹44,000

Expected operational cost:
Higher
```

The system can compare recovery outcomes against the cost/effort of different actions.

## Product Message

> **Recover more while spending less to recover it.**

This gives the project an economic optimization layer rather than just an automation layer.

---

# 12. Enhancement 8 — Recovery Diagnosis

Add a section:

```text
WHY IS THIS PAYMENT DELAYED?
```

Example:

```text
Likely recovery diagnosis:

Temporary cash-flow pressure
Confidence: 81%

Observed signals:
- Recent inflows declined
- Historical payment behavior was reliable
- Buyer responded to previous reminders
- No active dispute detected
```

Another trader:

```text
Likely recovery diagnosis:

Chronic payment delay behavior
Confidence: 87%

Observed signals:
- Repeated overdue invoices
- Multiple broken promises
- Normal recent transaction activity
- Low response rate
```

## Why This Matters

The agent should not only know:

> "What happened?"

It should help answer:

> "What appears to be happening?"

---

# 13. Enhancement 9 — Recovery Strategy Simulator

This is a strong hackathon demonstration feature.

Create a simulation interface where the user can compare strategies.

Example:

```text
Invoice Portfolio: 100 invoices

Strategy A:
Basic reminders

Strategy B:
Current Revenue Recovery Agent

Strategy C:
Predictive + Cash-Flow-Aware Agent
```

Results:

```text
                    Strategy A    Strategy B    Strategy C

Recovered           ₹X            ₹Y            ₹Z
Avg Days-to-Pay     X             Y             Z
Messages            X             Y             Z
Human Handoffs      X             Y             Z
Recovery Cost       X             Y             Z
```

## Why This Is Important

The judge can visually see the difference between:

```text
Basic automation
        vs
Intelligent recovery
```

---

# 14. Enhancement 10 — Recovery "What-If" Simulation

A stronger version of the simulator can answer:

> "What happens if we use a different recovery strategy?"

Example:

```text
Current strategy

Expected recovery:
₹72L

Average recovery time:
31 days
```

Alternative:

```text
Cash-flow-aware strategy

Expected recovery:
₹79L

Average recovery time:
24 days
```

Possible result:

```text
+₹7L expected recovery
-7 days average recovery time
-522 communications
```

This should be presented as a **simulation/estimate**, not as a guaranteed real-world result.

---

# 15. Enhancement 11 — Explainable Recovery Decisions

Every important decision should be explainable.

Example:

```text
WHY DID THE AGENT CHOOSE THIS ACTION?

Action:
Soft reminder

Signals:
✓ Payment reliability: 84/100
✓ Promise reliability: 82%
✓ No dispute detected
✓ Recent response activity: high
✓ Cash-flow pressure: moderate
✓ Invoice value: ₹2.5L
```

The judge should never see:

```text
AI decided this.
```

Instead:

```text
AI selected this because...
```

---

# 16. Enhancement 12 — Closed-Loop Learning

The trader profile should change after actual outcomes.

Example:

```text
Before:
Promise reliability = 72%

Buyer promises payment
        |
        v
Payment received on time
        |
        v
Profile updated
        |
        v
Promise reliability = 76%
```

Another case:

```text
Buyer promises payment
        |
        v
Promise broken
        |
        v
Profile updated
        |
        v
Promise reliability decreases
```

This makes the system dynamic instead of static.

---

# 17. Enhancement 13 — Trader-Level Recovery Intelligence

The system should operate at two levels.

## Invoice Level

```text
Invoice #204
₹5L
17 days overdue
```

## Trader Level

```text
ABC Traders
Total outstanding:
₹12.4L

Overdue invoices:
4

Recovery score:
78

Promise reliability:
72%

Cash-flow trend:
Deteriorating
```

This distinction is important because a business may have multiple invoices at the same time.

---

# 18. Enhancement 14 — Buyer-Level Communication Awareness

If one trader has five overdue invoices, the system should understand that five separate invoice reminders can become excessive.

Example:

```text
ABC Traders

Invoice A → overdue
Invoice B → overdue
Invoice C → overdue
Invoice D → overdue
Invoice E → overdue
```

Instead of treating every invoice as an independent communication opportunity, the system can understand the overall buyer relationship.

This supports a more realistic recovery experience.

---

# 19. Recommended UI for the Winning Layer

A full SaaS dashboard is not necessary.

A focused demo dashboard is enough.

## Screen 1 — Portfolio Overview

```text
REVENUE RECOVERY

₹38.4L Outstanding
₹12.7L Recovered
42 Overdue
17 High-Risk
9 Early Warnings
```

---

## Screen 2 — Trader Profile

```text
ABC TRADERS

Recovery Score       78
Payment Reliability  84
Cash-Flow Health     62
Promise Reliability  72
Recovery Risk        76

Recent Cash Flow
[chart]

Payment History
[chart]

Risk Signals
[list]
```

---

## Screen 3 — Invoice Recovery

```text
Invoice #204

₹5,00,000
17 days overdue

Payment Risk:
78%

Cash-Flow:
Deteriorating

Promise Reliability:
72%

NEXT BEST ACTION:
Soft Reminder

WHY:
[list of signals]
```

---

## Screen 4 — Recovery Timeline

```text
Invoice Created
      |
Due Date
      |
Early Warning
      |
Reminder
      |
Buyer Promise
      |
Promise Broken
      |
Escalation
      |
Partial Payment
      |
Recovered
```

---

## Screen 5 — Strategy Simulator

```text
COMPARE STRATEGIES

Basic Reminder
vs
Current Agent
vs
Predictive Agent

Expected Recovery
Average Recovery Time
Messages
Human Handoffs
Recovery Cost
```

This is enough for a judge to understand the entire product.

---

# 20. Data Model Additions

The current system can retain its existing invoice/buyer structures.

Add fields conceptually for:

```text
Trader Profile
----------------
payment_reliability
cash_flow_health
promise_reliability
recent_activity_score
recovery_risk
payment_propensity
risk_confidence
```

For invoices:

```text
Invoice
----------------
payment_risk
payment_probability_7d
payment_probability_14d
payment_probability_30d
recovery_priority
recommended_action
action_reason
```

For promises:

```text
Promise
----------------
promise_date
promised_amount
actual_amount
outcome
days_late
```

For actions:

```text
Recovery Action
----------------
action_type
expected_recovery
estimated_cost
reason
confidence
outcome
```

---

# 21. What NOT to Add

After Day 12, do not suddenly add:

```text
Voice calls
Full WhatsApp integration
Complete banking integration
Full accounting software integration
Large CRM
Complex legal filing automation
Loan underwriting
Autonomous settlement negotiation
Large enterprise dashboard
Dozens of ML models
```

These can make the project look larger while reducing the chance of having a stable demo.

The Winning Layer should remain focused.

---

# 22. Recommended Implementation Order

After the original 12-day MVP is stable:

## Phase W1 — Trader Profile

Build:

```text
Payment Reliability
Cash-Flow Health
Promise Reliability
Recent Activity
Recovery Risk
```

---

## Phase W2 — Early Risk

Build:

```text
Payment Risk
Payment Propensity
Early Warning
```

---

## Phase W3 — Next-Best Action

Upgrade:

```text
Brain
```

to use:

```text
Trader Profile
+
Risk
+
Cash Flow
+
Invoice
+
Promise History
+
Communication History
```

---

## Phase W4 — Explainability

Add:

```text
WHY THIS SCORE?
WHY THIS RISK?
WHY THIS ACTION?
```

---

## Phase W5 — Simulation

Add:

```text
Strategy comparison
What-if simulation
Expected recovery comparison
```

---

## Phase W6 — Closed Loop

Connect:

```text
Action
  |
Outcome
  |
Trader Profile
  |
Updated Risk
```

---

# 23. Final Winning Architecture

The final product should conceptually look like:

```text
                    TRANSACTION DATA
                           |
                    INVOICE DATA
                           |
                    PAYMENT HISTORY
                           |
                           v
                 +-------------------+
                 | TRADER PROFILE    |
                 |                   |
                 | Payment Score     |
                 | Cash-Flow Health  |
                 | Promise Reliability
                 | Recent Activity   |
                 +---------+---------+
                           |
                           v
                 +-------------------+
                 | PREDICTIVE RISK   |
                 |                   |
                 | Payment Risk      |
                 | Payment Propensity|
                 | Early Warning      |
                 +---------+---------+
                           |
                           v
                 +-------------------+
                 | RECOVERY DIAGNOSIS|
                 |                   |
                 | Why delayed?      |
                 +---------+---------+
                           |
                           v
                 +-------------------+
                 | RECOVERY BRAIN    |
                 |                   |
                 | Next Best Action  |
                 +---------+---------+
                           |
              +------------+------------+
              |            |            |
              v            v            v
           Message      Promise      Human
            Writer       Tracker     Handoff
              |            |            |
              +------------+------------+
                           |
                           v
                    PAYMENT OUTCOME
                           |
                           v
                 +-------------------+
                 | CLOSED-LOOP       |
                 | LEARNING          |
                 +---------+---------+
                           |
                           +--------> Trader Profile
```

---

# 24. Final Product Statement

The final project should be presented as:

> **An AI-powered predictive revenue recovery agent that builds a dynamic financial profile for every trader, identifies early signs of payment risk, understands the likely reason for delayed payment, and selects the next-best recovery action based on payment behavior, cash-flow signals, promises, invoice context, and expected recovery value.**

Short version:

> **Predict → Understand → Recover → Learn.**

---

# 25. The Winning Demo

The strongest demo should follow one trader from start to finish.

### Step 1 — Healthy Trader

```text
ABC Traders
Recovery Score: 84
Risk: Low
```

### Step 2 — Early Warning

Before the invoice becomes overdue:

```text
⚠ Payment Risk: 76%

Reason:
Recent inflows declining
Payment delays increasing
```

### Step 3 — Invoice Becomes Due

The system tracks the invoice.

### Step 4 — Buyer Responds

```text
"Cash flow tight hai.
Friday ko ₹1 lakh kar dunga."
```

### Step 5 — AI Understands

```text
Intent:
Payment Promise

Amount:
₹1,00,000

Date:
Friday

Cash-flow stress:
Likely
```

### Step 6 — Next Best Action

```text
Recommended:
Payment-plan / soft recovery

Why:
Trader historically responds to low-pressure recovery
and has relatively strong promise reliability.
```

### Step 7 — Payment

Buyer pays ₹1 lakh.

### Step 8 — Profile Changes

```text
Promise Reliability:
72 → 76

Outstanding:
₹5L → ₹4L
```

### Step 9 — Judge Sees the Impact

```text
Predictive Agent vs Basic Reminder

Recovery:
+₹X

Recovery Time:
-X days

Messages:
-X%

Human Escalations:
-X
```

This demonstrates the entire intelligence loop in a few minutes.

---

# 26. Definition of Done for the Winning Layer

Do not consider the Winning Layer complete because the UI exists.

**Updated Phase 5 (close-out):** this checklist was written before any of
W1-W4 or Phase 1-4 existed and sat entirely unchecked ever since, even
though most of it is now true -- see the "Implementation Status" section
near the top of this document for the honest scope behind each checked
item; a checkmark here means "a real, if partial, slice exists," not "built
exactly as this document originally envisioned."

It should satisfy:

```text
[ ] Trader has a multi-dimensional profile         (scattered across the buyer
                                                      panel + ability/willingness
                                                      axes, never unified into one
                                                      profile object -- Enhancement 1
                                                      as scoped remains unbuilt)
[x] Payment risk can be displayed before default   (W1 early warning)
[ ] Payment propensity can be demonstrated         (no probability MODEL exists --
                                                      P(recover) is a stated
                                                      assumption grid, Phase 2)
[x] Cash-flow signals are visible                  (Phase 1 ability axis)
[x] Promise reliability updates from outcomes      (W2 buyer panel)
[x] Brain uses the new signals                     (Phase 3, behind brain.ev_mode,
                                                      off by default)
[x] Brain produces a next-best action              (Phase 3 EV ranking)
[x] Decision has an explanation                    (negotiation_action/ev detail
                                                      in the audit trail, Phase 3)
[ ] Recovery outcomes update trader data           (no persisted cross-run
                                                      learning -- Closed-Loop
                                                      Learning, Enhancement 8,
                                                      remains unbuilt)
[x] Strategy comparison can be demonstrated        (sim/run_sim.py --compare's
                                                      3-arm report, Phase 4)
[x] Existing MVP still works                       (877 tests, byte-identical
                                                      proofs at every phase)
[x] Existing audit trail still works
[x] Existing baseline experiment still works
[x] No existing safety/stop rules are bypassed     (dedicated hard-stop-
                                                      precedence tests, Phase 3)
[ ] Demo can be completed reliably                 (Phases 11-12, not yet done)
```

---

# 27. Priority Ranking

**Updated Phase 5 (close-out):** this is the ORIGINAL plan, written before
any implementation started. 8 of these 12 items now have a real, if partial,
slice built -- items 1, 2, 3, 4, 5, 7, 9, and 12 (see "Implementation
Status" near the top of this document for exactly how much of each). Items
6 (Payment Propensity), 8 (Closed-Loop Profile Updates), 10 (Strategy
Simulator), and 11 (What-If Simulation) remain genuinely unbuilt. Left as
originally written below, for the historical record of what was planned and
in what order.

If time becomes limited, implement in this exact order:

### MUST HAVE

1. **Trader Financial Profile**
2. **Early Payment Risk**
3. **Cash-Flow Intelligence**
4. **Next-Best Recovery Action**
5. **Explainable Decisions**

### STRONGLY RECOMMENDED

6. Payment Propensity
7. Promise Reliability
8. Closed-Loop Profile Updates

### IF TIME REMAINS

9. Recovery Cost / Expected Recovery
10. Strategy Simulator
11. What-If Simulation
12. Advanced buyer-level communication intelligence

---

# 28. The Core Principle

Do not turn the project into a collection of AI features.

Every enhancement must answer one question:

> **Does this help the system recover money more intelligently, earlier, faster, cheaper, or with less unnecessary customer friction?**

If the answer is no, it should not be part of the Winning Layer.

The strongest final product is therefore not:

```text
AI + Dashboard + Chatbot + Score + Simulator
```

It is:

```text
Financial Behavior
       +
Prediction
       +
Diagnosis
       +
Decision Intelligence
       +
Recovery Automation
       +
Learning
```

That is the layer to build after the original 12-day MVP is complete.
