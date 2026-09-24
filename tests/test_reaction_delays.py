"""Phase R1 -- delayed buyer reactions (sim/run_sim.py --reaction-delays).

Before R1 a buyer's reaction to a message -- a payment, a promise, a dispute --
landed the same simulated day the message went out, which made every
days-to-pay figure optimistic. R1 lands the SAME reaction later. What has to
stay true:

  * off (the default) is byte-identical to before -- pinned by
    tests/test_run_sim.py's existing snapshot, and by the report shape here;
  * a delay is a fixed, stated assumption per outcome (sim/personas.py
    REACTION_DELAY_DAYS), drawn from its own seeded stream, so the agent and
    the baseline see the same delay for the same buyer on the same day;
  * money never lands on the day it was asked for;
  * a reaction that has not landed is not counted as a reply.
"""

from __future__ import annotations

import random
from datetime import date

import pytest

from engine import buyer_panel
from sim import personas, run_sim

SEED = 42
DAYS = 45


# --------------------------------------------------------------------------
# the delay table and draw
# --------------------------------------------------------------------------

def test_every_outcome_has_a_delay_range_and_silence_has_none() -> None:
    assert set(personas.REACTION_DELAY_DAYS) == set(personas.OUTCOMES)
    assert personas.REACTION_DELAY_DAYS[personas.SILENCE] == (0, 0)
    for low, high in personas.REACTION_DELAY_DAYS.values():
        assert 0 <= low <= high


def test_money_never_lands_the_day_it_was_asked_for() -> None:
    for outcome in (personas.PAY_FULL, personas.PAY_PARTIAL):
        low, high = personas.REACTION_DELAY_DAYS[outcome]
        assert low >= 1
        draws = {personas.reaction_delay(outcome, random.Random(i)) for i in range(300)}
        assert draws == set(range(low, high + 1)), "every day in the range is reachable"


def test_the_delay_is_fixed_by_invoice_and_send_day_alone() -> None:
    """Both arms build the delay stream from the same (seed, invoice, send day,
    "react_delay") key, so a buyer who reacts to the baseline's reminder and to
    the agent's message on the same day gets the same delay from both."""
    day = date(2026, 9, 1)
    a = run_sim._rng(SEED, "INV-1", day, "react_delay")
    b = run_sim._rng(SEED, "INV-1", day, "react_delay")
    assert (personas.reaction_delay(personas.PAY_FULL, a)
            == personas.reaction_delay(personas.PAY_FULL, b))


def test_an_unknown_outcome_is_refused() -> None:
    with pytest.raises(ValueError):
        personas.reaction_delay("shrug", random.Random(0))


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def delayed() -> dict:
    return run_sim.run_agent(SEED, DAYS, reaction_delays=True)


def test_delays_off_adds_nothing_to_the_report() -> None:
    """Additive and opt-in: a report with delays off carries no R1 keys, so
    every pre-R1 reader and results.json keep their exact shape."""
    off = run_sim.run_baseline(SEED, 10)
    assert "reaction_delays" not in off and "in_flight" not in off


def test_a_delayed_run_reports_what_was_still_in_flight(delayed: dict) -> None:
    assert delayed["reaction_delays"] is True
    flight = delayed["in_flight"]
    assert set(flight) == {"reactions_in_flight_at_end", "payments_in_flight_at_end"}
    assert 0 <= flight["payments_in_flight_at_end"] <= flight["reactions_in_flight_at_end"]


def test_no_reaction_payment_lands_on_its_send_day(tmp_path, monkeypatch) -> None:
    """Every payment that follows a message is a delayed reaction, a kept
    promise, or nothing -- never a same-day reaction. Checked against the
    outcome ledger: a payment on the very day of a send to that invoice may
    only be a kept promise maturing that morning, and the ledger orders it
    BEFORE the send (lower seq), because promises mature before the Brain
    decides."""
    from engine import outcomes

    monkeypatch.setattr(outcomes, "OUTCOMES_PATH", tmp_path / "outcomes.jsonl")
    captured = {}
    real_write = outcomes.OutcomeLedger.write

    def spy(self, path=None):
        captured["ledger"] = self
        return real_write(self, path)

    monkeypatch.setattr(outcomes.OutcomeLedger, "write", spy)
    run_sim.run_agent(SEED, DAYS, reaction_delays=True)
    ledger = captured["ledger"]

    sends = {(a.invoice_id, a.day): a.seq for a in ledger.actions
             if a.action_kind in ("send", "payment_plan", "counter_settle")}
    assert sends, "the run sent nothing, so nothing was tested"
    for payment in ledger.payments:
        send_seq = sends.get((payment.invoice_id, payment.day))
        if send_seq is not None:
            assert payment.seq < send_seq, (
                f"{payment.invoice_id} was paid on {payment.day} AFTER a message that "
                f"same day -- a same-day reaction, which R1 rules out")


def test_the_same_seed_gives_the_same_delayed_run() -> None:
    first = run_sim.run_agent(SEED, 30, reaction_delays=True)
    second = run_sim.run_agent(SEED, 30, reaction_delays=True)
    assert first["final"] == second["final"]
    assert first["paid_invoices"] == second["paid_invoices"]


def test_a_delay_changes_when_a_partial_payment_lands_not_how_much() -> None:
    """R1's own investigation found the partial-payment share drawn from the
    LANDING day's stream, so a delay re-rolled how much the buyer paid. It is
    drawn from the day the reaction was decided (rolled_on) now."""
    def paid_on(landing: date) -> int:
        invoice = {"invoice_id": "INV-X", "buyer_id": "BUY-01", "amount_paise": 10_000_000,
                   "acceptance_date": "2026-05-01", "issue_date": "2026-05-01",
                   "written_agreement": False, "agreed_days": None, "status": "open",
                   "partial_payments": [], "amount_paid_paise": 0}
        run_sim._apply_reaction(invoice, [], {"outcome": personas.PAY_PARTIAL}, landing,
                                SEED, log=False, rolled_on=date(2026, 8, 24))
        return invoice["amount_paid_paise"]

    assert paid_on(date(2026, 8, 25)) == paid_on(date(2026, 8, 29)) > 0


# --------------------------------------------------------------------------
# a reaction that has not landed is not a reply
# --------------------------------------------------------------------------

def test_awaiting_and_moot_contacts_do_not_count_as_replies() -> None:
    """engine/buyer_panel.py's response rate reads history outcomes; R1's two
    new ones -- a reaction still in flight, and one dropped because the
    invoice was settled first -- must not read as the buyer having replied."""
    history = [{"outcome": o} for o in (
        "paid_full", "promise_made", "no_reply", "awaiting_reply", "moot_already_paid")]
    rate = buyer_panel._response_rate(history)
    assert rate == {"messages_sent": 5, "replies": 2, "response_rate_pct": 40.0}
