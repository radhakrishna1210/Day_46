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
- [x] P3 - Wired the Brain to EV, DONE (closed after two correctness
      follow-ups -- see notes): engine/brain.py's decide() can now replace
      its unconditional SEND fallthrough with negotiation.rank_actions()'s
      top pick, behind config/rules.yaml's brain.ev_mode (shipped "off").
      New config/rules.yaml negotiation.eligible_actions table gates
      candidates per quadrant (the good_customer relationship-cost fix).
      New Action.kind values payment_plan/counter_settle; both landmines
      fixed (consolidate.py _eligible(), buyer_panel.py _LADDER_KINDS).
      sim/run_sim.py's day loop now feeds brain.decide() a two_axis_score()
      instead of score_buyer() (byte-identical with ev_mode off, proven by
      a pinned seed-42 snapshot test in tests/test_run_sim.py). All 8 of
      negotiation's actions are now reachable through decide() under
      ev_mode: on -- 6 by the general-action choice (step 13), and
      human_handoff/legal_escalation by a separate handoff-FLAVOR choice at
      the existing rung-4 step (step 8) once a handoff is already certain.
- [x] P4 - Persona differentiation + the EV ablation: sim/personas.py's
      react() gained action_kind= (default "send", byte-identical to
      pre-P4); cash_tight promises 20-27pts more often for a payment_plan,
      habitual_delayer lowballs a counter_settle via the existing partial-
      promise fixture. run_agent(ev_mode=True) is the real third experiment
      arm; multi_seed_summary()'s new primary_agent_ev param and
      results.json's agent_ev section are both additive/opt-in.
      report/build_report.py + the Jinja template render a third column
      when present. ABLATION FINDING: agent+EV beats plain agent on rupees
      recovered in 5/6 seeds (seed 2024 the one loss) -- see notes for the
      mechanism. Caught and fixed a real audit-trail-ordering bug along the
      way (see notes).
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- Phase 4 done and committed (see git log for the hash(es)). Part A:
  sim/personas.py's react() gained action_kind= ("send" default,
  byte-identical to pre-P4, pinned by a snapshot test); cash_tight boosts
  PROMISE probability for payment_plan (PAYMENT_PLAN_PROMISE_BOOST),
  habitual_delayer skews toward the existing promise_partial_hinglish
  fixture for counter_settle (COUNTER_SETTLE_PARTIAL_BIAS) -- both gated so
  a persona with no configured entry behaves exactly like "send", with zero
  extra rng draws (matters: an unconditional rng.random() call, even one
  whose result is never used, still perturbs the NEXT draw). Part B:
  run_agent(seed, days, ev_mode=False) is the real third arm; sim/run_sim.py
  --compare now runs and reports all three (baseline/agent/agent+EV) by
  default on the same 6-seed set. multi_seed_summary()'s primary_agent_ev
  param and results.json's agent_ev section are both optional/additive.
  report/build_report.py + report.html.j2 render a 3rd column when present.
- ABLATION FINDING, the number Phase 2/3 deferred: agent+EV beats plain
  agent on rupees recovered in 5/6 seeds (seed 2024: -Rs 51,765; every other
  seed +Rs 45,978 to +Rs 9,81,368). MECHANISM, worth remembering before
  touching this area again: almost the entire effect traces to payment_plan
  specifically -- every OTHER thing EV changes (firm vs soft_nudge,
  human_handoff vs legal_escalation) maps to the IDENTICAL Action.kind/rung/
  skeleton either way (writer.py untouched, rung 4 sends nothing to a
  buyer), so it only relabels the audit trail and cannot move simulated
  behavior at all. payment_plan is the only action that changes Action.kind
  in a way personas.react() actually treats differently.
- A FINDING SURFACED, NOT FIXED: counter_settle's persona behaviour (Part A)
  is implemented and directly unit-tested, but never actually fires in a
  real run -- can_pay_but_wont's EV ranking always puts legal_facts (100%
  recovery fraction, never penalised by broken promises) ahead of
  counter_settle (70% fraction, IS penalised) at every outstanding amount
  and broken-promise count under the shipped config/rules.yaml grid. Stated
  plainly rather than re-tuning the grid to manufacture a win, same
  treatment as Phase 2's own good_customer finding.
- A REAL BUG CAUGHT ON REVIEW: run_agent() unconditionally clears+rewrites
  the shared on-disk audit trail every call. Computing agent_ev right after
  agent (the natural CLI order) silently left agent_ev's trail on disk where
  report/build_report.py's excerpt/early-warnings/trip-wires and
  multi_seed_summary()'s own "restore the primary trail" both expect
  agent's. Fixed with the same snapshot/restore pattern multi_seed_summary()
  already uses, right around the agent_ev call in main()'s --compare branch.
- 877 tests passing (850 + 27). Zero regressions -- full suite plus a
  git-stash-verified byte-identical proof for personas.react()'s default
  action_kind and run_agent()'s default ev_mode. Known limitations carried
  forward: no message-content differentiation by action (engine/writer.py
  untouched), no reactive settlement-offer handling, counter_settle inert
  in practice (above). willingness/average_delay_days, Action.kind's
  string-based design, and stop_rules.max_per_rung remain as before.
