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
- [ ] W4 - Re-run experiment, regenerate report with new numbers
- [ ] 10 - README + ARCHITECTURE sync + hygiene
- [ ] 11 - Demo assets + video prep
- [ ] 12 - Final check + submit
Notes for next session: (keep 3-5 bullets max, prune old ones)
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
  baseline ever reached one.
- W2 done: engine/buyer_panel.py -- one buyer-level rollup, called ONCE at
  the end of sim/run_sim.py's run_agent() (not baseline, not inside the day
  loop), reusing score.py's score/confidence/trend unmodified. Confirmed
  surfacing-only, zero decision influence: nothing in the day loop (every
  brain.decide() call already happened before buyer_panel() runs) or
  anywhere else reads its output this phase -- grepped, only sim/run_sim.py
  imports it. Promise "avg days late" is NOT broken_on - promised_date (that
  gap is a constant ~1 day, an artifact of the daily sweep() cadence, not a
  buyer signal) -- it's paid_date - promised_date for broken promises whose
  invoice was eventually paid; unresolved ones count toward `broken` but not
  the average, and say so ("no resolved-late data") rather than a misleading
  number. "Response rate" (replies of any non-silent outcome / messages
  sent) was grepped for name collisions first -- nothing in engine/,
  report/ or sim/ computed anything response-rate-like before this; only
  docs/winning_layer.md's aspirational prose used the phrase, no formula.
  Seed-42 check: results.json's ONLY diff before/after is the added
  buyer_panel key (confirmed via a stripped-key structural diff); the
  on-disk audit trail is byte-identical, same SHA-256, both runs.
- Gotcha for any future code touching sim/ or engine/: tests/test_no_legal_
  constants.py bans "Samadhaan" (and other statutory names/numbers) as a
  literal string constant anywhere outside config/legal.yaml, INCLUDING in
  narration/log text, not just message drafts -- pull such names via
  engine.config.legal() at runtime instead.
- LLM provider is Gemini via engine/llm.py (LLM_MODE=live), not Anthropic,
  despite the Architecture rules section above being unrevised.
- 748 tests passing (pytest -q).
