"""Build the Receivables Command Center — the demo dashboard.

A read-only consumer of the agent's existing outputs, exactly like
report/build_report.py. It assembles one payload from two clearly separated
time views of the SAME seeded world and renders a single self-contained HTML
page (report/templates/dashboard.html.j2) with the data embedded as a JSON
literal — it opens by double-clicking, no server, no fetch().

    python scripts/build_dashboard.py --seed 7 [--days 120] [--out report/out/dashboard.html]

The two views, each tagged with a `provenance` string the UI shows as a caption:

  * DAY 0 — today's queue. The same pipeline stages main.py runs (watchdog ->
    score -> law -> brain -> writer), in a dry run with logging off, on the
    given seed. Portfolio counts, early warnings, per-invoice decisions with
    their reason strings, legal positions, the drafted messages.

  * AFTER 120 DAYS — the simulation. report/out/results.json, produced by
    `sim/run_sim.py --compare`. The four-arm benchmark, the multi-seed table,
    per-rung effectiveness, exceptions, the buyer panel. If results.json is
    missing or was generated for another seed, this fails loudly.

Never invents a number: a figure the data cannot honestly support is left out
or labelled, never guessed.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jinja2 import Environment, FileSystemLoader, select_autoescape

from data import generate, store
from engine import audit, brain, consolidate, law, samadhaan, validate, watchdog, writer
from engine import ability_willingness as aw
from engine import score as score_engine
from engine.config import legal as legal_config
from engine.config import rules as rules_config
from engine.config import supplier as supplier_config
from engine.money import format_inr

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "report" / "templates"
DEFAULT_RESULTS_PATH = ROOT / "report" / "out" / "results.json"
DEFAULT_OUT_PATH = ROOT / "report" / "out" / "dashboard.html"
DEFAULT_JSON_PATH = ROOT / "report" / "out" / "dashboard.json"
#: The GitHub Pages entry point. Written in the SAME run as DEFAULT_OUT_PATH,
#: from the identical rendered string, so the published page and the repo copy
#: can never drift. `docs/.nojekyll` (created here if missing) tells Pages to
#: serve the folder as-is.
DEFAULT_PAGES_PATH = ROOT / "docs" / "index.html"
PAGES_NOJEKYLL = ROOT / "docs" / ".nojekyll"
AUDIT_LOG_PATH = ROOT / "audit" / "audit_log.jsonl"

#: How many of the newest audit-trail lines to embed. The full file is linked
#: by path in the caption; a 120-day agent run writes thousands of lines and
#: most are the same handoff repeated once per simulated day.
AUDIT_ROWS_EMBEDDED = 400

DAY0_PROVENANCE = "Day 0 — today's queue, from a live pipeline pass on seed {seed}"
SIM_PROVENANCE = "After 120 days — the seeded simulation (report/out/results.json)"


class ResultsMismatch(SystemExit):
    """results.json is missing or belongs to a different seed."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _inr(paise: int | None) -> str:
    return format_inr(int(paise)) if paise is not None else "n/a"


def _inr_p(paise: int | None) -> str:
    return format_inr(int(paise), decimals=True) if paise is not None else "n/a"


def _pct(value: float) -> str:
    return f"{value:.2f}"


_PHANTOM_RUNG_RE = re.compile(r"wanted rung (\d+) but the law supports at most (\d+)")


def _present_reason(text: str | None) -> str:
    """The same two presentation-layer touch-ups report/build_report.py makes:
    the deterministic mock-LLM marker, and a phantom rung 5 in an older trail."""
    if not text:
        return ""
    text = text.replace("MOCK:", "deterministic mock-LLM judgment:")

    def _fix(match: re.Match[str]) -> str:
        wanted, ceiling = int(match.group(1)), match.group(2)
        return (f"wanted to escalate further but the law supports at most {ceiling}"
                if wanted > 4 else match.group(0))

    return _PHANTOM_RUNG_RE.sub(_fix, text)


# --------------------------------------------------------------------------
# a tiny markdown renderer, for the Samadhaan draft only
# --------------------------------------------------------------------------

def _md_inline(text: str) -> str:
    """Escape, then re-enable the small set of inline marks the draft uses."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return out


def render_markdown(md: str) -> str:
    """Render the Samadhaan draft's markdown to HTML.

    Deliberately minimal — engine/samadhaan.py is the only source of this
    markdown and it uses a fixed, known subset: ATX headings, a blockquote,
    horizontal rules, pipe tables, checkbox and plain bullet lists, bold, and
    paragraphs. No third-party library (requirements.txt stays at six lines).
    """
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            out.append("<hr>")
            i += 1
            continue

        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        if heading:
            # shift down one level: the page already owns <h1>/<h2>, and this
            # is a document embedded inside a panel
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{_md_inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if stripped.startswith(">"):
            block: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                block.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f"<blockquote>{_md_inline(' '.join(block))}</blockquote>")
            continue

        if stripped.startswith("|") and i + 1 < n and re.match(r"\|[\s:|-]+\|", lines[i + 1].strip()):
            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]

            header = cells(lines[i])
            i += 2
            body_rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                body_rows.append(cells(lines[i]))
                i += 1
            thead = "".join(f"<th>{_md_inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in row) + "</tr>"
                for row in body_rows
            )
            out.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>")
            continue

        if re.match(r"[-*]\s+", stripped):
            items: list[str] = []
            while i < n and re.match(r"[-*]\s+", lines[i].strip()):
                item = re.sub(r"^[-*]\s+", "", lines[i].strip())
                box = ""
                checkbox = re.match(r"\[([ xX])\]\s*(.*)", item)
                if checkbox:
                    checked = checkbox.group(1).lower() == "x"
                    box = f'<span class="chk{" done" if checked else ""}"></span>'
                    item = checkbox.group(2)
                items.append(f"<li>{box}{_md_inline(item)}</li>")
                i += 1
            out.append(f'<ul class="doc-list">{"".join(items)}</ul>')
            continue

        para: list[str] = []
        while i < n and lines[i].strip() and not re.match(r"(#{1,6}\s|[-*]\s|\||>|---)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_md_inline(' '.join(para))}</p>")

    return "\n".join(out)


# --------------------------------------------------------------------------
# results.json — the 120-day view
# --------------------------------------------------------------------------

def load_results(path: Path, seed: int, days: int) -> dict[str, Any]:
    if not path.exists():
        raise ResultsMismatch(
            f"{path} not found. Regenerate it for this seed:\n"
            f"  python sim/run_sim.py --compare --seed {seed} "
            f"--extra-seeds 42,13,99,2024,555 --days {days}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("seed", -1)) != seed:
        raise ResultsMismatch(
            f"{path} was generated for seed {data.get('seed')}, not {seed}. "
            f"The dashboard would render two different worlds. Regenerate it:\n"
            f"  python sim/run_sim.py --compare --seed {seed} "
            f"--extra-seeds 42,13,99,2024,555 --days {days}"
        )
    return data


def _benchmark(results: dict[str, Any]) -> dict[str, Any]:
    baseline, agent = results["baseline"], results["agent"]
    agent_ev, agent_learned = results.get("agent_ev"), results.get("agent_learned")
    multi = results.get("multi_seed") or {}

    def arm(key: str, label: str, sub: str, node: dict[str, Any] | None) -> dict[str, Any] | None:
        if node is None:
            return None
        return {
            "key": key,
            "label": label,
            "sub": sub,
            "recovered_paise": node["final"]["recovered_paise"],
            "recovered": _inr(node["final"]["recovered_paise"]),
            "messages": node["messages_sent"],
            "handoffs": node["handoffs"],
            "not_recovered": len(node["exceptions"]),
            "avg_days_to_pay": node.get("avg_days_to_pay"),
        }

    arms = [
        arm("baseline", "Baseline",
            "fixed three-reminder schedule, same message for everyone — mirrors "
            "how payment-link reminders behave today", baseline),
        arm("agent", "Agent",
            "score + statutory position + bounded escalation + stop rules", agent),
        arm("agent_ev", "Agent + EV",
            "the agent, choosing the action at each rung by expected value", agent_ev),
        arm("agent_learned", "Agent + EV + learned bandit",
            "expected value using fitted recovery posteriors instead of the "
            "hand-typed grid", agent_learned),
    ]
    arms = [a for a in arms if a is not None]

    gain_paise = agent["final"]["recovered_paise"] - baseline["final"]["recovered_paise"]
    fewer = baseline["messages_sent"] - agent["messages_sent"]

    learned_spread = None
    if "agent_learned_delta_paise" in multi:
        s = multi["agent_learned_delta_paise"]
        learned_spread = {
            "win_rate": multi.get("agent_learned_money_win_rate"),
            "mean_paise": s["mean"], "mean": _inr(s["mean"]),
            "min_paise": s["min"], "min": _inr(s["min"]),
            "max_paise": s["max"], "max": _inr(s["max"]),
            "n_seeds": s["n_seeds"],
        }

    per_rung = []
    names = {"1": "Rung 1 — soft nudge", "2": "Rung 2 — firm", "3": "Rung 3 — legal facts"}
    for rid in sorted(agent["per_rung"], key=int):
        row = agent["per_rung"][rid]
        per_rung.append({
            "rung": names.get(str(rid), str(rid)),
            "contacted": row["invoices_contacted"],
            "recovered_here": row["recovered_here"],
            "effectiveness_pct": row["effectiveness_pct"],
        })
    baseline_decay = []
    for n in sorted(baseline["per_attempt"], key=int):
        row = baseline["per_attempt"][n]
        baseline_decay.append({
            "attempt": f"Reminder {n}",
            "contacted": row["invoices_contacted"],
            "recovered_here": row["recovered_here"],
            "effectiveness_pct": row["effectiveness_pct"],
        })

    multi_rows = []
    for row in multi.get("rows", []):
        multi_rows.append({
            "seed": row["seed"],
            "baseline": _inr(row["baseline_recovered_paise"]),
            "agent": _inr(row["agent_recovered_paise"]),
            "agent_ev": _inr(row["agent_ev_recovered_paise"]) if "agent_ev_recovered_paise" in row else None,
            "agent_learned": _inr(row["agent_learned_recovered_paise"]) if "agent_learned_recovered_paise" in row else None,
            "money_win": row["money_win"],
            "ev_win": row.get("agent_ev_money_win"),
            "learned_win": row.get("agent_learned_money_win"),
        })

    matched = results.get("matched_avg_days_to_pay") or {}
    invoice_contacts = agent.get("invoice_contacts", agent["messages_sent"])

    return {
        "provenance": SIM_PROVENANCE,
        "arms": arms,
        "max_recovered_paise": max(a["recovered_paise"] for a in arms),
        "agent_messages": agent["messages_sent"],
        "agent_invoice_contacts": invoice_contacts,
        "consolidation_ratio": round(invoice_contacts / agent["messages_sent"], 1) if agent["messages_sent"] else None,
        "headline": {
            "gain_paise": gain_paise,
            "gain": _inr(gain_paise),
            "fewer_messages": fewer,
            "seed_win_rate": multi.get("money_win_rate", "n/a"),
        },
        "ev_win_rate": multi.get("agent_ev_money_win_rate"),
        "learned": learned_spread,
        "per_rung": per_rung,
        "baseline_decay": baseline_decay,
        "matched_days": {
            "n": matched.get("n"),
            "baseline": matched.get("baseline"),
            "agent": matched.get("agent"),
        },
        "multi_seed": multi_rows,
    }


def _buyer_risk(results: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for entry in results.get("agent", {}).get("buyer_panel") or []:
        sc = entry.get("score") or {}
        promises, response, state = entry["promises"], entry["response"], entry["recovery_state"]
        rows.append({
            "buyer_id": entry["buyer_id"],
            "name": entry.get("name") or entry["buyer_id"],
            "outstanding_paise": entry["outstanding_paise"],
            "outstanding": _inr(entry["outstanding_paise"]),
            "overdue_count": entry["overdue_count"],
            "oldest_days_overdue": entry["oldest_days_overdue"],
            "score": sc.get("score"),
            "confidence": sc.get("confidence"),
            "trend": (sc.get("trend") or {}).get("direction", "unknown"),
            "broken_promises": (sc.get("signals") or {}).get("broken_promises"),
            "disputes": (sc.get("signals") or {}).get("disputes_raised"),
            "promise_reliability": (f"{promises['reliability_pct']}%"
                                    if promises["reliability_pct"] is not None else "no promises"),
            "response_rate": (f"{response['response_rate_pct']}%"
                              if response["response_rate_pct"] is not None else "not contacted"),
            "state": ", ".join(
                f"{n} {label}" for n, label in (
                    (state["in_ladder"], "in ladder"),
                    (state["handed_off"], "handed off"),
                    (state["stopped"], "stopped"),
                    (state.get("not_yet_due", 0), "not yet due"),
                ) if n
            ) or "settled",
        })
    rows.sort(key=lambda r: -r["outstanding_paise"])
    return {
        "provenance": "After 120 days — end-of-run buyer panel (results.json)",
        "rows": rows,
    }


def _handoff(results: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
    """Every case escalated out of the agent, split dispute vs rung-4 ceiling,
    plus a freshly rendered Samadhaan draft for the largest rung-4 case."""
    agent = results["agent"]
    dispute, rung4 = [], []
    for item in agent["exceptions"]:
        reason = item.get("reason") or ""
        row = {
            "invoice_id": item["invoice_id"],
            "buyer_name": item.get("buyer_name") or item["buyer_id"],
            "persona": item.get("persona"),
            "outstanding_paise": item["outstanding_paise"],
            "outstanding": _inr(item["outstanding_paise"]),
            "days_overdue": item["days_overdue"],
            "reason": _present_reason(reason),
        }
        if item.get("disputed"):
            dispute.append(row)
        elif "final rung" in reason or "human takes over" in reason:
            rung4.append(row)
    dispute.sort(key=lambda r: -r["outstanding_paise"])
    rung4.sort(key=lambda r: -r["outstanding_paise"])

    reasons = agent.get("handoff_reasons") or {}
    samadhaan_row = _samadhaan_draft(rung4, world, results)
    cap = 12
    return {
        "provenance": "After 120 days — cases escalated out of the agent (results.json)",
        "dispute": dispute[:cap],
        "dispute_more": max(0, len(dispute) - cap),
        "rung4": rung4[:cap],
        "rung4_more": max(0, len(rung4) - cap),
        "counts": {
            "dispute": reasons.get("disputed", len(dispute)),
            "rung4": reasons.get("rung4_escalation", len(rung4)),
            "total": agent["handoffs"],
        },
        "samadhaan": samadhaan_row,
    }


def _samadhaan_draft(rung4: list[dict[str, Any]], world: dict[str, Any],
                     results: dict[str, Any]) -> dict[str, Any] | None:
    """Render the Samadhaan draft for the largest invoice escalated to the
    rung-4 ceiling — the case where the Samadhaan reference path is the
    actual next step (a dispute goes to a human to resolve the dispute, not
    to file). Generated here via engine.samadhaan so it belongs to this seed."""
    if not rung4:
        return None
    target = rung4[0]
    invoice = world["invoices_by_id"].get(target["invoice_id"])
    buyer = world["buyers_by_id"].get(invoice["buyer_id"]) if invoice else None
    if invoice is None or buyer is None:
        return None
    as_of = date.fromisoformat(results["agent"]["final"]["day"])
    position = law.legal_position(invoice, as_of)
    draft = samadhaan.build_draft(invoice, buyer, position, as_of)
    return {
        "invoice_id": invoice["invoice_id"],
        "buyer_name": buyer.get("name") or invoice["buyer_id"],
        "outstanding": _inr(position["principal_paise"]),
        "as_of": as_of.isoformat(),
        "ready": draft["ready"],
        "blockers": draft["blockers"],
        "warnings": draft["warnings"],
        "html": render_markdown(draft["markdown"]),
        "note": "The largest invoice escalated to the rung-4 ceiling in the "
                "120-day run. Drafted just now from recorded invoice data.",
    }


# --------------------------------------------------------------------------
# the audit trail — a view of the real file
# --------------------------------------------------------------------------

#: A 120-day run ends with thousands of near-identical daily handoff/wait/stop
#: rows; the interesting actions (sends, drafts, promises, disputes) all happen
#: in the first ~40 days. Embedding the last N flat would show a wall of
#: handoffs and no sends at all -- so keep every non-repeating action and only
#: the tail of the repeaters, which keeps every filter useful.
_AUDIT_REPEATERS = frozenset({"handoff", "wait", "stop"})
_AUDIT_REPEATER_TAIL = 80


def _audit(seed: int) -> dict[str, Any]:
    entries = audit.entries()
    kinds = sorted({e.get("action") for e in entries if e.get("action")})

    tail_budget = {k: _AUDIT_REPEATER_TAIL for k in _AUDIT_REPEATERS}
    keep_idx: list[int] = []
    for i in range(len(entries) - 1, -1, -1):
        action = entries[i].get("action")
        if action in _AUDIT_REPEATERS:
            if tail_budget[action] <= 0:
                continue
            tail_budget[action] -= 1
        keep_idx.append(i)
    keep_idx.sort(reverse=True)  # newest first
    keep_idx = keep_idx[:AUDIT_ROWS_EMBEDDED]

    rows = [{
        "ts": entries[i].get("ts"),
        "invoice_id": entries[i].get("invoice_id") or "",
        "actor": entries[i].get("actor"),
        "action": entries[i].get("action"),
        "source": entries[i].get("source"),
        "reason": _present_reason(entries[i].get("reason")),
    } for i in keep_idx]

    return {
        "provenance": f"The append-only log at audit/audit_log.jsonl "
                      f"(120-day agent run, seed {seed})",
        "path": "audit/audit_log.jsonl",
        "total": len(entries),
        "shown": len(rows),
        "note": "every non-repeating action plus the last "
                f"{_AUDIT_REPEATER_TAIL} each of handoff / wait / stop -- "
                "the repeaters fire once per simulated day on a settled case",
        "action_kinds": kinds,
        "rows": rows,
    }


# --------------------------------------------------------------------------
# the Day-0 pipeline view
# --------------------------------------------------------------------------

_FACT_LABELS = {
    "section_15_no_agreement": "Section 15 — statutory due date (no written term)",
    "section_15_agreed": "Section 15 — statutory due date",
    "section_15_capped": "Section 15 — agreed term void, recomputed",
    "section_16": "Section 16 — interest accrued",
    "section_16_running": "Section 16 — cost of continuing to wait",
    "section_22": "Section 22 — disclosable in annual accounts",
    "section_23": "Section 23 — that interest is not deductible for the buyer",
    "tax_deduction_upcoming": "Section 37(2)(g) — deduction moves to year of payment",
    "tax_deduction_crystallised": "Section 37(2)(g) — deduction already deferred",
    "samadhaan": "MSME Samadhaan — reference path",
}


def _day0(seed: int) -> dict[str, Any]:
    """Run the same stages main.py runs, dry, logging off, on `seed`."""
    audit.disable()
    previous_llm = os.environ.get("LLM_MODE")
    os.environ["LLM_MODE"] = "mock"
    try:
        return _day0_inner(seed)
    finally:
        if previous_llm is None:
            os.environ.pop("LLM_MODE", None)
        else:
            os.environ["LLM_MODE"] = previous_llm


def _day0_inner(seed: int) -> dict[str, Any]:
    generate.ensure_dataset(seed)
    buyers = store.load_buyers()
    invoices = store.load_invoices()
    meta = store.load_meta()
    today = date.fromisoformat(meta["simulation_start"])

    buyers_by_id = {b["buyer_id"]: b for b in buyers}
    invoices_by_id = {inv["invoice_id"]: inv for inv in invoices}
    grouped = store.invoices_by_buyer(invoices)
    current = [inv for inv in invoices if inv.get("cohort") == "current"]

    invalid = validate.audit_invalid(invoices, today, log=False)
    queue = watchdog.overdue_invoices(invoices, today)
    unsettled = [inv for inv in invoices if watchdog.is_unsettled(inv)]
    at_risk = sum(watchdog.outstanding_paise(inv) for inv in queue)

    scores = {s["buyer_id"]: s for s in score_engine.score_all(buyers, grouped, today)}
    two_axis = {
        inv["invoice_id"]: aw.two_axis_score(
            buyers_by_id[inv["buyer_id"]], grouped.get(inv["buyer_id"], []), today, invoice=inv)
        for inv in queue
    }

    warnings = watchdog.early_warnings(invoices, [], scores, today)
    notable = [w for w in warnings if w["risk_band"] != "low"]

    positions = {inv["invoice_id"]: law.legal_position(inv, today) for inv in queue}
    void_current = [inv for inv in current if law.agreed_term_is_void(inv)]
    void_queue = [p for p in positions.values() if p["agreed_term_void"]]
    void_outstanding = sum(p["principal_paise"] for p in void_queue)

    # — the hero: the biggest void-term invoice, with the recomputation shown
    hero = None
    if void_queue:
        biggest = max(
            (inv for inv in queue if positions[inv["invoice_id"]]["agreed_term_void"]),
            key=lambda inv: positions[inv["invoice_id"]]["principal_paise"],
        )
        pos = positions[biggest["invoice_id"]]
        hero = {
            "invoice_id": biggest["invoice_id"],
            "buyer_name": buyers_by_id[biggest["buyer_id"]].get("name") or biggest["buyer_id"],
            "outstanding": _inr(pos["principal_paise"]),
            "agreed_days": biggest.get("agreed_days"),
            "statutory_days": law.statutory_term_days(biggest),
            "agreed_due_date": biggest.get("agreed_due_date"),
            "statutory_due_date": pos["statutory_due_date"],
            "days_gained": law.days_gained_by_law(biggest),
            "void_outstanding": _inr(void_outstanding),
        }

    # — decisions, then consolidate + draft (mock mode) exactly as the loop does
    actions = []
    for inv in queue:
        actions.append(brain.decide(
            inv, buyers_by_id[inv["buyer_id"]], scores[inv["buyer_id"]],
            positions[inv["invoice_id"]], promises=[], history=[], log=False))
    action_by_invoice = {a.invoice_id: a for a in actions}

    message_by_invoice: dict[str, dict[str, Any]] = {}
    for bundle in consolidate.bundle_sends(actions):
        buyer_id = bundle["buyer_id"]
        drafted = writer.write_consolidated_message(
            bundle["actions"], invoices_by_id=invoices_by_id, buyer=buyers_by_id[buyer_id],
            score=scores.get(buyer_id), today=today, log=False)
        for act in bundle["actions"]:
            message_by_invoice[act.invoice_id] = {
                "subject": drafted["subject"],
                "body": drafted["body"],
                "language": drafted["language"],
                "guardrail": drafted["guardrail"],
                "tier": drafted["tier"],
                "fallback_used": drafted["fallback_used"],
                "source": drafted["source"],
                "covers": drafted["invoice_ids"],
            }

    rows = []
    kind_counts: dict[str, int] = {}
    for inv in queue:
        act = action_by_invoice[inv["invoice_id"]]
        pos = positions[inv["invoice_id"]]
        sc = scores[inv["buyer_id"]]
        tax = two_axis[inv["invoice_id"]]
        kind_counts[act.kind] = kind_counts.get(act.kind, 0) + 1

        wanted = act.rung
        m = re.search(r"wanted rung (\d+)", act.reason)
        if m:
            wanted = int(m.group(1))
        elif act.escalation_capped:
            wanted = 5  # ran past the top of the ladder

        skeleton = act.skeleton or {}
        by_key = skeleton.get("facts_by_key", {})
        # allowed_fact_keys is everything the rung MAY state; facts_by_key is
        # what actually applies to this invoice (one Section 15 variant, tax
        # only if the rung and the calendar make it live). Show the latter.
        facts = [{
            "key": key,
            "label": _FACT_LABELS.get(key, key),
            "text": by_key[key],
        } for key in skeleton.get("allowed_fact_keys", []) if key in by_key]

        message = message_by_invoice.get(inv["invoice_id"])
        no_message_reason = None
        if message is None:
            no_message_reason = {
                brain.HANDOFF: "No message. The case goes to a human — the ladder is exhausted "
                               "or the invoice is disputed.",
                brain.STOP: "No message. Contact has stopped for this buyer.",
                brain.WAIT: "No message today. The agent is holding off.",
            }.get(act.kind, "No message was drafted for this decision.")

        rows.append({
            "invoice_id": inv["invoice_id"],
            "buyer_id": inv["buyer_id"],
            "buyer_name": buyers_by_id[inv["buyer_id"]].get("name") or inv["buyer_id"],
            "days_overdue": pos["days_overdue"],
            "score": sc["score"],
            "band": brain.band(sc["score"], rules_config()),
            "confidence": sc["confidence"],
            "quadrant": tax["quadrant"],
            "quadrant_meaning": aw.QUADRANT_MEANING[tax["quadrant"]],
            "decision": act.kind,
            "rung": act.rung,
            "ceiling": act.available_rung,
            "wanted": wanted,
            "capped": bool(act.escalation_capped),
            "source": act.source,
            "reason": _present_reason(act.reason),
            "outstanding": _inr(pos["principal_paise"]),
            "legal": {
                "statutory_due_date": pos["statutory_due_date"],
                "agreed_due_date": inv.get("agreed_due_date"),
                "agreed_days": inv.get("agreed_days"),
                "agreed_term_void": pos["agreed_term_void"],
                "days_gained_by_law": pos["days_gained_by_law"],
                "days_overdue": pos["days_overdue"],
                "principal": _inr(pos["principal_paise"]),
                "interest": _inr_p(pos["interest_paise"]),
                "total_payable": _inr_p(pos["total_payable_paise"]),
                "interest_per_day": _inr_p(pos["interest_per_day_average_paise"]),
                "cost_of_waiting": _inr_p(pos["cost_of_waiting_paise"]),
                "waiting_horizon_days": pos["waiting_horizon_days"],
                "effective_rate_pct": _pct(law.effective_annual_rate() * 100),
                "bank_rate_pct": _pct(float(legal_config()["rbi_bank_rate"]) * 100),
                "tax_exposure": _inr(pos["tax_exposure_paise"]),
                "tax_rate_pct": f"{float(legal_config()['buyer_tax_rate']) * 100:.0f}",
                "fy_end": pos["fy_end"],
                "tax_deduction_crystallised": pos["tax_deduction_crystallised"],
                "dispute_hold": pos["dispute_hold"],
                "available_rung": pos["available_rung"],
            },
            "message": message,
            "no_message_reason": no_message_reason,
            "facts": facts,
        })
    # Demo order: a capped SEND leads (the ladder graphic's whole point is the
    # statute stopping the walk — that must be the landing state), then the
    # rest of the sends by money at stake, then plans, handoffs, stops.
    _priority = {"send": 0, "payment_plan": 1, "counter_settle": 1, "handoff": 2, "wait": 3, "stop": 4}
    rows.sort(key=lambda r: (
        0 if (r["decision"] == "send" and r["capped"]) else 1,
        _priority.get(r["decision"], 5),
        -_paise(r["outstanding"]),
    ))

    tally = ", ".join(f"{v} {k}" for k, v in sorted(kind_counts.items()))

    return {
        "today": today.isoformat(),
        "provenance": DAY0_PROVENANCE.format(seed=seed),
        "portfolio": {
            "buyers": len(buyers),
            "invoices_current": len(current),
            "unsettled": len(unsettled),
            "overdue": len(queue),
            "amount_at_risk": _inr(at_risk),
            "amount_at_risk_paise": at_risk,
            "early_warnings": len(notable),
            "handoffs": kind_counts.get("handoff", 0),
            "sends": kind_counts.get("send", 0),
            "void_terms_queue": len(void_queue),
            "void_terms_current": len(void_current),
            "malformed": len(invalid),
            "decision_tally": tally,
        },
        "hero": hero,
        "early_warning": {
            "provenance": (f"Day 0 — invoices inside the "
                           f"{rules_config()['early_warning']['window_days']}-day window "
                           f"before their statutory due date. Nothing here is sent and no "
                           f"legal fact is stated: nothing on this list is legally due yet."),
            "rows": [{
                "invoice_id": w["invoice_id"],
                "buyer_id": w["buyer_id"],
                "buyer_name": buyers_by_id[w["buyer_id"]].get("name") or w["buyer_id"],
                "outstanding": _inr(w["outstanding_paise"]),
                "days_until_due": w["days_until_due"],
                "score": scores[w["buyer_id"]]["score"],
                "band": w["risk_band"],
                "signals": w["signals_triggered"],
                "reasons": "; ".join(w["reasons"]),
            } for w in notable],
        },
        "decision_queue": {
            "provenance": "Day 0 — one decision per overdue invoice. Selecting a "
                          "row updates the legal position, the ladder and the "
                          "message beside it.",
            "rows": rows,
        },
    }


def _paise(inr_text: str) -> int:
    digits = re.sub(r"[^\d]", "", inr_text.split(".")[0])
    return int(digits) if digits else 0


# --------------------------------------------------------------------------
# assembling the payload
# --------------------------------------------------------------------------

def build_payload(seed: int, days: int, results_path: Path) -> dict[str, Any]:
    results = load_results(results_path, seed, days)
    day0 = _day0(seed)

    buyers = store.load_buyers()
    invoices = store.load_invoices()
    world = {
        "buyers_by_id": {b["buyer_id"]: b for b in buyers},
        "invoices_by_id": {inv["invoice_id"]: inv for inv in invoices},
    }

    supplier_profile = supplier_config()["supplier"]
    lc = legal_config()

    return {
        "meta": {
            "seed": seed,
            "days": results["days"],
            "day0": day0["today"],
            "day120": results["agent"]["final"]["day"],
            "generated": datetime.now().isoformat(timespec="seconds"),
            "results_generated": results.get("generated"),
            "supplier": {
                "legal_name": supplier_profile["legal_name"],
                "state": supplier_profile["state"],
                "udyam": supplier_profile["udyam_registration"],
            },
            "legal": {
                "config_version": lc["version"],
                "as_of": lc["as_of"],
                "bank_rate_pct": _pct(float(lc["rbi_bank_rate"]) * 100),
                "effective_rate_pct": _pct(law.effective_annual_rate() * 100),
                "multiplier": lc["bank_rate_multiplier"],
                "tax_rate_pct": f"{float(lc['buyer_tax_rate']) * 100:.0f}",
                "max_agreement_days": lc["max_agreement_days"],
                "no_agreement_days": lc["no_agreement_days"],
            },
            "early_warning_window_days": rules_config()["early_warning"]["window_days"],
        },
        "day0": day0,
        "benchmark": _benchmark(results),
        "buyer_risk": _buyer_risk(results),
        "handoff": _handoff(results, world),
        "audit": _audit(seed),
    }


def build(payload: dict[str, Any], out_path: Path, json_path: Path,
          pages_path: Path | None = DEFAULT_PAGES_PATH) -> list[Path]:
    """Render the dashboard once and write every destination from that one string.

    Returns the list of HTML paths written. `pages_path` is the GitHub Pages
    entry point (docs/index.html) -- written from the identical rendered text as
    `out_path`, so the two are byte-identical by construction and cannot drift.
    Pass pages_path=None to skip it.
    """
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    template = env.get_template("dashboard.html.j2")
    # Embedded as a JSON literal inside <script type="application/json">; the
    # only sequence that can break out of that context is "</script", so the
    # slash is escaped. Nothing here is fetched — the page works under file://.
    embedded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_text = template.render(payload=payload, payload_json=embedded)

    written: list[Path] = []
    targets = [out_path] if pages_path is None else [out_path, pages_path]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_text, encoding="utf-8")
        written.append(target)
    if pages_path is not None and not PAGES_NOJEKYLL.exists():
        PAGES_NOJEKYLL.parent.mkdir(parents=True, exist_ok=True)
        PAGES_NOJEKYLL.write_text("", encoding="utf-8")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Receivables Command Center dashboard.")
    parser.add_argument("--seed", type=int, default=7, help="the seeded world (default: 7)")
    parser.add_argument("--days", type=int, default=120, help="simulated days (default: 120)")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH,
                        help="results.json to read (default: report/out/results.json)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH,
                        help="where to write the dashboard (default: report/out/dashboard.html)")
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_PATH,
                        help="where to write the debug payload (default: report/out/dashboard.json)")
    parser.add_argument("--pages-out", type=Path, default=DEFAULT_PAGES_PATH,
                        help="GitHub Pages entry point, written identically to --out "
                             "(default: docs/index.html)")
    parser.add_argument("--no-pages", action="store_true",
                        help="do not write the GitHub Pages entry point")
    args = parser.parse_args()

    payload = build_payload(args.seed, args.days, args.results)
    written = build(payload, args.out, args.json_out,
                    pages_path=None if args.no_pages else args.pages_out)
    for path in written:
        print(f"dashboard written to {path}")
    print(f"payload written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
