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
- [x] P5 - Final close-out: coherence + staleness pass across README/
      ARCHITECTURE/PROJECT_WALKTHROUGH/winning_layer before the demo video.
      PROJECT_WALKTHROUGH.md was badly stale (pre-Phase-1 snapshot: wrong
      test count, no P0-P4 rows in its status table, a flatly false "no
      ablation arm exists" claim in two places) -- now synced. winning_layer.md
      had a wrong phase attribution and a 15-item Definition of Done
      checklist that had sat 100% unchecked despite ~10 items being true --
      now corrected/checked with citations. Removed the dead
      stop_rules.max_per_rung config key (never read by any code; wiring it
      up now would have been a real behaviour change, not a cleanup) --
      tests/test_smoke.py now pins each rung's own max_messages instead.
      Ran sim/run_sim.py --compare end-to-end and read the actual rendered
      report.html; no glitches found in the new 3-column layout.
- [x] P6 - Outcome attribution, SIMULATOR ONLY: new engine/outcomes.py
      (OutcomeLedger) credits each payment to the MOST RECENT action on
      that invoice within config/rules.yaml's new learning.
      attribution_horizon_days (14); actions with no payment inside their
      horizon are recorded as failures, payments with no action before
      them are recorded UNATTRIBUTED and counted, never dropped. Writes
      audit/outcomes.jsonl (already gitignored by audit/*). sim/run_sim.py
      records outbound contacts + handoffs (never waits/stops) in both
      run_agent arms and run_baseline; quadrant comes from
      two_axis_score(), null for the baseline. engine/brain.py and
      engine/law.py untouched -- a test asserts no engine module reads
      outcomes. All headline numbers unchanged (6/6, 5/6, Rs 9,81,368).
- [x] P6b - outcomes provenance + file lifecycle. Every row now carries
      run_id (f"{seed}_{mode}_{timestamp}", wall clock, rows only -- kept OUT
      of the summary so results.json stays byte-reproducible) alongside the
      seed the simulator was actually run with. Truncation was implicit and
      per-PROCESS, which silently stacked pytest's many run_agent() calls
      into the production file; it is now ONE explicit outcomes.start_file()
      in run_sim.main(), and conftest.py redirects the whole test session to
      a tmp file so the suite cannot write the artifact at all. New
      outcomes.runs() reports what a file actually holds, and --compare now
      prints it ("18 run(s), 3799 rows, seeds [7, 13, 42, 99, 555, 2024]").
- [x] P7 - Exploration mode, SIMULATOR ONLY: sim/run_sim.py's
      run_agent(explore=True) makes brain.decide() sample UNIFORMLY from the
      already-gated eligible_actions list instead of taking the top-EV pick,
      so a learning run can see what happens after actions the EV grid would
      never choose. The switch is an OBJECT (decide(explore_rng=...)), not a
      config key -- no edit to config/rules.yaml and no CLI flag can turn it
      on, and main.py neither constructs one nor passes a quadrant-carrying
      score. Sampling happens INSIDE decide(), after every stop rule, spacing
      rule, rung gate and law ceiling has already run, and over the same list
      eligible_negotiation_actions() had already produced -- so it can only
      pick differently among what the rules already allowed, never widen it.
      outcomes.jsonl rows gained proposed_action_kind / proposed_rung /
      gate_override; action_kind + rung stay the ACTUALLY EXECUTED action, so
      payments are still credited to what the buyer really received. New
      outcomes.gate_overrides() reads the rate back (kept out of the summary
      so results.json does not churn). explore=True implies ev_mode on and
      labels its ledger rows "agent_ev_explore".
- [x] P8 - Recovery-probability fit, TRAINING SEEDS ONLY, OUTPUT ONLY: new
      scripts/fit_recovery.py runs sim/run_sim.py's exploration mode across
      seeds 1000-1029 (30, asserted disjoint from the 6 benchmark seeds
      42/7/13/99/2024/555 -- fitting on a benchmark seed would let the numbers
      memorise the world they are later scored on), accumulates into
      audit/outcomes_train.jsonl (NOT the production audit/outcomes.jsonl),
      and fits one Beta(1+successes, 1+failures) posterior -- weak uniform
      prior -- per (quadrant, action_kind) cell. Excludes handoff rows
      (post-handoff recovery is unobservable in the sim, per the FINDING note
      below), right-censored rows (action + horizon past run end -- 0 at 120
      days) and null-quadrant rows (0). Writes config/learned_recovery.yaml
      (alpha/beta/mean/ci95_width/observations/successes/failures per cell;
      header says nothing reads it) and docs/learning_data.md (seeds, the
      exact 5499-row partition -> 4153 fitted, date, parameters). 7 cells.
      NOTHING in engine/ imports or reads the YAML (grep-verified). The script
      snapshots+restores data/seed/ and the audit trail. Re-fit fast with
      `python scripts/fit_recovery.py --skip-run`.
- [x] P9 - Wired the learned posteriors into the EV formula, behind a switch
      that SHIPS OFF. New engine/learning.py: recovery_probability(quadrant,
      action_kind) -> float in [0,1], read via engine/config.py's cached
      learned_recovery() loader (same pattern as every other config file).
      New config/rules.yaml learning.enabled (false) + learning.mode
      (offline). engine/negotiation.py's recovery_probability() takes the
      posterior mean as its base rate ONLY when learning.enabled; a cell
      missing from the YAML falls back to the hand-typed grid value and logs
      it (deduped, stderr), never crashes. (P9 fitted one coarse "send" cell
      per quadrant; P10 split that by delivered rung -- see below.)
      learning.check_config() (called at startup
      by main.py and sim/run_sim.py) raises LearningConfigError on
      enabled+ev_mode-off, enabled+mode:online, enabled+missing YAML -- never
      a silent no-op. Fixed a latent bug this surfaced: YAML `brain.ev_mode:
      on` parses as the boolean True, which the old `str(...) == "on"` check
      read as OFF -- now one engine/config.ev_mode_on() accepts True or "on",
      used by both brain.py and learning.py. ev_mode:off snapshot tests
      unchanged (shipped defaults untouched). New tests/test_learning.py (29);
      956 tests pass.
- [x] P10 - SEND cells re-fit split by DELIVERED RUNG (Day 4 re-aggregation +
      Day 5 lookup only; brain.py decision logic, Day 1/Day 3 data + code all
      UNTOUCHED). Investigation found: engine/negotiation.py distinguishes
      soft_nudge/firm/legal_facts as SEND actions and EV selects among them,
      but engine/brain.py's escalation walk sets the delivered rung
      INDEPENDENTLY -- proposed_action_kind matched the delivered rung only
      7-80% of the time (gate_override true on 56% of SEND rows). So
      scripts/fit_recovery.py now groups a SEND by the rung it was delivered
      at, mapped to a tier via config/rules.yaml's ladder (1=soft_nudge,
      2=firm, 3=legal_facts -- read from engine.rungs, not a new map), stored
      nested at recovery.<quadrant>.send.<tier> with delivered_rung on each
      cell. payment_plan/counter_settle unchanged (1:1 select->execute, one
      flat cell). config/learned_recovery.yaml schema -> version 2. Re-fit
      from the EXISTING audit/outcomes_train.jsonl (--skip-run, no new sim
      runs). engine/learning.py resolves soft_nudge/firm/legal_facts to the
      nested send.<tier> cell; a quadrant with no rung-1 sends (can_pay_but_wont)
      has no soft_nudge cell and falls back to the hand-typed grid, logged.
      Thin cells flagged: soft_nudge n=9-79 (walk rarely stops at rung 1) --
      ci95_width 0.21-0.42 carries the warning, no falsely-confident number.
      New docs/learning_findings.md records the label/execution gap as a KNOWN
      LIMITATION with the future fix (make EV's choice set the executed rung)
      recorded but NOT attempted. New tests/test_fit_recovery.py (13);
      tests/test_learning.py updated to 32. ev_mode:off snapshot tests
      untouched and passing.
- [x] P11 - Online learning (config/rules.yaml learning.mode: online), SHIPS
      OFF. engine/learning.py OnlineLearner: Beta posteriors in memory,
      warm-started from config/learned_recovery.yaml (or uniform Beta(1,1) if
      learning.cold_start: true, same cell set). engine/negotiation.py
      Thompson-SAMPLES each eligible cell (learning.sample_probability, seeded
      by sim/run_sim.py's own _rng(seed, inv, day, "thompson") handed in via
      online_sampling()) and feeds the sample -- not the mean -- to the EV
      formula. sim/run_sim.py's _resolve_online() does incremental horizon
      attribution each simulated day (SAME rule engine/outcomes.py applies at
      end-of-run; right-censored actions excluded) and calls learner.update()
      -- alpha += 1 on a credited payment, beta += 1 on an elapsed horizon.
      Executed (kind, rung) -> cell key via learning.delivered_action_kind()
      (SEND -> delivered tier, plan/settle -> self, handoff -> None). ONE
      resolver (_resolve_cell) for offline lookup + online sample + online
      update. End of run: posteriors dump to
      report/out/learned_posteriors_final.yaml (nested v2 schema, gitignored);
      config/learned_recovery.yaml is NEVER written. Online mode is its own
      experiment: sim/run_sim.py main() runs ONE online agent, skips --compare.
      check_config() now ACCEPTS online (was "not implemented").
      engine/brain.py decision logic UNTOUCHED -- the sampling context is a
      learning.py module-global negotiation.py reads. engine/learning.py still
      has no "outcomes" string (the guard test). New
      tests/test_online_learning.py (17): reproducibility (same seed ->
      identical posteriors on a full run), rising mean on repeated successes
      (nested send/firm cell), cold start, dump schema, seeded sampling. 988
      tests pass.
- [x] P12 - Learned-decision provenance in the audit trail + report, SHIPS
      OFF with the rest of the learning layer. When config/rules.yaml's
      learning.enabled is on, engine/brain.py's decide() writes six keys into
      each EV decision's audit detail: learning_method (thompson_sampling |
      posterior_mean | hardcoded), estimated_probability (the P(recover) the EV
      formula used for the chosen action, 0-1), observations (behind that
      number -- the live online posterior's alpha+beta-2, else the fitted
      cell's, else null), bandit_top_choice (negotiation.rank_actions() over
      the FULL 8-action space -- what raw EV wants with every gate removed),
      executed_action, and gate_reason. gate_reason is null when the bandit got
      its way, else names the binding rule (law_ceiling_rung_N /
      escalation_rung_N_below_handoff / eligible_actions_policy /
      escalation_walk_rung_N / exploration_sample) -- that null-vs-string is
      the visible proof the RULES, not the learner, have the final say. reason
      + source untouched; all six are new detail keys, so every existing audit
      reader is unmodified. New engine/learning.py read-only helpers:
      online_active(), audit_method(), observations(), +
      OnlineLearner.observations(). report/build_report.py:
      _learned_decision_rows() scans the FULL trail (like _trip_wire_rows()),
      _learned_decisions_excerpt() floats override lines to the front; new
      "Learned decisions" section in report.html.j2 with a learning-off empty
      state (the shipped case) + a matching GUARDRAILS entry. brain.py's
      rank_actions() call runs AFTER the executed action is chosen, so it
      cannot change the decision; under online learning it draws from the same
      fresh per-decision Thompson RNG (discarded at context exit) so a seeded
      run stays reproducible. learning.enabled: false -> not one new key is
      written, byte-identical to before. New tests: test_learning.py +9,
      test_online_learning.py +3, test_experiment.py +6. 1005 tests pass.
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- P8 + P9 + P10 + P11 + P12 landed this session on top of committed P6/P6b/P7
  (eb7515a, 1d1099b, 84979a4). Uncommitted new files: scripts/fit_recovery.py,
  config/learned_recovery.yaml, docs/learning_data.md, docs/learning_findings.md,
  engine/learning.py, tests/test_learning.py, tests/test_fit_recovery.py,
  tests/test_online_learning.py.
  Changed: config/rules.yaml (learning block), engine/{config,brain,negotiation}.py,
  main.py, sim/run_sim.py, report/build_report.py, report/templates/report.html.j2,
  tests/test_experiment.py, CLAUDE.md. audit/outcomes_train.jsonl gitignored.
- P12 is audit/report only: the six learned-decision keys appear in
  audit/audit_log.jsonl only when learning.enabled + brain.ev_mode are both on;
  the report's new "Learned decisions" section shows an empty state in the
  shipped --compare (learning off). Verified over a learning-on 60-day run:
  733/1644 learned decisions were rule-overridden (law_ceiling_rung_2 132x,
  eligible_actions_policy 111x, escalation_rung_N_below_handoff 490x).
- SHIPS OFF: config/rules.yaml learning.enabled: false -> negotiation uses the
  hand-typed grid, byte-identical to before. 988 tests pass. To demo:
  learning.enabled: true + brain.ev_mode: on (both, or check_config raises),
  then learning.mode offline = posterior mean, online = Thompson sampling +
  in-run updates (single agent run, dumps report/out/learned_posteriors_final.yaml).
- P10 fitted means, per DELIVERED rung. SEND: good_customer firm 0.61 (n=748,
  the workhorse) / soft_nudge 0.38 (n=79, thin) / legal_facts 0.44 (n=34, thin);
  high_risk firm 0.17 / legal_facts 0.22; cash_flow firm 0.51 / legal_facts
  0.77 (n=55, thin, selection-confounded); can_pay_but_wont firm 0.28 /
  legal_facts 0.42 (no rung-1 cell -> soft_nudge falls back to hand-typed).
  payment_plan/counter_settle unchanged: cash_flow 0.64, good_customer 0.59,
  can_pay_but_wont counter_settle 0.31.
- RECONCILIATION, settled: ledger sum + unattributed is SHORT of results.json
  recovered_paise by exactly 189,480,000 paise on seed 42, and by the SAME
  constant on all three arms. Not a leak -- 12 current invoices arrive at day0
  already part-paid, and recovered_paise is a cumulative stock while the
  ledger is an in-run flow. The ledger under-claims, which is the right
  direction.
- FINDING worth keeping: over a full --compare, hit rate within the 14-day
  horizon is payment_plan 85/131, send 643/1616, baseline reminder 247/1519,
  handoff 0/496. The handoff zero is NOT a bug -- the simulator has no model
  of what the owner does after taking a case over, so no money can ever
  arrive behind a handoff. Anything that later learns from outcomes.jsonl
  must exclude handoff rows rather than read them as failures.
- THE TWO HEADLINE CLAIMS, both true, both stated together everywhere a judge
  would look: the core agent beats the naive baseline on rupees recovered in
  6/6 tested seeds; the EV/negotiation layer adds a further Rs 9,81,368 on
  seed 42 and wins on 5/6 of those same seeds (seed 2024 the one loss,
  reported not hidden). 924 tests passing. All three shipped arms verified
  byte-identical to the committed results.json after P7. One staleness note:
  run_agent()'s report dict gained an "explore" key that the committed
  results.json predates -- additive, read by nothing, picked up by the next
  --compare. Only Phase 11 (demo video) and 12 (final submit) remain.
