# Learning data -- provenance for `config/learned_recovery.yaml`

This file is the answer to "what did you train on?". It is written by
`scripts/fit_recovery.py` in the same run that writes the YAML, so the two
cannot drift apart. Analysis notes and known limitations are separate --
see `docs/learning_findings.md`.

- **Generated:** 2026-09-23
- **Generator:** `scripts/fit_recovery.py`
- **Arm:** `agent_ev_explore` -- `sim/run_sim.py` `run_agent(explore=True)`,
  exploration mode: the brain samples uniformly from the already-gated
  eligible-action list instead of taking the top-EV pick, so every
  (quadrant, action) cell the rules allow gets observed.
- **Reproduce:** `python scripts/fit_recovery.py` (or `--skip-run` to
  re-aggregate the existing `audit/outcomes_train.jsonl` without new sim runs).

## Seeds

**Training seeds (30):** 1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014, 1015, 1016, 1017, 1018, 1019, 1020, 1021, 1022, 1023, 1024, 1025, 1026, 1027, 1028, 1029

**Benchmark seeds, HELD OUT (never fitted on):** 7, 13, 42, 99, 555, 2024

The two sets are asserted disjoint before any run starts. Every headline
number in `report/out/results.json` is measured on the benchmark seeds;
fitting on one would let these posteriors memorise the world they are later
evaluated against.

## Parameters

| Parameter | Value |
| --- | --- |
| Simulated days per seed | 120 |
| Attribution horizon | 14 days |
| Run window | 2026-08-24 .. 2026-12-21 |
| Right-censoring cutoff | action on or before 2026-12-07 |
| Prior | Beta(1, 1), weak uniform |
| Outcomes ledger | `audit/outcomes_train.jsonl` |

## Cell grouping

`payment_plan` and `counter_settle` each get one flat cell per quadrant --
they map 1:1 from what EV selected to what was executed.

`wait` gets one flat cell per quadrant too: a wait the EV ranking **chose**
over contacting the buyer, recorded once per wait episode by `sim/run_sim.py`
(rule waits -- spacing, weekends, an active promise -- are never recorded).
It is judged by the same most-recent-action rule as every other row.

A **SEND** is grouped by the rung it was **delivered** at, mapped to a tier
name through `config/rules.yaml`'s ladder (rung 1 = `soft_nudge`, 2 = `firm`,
3 = `legal_facts`), and stored nested under `recovery.<quadrant>.send.<tier>`.
With `brain.ev_sets_rung: true` the tier EV picked is the rung delivered, so
this grouping and `proposed_action_kind` agree; the label/execution-gap
history is in `docs/learning_findings.md`.

**Gate overrides in this training set:** **0** of 3218 fitted SEND rows (0.0%) were delivered at a rung other than the tier EV picked.

`send_rows_off_ladder`: **0** (a SEND recorded at rung 0 or 4 --
kept in a coarse `send` cell rather than dropped; expected to be 0).

## Observation counts

Exact partition of every action row produced across all training seeds:

| Bucket | Rows |
| --- | ---: |
| Action rows seen | 7299 |
| Excluded -- handoff (unobservable outcome) | 1327 |
| Excluded -- right-censored | 0 |
| Excluded -- null quadrant | 0 |
| **Fitted observations** | **5972** |

## Fitted cells

| Quadrant | Cell | Successes | Failures | Obs | Posterior mean | 95% CI width | Note |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| can_pay_but_wont | send/soft_nudge | 0 | 18 | 18 | 0.050 | 0.175 | thin |
| can_pay_but_wont | send/firm | 111 | 273 | 384 | 0.290 | 0.090 |  |
| can_pay_but_wont | send/legal_facts | 74 | 69 | 143 | 0.517 | 0.162 |  |
| can_pay_but_wont | counter_settle | 129 | 297 | 426 | 0.304 | 0.087 |  |
| can_pay_but_wont | wait | 0 | 283 | 283 | 0.004 | 0.013 |  |
| cash_flow_problem | send/soft_nudge | 49 | 209 | 258 | 0.192 | 0.096 |  |
| cash_flow_problem | send/firm | 159 | 131 | 290 | 0.548 | 0.114 |  |
| cash_flow_problem | send/legal_facts | 10 | 7 | 17 | 0.579 | 0.427 | thin |
| cash_flow_problem | payment_plan | 208 | 114 | 322 | 0.645 | 0.104 |  |
| cash_flow_problem | wait | 0 | 238 | 238 | 0.004 | 0.015 |  |
| good_customer | send/soft_nudge | 214 | 215 | 429 | 0.499 | 0.094 |  |
| good_customer | send/firm | 254 | 185 | 439 | 0.578 | 0.092 |  |
| good_customer | send/legal_facts | 6 | 16 | 22 | 0.292 | 0.352 | thin |
| good_customer | payment_plan | 281 | 212 | 493 | 0.570 | 0.087 |  |
| good_customer | wait | 0 | 373 | 373 | 0.003 | 0.010 |  |
| high_risk | send/soft_nudge | 0 | 6 | 6 | 0.125 | 0.406 | thin |
| high_risk | send/firm | 110 | 551 | 661 | 0.167 | 0.057 |  |
| high_risk | send/legal_facts | 118 | 433 | 551 | 0.215 | 0.068 |  |
| high_risk | wait | 0 | 619 | 619 | 0.002 | 0.006 |  |

A wide CI is the honest signal that a cell is thin -- with the Beta(1,1)
prior a cell of zero observations reads as mean 0.500, CI width ~0.95.

### Thin cells (n below 100)

A thin cell's point estimate sits near the prior; its `ci95_width` is what
says so. A (quadrant, action) pair never executed in training has no cell at
all -- `engine/learning.py` falls back to the hand-typed grid value for it
(logged once).

- `can_pay_but_wont` / `send/soft_nudge` -- n=18 (mean 0.050, 95% CI width 0.175)
- `cash_flow_problem` / `send/legal_facts` -- n=17 (mean 0.579, 95% CI width 0.427)
- `good_customer` / `send/legal_facts` -- n=22 (mean 0.292, 95% CI width 0.352)
- `high_risk` / `send/soft_nudge` -- n=6 (mean 0.125, 95% CI width 0.406)

## Notes

- **`config/learned_recovery.yaml` is read by `engine/learning.py`**, behind
  `config/rules.yaml`'s `learning.enabled` switch, which ships OFF. With the
  switch off nothing consults it and behaviour is byte-identical to before it
  existed.
- **handoff rows are excluded, not fitted as a zero cell.** The simulator has
  no model of what the owner does after taking a case over, so no money can
  ever land behind a handoff here -- that is an unobservable outcome, not
  evidence that handoffs fail.
- **`legal_escalation` never appears as its own action_kind.** Both it and
  `human_handoff` execute as a rung-4 `handoff` (`engine/brain.py`), so they
  fall under the handoff exclusion above.
- **Right-censored actions are dropped** rather than counted as failures:
  their horizon ran past the end of the simulated window, so a "no payment"
  verdict on them is not yet earned. With a 120-day window and a 14-day
  horizon this rarely fires -- the training worlds run out of overdue invoices
  well before the cutoff -- but the exclusion is applied regardless.
- **Side effects are restored.** A full run re-runs the simulator, which
  regenerates `data/seed/` per seed and rewrites the audit trail; the script
  snapshots both and puts them back before it exits. `--skip-run` touches
  neither.
