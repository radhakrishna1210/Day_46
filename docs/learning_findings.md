# Learning findings

Analysis notes and known limitations from fitting recovery-probability
posteriors (`scripts/fit_recovery.py` → `config/learned_recovery.yaml`).
Provenance -- seeds, counts, dates -- lives in `docs/learning_data.md`; this
file is the "what should a reader be careful about" companion.

---

## The label / execution gap in SEND actions  (known limitation)

`engine/negotiation.py`'s action space distinguishes `soft_nudge`, `firm` and
`legal_facts` as separate SEND actions, and the EV model selects among them.
But `engine/brain.py`'s `decide()` does **not** execute the selected label at
that label's own rung. It sends at whatever rung the **escalation walk** (score
band and pacing, backlog on a first contact, broken-promise jump, per-rung
message exhaustion, and the `engine/law.py` ceiling) has independently settled
on. The negotiation label rides along in the audit trail as
`proposed_action_kind`; the rung that actually goes out is `chosen`.

Measured over the 30 training seeds (exploration mode, `outcomes_train.jsonl`):

| Selected label | Delivered at its nominal rung |
| --- | ---: |
| `soft_nudge` (rung 1) | **7%** (59 / 892) |
| `firm` (rung 2) | 80% (715 / 893) |
| `legal_facts` (rung 3) | 44% (639 / 1446) |

`gate_override` was true on **56% of SEND rows** (1818 / 3231). The walk lands
at rung 2 for ~68% of all SENDs, so no matter which of the three labels EV
picks, the buyer usually receives a rung-2 (firm) message.

`payment_plan` and `counter_settle` do **not** have this gap -- they get their
own `Action.kind` and map 1:1 from selection to execution (686 → 686,
236 → 236 in training).

### What the fit does about it

`scripts/fit_recovery.py` groups a SEND by the rung it was **delivered** at,
mapped to a tier name through `config/rules.yaml`'s existing ladder
(rung 1 = `soft_nudge`, 2 = `firm`, 3 = `legal_facts` -- not a new mapping).
The learned number therefore answers *"what happens when the buyer receives a
rung-N message?"* -- which is what actually reaches the buyer -- rather than
*"what happens when EV picks label X?"*, which is confounded by the walk.

`engine/learning.py`'s `recovery_probability(quadrant, "firm")` resolves to the
rung-2 (firm-tier) cell. So when EV compares `firm` vs `soft_nudge` it is
comparing the recovery rates of rung-2 vs rung-1 messages. That is the right
comparison **if** the walk then delivers at the matching rung, and a reasonable
proxy otherwise -- and it is strictly better than fitting on the label EV chose,
which the walk overrides more than half the time.

### The residual gap, and a future fix (do NOT attempt now)

Even with per-tier cells, a gap remains on the consumer side: `firm` winning EV
does not reliably deliver a rung-2 message, so the number the EV comparison
uses ("rung-2 recovery rate") is not always the number that describes the
outcome ("rung-`chosen` recovery rate").

The clean fix is upstream, in `engine/brain.py`: make EV's choice among
`soft_nudge` / `firm` / `legal_facts` **directly set the executed rung** (within
the `engine/law.py` ceiling), instead of the escalation walk deciding
independently and the label riding along. That collapses the gap -- the
selected action would always be the delivered action -- but it is a change to
decision logic, out of scope for the Day 4/Day 5 fit-and-lookup work, and is
recorded here only as a known direction.

---

## Thin SEND cells at rung 1

The escalation walk rarely stops at rung 1, so the `soft_nudge` (rung-1
delivery) cells are thin: n ranges from 9 (`high_risk`) to 79
(`good_customer`). Their posterior means sit close to the Beta(1, 1) prior and
their `ci95_width` is wide (0.21 – 0.42) -- the width is the signal to distrust
the point estimate. `can_pay_but_wont` has **no** rung-1 sends in training at
all, so it has no `soft_nudge` cell and `recovery_probability()` falls back to
the hand-typed grid value for it (logged once).

`engine/negotiation.py` still uses whatever mean a cell reports -- it does not
special-case a thin cell. The honesty here is in the `ci95_width` being visible,
not in the code refusing to use a wide one.

---

## Selection confounding (inherited from `engine/outcomes.py`)

The `send/legal_facts` cell for `cash_flow_problem` shows mean 0.77 (n=55) --
higher than that quadrant's `firm` cell (0.51). This is unlikely to be a causal
effect of legal-tier messaging on a cash-strapped buyer; more plausibly, a
rung-3 message is only reached for the cash_flow cases that were already close
to paying (or that finally responded). Proximity is not causation -- the same
correlational-window caveat `engine/outcomes.py`'s docstring already states
applies to every cell here, and doubly to the small ones.
