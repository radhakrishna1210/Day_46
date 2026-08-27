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
end to end as a forward-looking plan. Four slices of it have since actually
been built, under the project's own W1-W4 labels (see CLAUDE.md's Current
status). **Those labels do not map 1:1 onto this document's own
Enhancement/Phase numbering above** -- they were renamed and reordered
during real implementation, not built in the order or shape originally
planned here. The rest of this document -- everything not listed below --
remains unbuilt.

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

**FUTURE / UNBUILT** (everything else in this document): Enhancements 1, 3,
4, 6-12 (Dynamic Trader Financial Profile as originally scoped, Cash-Flow
Intelligence, Payment Propensity Prediction, Next-Best Recovery Action
beyond the existing rung ladder, Expected Recovery/Cost Intelligence,
Recovery Explanation beyond the existing audit trail, Recovery Strategy
Simulation, Closed-Loop Learning), and the UI Screens 1-5. None of these
have any code behind them -- no cash-flow trend, no payment-propensity
score, no dashboard, no what-if simulator. They require either real
transaction/payment data this standalone project doesn't have, or are
explicitly out of the 12-day build's scope (dashboards, ML models -- see
CLAUDE.md's scope guard).

**Reconciliation -- actual W-label vs. this document's original plan:**

| Actually built | Closest match in this document's original numbering |
|---|---|
| W1 Early Warning (low/watch/high band, pre-due-date) | A narrower slice of Enhancement 2 (Early Payment Risk Prediction) -- no probability score, just a fixed band |
| W2 Buyer panel + promise reliability | Enhancement 5 (Promise Reliability) + Enhancement 13 (Trader-Level Recovery Intelligence) |
| W3 Buyer-level message consolidation | Enhancement 14 / "Buyer-Level Communication Awareness" -- this document's own lowest-priority item (see Section 27, Priority Ranking, below) |
| W4 Experiment refresh + edge-case transparency | Not one of this document's original Enhancements -- an experiment-honesty hardening pass, not a new product capability |

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

It should satisfy:

```text
[ ] Trader has a multi-dimensional profile
[ ] Payment risk can be displayed before default
[ ] Payment propensity can be demonstrated
[ ] Cash-flow signals are visible
[ ] Promise reliability updates from outcomes
[ ] Brain uses the new signals
[ ] Brain produces a next-best action
[ ] Decision has an explanation
[ ] Recovery outcomes update trader data
[ ] Strategy comparison can be demonstrated
[ ] Existing MVP still works
[ ] Existing audit trail still works
[ ] Existing baseline experiment still works
[ ] No existing safety/stop rules are bypassed
[ ] Demo can be completed reliably
```

---

# 27. Priority Ranking

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
