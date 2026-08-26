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
- [x] E3 - Tier 3 edge cases: regression tests + edge_cases.md status markup
- [x] E4 - TC-141 end-to-end scenario fixture
- [x] W1 - Early warning (pre-overdue risk surfacing)
- [x] W2 - Buyer-level panel + promise reliability
- [x] W3 - Buyer-level message consolidation
- [x] W4 - Re-run experiment, regenerate report with new numbers
- [ ] 10 - README + ARCHITECTURE sync + hygiene
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
- W4 done: re-ran the full 6-seed comparison (42, 7, 13, 99, 2024, 555) at
  HEAD via the real CLI (`sim/run_sim.py --compare --seed 42 --days 120`) --
  agent wins 6/6 on rupees recovered, 6/6 on matched-set days-to-pay, no
  regression on any seed. report/out/results.json + report.html regenerated
  (has W1's early-warning section, W2's buyer panel, W3's messages_sent/
  invoice_contacts split). Historical "Day 9" comparison done via throwaway
  git worktrees at the exact pre-E1, post-E4/pre-W1, post-W1/pre-W2 and
  post-W2/pre-W3 commits (never touching the main working tree's branch or
  dataset) -- on SEED 42 specifically, every headline number (recovered,
  outstanding, both days-to-pay figures, handoffs, exceptions) is
  BYTE-IDENTICAL across all four historical checkpoints and HEAD; only
  messages_sent moved (141->73, the W3 drop). Investigated WHY E1/E2 showed
  zero effect on 42 rather than assuming it: checked all 6 seeds directly --
  42 is the ONE seed of six with neither a malformed invoice (E2) nor a
  superseded promise (E1/TC-014) anywhere in its data. The other 5 DO
  exercise both (seed 555: 1 malformed + 5 superseded) -- re-running seed
  555's PRE-E1 code in a worktree showed identical recovered/outstanding/
  handoffs to HEAD too, and BOTH mechanisms are now verified with real
  instrumented data, not inferred: (1) the malformed invoice (TC-050, future
  issue_date) is only structurally invalid on day0-day1, and its own
  statutory due date is 20 days out -- watchdog.overdue_invoices() confirmed
  NEVER queues it on either invalid day, so brain.decide() never sees it
  during the only window pre/post-E2 code could disagree about it; the two
  code paths are not "coincidentally" the same, they structurally cannot
  diverge for this invoice. (2) TC-014: instrumented brain.broken_promises()
  to compare old-buggy vs new-fixed counts on every real call across the
  full run -- 4 of the 5 superseded-promise invoices never differ at all
  (the bug's precondition never arose); the 5th (INV-2026-0141) differs by
  1 on 107 of 108 days, but on ALL 107 of those days chosen_rung already
  equalled available_rung (the law ceiling) -- min(desired, ceiling) was
  already clamping before the bug's extra +1 could matter, 0 of 107 days
  had room for it to bite. E1/E2 are correctness fixes verified by their OWN
  dedicated tests, not by this experiment's aggregate numbers. Money
  conservation confirmed on all 12 runs (6 seeds x baseline+agent).
  tests/test_sim_isolation.py: 24/24 passed, automatically covers
  engine/consolidate.py too (glob-discovered). ADVISOR ITEMS 1+2 built (item
  3, the ablation arm, stays parked in README Future Work): sim/run_sim.py
  gained edge_case_counts(invoices, day0, promises_by_invoice) -- malformed
  counted at day0 (not last_day: a clock-relative defect can self-resolve by
  last_day and correctly stop being "invalid" there, but this count is about
  whether E2 ever needed to act, not the final verdict), superseded
  promises = invoices with >1 promise ever recorded. Threaded through
  multi_seed_summary()'s per-seed rows and report/build_report.py's
  _edge_case_note() -- the multi-seed table now shows two extra columns and
  one summary sentence ("N of 6 seeds contain a malformed invoice...") so a
  judge sees the 6/6 win happened DESPITE these edge cases, not in their
  absence.
- W3 done: engine/consolidate.py (pure: groups a day's already-decided SEND
  Actions by buyer into rung TIERS -- courtesy=rung<=1, escalated=rung>=2,
  never mixed, so one buyer may get up to two envelopes/day, never a single-
  envelope guarantee -- deliberate, see the section-aware-guardrail
  alternative rejected in the W3 plan). engine.brain.decide(), engine.rungs,
  engine.law and sim.run_sim.run_baseline() are ALL untouched -- consolidation
  is purely a post-decide grouping + a new writer path (writer.
  write_consolidated_message/passes_guardrail_multi/fallback_message_multi)
  + a new channels path (channels.send_consolidated: one real send, N audit
  rows, one per invoice, linked by detail.bundle_invoice_ids -- keeps
  entries_for()/_already_sent() working per-invoice unmodified). Wired into
  both sim/run_sim.py's run_agent() day loop and main.py's stage_writer/
  stage_post_office; every buyer-facing send, even a lone invoice, now goes
  through the same "bundle of one" path -- no separate single-invoice code
  path left to drift. messages_sent now counts outbound ENVELOPES;
  run_agent()'s new invoice_contacts field is the OLD per-invoice-contact
  semantics, also surfaced in report/build_report.py's headline table.
  Digest-aware subject line (total ₹ in the subject) explicitly DEFERRED to
  W4 -- not built. Seed-42 verified empirically, invoice-for-invoice: agent
  invoice_contacts (141) == pre-change messages_sent (141) exactly, same 93
  invoices, zero per-invoice differences; agent final.recovered_paise,
  outstanding_paise, handoffs, stops, disputes, exceptions and paid_invoices
  all identical before/after; only agent messages_sent dropped 141->73.
  run_baseline()'s own output is a structurally empty diff before/after, and
  tests/test_run_sim.py::test_run_baseline_never_touches_consolidation_
  machinery proves it by making every consolidation entry point explode if
  baseline ever reached one. STRUCTURAL check, not just aggregate sums: for
  all 93 invoices the seed-42 run ever contacted, the exact SET of calendar
  days each was contacted on is byte-identical before/after (0 mismatches) --
  ruling out same-total-different-pattern. That one-time diff can't be re-run
  once the old code is gone, so the permanent guard is
  test_every_send_decision_has_exactly_one_matching_writer_entry: every
  (invoice, day) brain.decide() chose SEND for has EXACTLY one writer audit
  entry, forever, from a single run's own trail -- no dropped or duplicated
  contact, whatever future change touches this path.
- Gotcha for any future code touching sim/ or engine/: tests/test_no_legal_
  constants.py bans "Samadhaan" (and other statutory names/numbers) as a
  literal string constant anywhere outside config/legal.yaml, INCLUDING in
  narration/log text, not just message drafts -- pull such names via
  engine.config.legal() at runtime instead.
- LLM provider is Gemini via engine/llm.py (LLM_MODE=live), not Anthropic,
  despite the Architecture rules section above being unrevised.
- 760 tests passing (pytest -q).
- Gemini key rotated 2026-08-27, old key revoked — resolved, see memory.
