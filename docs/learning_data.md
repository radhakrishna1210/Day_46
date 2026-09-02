# Learning data -- provenance for `config/learned_recovery.yaml`

This file is the answer to "what did you train on?". It is written by
`scripts/fit_recovery.py` in the same run that writes the YAML, so the two
cannot drift apart. Analysis notes and known limitations are separate --
see `docs/learning_findings.md`.

- **Generated:** 2026-09-02
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

A **SEND** is grouped by the rung it was **delivered** at, mapped to a tier
name through `config/rules.yaml`'s ladder (rung 1 = `soft_nudge`, 2 = `firm`,
3 = `legal_facts`), and stored nested under `recovery.<quadrant>.send.<tier>`.
It is **not** grouped by `proposed_action_kind` (the `soft_nudge`/`firm`/
`legal_facts` label EV nominally selected): the escalation walk in
`engine/brain.py` sets the delivered rung independently, and the two disagreed
on 56% of SEND rows in this training set. See `docs/learning_findings.md` for
the label/execution-gap write-up.

`send_rows_off_ladder`: **0** (a SEND recorded at rung 0 or 4 --
kept in a coarse `send` cell rather than dropped; expected to be 0).

## Observation counts

Exact partition of every action row produced across all training seeds:

| Bucket | Rows |
| --- | ---: |
| Action rows seen | 5499 |
| Excluded -- handoff (unobservable outcome) | 1346 |
| Excluded -- right-censored | 0 |
| Excluded -- null quadrant | 0 |
| **Fitted observations** | **4153** |

## Fitted cells

| Quadrant | Cell | Successes | Failures | Obs | Posterior mean | 95% CI width | Note |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| can_pay_but_wont | send/firm | 112 | 293 | 405 | 0.278 | 0.087 |  |
| can_pay_but_wont | send/legal_facts | 114 | 156 | 270 | 0.423 | 0.117 |  |
| can_pay_but_wont | counter_settle | 72 | 164 | 236 | 0.307 | 0.117 |  |
| cash_flow_problem | send/soft_nudge | 6 | 23 | 29 | 0.226 | 0.286 | thin |
| cash_flow_problem | send/firm | 201 | 196 | 397 | 0.506 | 0.098 |  |
| cash_flow_problem | send/legal_facts | 43 | 12 | 55 | 0.772 | 0.215 | thin |
| cash_flow_problem | payment_plan | 166 | 94 | 260 | 0.637 | 0.116 |  |
| good_customer | send/soft_nudge | 30 | 49 | 79 | 0.383 | 0.210 | thin |
| good_customer | send/firm | 460 | 288 | 748 | 0.615 | 0.070 |  |
| good_customer | send/legal_facts | 15 | 19 | 34 | 0.444 | 0.318 | thin |
| good_customer | payment_plan | 250 | 176 | 426 | 0.586 | 0.093 |  |
| high_risk | send/soft_nudge | 1 | 8 | 9 | 0.182 | 0.420 | thin |
| high_risk | send/firm | 114 | 544 | 658 | 0.174 | 0.058 |  |
| high_risk | send/legal_facts | 118 | 429 | 547 | 0.217 | 0.069 |  |

A wide CI is the honest signal that a cell is thin -- with the Beta(1,1)
prior a cell of zero observations reads as mean 0.500, CI width ~0.95.

### Thin cells (n below 100)

The escalation walk rarely stops at rung 1, so the `soft_nudge` (rung-1
delivery) SEND cells are thin. Their point estimates sit near the prior; the
`ci95_width` is what says so. A quadrant with **no** rung-1 sends at all has no
`soft_nudge` cell -- `engine/learning.py` falls back to the hand-typed grid
value for it (logged once).

- `cash_flow_problem` / `send/soft_nudge` -- n=29 (mean 0.226, 95% CI width 0.286)
- `cash_flow_problem` / `send/legal_facts` -- n=55 (mean 0.772, 95% CI width 0.215)
- `good_customer` / `send/soft_nudge` -- n=79 (mean 0.383, 95% CI width 0.210)
- `good_customer` / `send/legal_facts` -- n=34 (mean 0.444, 95% CI width 0.318)
- `high_risk` / `send/soft_nudge` -- n=9 (mean 0.182, 95% CI width 0.420)

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
