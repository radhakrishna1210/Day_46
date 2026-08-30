"""Revenue Recovery Agent -- one command runs the whole thing.

    python main.py --seed 42

This is the live single-pass pipeline: every stage below does its work on the
real clock and reports it. The last two -- the baseline-vs-agent simulator and
the scoreboard -- are built, but as their own scripts (sim/run_sim.py and
report/build_report.py); they name their command here rather than running it,
because a pipeline that pretends to have run is worse than one that says
plainly where the work actually happens.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from data import generate, store
from engine.money import enable_unicode_output, format_inr
from engine import (audit, brain, channels, consolidate, law, llm, promises,
                    score, validate, watchdog, writer)

DEFAULT_SEED = 42


@dataclass
class Context:
    """What the stages hand each other as the run proceeds."""

    seed: int
    today: date
    buyers: list[dict[str, Any]]
    invoices: list[dict[str, Any]]
    queue: list[dict[str, Any]]
    scores: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    early_warnings: list[dict[str, Any]] = field(default_factory=list)
    invalid_invoices: dict[str, str] = field(default_factory=dict)
    actions: list[Any] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    promises: list[dict[str, Any]] = field(default_factory=list)
    send_email: bool = False
    ignore_quiet_hours: bool = False
    dry_run: bool = False


def _ensure_dataset(seed: int) -> None:
    """Generate the world if it is not on disk yet, or was built for another seed.

    The dataset is gitignored, so a fresh clone has none. Building it here means
    `python main.py --seed 42` works on its own rather than failing on a missing
    file; running data/generate.py yourself does exactly the same thing.
    """
    if generate.ensure_dataset(seed):
        print(f"  dataset missing or built for a different seed, generating now (seed {seed})")


# --- the stages -----------------------------------------------------------

def stage_data(context: Context) -> str:
    _ensure_dataset(context.seed)
    context.buyers = store.load_buyers()
    context.invoices = store.load_invoices()
    meta = store.load_meta()
    context.today = date.fromisoformat(meta["simulation_start"])
    current = sum(1 for inv in context.invoices if inv["cohort"] == "current")
    # Malformed invoices (engine.validate) are found once here, before anything
    # downstream ever sees them -- watchdog excludes them from the queue, so
    # this is the one place a human running the pipeline sees they exist at
    # all. Non-negotiable #1: nothing about this may happen silently.
    context.invalid_invoices = validate.audit_invalid(context.invoices, context.today,
                                                       log=not context.dry_run)
    invalid_note = (f", {len(context.invalid_invoices)} malformed (excluded, see audit trail)"
                    if context.invalid_invoices else "")
    return (f"{len(context.buyers)} buyers, {len(context.invoices)} invoices "
            f"({current} current{invalid_note})")


def stage_watchdog(context: Context) -> str:
    context.queue = watchdog.overdue_invoices(context.invoices, context.today)
    unsettled = [inv for inv in context.invoices if watchdog.is_unsettled(inv)]
    at_risk = sum(watchdog.outstanding_paise(inv) for inv in context.queue)
    return (
        f"{len(context.queue)} overdue of {len(unsettled)} unsettled, "
        f"{format_inr(at_risk, 'Rs ')} at risk, as of {context.today.isoformat()}"
    )


def stage_score(context: Context) -> str:
    grouped = store.invoices_by_buyer(context.invoices)
    context.scores = score.score_all(context.buyers, grouped, context.today)
    low = sum(1 for s in context.scores if s["confidence"] == "low")
    worst = context.scores[0]
    return (
        f"{len(context.scores)} buyers scored, worst {worst['buyer_id']} at "
        f"{worst['score']}/100, {low} on low confidence"
    )


def stage_early_warning(context: Context) -> str:
    """Every invoice approaching its due date, with a real risk band --
    surfacing only, never a message (see CLAUDE.md's early-warning decision:
    Option A). `context.early_warnings` holds ALL of them, low band
    included, so downstream code has the honest full picture; this stage's
    summary and print_early_warnings() below only show the notable ones.
    """
    scores_by_buyer = {item["buyer_id"]: item for item in context.scores}
    context.early_warnings = watchdog.early_warnings(
        context.invoices, context.promises, scores_by_buyer, context.today,
    )
    notable = [w for w in context.early_warnings if w["risk_band"] != "low"]
    high = sum(1 for w in notable if w["risk_band"] == "high")
    window = watchdog.rules()["early_warning"]["window_days"]
    return (f"{len(notable)} flagged ({high} high, {len(notable) - high} watch), "
            f"{len(context.early_warnings) - len(notable)} low band, within {window}d of due")


def print_early_warnings(context: Context, limit: int = 10) -> None:
    """One line per NOTABLE early warning (watch/high; low band is computed
    but not shown here) -- this is the whole point: a human reading it
    should believe it, not just trust it."""
    notable = [w for w in context.early_warnings if w["risk_band"] != "low"]
    print()
    print(f"  {'invoice':<16}{'buyer':<9}{'band':<7}{'due in':>7}{'outstanding':>14}  reasons")
    for warning in notable[:limit]:
        print(
            f"  {warning['invoice_id']:<16}{warning['buyer_id']:<9}{warning['risk_band']:<7}"
            f"{warning['days_until_due']:>6}d"
            f"{format_inr(warning['outstanding_paise'], 'Rs '):>14}  "
            + "; ".join(warning["reasons"])
        )
    if len(notable) > limit:
        print(f"  ... and {len(notable) - limit} more")


def stage_law(context: Context) -> str:
    context.positions = [law.legal_position(inv, context.today) for inv in context.queue]
    interest = sum(p["interest_paise"] for p in context.positions)
    tax = sum(p["tax_exposure_paise"] for p in context.positions)
    void = [p for p in context.positions if p["agreed_term_void"]]
    held = [p for p in context.positions if p["dispute_hold"]]
    rate = law.effective_annual_rate() * 100
    return (
        f"{format_inr(interest, 'Rs ', decimals=True)} interest accrued at {rate:.2f}% "
        f"({len(void)} void terms), {format_inr(tax, 'Rs ')} of buyer tax exposure, "
        f"{len(held)} held for dispute"
    )


def print_legal_detail(context: Context, limit: int = 5) -> None:
    """The per-invoice legal position, largest exposure first."""
    ranked = sorted(context.positions, key=lambda p: -p["interest_paise"])[:limit]
    print()
    print(f"  {'invoice':<16}{'overdue':>8}{'principal':>14}{'interest':>14}"
          f"{'tax exposure':>15}{'rung':>6}")
    for position in ranked:
        print(
            f"  {position['invoice_id']:<16}{position['days_overdue']:>7}d"
            f"{format_inr(position['principal_paise'], 'Rs '):>14}"
            f"{format_inr(position['interest_paise'], 'Rs ', decimals=True):>14}"
            f"{format_inr(position['tax_exposure_paise'], 'Rs '):>15}"
            f"{position['available_rung']:>6}"
        )
    worst = ranked[0]
    print()
    print(f"  what the agent may state about {worst['invoice_id']}:")
    for fact in worst["facts"]:
        print(f"    - {fact}")


def stage_brain(context: Context) -> str:
    by_id = {buyer["buyer_id"]: buyer for buyer in context.buyers}
    scores = {item["buyer_id"]: item for item in context.scores}

    context.actions = []
    for invoice, position in zip(context.queue, context.positions):
        context.actions.append(brain.decide(
            invoice, by_id[invoice["buyer_id"]], scores[invoice["buyer_id"]],
            position, promises=[], history=[], log=not context.dry_run,
        ))

    counts: dict[str, int] = {}
    for action in context.actions:
        counts[action.kind] = counts.get(action.kind, 0) + 1
    tally = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
    where = "dry run, nothing logged" if context.dry_run else f"logged to {audit.LOG_PATH.name}"
    return f"{len(context.actions)} decisions ({tally}); {where}"


def print_decisions(context: Context, limit: int = 10) -> None:
    """One line per decision, with the reason. This is the dry-run output."""
    print()
    print(f"  {'invoice':<16}{'buyer':<9}{'kind':<9}{'rung':>5}{'src':>6}  reason")
    for action in context.actions[:limit]:
        print(f"  {action.invoice_id:<16}{action.buyer_id:<9}{action.kind:<9}"
              f"{action.rung:>5}{action.source:>6}  {action.reason}")
    if len(context.actions) > limit:
        print(f"  ... and {len(context.actions) - limit} more")


def stage_writer(context: Context) -> str:
    """Draft one message per engine.consolidate bundle, not per invoice.

    A buyer with several invoices sendable today gets grouped into one (or,
    across the rung-1/rung>=2 tier boundary, at most two) bundle -- see
    CLAUDE.md's W3 note. A buyer with a single eligible invoice still goes
    through this same "bundle of one" path; there is no separate
    single-invoice code path left to drift from this one.
    """
    by_id = {buyer["buyer_id"]: buyer for buyer in context.buyers}
    scores = {item["buyer_id"]: item for item in context.scores}
    invoices_by_id = {inv["invoice_id"]: inv for inv in context.queue}

    context.messages = []
    fallbacks = 0
    for bundle in consolidate.bundle_sends(context.actions):
        buyer_id = bundle["buyer_id"]
        drafted = writer.write_consolidated_message(
            bundle["actions"], invoices_by_id=invoices_by_id, buyer=by_id[buyer_id],
            score=scores.get(buyer_id), today=context.today, log=not context.dry_run,
        )
        drafted["buyer_id"] = buyer_id
        drafted["invoice_rungs"] = {a.invoice_id: a.rung for a in bundle["actions"]}
        context.messages.append(drafted)
        fallbacks += drafted["fallback_used"]

    hinglish = sum(1 for m in context.messages if m["language"] == "hinglish")
    invoices_covered = sum(len(m["invoice_ids"]) for m in context.messages)
    return (f"{len(context.messages)} messages drafted covering {invoices_covered} "
            f"invoices, {hinglish} in Hinglish, {fallbacks} fell back to the plain skeleton")


def print_messages(context: Context, limit: int = 5) -> None:
    """Full drafted messages, so they can be read rather than counted."""
    for drafted in context.messages[:limit]:
        print()
        print("  " + "-" * 74)
        print(f"  {', '.join(drafted['invoice_ids'])}  tier {drafted['tier']}  "
              f"{drafted['language']}  guardrail: {drafted['guardrail']}")
        print("  " + "-" * 74)
        for refused in drafted.get("rejected_drafts", []):
            print(f"  REFUSED (attempt {refused['attempt']}) -- "
                  f"{'; '.join(refused['failures'])}")
            for line in refused["body"].splitlines():
                print(f"  | {line}" if line else "  |")
            print("  SENT INSTEAD:")
        print(f"  Subject: {drafted['subject']}")
        print()
        for line in drafted["body"].splitlines():
            print(f"  {line}" if line else "")


def stage_promises(context: Context) -> str:
    """Sweep for broken promises so the next pass can escalate on them.

    Buyer replies are driven by the simulator (sim/run_sim.py), which runs its
    own promise sweep day by day; in this live single pass, this is the daily
    check that turns an unpaid commitment into a broken one.
    """
    broken = promises.sweep(context.promises, context.today,
                            log=not context.dry_run)
    still_open = sum(1 for p in context.promises if p["status"] == "open")
    return (f"{len(context.promises)} promises on file, {still_open} open, "
            f"{len(broken)} newly broken")


def stage_post_office(context: Context) -> str:
    """One physical send per bundle, logged once per invoice it covers --
    see engine.channels.send_consolidated()'s own docstring."""
    by_id = {buyer["buyer_id"]: buyer for buyer in context.buyers}
    context.deliveries = []

    for drafted in context.messages:
        buyer = by_id.get(drafted.get("buyer_id"))
        if buyer is None:
            continue
        channel = buyer.get("preferred_channel", "email")
        for target in {channel, "email"}:
            to = (buyer["contact_email"] if target == "email"
                  else buyer.get("contact_phone", ""))
            context.deliveries.extend(channels.send_consolidated(
                target, to, drafted, invoice_rungs=drafted["invoice_rungs"],
                buyer_id=buyer["buyer_id"], today=context.today,
                # A real wall clock, because this is the live single pass and
                # a real send can happen right now. The simulator
                # (sim/run_sim.py) is a separate workflow over a seeded,
                # time-travelling clock: it passes only its simulated `today`
                # and leaves `now` unset.
                now=datetime.now(),
                enabled=context.send_email,
                ignore_quiet_hours=context.ignore_quiet_hours,
                log=not context.dry_run,
            ))

    counts: dict[str, int] = {}
    for delivery in context.deliveries:
        counts[delivery["status"]] = counts.get(delivery["status"], 0) + 1
    tally = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    return f"{len(context.deliveries)} deliveries ({tally})"


def print_deliveries(context: Context, limit: int = 6) -> None:
    print()
    for delivery in context.deliveries[:limit]:
        print(f"  {channels.describe(delivery)}")
    if len(context.deliveries) > limit:
        print(f"  ... and {len(context.deliveries) - limit} more")


def _separately(command: str) -> Callable[[Context], str]:
    """A stage that is built, but as its own entry point rather than here.

    The simulator and the scoreboard are real (sim/run_sim.py and
    report/build_report.py); they were deliberately built as separate
    scripts instead of pipeline stages, because both re-run the world many
    times over and neither belongs in a single live pass. This names the
    command that does the work rather than running it from here, so the
    pipeline neither pretends to have run it nor implies it is missing.
    """
    def run(context: Context) -> str:
        return f"run separately: {command.format(seed=context.seed)}"
    run.separate = True                                     # type: ignore[attr-defined]
    return run


@dataclass(frozen=True)
class Stage:
    """One stage of the pipeline: what it is, and what it does."""

    name: str
    what: str
    run: Callable[[Context], str]


PIPELINE: tuple[Stage, ...] = (
    Stage("data factory", "load or build the synthetic world", stage_data),
    Stage("watchdog", "find the invoices that are overdue today", stage_watchdog),
    Stage("score engine", "score each buyer from their payment history", stage_score),
    Stage("early warning", "flag invoices approaching due date with bad signals",
          stage_early_warning),
    Stage("law engine", "statutory due date, penal interest, buyer tax exposure", stage_law),
    Stage("brain", "pick one escalation rung, or stop", stage_brain),
    Stage("message writer", "draft the message for the chosen rung", stage_writer),
    Stage("promise tracker", "read replies, remember and check promises", stage_promises),
    Stage("post office", "send the email, log the stubbed channels", stage_post_office),
    Stage("simulator", "run baseline and agent over the same seeded world",
          _separately("python sim/run_sim.py --compare --seed {seed} --days 120")),
    Stage("scoreboard", "build the comparison report and exceptions list",
          _separately("python report/build_report.py")),
)


def run(seed: int, dry_run: bool = False, send_email: bool = False,
        ignore_quiet_hours: bool = False) -> int:
    """Walk the pipeline. Returns a process exit code."""
    enable_unicode_output()
    print(f"revenue recovery agent: starting (seed={seed}, llm_mode={llm.get_mode()})")
    if dry_run:
        audit.disable()
    context = Context(seed=seed, today=date.today(), buyers=[], invoices=[],
                      queue=[], scores=[], positions=[], dry_run=dry_run,
                      send_email=send_email,
                      ignore_quiet_hours=ignore_quiet_hours)

    for number, stage in enumerate(PIPELINE, start=1):
        print(f"step {number}: {stage.name} -- {stage.what}")
        print(f"  {stage.run(context)}")
        if stage.name == "early warning" and any(
                w["risk_band"] != "low" for w in context.early_warnings):
            print_early_warnings(context)
        if stage.name == "law engine" and context.positions:
            print_legal_detail(context)
        if stage.name == "brain" and context.actions:
            print_decisions(context)
        if stage.name == "message writer" and context.messages:
            print_messages(context)
        if stage.name == "post office" and context.deliveries:
            print_deliveries(context)

    built = sum(1 for stage in PIPELINE if not getattr(stage.run, "separate", False))
    print(f"audit trail ({audit.__name__}): {built} pipeline stages complete; "
          f"the simulator and the scoreboard run separately (commands above)")
    print("revenue recovery agent: done")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full revenue recovery simulation end to end.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"random seed, for reproducible runs (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="decide and print, but write nothing to the audit trail",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="actually deliver email, to TEST_INBOX_EMAIL and nowhere else "
             "(off by default; nothing opens a socket without it)",
    )
    parser.add_argument(
        "--ignore-quiet-hours",
        action="store_true",
        help="send even inside quiet hours. Recorded in the audit trail as an "
             "explicit human override, not a silent skip.",
    )
    args = parser.parse_args()
    return run(args.seed, dry_run=args.dry_run, send_email=args.send_email,
               ignore_quiet_hours=args.ignore_quiet_hours)


if __name__ == "__main__":
    raise SystemExit(main())
