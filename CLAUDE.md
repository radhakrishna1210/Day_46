# Revenue Recovery Agent — Project Memory

## What this project is
An AI agent for the Razorpay AI Buildathon 2026 (Track 3: AI Revenue Recovery).
It recovers overdue B2B invoice money for Indian MSMEs: detects overdue invoices,
scores the buyer from payment history, computes legal leverage (MSMED Act),
picks an escalation rung, writes the message (English/Hinglish), tracks promises,
and proves recovery uplift against a baseline bot in simulation.
Full design: see ARCHITECTURE.md (read it when planning any new module).
Hard deadline: submission by Sept 5, 2026. Prefer finished-and-honest over fancy.

## Non-negotiables (the judging bar — never trade these away)
1. Every money-related action must be explainable and written to the audit trail
   (timestamp, invoice, action, reason, rule-or-AI source). No silent actions.
2. Stopping rules are hard limits enforced in code (not left to the LLM):
   max 3 messages per rung, max 5 total per invoice, quiet hours respected,
   opt-out stops everything, detected dispute → immediate human handoff.
3. Messages state facts only. Never threats. Legal numbers must come from
   config/legal.yaml — never invented, never hardcoded in prose or code.
4. No real-world contact: email goes only to the owner's own test inbox
   (address in .env). WhatsApp/SMS are stubs that log "would send".
5. Results are measured honestly: baseline vs agent on the same seeded data,
   including the exceptions list of what we failed to recover and why.

## Architecture rules
- Rules vs AI split (do not blur it):
  RULES: watchdog dates, score math, law math, escalation ladder, stop rules.
  AI (LLM): parsing buyer replies to structured intent, drafting messages,
  ambiguous judgment calls (log the reasoning to audit).
- All LLM calls go through engine/llm.py ONLY. It reads LLM_MODE from .env:
  "mock" = canned deterministic responses (default for dev and for judges
  without a key), "live" = the google-genai SDK using GEMINI_API_KEY from
  .env. Never scatter API calls across modules.
- All tunables live in config/rules.yaml (ladder timings, score weights,
  stop limits) and config/legal.yaml (15/45 day rule, RBI bank rate,
  tax rate, marked "as of Aug 2026"). Code reads config; code never
  embeds these numbers.
- Data layer is JSON in data/seed/. The audit trail is append-only JSONL
  in audit/. No database, no servers.
- Randomness in data generation and simulation must accept a --seed flag
  so experiments are reproducible.

## Code conventions
- Python 3.11+, type hints on public functions, small modules per
  ARCHITECTURE.md layout (engine/, sim/, report/, data/, config/, tests/).
- Money is stored in integer paise internally; format as ₹ only for display.
- Dates: datetime.date everywhere; the simulation clock is passed in,
  never taken from "now" inside engine code (so tests can time-travel).
- Write or update pytest tests for anything in engine/law.py and
  engine/score.py BEFORE marking that module done. Law math bugs are
  disqualifying; tests are cheap insurance.
- Log lines: human-readable, one action per line, no emoji.

## Commands
- Run full simulation:      python main.py --seed 42
- Run baseline comparison:  python sim/run_sim.py --compare --seed 42
- Regenerate fake data:     python data/generate.py --seed 42
- Tests:                    pytest -q
- Build report:             python report/build_report.py

## Git & secrets
- .env holds GEMINI_API_KEY, TEST_INBOX_EMAIL, SMTP creds. .env is in
  .gitignore. Never commit keys; check `git diff` before every commit.
- Do NOT export ANTHROPIC_API_KEY globally in the shell — a global env var
  makes Claude Code bill the API instead of the Pro plan. Keep it in .env,
  loaded only by the app (python-dotenv).
- Commit small and often with clear messages ("law: add 43B(h) calc + tests").
  The repo history is part of the submission story.

## Scope guard (protect the 12 days)
- Do not add features outside ARCHITECTURE.md. If an idea appears, add one
  line to the "Future Work" section of README.md instead of building it.
- Explicitly out of scope: real WhatsApp/SMS APIs, dashboards, auth/login,
  multi-tenant anything, blockchain, fine-tuning models.
- If a task is taking way longer than its day-plan slot, stop and simplify:
  a working simple version beats a broken clever one.

## Current status (update at the end of every session)
- [x] Day 1: repo scaffold, config files, empty main.py pipeline
- [x] Day 2: data factory
- [x] Day 3: score engine + watchdog
- [x] Day 4-5: law engine + tests + Samadhaan draft
- [x] Day 6: brain + writer
- [x] Day 7: channels + promises DONE; the Ctrl+C mid-send gap is closed by
      the send idempotency guard (_already_sent, commit a67455e)
- [x] Day 8: simulator + persona reaction table + P2 fix
- [x] Day 9: experiment + honest report (results.json, report.html)
- [x] E1 - Tier 1 edge cases: promise sanity bounds (real bugs)
- [x] E2 - Tier 2 edge cases: invoice/input validation
- [x] E3 - Tier 3 edge cases: regression tests + edge_cases.md status markup
- [x] E4 - TC-141 end-to-end scenario fixture
- [x] W1 - Early warning (pre-overdue risk surfacing)
- [x] W2 - Buyer-level panel + promise reliability
- [x] W3 - Buyer-level message consolidation
- [x] W4 - Re-run experiment, regenerate report with new numbers
- [x] 10 - README + ARCHITECTURE sync + hygiene
- [x] P0 - Repo hygiene: committed the held doc/rename split, fixed the
      multi-seed audit-trail clobbering bug, regenerated the Quickstart
      artifacts self-consistently (d8abef4, 97e27e4, 449dccb)
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- Phase 0 (repo hygiene) done, three commits: d8abef4 "refactor: rename
  pending-stage stubs to reflect deliberate separate-command design"
  (main.py's two stub stages now print "run separately: <command>" instead of
  the false "not implemented (Day 8/10)", plus 5 unused imports dropped;
  ARCHITECTURE.md updated to match -- stdout text only, no logic touched),
  97e27e4 "docs: add full project walkthrough" (PROJECT_WALKTHROUGH.md,
  ~700 lines), 449dccb "fix: restore primary seed's audit trail after
  multi-seed comparison run".
- The audit-trail bug 449dccb fixes was real and structural: run_agent()
  starts with audit.clear(), so multi_seed_summary()'s extra-seeds loop left
  audit/audit_log.jsonl holding the LAST extra seed's trail (seed 555) while
  results.json reported the primary seed -- and nothing on disk said so. Fix
  mirrors the existing generate.ensure_dataset(primary_seed) restore in the
  same function: new engine/audit.py snapshot()/restore() (bytes, so the
  round-trip is exact), captured before the loop and restored after it. Zero
  extra simulation cost -- it puts back output already paid for, never
  re-runs the primary seed. Guarded permanently by tests/test_run_sim.py::
  test_a_multi_seed_run_leaves_the_audit_trail_matching_the_primary_seed,
  which was verified to actually FAIL against the pre-fix code (git stash),
  not merely pass against the new one.
- Quickstart artifacts are now three-way self-consistent and traceable to
  seed 42: results.json (multi_seed populated, all 6 seeds, 6/6 money and
  6/6 days), report.html (renders every one of those figures), and
  audit_log.jsonl -- whose sha256 after `--compare --seed 42 --days 120` is
  byte-for-byte identical to a standalone run_agent(42, 120)'s own trail
  (7656 rows, 141 sends across 93 invoices == results.json's agent
  invoice_contacts of 141). Note pytest itself rewrites the trail, so run
  the compare command last if you want seed 42's trail on disk.
- 760 tests passing (759 + the new audit-trail guard). One caveat worth
  knowing: during this phase a full-suite run threw a one-off
  OSError [Errno 22] on data/seed/invoices.json inside the new test, on a
  run that also took 466s instead of the usual ~50s -- this machine has
  heavy intermittent disk contention (AV scanning). data/generate.py's
  _write_json() is a plain non-atomic Path.write_text, so any test churning
  the dataset can hit this. Trimmed the new test from 2 extra seeds to 1
  (same proof, a third of the file churn); 4/4 green full-suite runs since.
  If it ever recurs, make _write_json() atomic (tmp file + os.replace)
  rather than chasing the test.
- NEXT: Phase 1 -- Ability/Willingness score split. It starts fresh and
  needs engine/score.py, brain.py, law.py, sim/personas.py and run_sim.py
  exactly as they are now (Phase 0 deliberately left all five untouched).
  Two known items still parked by decision, both to be folded into Phase 2
  (which introduces payment_plan/counter_settlement): Action.kind's
  string-based non-enum design with two silent-failure consumers in
  consolidate.py/buyer_panel.py, and the dead stop_rules.max_per_rung config
  key that engine/brain.py never reads.
