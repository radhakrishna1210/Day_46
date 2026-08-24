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
- [ ] Day 9: full baseline-vs-agent report + exceptions list
- [ ] Day 10: report, README, ARCHITECTURE.md polish
- [ ] Day 11-12: video, fresh-machine test, submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- Day 8 done: sim/personas.py (reaction table + load_hidden_personas) and
  sim/run_sim.py (run_agent/run_baseline, day-loop, --compare, --verbose)
  built. P2 fixed in engine/brain.py: a first-ever contact now opens one
  rung above the pacing band's base once one cadence interval has already
  passed uncontacted, capped at +1 (an uncapped days_overdue // cadence
  version was tried first and instantly handed off ~70% of the real dataset
  on day 1 with zero messages ever sent -- reverted for the capped version;
  see the comment above the backlog_steps block). LLM_MODE is force-mocked
  inside sim/run_sim.py's day-loop (_forced_mock_mode, os.environ, restored
  after) regardless of .env; engine/llm.py's --calibrate/--list-models stay
  the only live spot-check path. 584 tests.
- `python sim/run_sim.py --compare --seed 42 --days 120` (or 60) works
  end-to-end: real history/promises threaded day-to-day (brain.decide is
  never called with history=[] here), persona reactions run through the
  real promises.parse_reply/apply_reply path, habitual_delayer invoices
  visibly climb rung 2->3, forgetful buyers pay after one message, a
  deadbeat hits HANDOFF. Sample 60-day run: agent recovered ~Rs 22.3L more
  than the baseline with 112 fewer messages sent (252 vs 140) and correctly
  escalated 41 invoices to a human vs the baseline's 0.
- sim/hidden_personas.json is read only by sim/personas.load_hidden_personas.
  tests/test_sim_isolation.py proves engine/ + main.py can never reference
  it -- verified by hand to actually fail when a leak is introduced, then
  reverted (same discipline as test_no_legal_constants.py).
- CALIBRATION STATUS unchanged since 2026-08-24 (still not re-run): parse_reply
  is confirmed against the real model; draft_message/judgment_call are not
  -- blocked on the GEMINI free-tier daily quota. Re-run `--calibrate` after
  a reset. All three purposes still on gemini-3.7-flash (cost/quality
  tradeoff, noted in README Future Work).
- OUTSTANDING from Day 7, still unresolved: the interrupted `--send-email`
  run (2026-08-24, seed 42) got through 20 real sends + 5 whatsapp stubs
  before a Ctrl+C; an idempotency guard (`_already_sent`, SKIPPED status)
  now makes re-running safe, but a narrow gap remains where Ctrl+C landing
  between a successful SMTP send and the audit write would leave one email
  real but unaudited (`except Exception` in `_send_email` cannot catch a
  BaseException). Waiting on the user to check the actual test inbox count
  (20 vs 21) before deciding whether to harden it.
- Next: Day 9 -- turn `--compare`'s numbers into report/build_report.py's
  HTML report + exceptions list (what wasn't recovered, and why); then
  confirm inbox count and re-run `--send-email` clean.
