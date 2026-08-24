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
- [x] Day 7: channels + promises DONE; the Ctrl+C mid-send gap is closed by
      the send idempotency guard (_already_sent, commit a67455e)
- [x] Day 8: simulator + persona reaction table + P2 fix
- [x] Day 9: experiment + honest report (results.json, report.html)
- [x] E1 - Tier 1 edge cases: promise sanity bounds (real bugs)
- [x] E2 - Tier 2 edge cases: invoice/input validation
- [ ] E3 - Tier 3 edge cases: regression tests + edge_cases.md status markup
- [ ] E4 - TC-141 end-to-end scenario fixture
- [ ] W1 - Early warning (pre-overdue risk surfacing)
- [ ] W2 - Trader-level panel + promise reliability
- [ ] W3 - Buyer-level message consolidation
- [ ] W4 - Re-run experiment, regenerate report with new numbers
- [ ] 10 - README + ARCHITECTURE sync + hygiene
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- E2 done: new engine/validate.py rejects TC-045/049/050/051/053/054 at a
  single choke point (watchdog.overdue_invoices() -- nothing malformed ever
  reaches law.py or brain.py). An invalid invoice is never dropped: audited
  once per run (invoice_validation_failed) and surfaced in the SAME
  exceptions list sim/run_sim.py already builds, with a plain-English reason.
  TC-032/TC-036 (dispute+promise, multi-amount in one reply) got a documented
  precedence rule in the parse_reply() prompt (dispute beats promise; earliest
  instalment wins) plus a coarse rule-based audit trip-wire -- no schema
  change. data/generate.py --with-malformed makes all eight reachable from
  real data; default seed-42 output confirmed byte-identical without the flag.
- Subtlety worth remembering: TC-050's defect (issue_date in the future) is
  clock-relative and self-heals once simulated time passes it, unlike the
  other five which are permanently invalid. sim/run_sim.py therefore checks
  validity once at last_day (not day0) when building the final report --
  checking at day0 would freeze a stale verdict for invoices that later
  became perfectly ordinary. See tests/test_run_sim.py's regression test.
- Two docs from an earlier session: docs/edge_cases.md (141 catalogued edge
  cases, drives E1-E4) and docs/winning_layer.md (roadmap; only W1-W3 are
  being built for this submission, the rest stays Future Work).
- LLM provider is Gemini via engine/llm.py (LLM_MODE=live uses google-genai +
  GEMINI_API_KEY, flash tier only), not Anthropic, despite the Architecture
  rules section above being unrevised. sim/run_sim.py force-locks LLM_MODE to
  mock for the whole day-loop regardless of .env.
- 647 tests passing (pytest -q).
