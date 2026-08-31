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
      Computed and ranked only -- the Brain did not read it yet (that was P3).
- [x] P3 - Wired the Brain to EV: engine/brain.py's decide() can now replace
      its unconditional SEND fallthrough with negotiation.rank_actions()'s
      top pick, behind config/rules.yaml's brain.ev_mode (shipped "off").
      New config/rules.yaml negotiation.eligible_actions table gates
      candidates per quadrant (the good_customer relationship-cost fix);
      the legal ceiling gates human_handoff/legal_escalation separately.
      New Action.kind values payment_plan/counter_settle; both landmines
      fixed (consolidate.py _eligible(), buyer_panel.py _LADDER_KINDS).
      sim/run_sim.py's day loop now feeds brain.decide() a two_axis_score()
      instead of score_buyer() (byte-identical with ev_mode off, proven by
      a pinned seed-42 snapshot test in tests/test_run_sim.py).
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- Phase 3 done and committed (see git log for the hash(es)). decide()'s new
  EV branch, config/rules.yaml's brain.ev_mode (off) + negotiation.
  eligible_actions, new Action kinds payment_plan/counter_settle, both
  landmines fixed (consolidate.py _eligible(), buyer_panel.py
  _LADDER_KINDS), sim/run_sim.py's day loop feeding brain.decide() a
  two_axis_score(). Both Phase 1/2 tripwires replaced with their inverse.
- CORRECTION found on follow-up review, worth knowing if this area is
  touched again: the handoff-reachability gate was FIRST written as
  "available_rung == HANDOFF_RUNG" (the legal ceiling), which is strictly
  MORE permissive than decide()'s own non-EV rung-4 step -- a first-ever
  contact can have a wide-open ceiling while the escalation walk's own
  `chosen` sits at rung 1-2 (the backlog formula never desires more than
  base+1 on a first contact), so the ceiling-only gate could have sent EV
  straight to a handoff sooner than the ordinary walk ever would. Fixed to
  gate on `chosen` reaching HANDOFF_RUNG instead -- the IDENTICAL condition
  decide()'s own step 8 uses. Net effect, stated plainly rather than buried:
  since step 8 already intercepts and returns HANDOFF, unconditionally,
  every time that condition is true (before the EV branch ever runs),
  human_handoff/legal_escalation are as-shipped PERMANENTLY UNREACHABLE
  through decide()'s EV branch -- EV can only choose a different kind of
  action among what the escalation walk already made reachable, never jump
  a case to a human sooner than it would have anyway. Kept as live,
  independently-tested code in eligible_negotiation_actions() (not deleted)
  in case that invariant ever changes.
- SANITY CHECK RESULT (per this phase's own brief): the proposed
  eligible_actions table held for cash_flow_problem/can_pay_but_wont/
  high_risk exactly as Phase 2 reported. good_customer's top action shifted
  from legal_facts to firm once legal pressure was excluded from its
  candidate set -- harmless, since soft_nudge/firm/legal_facts all map to
  the identical kind="send" at the identical rung (writer.py untouched), so
  this only changes the audit-trail label, not what's sent.
- DESIGN CALL not spelled out in the brief: score["signals"]["broken_promises"]
  (the buyer's historical settled-invoice reliability, from
  engine.score.signals(), unchanged by two_axis_score()) is what feeds
  negotiation.rank_actions()'s broken_promises= argument -- NOT
  engine.brain.broken_promises(promises, today, grace) (this invoice's own
  active/unresolved promise count, already used elsewhere in decide() for
  rung-jumping). Deliberate: EV is modelling the buyer's general
  follow-through record, the same signal recovery_probability's own
  promise_adjustment block is about, not this one invoice's ladder state.
- 846 tests passing (829 + 17). Zero regressions -- proven two ways: the
  full suite, and a snapshot-diff test pinning run_agent(seed=42, days=45)'s
  headline numbers from immediately before this phase (captured via
  `git stash` to the pre-Phase-3 tree and back). Known limitations carried
  forward unchanged: willingness still inherits average_delay_days
  (ability-contaminated, stated not hidden); Action.kind is still
  string-based, not an enum; stop_rules.max_per_rung is still dead config.
  New limitation this phase adds: with ev_mode on, soft_nudge/firm/
  legal_facts/payment_plan/counter_settle all draft through the SAME
  rung-based skeleton (engine/writer.py untouched) -- the chosen
  negotiation action changes only the audit trail's stated reasoning,
  not the message a buyer actually reads. Message-content differentiation by
  action, and any reactive settlement-offer handling, remain future work.
