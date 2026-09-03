# Demo runbook

Exact commands for the live demo/video, the output each one actually
produces (captured from real runs, not guessed), and the four numbers to
say out loud. Every command below was run against this repo on 2026-09-03
to produce the excerpts shown; re-running them is deterministic (same
seed -> same numbers) as long as `config/rules.yaml`, `config/legal.yaml`
and the persona tables in `sim/personas.py` are unchanged.

Two different seeds are used on purpose, for two different points:

* **seed 42** -- the live single-pass agent (`main.py`). Shows the watch
  band of early warning, the Brain choosing different rungs, a Hinglish
  message, the Law Engine's interest/tax math, and the post office.
* **seed 7** -- the simulator (`sim/run_sim.py --compare`). Shows the
  "high" early-warning band (seed 42 never reaches it -- see "Why 'high'
  needs the simulator" below, this is architectural, not a seed problem)
  and the honest four-arm recovery comparison.

Nothing here sends a real email or touches a real buyer. `LLM_MODE=mock`
in `.env` makes every LLM call deterministic and keyless; `--send-email`
is off unless you explicitly add it, and even then it can only reach
`TEST_INBOX_EMAIL` (also in `.env`), never a real address.

---

## Pre-flight (do this before recording, not on camera)

```
pytest -q
```
Expect a clean green run (1032+ tests, 0 skipped -- if anything is
skipped, `scipy` is probably missing from the environment; it's in
`requirements.txt`).

Confirm the shipped defaults are still what you think they are:
`config/rules.yaml`'s `learning.enabled: false` and `brain.ev_mode: off`.
A fresh clone with no flags flipped reproduces the pre-learning agent
exactly -- if you want to demo the learning layer live, that's a
deliberate, visible flag flip, not the default state.

---

## Segment 1 -- the live agent, seed 42

**Locked command:**
```
python main.py --dry-run --seed 42
```

`--dry-run` is part of the locked command, not a rehearsal-only option:
Segment 2's `run_agent()` unconditionally clears and rewrites
`audit/audit_log.jsonl` at the start of its own run (that's how the
simulator guarantees a fresh, self-consistent trail for its seed and
window), so anything Segment 1 wrote there would be silently overwritten
the moment Segment 2 runs anyway. `--dry-run` just makes that explicit
and keeps this segment fast and repeatable across rehearsal takes,
without a leftover audit trail that quietly vanishes a minute later.

If you also want the "a real email lands in the inbox" beat, that's a
**separate, additional take**, not part of this locked command --
`--send-email` needs `--dry-run` left off (dry-run only silences the
audit trail, not the send, and sending while `--dry-run` claims "nothing
logged" would violate non-negotiable #1):
```
python main.py --seed 42 --send-email
```

### What comes back, in order

**Step 1 -- data factory.** `20 buyers, 252 invoices (100 current)`. If
`data/seed/` doesn't already hold seed 42's data, this line reads
"dataset missing or built for a different seed, generating now" first --
expected on a fresh clone, harmless.

**Step 4 -- early warning.** This is the beat to slow down on:

```
step 4: early warning -- flag invoices approaching due date with bad signals
  4 flagged (0 high, 4 watch), 4 low band, within 14d of due

  invoice         buyer    band    due in   outstanding  reasons
  INV-2026-0171   BUY-11   watch       4d   Rs 3,90,600  due in 4 day(s); buyer score 0 (poor); 9 prior invoices went overdue
  INV-2026-0166   BUY-06   watch      10d     Rs 77,000  due in 10 day(s); buyer score 20 (poor); 10 prior invoices went overdue
  INV-2026-0157   BUY-10   watch       3d     Rs 44,700  due in 3 day(s); buyer score 7 (poor); 15 prior invoices went overdue
  INV-2026-0177   BUY-12   watch       8d     Rs 17,900  due in 8 day(s); buyer score 29 (poor); 7 prior invoices went overdue
```

Four invoices, not yet overdue, flagged "watch" because two of three
signal categories fired for each (a poor buyer score plus a pattern of
that buyer's other invoices going overdue -- see "Why 'high' needs the
simulator" for why this run never shows "high"). This is confirmed
live, freshly re-run three times against the current repo state -- not a
stale note.

**Step 5 -- law engine.** The interest/tax math, per invoice, with the
exact facts the agent is allowed to state (`what the agent may state
about INV-2026-0016:` followed by the Section 15/16/22/23/37(2)(g)
sentences, each carrying a computed number). Good beat for "every legal
number traces to config, not to the LLM."

**Step 6 -- brain.** `90 decisions (1 handoff, 85 send, 4 stop); dry run,
nothing logged`. Point at two contrasting reason strings in the printed
table -- e.g. a `good band` buyer paced gently vs. a `poor band` one
escalated straight to a higher rung -- to show the rung choice is read
off the score, not guessed.

**Step 7 -- message writer.** `22 messages drafted covering 85 invoices,
8 in Hinglish, 0 fell back to the plain skeleton`. First block printed is
a real Hinglish message:

```
Subject: 4 invoices pending

Sir,

4 invoices abhi tak pending hain:

Invoice INV-2026-0123 — ₹11,32,000 (860 units MCB distribution boards). Due date
2026-07-29 thi, 26 din ho chuke hain.

Payment fell due on 2026-07-29, 45 days from acceptance of the goods (Section 15, MSMED Act 2006).

Interest has accrued from 2026-07-30 as compound interest with monthly rests at 16.50% per annum, being three times the RBI Bank Rate of 5.50% (Section 16, MSMED Act 2006). Interest to date: ₹12,970.83.
...
```

**Step 8 -- promise tracker.** `0 promises on file, 0 open, 0 newly
broken`. This is the correct, expected result for a single-pass run --
say so on camera rather than skipping past it, since it sets up why
Segment 2's "promise made, broken, and caught" beat needs the simulator
instead.

**Step 9 -- post office.** `117 deliveries (85 blocked, 32 would_send)`,
each blocked line reading `sending is off; run with --send-email to
deliver`. Add `--send-email` to the command for the take where you want
a real (test-inbox-only) email to land on screen.

**Steps 10-11** just name the commands that Segment 2 below actually
runs (`sim/run_sim.py --compare`, `report/build_report.py`) -- `main.py`
deliberately doesn't run them itself.

### Why "high" needs the simulator, not a different seed

This is architectural, not a seed-selection problem, and no seed will
ever change it. `early_warnings()` needs 2 of 3 signal categories to
reach "watch" and all 3 to reach "high"
(`config/rules.yaml early_warning.bands`). One category is broken-promise
ratio, which reads the `promises` list. In `main.py`, `Context.promises`
starts as `[]` (`main.py:44`) and nothing in the single-pass pipeline
ever populates it before `stage_early_warning` runs (promise tracker is
step 8, early warning is step 4 -- and even step 8 only sweeps whatever
is already in `context.promises`, which stays empty all through this
pipeline). So at most 2 of 3 signals can ever fire through `main.py`,
capping every result at "watch." A promise has to actually be made and
then broken for "high" to be reachable, and that only happens across
simulated days of buyer replies -- i.e. only through `sim/run_sim.py`.
Segment 2 is where that lives.

### Backups, if seed 42 misbehaves on the day

All three re-confirmed live against the current repo, same command shape
(`python main.py --dry-run --seed N`):

| seed | flagged | watch | high |
|---|---|---|---|
| 42 (primary) | 4 | 4 | 0 |
| 99 | 4 | 4 | 0 |
| 13 | 2 | 2 | 0 |

**Seed 99** is the strongest backup -- same count as 42, all four
reasons are clean two-signal combinations.

**Documented miss, for honesty: seed 555** produces `0 flagged (0 high, 0
watch), 7 low band` -- every invoice in the 14-day window lands in the
low band for that seed. Don't use it for this segment; it's named here so
it's a known, checked fact rather than a surprise if someone else tries
it.

---

## Segment 2 -- the simulator: "high" band + the honest 4-arm comparison, seed 7

```
python sim/run_sim.py --compare --seed 7 --extra-seeds 42,13,99,2024,555 --days 120
python report/build_report.py
```

**Expected runtime, so this is known going into rehearsal, not
discovered live:** the `--compare` command takes **about 3 to 3.5
minutes** on this machine (six seeds x four arms x 120 simulated days
each, all mock-LLM so no network calls). Measured twice, back to back:
**3m16.6s** and **3m9.7s**, and the two runs produced byte-identical
output -- deterministic, just not instant. Budget **4-5 minutes** of
dead air (or cut to it) when planning the recording, and don't start
talking through it expecting it to finish inside a minute.
`report/build_report.py` afterward is fast (a few seconds; it only reads
the audit trail and renders a template, no simulation).

`--extra-seeds` is set explicitly here (instead of the default, which
assumes seed 42 is primary) so the six benchmark seeds
(7, 42, 13, 99, 2024, 555) each run exactly once. This does not change
any headline number -- re-running with seed 7 as primary reproduced the
existing documented result byte-for-byte: agent beats baseline 6/6,
agent+EV beats agent 5/6, agent+EV+learned loses to agent+EV 6/6, mean
delta -Rs 22,53,175. Re-ordering which seed is "primary" only changes
which single seed's narrative gets the detailed printout; the aggregate
is over the same six seeds either way.

### The "high" band, confirmed two ways

First, via the CLI run above -- `audit/audit_log.jsonl` (which holds the
plain agent-arm trail for the primary seed once `--compare` finishes)
contains:

```
2026-09-06  INV-2026-0156  BUY-05  high risk, 3 signal(s): due in 1 day(s); buyer score 43 (poor); broke 1 of last 2 promises; 5 prior invoices went overdue
```

That's simulated day 13 (the world starts 2026-08-24). All three signal
categories fired together: a poor score, a broken promise, and a
pattern of prior overdue invoices.

Second, via the report -- after `python report/build_report.py`, open
`report/out/report.html` and scroll to "Early warning -- invoices
flagged before they went overdue." `INV-2026-0156` / `BUY-05` shows a
red **high** badge (visually distinct from the amber **watch** badges
around it), outstanding ₹26,200, due in 1 day, same reason string.

Neither the CLI run nor `--verbose` prints this line live to the
terminal -- `sim/run_sim.py`'s narrative only prints on a rung change or
a payment/dispute outcome, not on an early-warning crossing. If you want
it visible **live on screen** rather than opened afterward, grep for it
right after the run:

```
python -c "import json; [print(l) for l in open('audit/audit_log.jsonl', encoding='utf-8') if 'early_warning_raised' in l and '\"high\"' in l]"
```

or just cut to the report.html tab you already have open.

### The four-arm printout

The `--compare` run above prints, for the primary seed (7):

```
baseline           recovered  Rs   88,38,375   messages sent  259
agent (ev off)     recovered  Rs 1,44,80,534   messages sent   63
agent+EV (ev on)   recovered  Rs 1,48,33,614   messages sent   59
agent+EV+learned   recovered  Rs 1,16,76,702   messages sent   53

agent recovered Rs 56,42,158 more than the baseline with -196 messages
agent+EV recovered Rs 3,53,079 more than agent (ev off) -- the ablation
agent+EV+learned recovered Rs 31,56,911 less than agent+EV -- the learned-posteriors ablation
```

then, after the five extra seeds finish:

```
agent won on rupees recovered in 6/6 seeds, on avg days-to-pay (fair comparison) in 6/6 seeds
agent+EV beat agent (ev off) on rupees recovered in 5/6 seeds -- the ablation
agent+EV+learned beat agent+EV on rupees recovered in 0/6 seeds -- the learned-posteriors ablation.
  6 seeds is a small sample: mean delta -Rs 22,53,175, range -Rs 31,56,911 to -Rs 5,16,048
```

---

## Segment 3 -- Samadhaan draft (the deadbeat escalation)

`audit/drafts/samadhaan-INV-2026-0016.md` already exists from Segment 1
(any full `main.py`/`sim/run_sim.py` run regenerates it for whichever
invoices reach a handoff). Open it and show the banner:

```
> **BLOCKED — NOT READY TO FILE** · drafted 2026-08-24 · invoice INV-2026-0016
```

with, in section 9 ("Readiness"), the specific blocker:

```
The Udyam registration number on file (UDYAM-XX-00-0000000) is the placeholder
shipped in config/supplier.yaml, not a real registration.
```

This is correct, deliberate behaviour, not a bug to hide --
`engine/samadhaan.py` refuses to mark a draft ready to file while
`config/supplier.yaml`'s Udyam number starts with the placeholder prefix
`UDYAM-XX` (`config/supplier.yaml`'s own header explains why: a draft
that invented a registration number would be worse than one that
honestly says it has none). Every other figure on the draft -- interest,
tax exposure, the statutory due date -- is real, computed, checkable
arithmetic; only the identity block is blocked.

**If you want a "READY TO FILE" take instead of the "BLOCKED" one**, use
this exact value -- format-valid, and confirmed live (in-memory, no file
left touched) to flip `INV-2026-0016`'s draft to `READY TO FILE` with
zero blockers and zero warnings:

```
UDYAM-KA-05-1234567
```

The real Udyam Registration Number format is `UDYAM-<2-letter state
code>-<2-digit district code>-<7-digit serial>` (already documented in
`config/supplier.yaml`'s own comment). This value follows that shape --
`KA` matches the supplier's own state (`config/supplier.yaml`'s
`supplier.state` is Karnataka), `05` is a plausible 2-digit district
code -- but `1234567` as the serial is a deliberately obvious
placeholder pattern, chosen so it reads as synthetic on sight rather
than looking like it could be a real registration number.

To use it, temporarily edit `config/supplier.yaml` line 24, labelling the
change unmistakably as demo-only right on the line:

```yaml
  udyam_registration: "UDYAM-KA-05-1234567"   # DEMO PLACEHOLDER FOR RECORDING -- NOT A REAL UDYAM NUMBER, revert before commit
```

That comment is the label the task asked for: anyone reading the diff,
the file, or a screen-recording of the terminal sees immediately that
this isn't a real registration, not just that it looks synthetic.

This is a decision to make deliberately, not something to default into:
swapping it in makes **every** draft across the whole repo say READY TO
FILE, which is a real behaviour change to a config file, not a cosmetic
demo tweak -- **revert it immediately after recording this segment**
(`git checkout -- config/supplier.yaml`, or just retype the shipped
placeholder `UDYAM-XX-00-0000000` back in) before touching anything else,
so the committed config never carries this value.

---

## The four numbers to say out loud

All four are from the seed-7-primary `--compare` run above, freshly
re-run and matching the existing documented headline in `CLAUDE.md` /
`docs/learning_findings.md` exactly (confirming the primary-seed swap
changed nothing about the aggregate result).

1. **Recovered, by arm** (seed 7, the one on screen):
   baseline **Rs 88,38,375** -> agent **Rs 1,44,80,534** -> agent+EV
   **Rs 1,48,33,614** -> agent+EV+learned **Rs 1,16,76,702**.

2. **Envelopes (messages) sent, by arm** (same run):
   baseline **259** -> agent **63** -> agent+EV **59** -> agent+EV+learned
   **53**. (Fewer messages *and* more money recovered, agent vs.
   baseline -- that contrast is worth stating explicitly.)

3. **Largest hardcoded-vs-measured delta** -- this is the grid comparison
   itself (hand-typed `config/rules.yaml` probability vs. the fitted
   `config/learned_recovery.yaml` posterior for the same cell), a
   percentage-point gap, **not** a rupee outcome:
   **`good_customer` / `firm`: hand-typed 88.0% vs. fitted 61.47%, a
   -26.5 point delta.** Confirmed live via `python scripts/compare_grids.py`,
   which ranks it #1 of every (quadrant, action) cell in the file and
   labels it explicitly `FEATURED PRIMARY EXAMPLE`: n=748 (the largest
   sample of any cell), ci95_width 0.0696 (the tightest) -- the
   best-supported number the fit produced, not a thin-cell fluke. (An
   earlier draft of this doc misquoted this as "1.78pt" -- that number is
   a different cell's ci95_width, not a hand-typed-vs-fitted delta at
   all. -26.5pt is the correct, source-confirmed figure.)

4. **Per-seed win/loss record of the learned arm** (agent+EV+learned vs.
   agent+EV, all 6 benchmark seeds): **0/6** -- it loses on every single
   seed (7, 42, 13, 99, 2024, 555), not just on average. State this
   plainly; it's the honest finding, not a gap to smooth over. The single
   worst seed by rupees is seed 7 itself -- the one on screen --
   where agent+EV+learned recovered **Rs 31,56,911 less** than agent+EV;
   across all six seeds the mean loss is **-Rs 22,53,175**, range
   **-Rs 31,56,911 to -Rs 5,16,048**. (This rupee figure is the
   *consequence*, run downstream through 120 simulated days of decisions
   -- #3 above, the -26.5pt grid delta, is the *cause*. Say them together
   if there's time, but they're one finding, not two.)

---

## Full command reference

```
pytest -q
python main.py --dry-run --seed 42                                                   # Segment 1 -- locked
python main.py --seed 42 --send-email                                                # optional separate take, email beat
python sim/run_sim.py --compare --seed 7 --extra-seeds 42,13,99,2024,555 --days 120   # Segment 2 -- locked, ~3-3.5 min
python report/build_report.py                                                        # then open report/out/report.html
```
