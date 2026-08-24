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
  without a key), "live" = anthropic SDK using ANTHROPIC_API_KEY from .env.
  Never scatter API calls across modules.
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
- .env holds ANTHROPIC_API_KEY, TEST_INBOX_EMAIL, SMTP creds. .env is in
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
- [~] Day 7: channels + promises DONE; real-inbox send confirmed working but
      interrupted mid-run (see notes) -- re-run pending
- [x] Day 8: simulator + persona reaction table + P2 fix
- [x] Day 9: experiment + honest report (results.json, report.html)
- [ ] Day 10: README, ARCHITECTURE.md polish, wire scoreboard into main.py
- [ ] Day 11-12: video, fresh-machine test, submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- Day 9 done: `data.generate.ensure_dataset(seed)` (new) regenerates
  data/seed/ whenever the on-disk seed doesn't match the requested one --
  main.py and sim/run_sim.py both used to check existence only, so `--seed 7`
  used to silently replay whatever was on disk. sim/run_sim.py now computes
  avg_days_to_pay, per_rung/per_attempt effectiveness, handoff/stop reason
  buckets, and a rich `exceptions` list (buyer, persona, status, reason);
  `--compare` writes report/out/results.json, and
  `python report/build_report.py` renders report/out/report.html (Jinja2,
  report/templates/report.html.j2). Extended same day with two
  credibility features: `--extra-seeds` (default 5 fixed seeds beyond
  --seed) runs the full comparison on each and the report's "Is this just
  one lucky seed?" table shows the win rate (6/6 on both money and fair
  days-to-pay, seed 42 + the 5 defaults) -- and a static Methodology
  section citing the actual test/mechanism behind each anti-rigging claim
  (persona isolation, identical invoice sets, mocked LLM output,
  conservation, multi-seed proof, full audit trail). Fixed a false
  positive this surfaced: tests/test_sim_isolation.py's guard scanned
  report/ too and flagged build_report.py for *naming*
  hidden_personas.json in that Methodology text; narrowed the guard to
  engine/ + main.py (the actual decision pipeline) with a dedicated test
  that build_report.py's mention stays confined to documentation, never a
  functional read. 600 tests.
- IMPORTANT finding, reported honestly rather than tuned away: the RAW "avg
  days to pay" (each agent's own average over whatever it recovered) makes
  the agent look slightly slower than baseline (95.5d vs 93.3d, seed 42) --
  this is a selection-bias artifact, not a real regression: baseline's
  average excludes every hard invoice it simply never recovers, while the
  agent goes after them too. On the matched set both runs actually
  recovered, the agent is faster (97.7d vs 101.2d). Per your call, the report
  and results.json show BOTH numbers, clearly labeled -- see
  sim.run_sim.matched_avg_days_to_pay's docstring. The multi-seed test
  (tests/test_experiment.py) asserts against the fair matched number, not
  the raw one, and passes on seeds 42, 7 and 2024.
- Razorpay Payment Links reminders confirmed via live web search (not
  memory): cap at 3, scheduled off the link's date not buyer behaviour, no
  personalisation -- our baseline already matched this; cited in
  sim/run_sim.py next to BASELINE_MAX_MESSAGES.
- CALIBRATION STATUS unchanged since 2026-08-24 (still not re-run): parse_reply
  is confirmed against the real model; draft_message/judgment_call are not
  -- blocked on the GEMINI free-tier daily quota. Re-run `--calibrate` after
  a reset.
- OUTSTANDING from Day 7, still unresolved: a narrow gap where Ctrl+C landing
  between a successful SMTP send and the audit write during `--send-email`
  would leave one email real but unaudited. Waiting on the user to check the
  actual test inbox count (20 vs 21) before deciding whether to harden it.
- Next: Day 10 -- README results table + demo instructions, wire the
  scoreboard stage into main.py's own pipeline (currently only reachable via
  sim/run_sim.py + report/build_report.py separately), then the Day 7 inbox
  check above.
