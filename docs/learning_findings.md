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

---

## The `good_customer`/`firm` cell, and the Day 8 6/6 loss

`scripts/compare_grids.py` ranks every fitted cell by
`|hand-typed - fitted mean|` weighted by `observations / ci95_width` -- a big
delta on a thin cell is noise dressed as a finding, and the weighting says so.
The single highest-scoring cell in the whole file, by a wide margin, is:

| Cell | Hand-typed | Fitted mean | Delta | Observations | 95% CI width |
| --- | ---: | ---: | ---: | ---: | ---: |
| `good_customer` / `firm` | 88.0% | 61.47% | -26.5pt | 748 | 0.0696 |

n=748 is the largest sample of any cell in the file; `ci95_width`=0.0696 is
the tightest. This is not a thin, uncertain correction -- it is the
best-supported number `scripts/fit_recovery.py` produced, and it says the
hand-typed grid was badly overoptimistic about how often a firm-tier message
actually recovers money from a `good_customer` buyer.

**This single cell, combined with two rules that already existed before this
fit, is the entire explanation for Day 8's agent+EV+learned 6/6 loss against
agent+EV** (see `CLAUDE.md`'s Day 8 notes) -- not six independent per-seed
findings. Confirmed by instrumented replay: `engine.negotiation.rank_actions()`
spied during a real `learned=True` run, then replayed against the IDENTICAL
`(quadrant, outstanding_paise, broken_promises, candidates)` context under the
hand-typed grid (learning back off by then), on all six benchmark seeds.

- `good_customer`/`wait` has no learned cell -- `wait` produces no
  attributable action row, so it is structurally unmeasured (see below) and
  stays at its hand-typed **60%** forever, learning on or off.
- At `broken_promises == 0`, firm's 61.47% still clears wait's 60% by 1.5
  points -- comfortably enough to cover a 72-paise message cost against any
  real invoice size, so firm still wins. Confirmed: every non-flip
  `good_customer`/firm-eligible decision, on every seed, has
  `broken_promises == 0` (20-39 cases per seed).
- `config/rules.yaml`'s `negotiation.promise_adjustment` already subtracts
  **4 points per broken promise** from `firm` (not from `wait`) -- a rule that
  predates this fit and has nothing to do with it. `61.47 - 4 = 57.47`,
  already below wait's 60%. So **any** broken promise at all flips the
  ranking to `wait`.
- On all six benchmark seeds, **100% of that seed's flips** are this exact
  transition (`good_customer`: hand-typed picks `firm`, learned picks
  `wait`), and every flip has `broken_promises >= 1`. Only the seed-to-seed
  mix of `broken_promises == 1` vs `== 2` at the flip point differs -- a
  property of each seed's own random draws, not of the mechanism: seed 42's
  flips sit entirely at `== 2`; seeds 7, 13 and 555 sit entirely at `== 1`;
  seeds 99 and 2024 are a mix of both. No seed shows a different transition
  anywhere in its flip set, and no seed's flips are driven by any other cell.

This is a real, well-supported correction (n=748, the tightest CI in the
file) landing close enough to an untested number (`wait`'s 60%) that an
existing, unrelated penalty rule crosses the gap. It is not overfitting and
it is not noise. It is also not a partial win dressed up as one: replacing
the hand-typed grid with the fitted one recovered LESS money on every one of
the six benchmark seeds on this run, and that is reported as exactly what it
is -- not reframed, not hedged into a tie.

---

## `wait` is structurally unmeasured -- a real gap, not a bug

Every quadrant's `wait` row has no learned cell, ever, and never will under
the current simulator. This is **not** a data gap `scripts/fit_recovery.py`
could close by training on more seeds or more days -- it is a structural
blind spot in what the simulator can observe at all.

`wait` means the agent takes no action on an invoice today. Taking no action
produces no attributable action row: `engine/outcomes.py`'s ledger only ever
credits or fails an actual SEND / `payment_plan` / `counter_settle` / handoff,
so there is nothing for `scripts/fit_recovery.py` to fit `wait` against. Its
`config/rules.yaml` hand-typed `recovery_probability` is therefore an
assumption that has **never** been checked against a real simulated outcome
-- not once, not on any training seed, not on any benchmark seed -- and,
absent a change to what the simulator is able to observe, never will be.

This matters most exactly when a learned cell's fitted mean lands close to a
never-corrected `wait` value, as `good_customer`/`firm` (61.47%) now does to
`good_customer`/`wait` (60%, untested) above. The EV comparison in that case
is between one number the data corrected and one number that was never put
to the test at all. That asymmetry is not a flaw in the fit -- the fit is
doing exactly what it can do, honestly, on the data the simulator can
produce -- but it is a real limitation to weigh before trusting a decision
that turns on a margin this thin, and it is recorded here as a limitation,
not quietly worked around.

---

## The other two most-wrong cells

`scripts/compare_grids.py` (`--top 3`, the default) ranks every fitted cell
by `|hand-typed - fitted mean| x (observations / ci95_width)`, excluding the
already-flagged thin `soft_nudge` cells above. After `good_customer`/`firm`
(the featured cell above, and the top score by a wide margin), the next two
are:

| Rank | Cell | Hand-typed | Fitted mean | Delta | Observations | 95% CI width |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2 | `good_customer` / `payment_plan` | 80.0% | 58.64% | -21.4pt | 426 | 0.0932 |
| 3 | `can_pay_but_wont` / `firm` | 45.0% | 27.76% | -17.2pt | 405 | 0.0868 |

Both are well-observed (n>400) and reasonably tight (`ci95_width`<0.1) -- not
thin cells riding a lucky draw. Neither has (yet) been checked for a
`wait`-adjacent threshold effect the way `good_customer`/`firm` has above;
they are named here as the next places to look if the learned-posteriors
ablation is revisited, not as a second explanation for the Day 8 loss.

---

## Robustness check: does the loss survive a perturbed persona world?

`sim/personas.py`'s `REACTION_TABLE` / `PROMISE_KEEP_CHANCE` describe the fake
buyers -- not anything `engine/` is audited against (see that module's own
docstring). `config/learned_recovery.yaml` was fit against the world these
tables define. If the 6/6 loss above only holds because the fitted numbers
happen to match this EXACT synthetic persona table, that would mean we
learned the personas, not something more general about `good_customer`
buyers. This check asks: perturb the world +/-10% and re-run -- without
re-fitting -- does agent+EV+learned still lose to agent+EV?

**Method** (fixed and written down before running, so nothing here was tuned
after seeing a result): every individual value in `REACTION_TABLE` and
`PROMISE_KEEP_CHANCE` was scaled by `1 + sign * 0.10`, `sign` drawn `+1`/`-1`
(p=0.5 each) from one `random.Random("day10_persona_robustness_v1")` stream,
in a fixed order (personas, then rungs 1/2/3, then outcomes for
`REACTION_TABLE`; personas for `PROMISE_KEEP_CHANCE`). `REACTION_TABLE` rows
were renormalized back to sum to 1.0 afterward (a uniform +10% to every value
in a row and renormalizing is a no-op, so the sign has to vary per value for
this to test anything). A `0.00` entry (e.g. `DISPUTE` for a persona that
structurally never disputes) stays `0.00` under a relative perturbation --
expected, not a bug. `config/learned_recovery.yaml` was **not** re-fit; the
same posteriors trained on the unperturbed world were evaluated against the
perturbed one, in-process (monkeypatching `sim.personas.REACTION_TABLE` /
`.PROMISE_KEEP_CHANCE`, which `react()` / `keeps_promise()` read as globals at
call time -- no file in the repo was edited).

**Result: still a 6/6 loss**, and close to the same size:

| Seed | agent+EV | agent+EV+learned | Delta |
| --- | ---: | ---: | ---: |
| 42 | ₹1,69,44,019 | ₹1,51,85,019 | -₹17,59,000 |
| 7 | ₹1,50,22,822 | ₹1,18,65,910 | -₹31,56,912 |
| 13 | ₹1,73,17,157 | ₹1,57,02,734 | -₹16,14,423 |
| 99 | ₹1,43,09,111 | ₹1,37,93,062 | -₹5,16,048 |
| 2024 | ₹1,50,13,343 | ₹1,18,78,957 | -₹31,34,386 |
| 555 | ₹1,14,39,262 | ₹85,09,476 | -₹29,29,786 |

Mean delta -₹21,85,092 (unperturbed: -₹22,53,175), range -₹31,56,912 to
-₹5,16,048 (unperturbed: -₹31,56,911 to -₹5,16,048). Seed 7 is the worst seed
and seed 99 the least-bad seed in **both** worlds. Re-running the seed-42
instrumented replay under the perturbed world (same method as the
`good_customer`/`firm` finding above) found the **identical** dominant
transition -- `good_customer`: hand-typed picks `firm`, learned picks `wait`
-- as 100% of the flips, on all six seeds, again.

This is expected once the mechanism is understood, not a coincidence: the
`good_customer`/`firm`-vs-`wait` threshold is a comparison of two **fixed
config numbers** (the fitted mean and the hand-typed grid value) plus a
**fixed penalty rule** -- none of which read `sim/personas.py` at all. A
persona perturbation changes which invoices get paid, promised, or broken,
which can shift how many decisions land at `broken_promises >= 1` and
therefore the total rupees at stake -- and it did shift the per-seed deltas a
little -- but it cannot change the threshold arithmetic itself. **We learned
something about the relationship between two config numbers, not about this
exact persona table** -- which is exactly what this check set out to
distinguish, and the honest answer either way was going to be reported.

---

## Robustness check: thin-cell audit -- does the agent explore or commit?

Every `(quadrant, action)` cell with fewer than 20 observations, and whether
it is even **reachable** -- present in `config/rules.yaml`'s
`negotiation.eligible_actions` for that quadrant, so the real, gated decision
path (`engine/brain.py`'s `_pick_negotiation_action`) could ever select it,
as opposed to only the ungated `bandit_top_choice` audit computation, which
ranks the full 8-action space regardless of what is actually allowed:

| Quadrant | Action | n | Reachable? |
| --- | --- | ---: | --- |
| `good_customer` | `counter_settle` | 0 (no cell) | No |
| `cash_flow_problem` | `counter_settle` | 0 (no cell) | No |
| `can_pay_but_wont` | `soft_nudge` | 0 (no cell) | **Yes** |
| `can_pay_but_wont` | `payment_plan` | 0 (no cell) | No |
| `high_risk` | `soft_nudge` | 9 | No |
| `high_risk` | `payment_plan` | 0 (no cell) | No |
| `high_risk` | `counter_settle` | 0 (no cell) | No |

**Six of these seven cells are excluded from every real decision by the
RULES, before learning ever gets a say** -- the action simply is not on that
quadrant's menu (matches existing notes: `counter_settle`/`payment_plan` are
deliberately not offered outside their intended quadrants). For these,
"explore vs. commit" does not apply: the agent can never hit them.

The one exception, `can_pay_but_wont`/`soft_nudge`, **is** reachable but has
**no fitted cell at all** (0 rung-1 sends in 30 training seeds -- already
noted in "Thin SEND cells at rung 1" above). `has_cell()` is `False`, so
**both** offline and online modes fall back identically to the hand-typed
15% -- no learned commitment and no exploration, the same permanent
situation `wait` is in, for a different reason (this one could in principle
be observed with a different training run; `wait` never can).

That leaves exactly one cell in this file that is both thin (n<20) **and**
fitted: `high_risk`/`soft_nudge` (n=9) -- and it is the `high_risk` row
above: **not reachable either** (`soft_nudge` is not in `high_risk`'s
`eligible_actions`). So on the current fitted grid, **no cell under 20
observations is ever hit by a real decision, in either mode** -- the
question the check asks does not have a live test case at this exact
threshold. That is itself the honest finding, not a dodge of the question:
reported below is what actually happens when it's evaluated anyway (via the
audit-only ungated path), plus the closest genuinely-reachable thin cells
(`good_customer`/`soft_nudge`, n=79; `cash_flow_problem`/`soft_nudge`, n=29)
as the real test of "wide posterior -> explore".

**Analytic check** (Beta posterior standard deviation -- exact, no
simulation): a thin cell's *sampled* probability (Thompson sampling, online
mode) genuinely varies far more than a well-fitted cell's:

| Cell | n | Mean | Beta std |
| --- | ---: | ---: | ---: |
| `high_risk` / `soft_nudge` | 9 | 18.18% | **11.13 pts** |
| `cash_flow_problem` / `soft_nudge` | 29 | 22.58% | 7.39 pts |
| `good_customer` / `soft_nudge` | 79 | 38.27% | 5.37 pts |
| `good_customer` / `firm` | 748 | 61.47% | 1.78 pts |
| `high_risk` / `firm` | 658 | 17.42% | 1.48 pts |

**Empirical check** (one online-mode run, seed 42, 120 days -- every
`rank_actions()` call spied and recorded): the sampled probability for
`high_risk`/`soft_nudge` ranged **0% to 46%** across 1,179 evaluations
(stdev 7.05); `good_customer`/`firm`'s ranged **48% to 64%** across 1,076
evaluations (stdev 2.38). The thin cell's own instability was large enough
that it won the **ungated** (audit-only) argmax 17 of 1,179 times (1.4%)
despite a low mean -- a real instance of Thompson sampling favoring a
long-shot action purely from a lucky draw. It won the **gated** (executed)
argmax **zero** times, in either mode, on either run -- confirming Part A's
reachability finding empirically: the rules exclude it before sampling noise
ever gets a chance to matter.

The `good_customer` quadrant's own gated tally in this same run --
`wait` 1037, `firm` 37, `payment_plan` 2 -- lines up with the `good_customer`/
`firm`-vs-`wait` mechanism above (37 is close to the ~39 non-flip,
`broken_promises == 0` decisions per seed found there), confirming the same
mechanism operates under online mode too, not only offline.

**Answer, honestly**: "a wide posterior makes the agent explore rather than
commit" is **true, but conditionally**:

- **True** under `learning.mode: online` (Thompson sampling) -- confirmed
  both analytically (Beta std) and empirically (sampled-probability spread,
  and an occasional ungated win against the mean).
- **False** under `learning.mode: offline` -- the mode `agent+EV+learned`
  (Day 8's fourth arm) actually uses. Offline mode reads a cell's posterior
  **mean** as a fixed number every time; a thin cell's noisy mean is
  committed to just as confidently as a well-fitted one's, every single
  decision. This is not a new finding -- "Thin SEND cells at rung 1" above
  already says so -- this audit just confirms it precisely for the thinnest
  cell in the file.
- **Structurally moot** for every cell under 20 observations in the current
  fit: six of seven are excluded by `eligible_actions` regardless of mode,
  and the seventh has no posterior to sample from or commit to at all.
