# Revenue Recovery Agent

**Razorpay AI Buildathon 2026 -- Track 3: AI Revenue Recovery**

> Razorpay has the payment rails and sends reminders. This is the brain that
> decides *who* to chase, *how hard*, with *what legal leverage*, and *when to
> stop* -- and it can prove it recovers more money than a fixed reminder bot.

An AI agent that helps Indian MSMEs collect overdue B2B invoices. It watches
invoices, scores the buyer from payment history, computes the supplier's real
legal position under the MSMED Act, picks an escalation rung, writes the message
(English or Hinglish), remembers every promise a buyer makes, and stops before
it becomes spam. Every money-related action is written to an audit trail with
the reason behind it.

**Status: Day 1 of 12 -- scaffold only. No business logic yet.**

## How to run

> Placeholder -- the three-command version lands on Day 10. Right now this runs
> the pipeline skeleton, which announces each stage and does nothing.

```bash
pip install -r requirements.txt
cp .env.example .env          # defaults to LLM_MODE=mock; no API key needed
python data/generate.py --seed 42   # required first: the dataset is generated, not committed
python main.py --seed 42
pytest -q
```

Run `python data/generate.py --seed 42` before `main.py`. `buyers.json` and
`invoices.json` are gitignored, so a fresh clone has no dataset until you
generate one. The same seed always rebuilds byte-identical files.

Inspect the pieces that are built:

```bash
python engine/watchdog.py                # today's overdue work queue
python engine/score.py                   # every buyer scored, worst first
python engine/score.py --explain BUY-01  # the arithmetic behind one score
```

No API key is required. `LLM_MODE=mock` gives deterministic canned responses, so
the project runs end to end on a fresh clone.

## Results

> Placeholder -- baseline vs agent on the same 100 seeded invoices, plus the
> full exceptions list of what we failed to recover and why. Day 9.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the flow diagram and a description of
every block.

Rules where mistakes are expensive, AI where language is messy:

| Job | Rules or AI |
|---|---|
| Detect overdue, score buyers, law math, escalation ladder, stopping rules | Rules |
| Reading buyer replies, writing messages, ambiguous judgment calls | AI (logged to audit) |

## Scope (deliberate)

- Email is really sent, and only to the owner's own test inbox. **No real person
  is ever contacted.**
- WhatsApp and SMS log "would send". The WhatsApp Business API needs business
  verification, which does not fit in a 12-day build.
- All data is synthetic and generated from a seed, so any run is reproducible.

## Future Work

Ideas that came up during the build and were deliberately **not** built:

- Real WhatsApp Business API channel
- Voice calls with Hinglish TTS
- Live RBI bank-rate feed instead of a config value
- Tally / Zoho invoice import
- TReDS invoice-discounting suggestion for stuck invoices
- Network-level buyer score across many vendors (the Razorpay-scale version)
- Dispute-resolution assistant
- Financial-year seasonality in the synthetic data: a visible cluster of buyers
  settling just before March 31, so the Section 43B(h) tax-deduction cliff can
  be shown landing rather than asserted. Parked on Day 2 because the simulation
  window (starts 2026-08-24, runs 90 days) never crosses March 31 -- revisit on
  Day 8 if the window changes.
- `draft_message` and `judgment_call` run on a Flash-tier Gemini model rather
  than Pro, because this key's free tier has zero pro-tier quota and billing
  isn't available for it -- a known quality-vs-cost tradeoff, not a design choice.
- Simulator reply lag: a persona's reaction lands the same simulated day a
  message is sent. A real buyer takes a day or three, and the "days to pay"
  number in the Day 9 report would mean more with that modelled.
- Simulator fallback-message penalty: when the writer's guardrail rejects a
  draft and falls back to the plain skeleton, the persona reacts identically
  to a full LLM-drafted message today. A small penalty on the fallback path
  would give the guardrail work a measurable effect on outcomes, not just on
  audit-trail honesty.
- Simulator partial-payment realism: every `pay_partial` reaction is tagged
  as an unexplained, ambiguous reply (to exercise the brain's one LLM
  judgment-call path) rather than sometimes arriving with a normal
  explanation. Splitting some partial payments into a clean "partial,
  explained" case would stop that path from over-firing on every partial
  payment in the simulated world.
- Ablation experiment: a third arm with the baseline's fixed 3-message
  schedule but score-aware timing and no legal/tax content, to isolate how
  much of the agent's win over the baseline comes from smarter timing versus
  the law engine's leverage. The most direct answer to "how much of this is
  really the legal argument" a skeptical judge could ask -- not built for
  Day 9 because it's a third full pipeline variant, not a report tweak.

## Legal disclaimer

The legal calculations here are **simplified for a demonstration, current as of
Aug 2026, and are not legal advice.** All figures live in `config/legal.yaml`
and should be verified against the current RBI bank rate and the prevailing text
of the MSMED Act 2006 and the Income Tax Act before being relied on.
