# Demo runbook

Exact commands for the live demo/video, the output each one actually
produces (captured from real runs against this repo on 2026-09-04, not
guessed), and the four numbers to say out loud. Re-running is
deterministic (same seed -> same numbers) as long as `config/rules.yaml`,
`config/legal.yaml` and the persona tables in `sim/personas.py` are
unchanged.

**One seed for the whole demo: seed 7.**

* Segment 1 is the live single-pass agent, `python main.py --dry-run --seed 7`.
  Watchdog, buyer scoring, the early-warning watch band, the Brain
  choosing a rung per invoice, a Hinglish message, the Law Engine's
  interest/tax math, the post office.
* Segment 2 is the simulator on the same seed,
  `python sim/run_sim.py --compare --seed 7 ...`. The "high"
  early-warning band and the honest four-arm recovery comparison.

The "high" band shows up in Segment 2 only. That is **architectural, not
a seed choice** -- `main.py` never populates the broken-promise signal,
so a single-pass run tops out at "watch" for every seed. See "Why 'high'
needs the simulator" below; no seed changes it.

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
`requirements.txt`). Run it **alone** -- a second process touching
`data/seed/` or `audit/` at the same time produces false failures.

Confirm the shipped defaults are still what you think they are:
`config/rules.yaml`'s `learning.enabled: false` and `brain.ev_mode: off`.
A fresh clone with no flags flipped reproduces the pre-learning agent
exactly -- if you want to demo the learning layer live, that's a
deliberate, visible flag flip, not the default state.

If you have run anything against the repo since the last artifact commit
(pytest included), regenerate the committed set before recording so the
report and dashboard match seed 7's trail:

```
python scripts/regen.py
```

This runs the simulation, the report and the dashboard in the one order
that keeps them consistent (see "Regenerating the artifacts" at the
bottom), and prints which files changed. It does not commit.

---

## Segment 1 -- the live agent, seed 7

**Locked command:**
```
python main.py --dry-run --seed 7
```

`--dry-run` is part of the locked command, not a rehearsal-only option:
Segment 2's `run_agent()` unconditionally clears and rewrites
`audit/audit_log.jsonl` at the start of its own run (that's how the
simulator guarantees a fresh, self-consistent trail for its seed and
window), so anything Segment 1 wrote there would be silently overwritten
the moment Segment 2 runs anyway. `--dry-run` just makes that explicit
and keeps this segment fast and repeatable across rehearsal takes.

If you also want the "a real email lands in the inbox" beat, that's a
**separate, additional take**, not part of this locked command --
`--send-email` needs `--dry-run` left off (dry-run only silences the
audit trail, not the send, and sending while `--dry-run` claims "nothing
logged" would violate non-negotiable #1):
```
python main.py --seed 7 --send-email
```

### What comes back, in order

**Step 1 -- data factory.** `20 buyers, 240 invoices (100 current)`. If
`data/seed/` doesn't already hold seed 7's data, this line reads
"dataset missing or built for a different seed, generating now" first --
expected on a fresh clone, harmless.

**Step 2 -- watchdog.** `90 overdue of 100 unsettled, Rs 2,36,22,100 at
risk, as of 2026-08-24`. That "as of" date is the world clock, not the
wall clock -- the simulation date is passed into engine code, never read
from `now()`, so tests can time-travel.

**Step 3 -- score engine.** `20 buyers scored, worst BUY-01 at 0/100, 3
on low confidence`. "Low confidence" = too few settled invoices to trust
the score yet; the Brain paces those buyers as one band gentler (you'll
see "paced as medium: low confidence" strings in step 6).

**Step 4 -- early warning.** This is the beat to slow down on:

```
step 4: early warning -- flag invoices approaching due date with bad signals
  3 flagged (0 high, 3 watch), 5 low band, within 14d of due

  invoice         buyer    band    due in   outstanding  reasons
  INV-2026-0165   BUY-12   watch      11d   Rs 4,46,400  due in 11 day(s); buyer score 32 (poor); 11 prior invoices went overdue
  INV-2026-0160   BUY-01   watch       4d     Rs 41,700  due in 4 day(s); buyer score 0 (poor); 9 prior invoices went overdue
  INV-2026-0163   BUY-15   watch      13d     Rs 14,600  due in 13 day(s); buyer score 21 (poor); 7 prior invoices went overdue
```

Three invoices, not yet overdue, flagged "watch" because two of the three
signal categories fired for each (a poor buyer score, plus a pattern of
that buyer's other invoices going overdue). None reach "high" -- see "Why
'high' needs the simulator" for why a single-pass run never does.
Confirmed live against the current repo on 2026-09-04.

**Step 5 -- law engine.** `Rs 6,16,249.97 interest accrued at 16.50% (21
void terms), Rs 70,86,630 of buyer tax exposure, 1 held for dispute`.
Then the per-invoice interest/tax table, and for each the exact facts the
agent is allowed to state -- `what the agent may state about
INV-2026-0017:` followed by the Section 15 / 16 / 22 / 23 / 37(2)(g)
sentences, each carrying a computed number. Good beat for "every legal
number traces to `config/legal.yaml`, not to the LLM." **"21 void terms"**
= 21 of the 100 current invoices carried a contract payment term longer
than the MSMED Act's 45-day ceiling, so the statutory due date was
recomputed down to the ceiling.

**Step 6 -- brain.** `90 decisions (1 handoff, 88 send, 1 stop); dry run,
nothing logged`. The one handoff is the disputed invoice from step 5.
Point at two contrasting rows in the printed table:

```
INV-2026-0017   BUY-11   send   3   rule   score 49 (poor band) starts at rung 2; 148 days overdue; ceiling 4; ... paced one rung ahead of the base for the backlog
INV-2026-0120   BUY-19   send   2   rule   score 93 (good band) starts at rung 1;  27 days overdue; ceiling 2; ... paced one rung ahead of the base for the backlog
```

A poor-band buyer 148 days overdue opens at rung 2 and is paced to rung 3
with headroom left to the rung-4 ceiling; a good-band buyer 27 days
overdue opens at rung 1, paced to rung 2, which is also its statutory
ceiling. The rung is read off the score and the law -- and `src` reads
`rule`, not `ai`, on every row.

**Step 7 -- message writer.** `23 messages drafted covering 88 invoices,
8 in Hinglish, 0 fell back to the plain skeleton`. Messages are
consolidated per buyer -- one email covering all of that buyer's overdue
invoices -- which is why 23 messages cover 88 invoices. The first block
printed is a real Hinglish message; its INV-2026-0017 line:

```
Invoice INV-2026-0017 — ₹10,80,600 (25740 units galvanised hinges, batch B-9900). Due date
2026-03-29 thi, 148 din ho chuke hain.

Payment fell due on 2026-03-29, 30 days from acceptance of the goods (Section 15, MSMED Act 2006).

Interest has accrued from 2026-03-30 as compound interest with monthly rests at 16.50% per annum, being three times the RBI Bank Rate of 5.50% (Section 16, MSMED Act 2006). Interest to date: ₹73,747.13.
```

Facts only, no threats. The Hindi is only connective tissue ("abhi tak
pending hain", "din ho chuke hain"); every legal sentence stays in
precise English.

**Step 8 -- promise tracker.** `0 promises on file, 0 open, 0 newly
broken`. The correct, expected result for a single-pass run -- say so on
camera rather than skipping past it, since it sets up why Segment 2's
"promise made, broken, and caught" beat needs the simulator instead.

**Step 9 -- post office.** `113 deliveries (88 blocked, 25 would_send)`.
Each blocked line reads `sending is off; run with --send-email to
deliver`; `would_send` is the stubbed WhatsApp/SMS channels, which log
"would send" and never call an API. Add `--send-email` to the command for
the take where a real (test-inbox-only) email lands on screen.

**Steps 10-11** just name the commands Segment 2 runs
(`sim/run_sim.py --compare`, `report/build_report.py`) -- `main.py`
deliberately doesn't run them itself.

### Why "high" needs the simulator, not a different seed

This is architectural, and no seed will change it. `early_warnings()`
needs 2 of 3 signal categories to reach "watch" and all 3 to reach "high"
(`config/rules.yaml` `early_warning.bands`). One category is
broken-promise ratio, which reads the `promises` list. In `main.py`,
`Context.promises` starts as `[]` (`main.py:44`) and nothing in the
single-pass pipeline populates it before `stage_early_warning` runs
(promise tracker is step 8, early warning is step 4 -- and step 8 only
sweeps whatever is already in `context.promises`, which stays empty all
through this pipeline). So at most 2 of 3 signals can ever fire through
`main.py`, capping every result at "watch." A promise has to actually be
made and then broken for "high" to be reachable, and that only happens
across simulated days of buyer replies -- i.e. only through
`sim/run_sim.py`. Segment 2 is where that lives.

### Backups, if seed 7's early-warning table misbehaves on the day

All re-confirmed live on 2026-09-04, same command shape
(`python main.py --dry-run --seed N`), step 4 line only:

| seed | flagged | watch | high |
|---|---|---|---|
| 7 (primary) | 3 | 3 | 0 |
| 42 | 4 | 4 | 0 |
| 99 | 4 | 4 | 0 |
| 13 | 2 | 2 | 0 |
| 2024 | 1 | 1 | 0 |

**Seeds 42 and 99** are the strongest backups -- four clean two-signal
watch rows each. Switching Segment 1 to one of them means Segment 2's
`--compare` command changes too (put the chosen seed first in `--seed`,
demote 7 into `--extra-seeds`); the six-seed aggregate is identical
either way, only the on-screen narrative seed changes.

**Documented miss, for honesty: seed 555** produces `0 flagged (0 high, 0
watch), 7 low band` -- every invoice in the 14-day window lands low for
that seed. Don't use it for Segment 1; it's named here so it's a known,
checked fact rather than a surprise.

---

## Segment 2 -- the simulator: "high" band + the honest 4-arm comparison, seed 7

```
python sim/run_sim.py --compare --seed 7 --extra-seeds 42,13,99,2024,555 --days 120
python report/build_report.py
```

**Expected runtime, so this is known going into rehearsal, not discovered
live:** the `--compare` command takes **roughly 3 to 5 minutes** on this
machine (six seeds x four arms x 120 simulated days each, all mock-LLM so
no network calls) -- observed between **3m10s** and **4m40s** across runs,
depending on background load, always byte-identical output. Budget
**5-6 minutes** of dead air, or cut to it. `report/build_report.py`
afterward is fast (a second or two; it reads the audit trail and renders a
template, no simulation).

`--extra-seeds` is passed explicitly because `sim/run_sim.py`'s own
default is still `--seed 42`. Putting seed 7 first, with 42 demoted to an
extra, runs the same six benchmark seeds (7, 42, 13, 99, 2024, 555) once
each and only changes which seed's narrative gets the detailed printout.
This does not move any headline number -- re-running with seed 7 as
primary reproduces the documented result byte-for-byte: agent beats
baseline 6/6, agent+EV beats agent 5/6, agent+EV+learned loses to
agent+EV 6/6, mean delta -Rs 22,53,175.

### The "high" band, confirmed two ways

First, via the CLI run above -- `audit/audit_log.jsonl` (which holds the
plain agent-arm trail for the primary seed once `--compare` finishes)
contains:

```
2026-09-06  INV-2026-0156  BUY-05  high risk, 3 signal(s): due in 1 day(s); buyer score 43 (poor); broke 1 of last 2 promises; 5 prior invoices went overdue
```

That's simulated day 13 (the world starts 2026-08-24). All three signal
categories fired together: a poor score, a broken promise, and a pattern
of prior overdue invoices.

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

The `--compare` run above prints one block per arm for the primary seed
(7) -- the baseline block in full, the other three abbreviated here to the
lines worth reading on camera:

```
baseline
  recovered                Rs 88,38,375
  outstanding            Rs 1,83,39,024
  of which disputed        Rs 67,29,600 (20 invoices)
  avg days to pay                  99.7
  messages sent                     259
  escalated to human                  0

agent (ev off)      recovered Rs 1,44,80,534   messages sent 63   escalated to human 47
agent+EV (ev on)    recovered Rs 1,48,33,614   messages sent 59   escalated to human 46
agent+EV+learned    recovered Rs 1,16,76,702   messages sent 53   escalated to human 43

agent recovered Rs 56,42,158 more than the baseline with -196 messages
agent+EV recovered Rs 3,53,079 more than agent (ev off) -- the ablation
agent+EV+learned recovered Rs 31,56,911 less than agent+EV -- the learned-posteriors ablation
avg days to pay, 21 invoices BOTH recovered (the fair comparison): baseline 99.4, agent 95.4
```

then, after the five extra seeds finish, verbatim:

```
agent won on rupees recovered in 6/6 seeds, on avg days-to-pay (fair comparison) in 6/6 seeds
agent+EV beat agent (ev off) on rupees recovered in 5/6 seeds -- the ablation
agent+EV+learned beat agent+EV on rupees recovered in 0/6 seeds -- the learned-posteriors ablation. 6 seeds is a small sample: mean delta -Rs 22,53,175, range -Rs 31,56,911 to -Rs 5,16,048 -- read the range, not just the win rate or the mean, before drawing a conclusion
```

---

## Segment 3 -- the Samadhaan draft (the deadbeat escalation)

The Samadhaan reference draft is **generated at build time and shown in
the dashboard**, not left as a file on disk. Nothing in the runtime
pipeline (`main.py`, `sim/run_sim.py`) writes a Samadhaan markdown file --
`engine/samadhaan.py`'s `write_draft()` is only ever called by its tests.
`scripts/build_dashboard.py` calls `engine/samadhaan.build_draft()`
in-process and renders it into the dashboard's Section 6, "Handoff &
Samadhaan," for the largest invoice that reached the rung-4 ceiling in the
120-day seed-7 run.

Open `report/out/dashboard.html` (or the published page) and scroll to
Section 6. For seed 7 that invoice is **INV-2026-0066, Meridian Logistics
India Pvt Ltd, ₹7,16,000 outstanding**, as of 2026-12-21. The draft
carries the banner:

```
BLOCKED — NOT READY TO FILE
```

with the specific blocker:

```
The Udyam registration number on file (UDYAM-XX-00-0000000) is the placeholder
shipped in config/supplier.yaml, not a real registration.
```

This is correct, deliberate behaviour, not a bug to hide.
`engine/samadhaan.py` refuses to mark a draft ready to file while
`config/supplier.yaml`'s Udyam number starts with the placeholder prefix
`UDYAM-XX` (that file's own header explains why: a draft that invented a
registration number would be worse than one that honestly says it has
none). Every other figure on the draft -- interest, tax exposure, the
statutory due date -- is real, computed, checkable arithmetic; only the
identity block is blocked.

**If you want a "READY TO FILE" take instead of the "BLOCKED" one**,
temporarily give `config/supplier.yaml` a format-valid Udyam number and
rebuild the dashboard. Use this exact value -- it follows the real format
(`UDYAM-<2-letter state code>-<2-digit district>-<7-digit serial>`, `KA`
matching the supplier's own state), but `1234567` as the serial is a
deliberately obvious placeholder so it reads as synthetic on sight:

```yaml
  udyam_registration: "UDYAM-KA-05-1234567"   # DEMO PLACEHOLDER FOR RECORDING -- NOT A REAL UDYAM NUMBER, revert before commit
```

That inline comment is the label: anyone reading the diff, the file, or a
screen recording sees at once that it isn't a real registration. Then:

```
python scripts/build_dashboard.py --seed 7
```

and Section 6's INV-2026-0066 draft flips to READY TO FILE with zero
blockers and zero warnings (its only blocker was the Udyam prefix, and it
has no outstanding warnings).

This is a decision to make deliberately, not default into: the swap makes
**every** draft across the repo say READY TO FILE, which is a real
behaviour change to a config file. **Revert it immediately after
recording**, before touching anything else, so neither the committed
config nor the committed artifacts ever carry this value:

```
git checkout -- config/supplier.yaml report/out/dashboard.html report/out/dashboard.json docs/index.html
```

(the last three undo the rebuilt dashboard; `git checkout` on the config
alone would leave the READY-TO-FILE draft baked into the committed HTML).

---

## The four numbers to say out loud

All four are from the seed-7 `--compare` run above, and match the
documented headline in `CLAUDE.md` / `docs/learning_findings.md` exactly.

1. **Recovered, by arm** (seed 7, the one on screen):
   baseline **Rs 88,38,375** -> agent **Rs 1,44,80,534** -> agent+EV
   **Rs 1,48,33,614** -> agent+EV+learned **Rs 1,16,76,702**.

2. **Envelopes (messages) sent, by arm** (same run):
   baseline **259** -> agent **63** -> agent+EV **59** -> agent+EV+learned
   **53**. (Fewer messages *and* more money recovered, agent vs.
   baseline -- state that contrast explicitly.)

3. **Largest hardcoded-vs-measured delta** -- the grid comparison itself
   (hand-typed `config/rules.yaml` probability vs. the fitted
   `config/learned_recovery.yaml` posterior for the same cell), a
   percentage-point gap, **not** a rupee outcome:
   **`good_customer` / `firm`: hand-typed 88.0% vs. fitted 61.47%, a
   -26.5 point delta.** Confirmed live via `python scripts/compare_grids.py`,
   which ranks it #1 of every (quadrant, action) cell in the file and
   labels it `FEATURED PRIMARY EXAMPLE`: n=748 (the largest sample of any
   cell), ci95_width 0.0696 (the tightest) -- the best-supported number
   the fit produced, not a thin-cell fluke.

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

## Regenerating the artifacts

Five files are committed so a reviewer sees the measured result without
running the simulation: `report/out/results.json`,
`report/out/report.html`, `report/out/dashboard.html`,
`report/out/dashboard.json`, `docs/index.html` (the GitHub Pages copy of
the dashboard). All five are downstream of one seed-7 run and must be
rebuilt together, in order:

```
python scripts/regen.py
```

That script runs, in sequence and stopping loudly at the first failure:

1. `sim/run_sim.py --compare --seed 7 --extra-seeds 42,13,99,2024,555 --days 120`
   -> `results.json`, and rewrites `audit/audit_log.jsonl`
2. `report/build_report.py` -> `report.html` (reads `results.json` **and**
   the trail from step 1)
3. `scripts/build_dashboard.py --seed 7` -> `dashboard.html`,
   `dashboard.json`, and `docs/index.html` in one render

The order matters because steps 2 and 3 both read
`audit/audit_log.jsonl`, and that file is not hermetic -- whatever wrote
it last wins. Running `pytest` or `python main.py` between the simulation
and the report silently builds the report against the wrong trail. The
script prints which artifacts changed at the end; it never commits or
pushes.

---

## Full command reference

```
pytest -q                                                                           # pre-flight, run alone
python scripts/regen.py                                                              # rebuild committed artifacts if the repo has been touched
python main.py --dry-run --seed 7                                                    # Segment 1 -- locked
python main.py --seed 7 --send-email                                                 # optional separate take, email beat
python sim/run_sim.py --compare --seed 7 --extra-seeds 42,13,99,2024,555 --days 120  # Segment 2 -- locked, ~3-5 min
python report/build_report.py                                                        # then open report/out/report.html
python scripts/compare_grids.py                                                      # the -26.5pt grid delta (number 3)
```
