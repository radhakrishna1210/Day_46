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
      Computed and explained only -- the Brain does not read it yet (P3).
- [x] P2 - Recovery-probability + EV model: engine/negotiation.py scores a
      fixed action space (wait/soft_nudge/firm/legal_facts, plus new
      payment_plan/counter_settle/human_handoff/legal_escalation) by
      EV = P(recover) x expected_recovery_paise - cost_paise, all
      config-driven, cost priced from real Gemini 3.7 Flash pricing.
      Computed and ranked only -- the Brain does not read it yet (P3).
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- Phase 2 done. New engine/negotiation.py: recovery_probability(),
  recovery_fraction(), expected_recovery_paise(), action_cost_paise(),
  evaluate_action(), rank_actions(), evaluate_invoice(), explain_action(),
  plus a CLI (`python engine/negotiation.py --explain INVOICE_ID`). New
  config/rules.yaml `negotiation:` block: recovery_probability (flat grid,
  quadrant x action), promise_adjustment, recovery_fraction, cost
  (llm_call_paise cited from real Gemini 3.7 Flash pricing + a cited
  USD->INR rate, human_minute_paise, human_handoff_minutes,
  legal_escalation_minutes). Zero dependency on engine.brain, enforced by
  tests/test_negotiation.py's AST-based no-cycle guard -- Phase 3 needs
  brain.py -> negotiation.py to stay a one-way import.
- DESIGN CALL, confirming this phase's own brief's lean: recovery_probability
  is a flat (quadrant, action) grid, not a weighted formula like
  ability()/willingness(). Those decompose into weighted terms because the
  terms have real units (percent inflow decline -> score points); a
  "probability weight" here would have none, since there is no measured
  recovery-rate data behind any of these numbers -- a visible guess beats a
  guess dressed up as arithmetic.
- A RESULT SURFACED, NOT HIDDEN, for Phase 3 to actually decide about: with
  the shipped grid, rank_actions() puts legal_facts above soft_nudge even for
  a good_customer -- the model has no term for the relationship cost of
  over-escalating a good payer, only P(recover), and assertive contact is
  always modelled as at least as likely to work at near-zero extra cost.
  Whoever wires the Brain to EV in Phase 3 needs to either add a
  relationship-cost term or constrain candidate actions by current rung
  rather than handing the Brain the raw top-EV action unconditionally.
- 829 tests passing (803 + 26: 23 in the new tests/test_negotiation.py
  (including the TC-143 zero-outstanding edge case), plus 3 the existing
  structural guards -- test_no_legal_constants.py's per-file legal-prose/rate
  scans and test_sim_isolation.py's persona-tag scan -- automatically picked
  up now that engine/negotiation.py exists). Zero regressions.
- NEXT: Phase 3 -- wire the Brain to actually choose based on EV. Both
  Phase 1's and Phase 2's tripwires guard this boundary and BOTH get deleted
  together, not before: tests/test_ability_willingness.py::test_the_brain_
  does_not_consume_the_two_axis_score_in_this_phase and tests/test_negotiation
  .py::test_the_brain_does_not_consume_negotiation_in_this_phase. Three more
  things waiting: (a) engine/consolidate.py:46-57's _eligible() (kind ==
  "send") and engine/buyer_panel.py:40,116-127's
  _LADDER_KINDS = frozenset({"wait", "send"}) both silently drop a
  payment_plan/counter_settle action the day brain.py starts emitting one --
  fix both as part of this phase, since it is the one adding the new kinds;
  (b) Action.kind's string-based non-enum design is still unfixed; (c) the
  dead stop_rules.max_per_rung key is still unread by brain.py. Known
  limitation to carry forward: willingness still inherits
  average_delay_days, which is ability-contaminated -- stated in the config
  comment, ARCHITECTURE.md and README rather than hidden.
