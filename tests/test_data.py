"""Tests for the data factory.

Two things matter here. First, reproducibility: the whole experiment rests on
"same seed, same world", so a drift in generation would quietly invalidate the
baseline-vs-agent comparison. Second, the mess is deliberate -- if a refactor
accidentally tidied the dataset (no disputes, no missing agreements, no
low-confidence buyers) the agent would look better than it is.

The leak test is the important one: the hidden persona must never reach
data/seed/, or the buyer score becomes a lookup instead of an inference.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from datetime import date

import pytest

from data import generate as gen

SEED = 42


@pytest.fixture(scope="module")
def world() -> dict:
    return gen.generate(SEED)


@pytest.fixture(scope="module")
def buyers(world: dict) -> list[dict]:
    return world["buyers"]["buyers"]


@pytest.fixture(scope="module")
def invoices(world: dict) -> list[dict]:
    return world["invoices"]["invoices"]


@pytest.fixture(scope="module")
def current(invoices: list[dict]) -> list[dict]:
    return [inv for inv in invoices if inv["cohort"] == "current"]


@pytest.fixture(scope="module")
def history(invoices: list[dict]) -> list[dict]:
    return [inv for inv in invoices if inv["cohort"] == "history"]


# --- reproducibility ------------------------------------------------------

def test_same_seed_produces_identical_json() -> None:
    first = json.dumps(gen.generate(SEED), sort_keys=True)
    second = json.dumps(gen.generate(SEED), sort_keys=True)
    assert first == second


def test_different_seed_produces_different_data() -> None:
    assert json.dumps(gen.generate(SEED)) != json.dumps(gen.generate(SEED + 1))


def test_output_carries_no_wall_clock_timestamp(world: dict) -> None:
    """A generated_at field is the classic way byte-identity dies.

    Checking that today's date is absent would be wrong -- the simulated world
    legitimately contains dates around today. What must be absent is anything
    read from the real clock: no timestamp keys, and no time-of-day anywhere,
    since every date in the dataset is a plain calendar date.
    """
    blob = json.dumps(world)
    for banned in ("generated_at", "created_at", "timestamp", "now"):
        assert banned not in blob

    for section in ("buyers", "invoices", "personas"):
        for key in world[section]["meta"]:
            assert key in {
                "schema_version", "seed", "buyer_count", "simulation_start",
                "current_invoice_count", "history_invoice_count", "warning",
                "with_malformed",
            }

    # Every date is YYYY-MM-DD. An ISO datetime would mean the clock got in.
    for invoice in world["invoices"]["invoices"]:
        for field in ("issue_date", "acceptance_date", "paid_date"):
            value = invoice[field]
            if value is not None:
                assert date.fromisoformat(value).isoformat() == value


# --- the persona must not leak -------------------------------------------

def test_persona_never_appears_in_seed_data(world: dict) -> None:
    seed_blob = json.dumps(world["buyers"]) + json.dumps(world["invoices"])
    for tag in gen.PERSONA_BEHAVIOUR:
        assert tag not in seed_blob, f"persona {tag!r} leaked into data/seed/"
    assert "persona" not in seed_blob


def test_every_buyer_has_exactly_one_persona(world: dict, buyers: list[dict]) -> None:
    personas = world["personas"]["personas"]
    assert sorted(personas) == sorted(b["buyer_id"] for b in buyers)
    assert Counter(personas.values()) == Counter(dict(gen.PERSONA_COUNTS))


# --- shape ----------------------------------------------------------------

def test_buyer_and_invoice_counts(buyers: list[dict], current: list[dict]) -> None:
    assert len(buyers) == gen.N_BUYERS
    assert len(current) == gen.N_CURRENT_INVOICES


def test_every_invoice_belongs_to_a_known_buyer(buyers: list[dict], invoices: list[dict]) -> None:
    known = {b["buyer_id"] for b in buyers}
    assert {inv["buyer_id"] for inv in invoices} <= known


def test_invoice_ids_are_unique(invoices: list[dict]) -> None:
    ids = [inv["invoice_id"] for inv in invoices]
    assert len(ids) == len(set(ids))


def test_amounts_are_whole_rupees_inside_the_stated_band(invoices: list[dict]) -> None:
    for invoice in invoices:
        amount = invoice["amount_paise"]
        assert gen.AMOUNT_MIN_PAISE <= amount <= gen.AMOUNT_MAX_PAISE
        assert amount % gen.AMOUNT_STEP_PAISE == 0
        assert isinstance(amount, int), "money must be integer paise, never float"


def test_payments_add_up(invoices: list[dict]) -> None:
    for invoice in invoices:
        total = sum(p["amount_paise"] for p in invoice["partial_payments"])
        assert total == invoice["amount_paid_paise"]
        assert total <= invoice["amount_paise"]


def test_dates_are_ordered_within_each_invoice(invoices: list[dict]) -> None:
    for invoice in invoices:
        issue = date.fromisoformat(invoice["issue_date"])
        acceptance = date.fromisoformat(invoice["acceptance_date"])
        assert issue <= acceptance, "an invoice cannot be issued after acceptance"
        if invoice["paid_date"]:
            assert date.fromisoformat(invoice["paid_date"]) > issue


def test_history_is_paid_and_in_the_past(history: list[dict]) -> None:
    for invoice in history:
        assert invoice["status"] == "paid"
        assert invoice["amount_paid_paise"] == invoice["amount_paise"]
        assert date.fromisoformat(invoice["paid_date"]) < gen.SIMULATION_START


def test_current_invoices_are_not_yet_settled(current: list[dict]) -> None:
    for invoice in current:
        assert invoice["status"] in {"open", "partially_paid", "disputed"}
        assert invoice["paid_date"] is None
        assert invoice["amount_paid_paise"] < invoice["amount_paise"]


# --- invoices have to read like business documents ------------------------

def test_every_invoice_describes_what_was_sold(invoices: list[dict]) -> None:
    """The writer quotes this line, so it cannot be blank or a bare number."""
    for invoice in invoices:
        description = invoice["description"]
        assert description and not description.isdigit()
        assert any(ch.isdigit() for ch in description), "a quantity is expected"
        assert any(ch.isalpha() for ch in description), "an item name is expected"


def test_descriptions_match_the_buyer_sector(buyers: list[dict], invoices: list[dict]) -> None:
    """A textiles buyer should not be invoiced for brake pads."""
    sector_of = {b["buyer_id"]: b["sector"] for b in buyers}
    for invoice in invoices:
        sector = sector_of[invoice["buyer_id"]]
        items = [item for item, _unit, _low, _high in gen.GOODS[sector]]
        assert any(item in invoice["description"] for item in items)


def test_purchase_orders_are_financial_year_formatted(invoices: list[dict]) -> None:
    for invoice in invoices:
        po = invoice["po_number"]
        if po is None:
            continue
        prefix, fy, serial = po.split("/")
        assert prefix == "PO"
        start, end = fy.split("-")
        assert (int(start) + 1) % 100 == int(end), "Indian FY runs April to March"
        assert serial.isdigit() and len(serial) == 5


def test_some_orders_have_no_purchase_order(invoices: list[dict]) -> None:
    """Plenty of MSME trade runs on a phone call. That gap is part of the problem."""
    missing = [inv for inv in invoices if inv["po_number"] is None]
    assert 0 < len(missing) < len(invoices)


def test_the_dispute_quotes_the_goods(buyers: list[dict], current: list[dict]) -> None:
    """A dispute is always about something specific, so the note names the item."""
    sector_of = {b["buyer_id"]: b["sector"] for b in buyers}
    disputed = next(inv for inv in current if inv["status"] == "disputed")
    item = gen._goods_label(disputed["description"], sector_of[disputed["buyer_id"]])
    assert item != "consignment", "the item should be recognised, not fall back"
    assert item in disputed["dispute_note"]


def test_invoice_quantities_agree_with_their_amounts(buyers: list[dict], invoices: list[dict]) -> None:
    """A line reading 810 cases against a Rs 9,600 total would read as fake.

    The implied per-unit rate must land inside the band declared for that item.
    """
    sector_of = {b["buyer_id"]: b["sector"] for b in buyers}
    for invoice in invoices:
        sector = sector_of[invoice["buyer_id"]]
        quantity = int(invoice["description"].split()[0])
        assert quantity >= 1
        implied_rate = invoice["amount_paise"] / quantity
        item = gen._goods_label(invoice["description"], sector)
        band = next(g for g in gen.GOODS[sector] if g[0] == item)
        # Rounding the quantity moves the implied rate a little; allow 20%.
        assert band[2] * 0.8 <= implied_rate <= band[3] * 1.2, (
            f"{invoice['invoice_id']}: {invoice['description']} does not fit "
            f"{gen.format_inr(invoice['amount_paise'], 'Rs ')}"
        )


# --- the mess, which is the point ----------------------------------------

def test_exactly_one_open_dispute(current: list[dict]) -> None:
    disputed = [inv for inv in current if inv["status"] == "disputed"]
    assert len(disputed) == 1
    assert disputed[0]["dispute_note"]


def test_some_invoices_have_no_written_agreement(current: list[dict]) -> None:
    """These are the ones where the 15-day rule bites."""
    missing = [inv for inv in current if not inv["written_agreement"]]
    assert len(missing) >= 15
    assert all(inv["agreed_days"] is None for inv in missing)


def test_some_agreed_terms_exceed_the_statutory_ceiling(current: list[dict]) -> None:
    """Void above 45 days -- this is what the law engine exists to catch."""
    _, ceiling = gen._load_statutory_terms()
    illegal = [inv for inv in current if (inv["agreed_days"] or 0) > ceiling]
    assert len(illegal) >= 10


def test_partial_payments_exist(current: list[dict]) -> None:
    partial = [inv for inv in current if inv["status"] == "partially_paid"]
    assert len(partial) == gen.N_PARTIALLY_PAID
    assert all(0 < inv["amount_paid_paise"] < inv["amount_paise"] for inv in partial)


def test_at_least_three_buyers_have_low_confidence(buyers: list[dict], history: list[dict]) -> None:
    counts = Counter(inv["buyer_id"] for inv in history)
    low = [b for b in buyers if counts[b["buyer_id"]] < 3]
    assert len(low) >= gen.N_LOW_CONFIDENCE_BUYERS


def test_some_invoices_are_not_yet_due(current: list[dict]) -> None:
    """The watchdog must filter, not blast everything in the table."""
    no_agreement, ceiling = gen._load_statutory_terms()
    not_due = [
        inv for inv in current
        if gen.statutory_due_date(inv, no_agreement, ceiling) > gen.SIMULATION_START
    ]
    assert len(not_due) == gen.N_NOT_YET_DUE


def test_exactly_one_buyer_has_opted_out(buyers: list[dict]) -> None:
    assert sum(1 for b in buyers if b["opted_out"]) == 1


def test_both_languages_and_profiles_are_represented(buyers: list[dict]) -> None:
    assert {b["profile"] for b in buyers} == {"corporate", "small_trader"}
    assert {b["language_pref"] for b in buyers} == {"english", "hinglish"}


# --- nobody real gets contacted ------------------------------------------

def test_no_deliverable_contact_details(buyers: list[dict]) -> None:
    """Non-negotiable #4, enforced at the data layer as well as the channel."""
    for buyer in buyers:
        assert buyer["contact_email"].endswith(".example.invalid")
        assert buyer["contact_phone"].startswith("+91-90000-")


# --- the persona signal has to be learnable ------------------------------

def test_personas_produce_distinguishable_payment_behaviour(world: dict, history: list[dict]) -> None:
    """If every persona paid the same way, the score engine would be scoring noise."""
    no_agreement, ceiling = gen._load_statutory_terms()
    personas = world["personas"]["personas"]
    totals: dict[str, list[int]] = {}
    for invoice in history:
        tag = personas[invoice["buyer_id"]]
        due = gen.statutory_due_date(invoice, no_agreement, ceiling)
        delay = (date.fromisoformat(invoice["paid_date"]) - due).days
        totals.setdefault(tag, []).append(delay)

    average = {tag: sum(d) / len(d) for tag, d in totals.items()}
    assert average["forgetful"] < average["cash_tight"] < average["habitual_delayer"] < average["deadbeat"]


# --- the inflow signals (Phase 1: the ability axis needs evidence) --------

#: More than one seed, because these are statistical tendencies, not rules.
#: A single seed's twenty buyers is four per persona -- too few to assert on
#: without pinning noise instead of behaviour.
INFLOW_SEEDS = (42, 7, 13, 99, 2024, 555)


def _buyers_by_persona(seeds=INFLOW_SEEDS) -> dict[str, list[dict]]:
    """Every generated buyer across several seeds, grouped by hidden persona."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for seed in seeds:
        world = gen.generate(seed)
        personas = world["personas"]["personas"]
        for buyer in world["buyers"]["buyers"]:
            grouped[personas[buyer["buyer_id"]]].append(buyer)
    return grouped


def _trend_pct(series: list[int]) -> float:
    """First half of the series against the second half, as a percentage."""
    half = len(series) // 2
    older = sum(series[:half]) / half
    newer = sum(series[half:]) / len(series[half:])
    return (newer - older) / older * 100


def test_every_buyer_carries_an_inflow_series_and_a_failed_payment_count(buyers: list[dict]) -> None:
    for buyer in buyers:
        series = buyer["monthly_inflow_paise"]
        assert gen.INFLOW_MONTHS_MIN <= len(series) <= gen.INFLOW_MONTHS_MAX
        assert all(isinstance(month, int) and month > 0 for month in series)
        assert isinstance(buyer["failed_payment_count"], int)
        assert 0 <= buyer["failed_payment_count"] <= gen.FAILED_PAYMENT_ATTEMPTS


def test_the_inflow_signals_are_reproducible_for_the_same_seed() -> None:
    first = [(b["monthly_inflow_paise"], b["failed_payment_count"])
             for b in gen.generate(SEED)["buyers"]["buyers"]]
    second = [(b["monthly_inflow_paise"], b["failed_payment_count"])
              for b in gen.generate(SEED)["buyers"]["buyers"]]
    assert first == second


def test_a_different_seed_produces_different_inflow_signals() -> None:
    first = [b["monthly_inflow_paise"] for b in gen.generate(SEED)["buyers"]["buyers"]]
    other = [b["monthly_inflow_paise"] for b in gen.generate(SEED + 1)["buyers"]["buyers"]]
    assert first != other


def test_the_inflow_signals_are_drawn_from_their_own_random_stream() -> None:
    """Adding a field must not change the world it is added to.

    These signals arrived after the dataset already had a published, pinned
    shape. Drawing them from the world's own rng would shift every later draw
    and silently rewrite every invoice, every delay, and every headline
    number in report/out/ -- so _add_inflow_signals() runs on a stream of its
    own. This proves it, rather than trusting the comment that says so.
    """
    rng = random.Random(SEED)
    personas = gen._assign_personas(rng)
    low_confidence = sorted(rng.sample(range(gen.N_BUYERS), gen.N_LOW_CONFIDENCE_BUYERS))
    buyer_records = gen._build_buyers(rng, low_confidence)

    before = rng.getstate()
    gen._add_inflow_signals(SEED, buyer_records, personas)
    assert rng.getstate() == before, "_add_inflow_signals consumed the world's random stream"


def test_the_inflow_signals_never_name_the_persona_that_shaped_them(buyers: list[dict]) -> None:
    """The correlation is one-directional: persona -> numbers, never back."""
    blob = json.dumps([{k: v for k, v in b.items()
                        if k in ("monthly_inflow_paise", "failed_payment_count")}
                       for b in buyers])
    for tag in gen.PERSONA_BEHAVIOUR:
        assert tag not in blob


def test_a_cash_tight_persona_tends_toward_a_declining_inflow_series() -> None:
    """The signal the ability axis exists to read. Statistical, not per-buyer."""
    grouped = _buyers_by_persona()
    average = {
        tag: sum(_trend_pct(b["monthly_inflow_paise"]) for b in records) / len(records)
        for tag, records in grouped.items()
    }
    assert average["cash_tight"] < -10
    assert average["deadbeat"] < average["cash_tight"]
    assert average["cash_tight"] < average["habitual_delayer"]
    assert average["cash_tight"] < average["forgetful"]


def test_a_forgetful_persona_looks_financially_healthy() -> None:
    """Forgetful is the control case: they pay late for reasons money cannot explain."""
    grouped = _buyers_by_persona()
    trend = [_trend_pct(b["monthly_inflow_paise"]) for b in grouped["forgetful"]]
    failed = [b["failed_payment_count"] for b in grouped["forgetful"]]
    assert sum(trend) / len(trend) > 0
    assert sum(failed) / len(failed) < 0.5


def test_failed_payments_concentrate_in_the_personas_that_are_short_of_money() -> None:
    grouped = _buyers_by_persona()
    average = {tag: sum(b["failed_payment_count"] for b in records) / len(records)
               for tag, records in grouped.items()}
    for broke in ("cash_tight", "deadbeat"):
        for solvent in ("forgetful", "habitual_delayer", "disputer"):
            assert average[broke] > average[solvent], f"{broke} should bounce more than {solvent}"


def test_a_habitual_delayer_looks_as_able_to_pay_as_a_prompt_payer() -> None:
    """The distinction the whole phase exists for.

    habitual_delayer and cash_tight both pay late, so the legacy score cannot
    separate them. Their MONEY must look different, or the ability axis has
    nothing to read and the split buys us nothing.
    """
    grouped = _buyers_by_persona()
    delayer = [_trend_pct(b["monthly_inflow_paise"]) for b in grouped["habitual_delayer"]]
    tight = [_trend_pct(b["monthly_inflow_paise"]) for b in grouped["cash_tight"]]
    assert sum(delayer) / len(delayer) > sum(tight) / len(tight) + 10


def test_a_corporate_buyer_has_a_bigger_typical_month_than_a_small_trader() -> None:
    """So the same invoice is routine for one and a hard ask for the other."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for seed in INFLOW_SEEDS:
        for buyer in gen.generate(seed)["buyers"]["buyers"]:
            grouped[buyer["profile"]].append(sum(buyer["monthly_inflow_paise"])
                                             / len(buyer["monthly_inflow_paise"]))
    corporate = sum(grouped["corporate"]) / len(grouped["corporate"])
    trader = sum(grouped["small_trader"]) / len(grouped["small_trader"])
    assert corporate > trader


def test_the_schema_version_records_that_the_buyer_shape_changed(world: dict) -> None:
    """Phase 1 added fields to the buyer record; the version says so."""
    assert gen.SCHEMA_VERSION >= 2
    assert world["buyers"]["meta"]["schema_version"] == gen.SCHEMA_VERSION


# --- display --------------------------------------------------------------

@pytest.mark.parametrize(
    ("paise", "expected"),
    [
        (0, "Rs 0"),
        (800_000, "Rs 8,000"),
        (5_000_000, "Rs 50,000"),
        (50_000_000, "Rs 5,00,000"),
        (120_000_000, "Rs 12,00,000"),
        (1_000_000_000, "Rs 1,00,00,000"),
    ],
)
def test_indian_digit_grouping(paise: int, expected: str) -> None:
    assert gen.format_inr(paise, "Rs ") == expected


# --- --with-malformed: deliberately bad records, opt-in only --------------
# docs/edge_cases.md TC-045, TC-049, TC-050, TC-051, TC-053, TC-054, made
# reachable from real data on disk (data/generate.py --with-malformed), not
# only from unit-test fixtures like tests/test_validate.py's.

_MALFORMED_TC_KEYWORDS = {
    "TC-045": "acceptance date",
    "TC-049": "agreed_days",
    "TC-050": "future",
    "TC-051": "chronology",
    "TC-053": "amount",
    "TC-054": "amount",
}


def test_default_generation_is_unaffected_by_the_flag_existing() -> None:
    """The default call stays byte-identical whether or not this flag exists."""
    assert json.dumps(gen.generate(SEED), sort_keys=True) == \
           json.dumps(gen.generate(SEED, with_malformed=False), sort_keys=True)


def test_with_malformed_adds_exactly_the_six_named_fixtures() -> None:
    world = gen.generate(SEED, with_malformed=True)
    ids = {inv["invoice_id"] for inv in world["invoices"]["invoices"]
           if inv["invoice_id"].startswith("INV-MALFORMED-")}
    assert ids == {f"INV-MALFORMED-{tc}" for tc in _MALFORMED_TC_KEYWORDS}


def test_with_malformed_does_not_perturb_the_other_invoices() -> None:
    """Appended after the RNG-driven world is fully built -- must not shift IDs."""
    plain = gen.generate(SEED)["invoices"]["invoices"]
    with_flag = gen.generate(SEED, with_malformed=True)["invoices"]["invoices"]
    non_malformed = [inv for inv in with_flag
                     if not inv["invoice_id"].startswith("INV-MALFORMED-")]
    assert json.dumps(plain, sort_keys=True) == json.dumps(non_malformed, sort_keys=True)


def test_each_malformed_fixture_trips_exactly_the_case_it_names() -> None:
    from engine import validate

    world = gen.generate(SEED, with_malformed=True)
    invoices = {inv["invoice_id"]: inv for inv in world["invoices"]["invoices"]}
    today = date.fromisoformat(world["invoices"]["meta"]["simulation_start"])
    for tc, keyword in _MALFORMED_TC_KEYWORDS.items():
        reason = validate.invalid_reason(invoices[f"INV-MALFORMED-{tc}"], today)
        assert reason is not None, f"{tc} fixture was not rejected"
        assert keyword in reason, f"{tc}: expected {keyword!r} in reason {reason!r}"


def test_malformed_fixtures_are_excluded_from_the_watchdog_queue() -> None:
    from engine import watchdog

    world = gen.generate(SEED, with_malformed=True)
    invoices = world["invoices"]["invoices"]
    today = date.fromisoformat(world["invoices"]["meta"]["simulation_start"])
    queued = {inv["invoice_id"] for inv in watchdog.overdue_invoices(invoices, today)}
    assert not any(inv_id.startswith("INV-MALFORMED-") for inv_id in queued)


def test_print_summary_does_not_crash_on_malformed_fixtures(capsys) -> None:
    world = gen.generate(SEED, with_malformed=True)
    gen.print_summary(world, {"buyers": gen.DEFAULT_SEED_DIR / "buyers.json"})
    assert "malformed" in capsys.readouterr().out


def test_with_malformed_is_recorded_in_the_invoices_meta() -> None:
    """The dataset on disk is self-describing -- see data.store.load_meta()."""
    assert gen.generate(SEED)["invoices"]["meta"]["with_malformed"] is False
    assert gen.generate(SEED, with_malformed=True)["invoices"]["meta"]["with_malformed"] is True
