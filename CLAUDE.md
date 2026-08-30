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
- [x] P1 - Ability/Willingness score split: synthetic inflow signals on the
      buyer record, a two-axis score + 4-way quadrant, all config-driven.
      Computed and explained only -- the Brain does not read it yet (P2).
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- Phase 1 done. New engine/ability_willingness.py: ability ("can they pay?",
  from inflow trend/volatility/failed payments/invoice-vs-typical-month),
  willingness ("will they pay?", a relabel of the legacy formula), quadrant()
  (good_customer / cash_flow_problem / can_pay_but_wont / high_risk),
  two_axis_score(), explain_ability(), explain_willingness(), plus a CLI
  (`python engine/ability_willingness.py --explain BUY-07`). data/generate.py
  now puts monthly_inflow_paise (6-12 months, most recent last) and
  failed_payment_count on every buyer, correlated with the hidden persona via
  three new Persona fields (inflow_drift, inflow_volatility,
  failed_payment_chance). SCHEMA_VERSION 1 -> 2.
- TWO DESIGN CHOICES I made where the plan left it open, both worth knowing
  before Phase 2. (1) ability/willingness/quadrant are NOT extra keys on
  score_buyer() -- they hang off a separate two_axis_score() that composes on
  top of it. Forced: tests/test_score.py::test_score_record_is_self_describing
  asserts set(result) == exactly 9 keys, so adding keys and "test_score.py
  passes unmodified" were mutually exclusive. Better anyway, since ability
  depends on a SPECIFIC invoice and score_buyer() is per-buyer. (2) The
  quadrant has its OWN thresholds (score.quadrant.ability_high_from /
  willingness_high_from, both 50), not score.bands -- bands defines two edges
  for a three-way pacing split; a 2x2 needs one edge per axis, and borrowing
  one would couple "is this buyer able" to "does the brain start at rung 2".
- The additive promise was verified end to end, not asserted: invoices are
  BYTE-IDENTICAL before/after on seeds 42/7/555 (only the intended
  schema_version bump differs), and a fresh 6-seed --compare gives exactly the
  same recovered_paise as pre-Phase-1 (baseline 1368047240, agent 1597922941,
  6/6 and 6/6). The mechanism: _add_inflow_signals() runs on its OWN RNG
  stream (random.Random(f"{seed}:inflow")) after the world is built -- drawing
  from the shared rng would have shifted every later draw and rewritten every
  invoice. tests/test_data.py::test_the_inflow_signals_are_drawn_from_their_
  own_random_stream pins that permanently via rng.getstate().
- 803 tests passing (760 + 43: 29 in the new tests/test_ability_willingness.py,
  14 in test_data.py). Zero regressions; test_score.py ran unmodified. Also
  moved score.py's last two hardcoded tunables into config as score.trend
  .window_days/.noise_floor. New config keys for Phase 2 to reference:
  score.trend.*, score.willingness.{base,min,max,weights.*},
  score.ability.{base,min,max,weights.*,volatility_floor_pct,recent_months,
  min_months_for_trend}, score.quadrant.*.
- NEXT: Phase 2 -- make the Brain act on the quadrant (payment_plan /
  counter_settlement action kinds). Three things waiting for it: (a)
  tests/test_ability_willingness.py::test_the_brain_does_not_consume_the_two_
  axis_score_in_this_phase is a deliberate tripwire that WILL fail the moment
  brain.py imports this -- delete it as part of Phase 2, it marks the boundary
  on purpose; (b) Action.kind's string-based non-enum design and its two
  silent-failure consumers in consolidate.py/buyer_panel.py should be fixed
  there, since that is the phase adding new kinds; (c) the dead
  stop_rules.max_per_rung key is still unread by brain.py. Known limitation to
  carry forward: willingness still inherits average_delay_days, which is
  ability-contaminated (a broke buyer loses willingness points too) -- stated
  in the config comment, ARCHITECTURE.md and README rather than hidden.
