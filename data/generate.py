"""Fake data factory -- builds the test world: 20 buyers, 100 open invoices.

Synthetic on purpose: the track allows it, and it means no API access and no
privacy concerns can block the build. Nobody in here is a real person, no
address is deliverable (every email sits on a reserved .invalid domain) and no
phone number is dialable.

Messy on purpose too. The dataset deliberately contains:

  * invoices with no written agreement, where the 15-day statutory rule applies
  * agreed terms of 60 and 90 days, which are void above the 45-day ceiling
  * partial payments
  * exactly one pre-disputed invoice
  * three buyers with fewer than three past invoices (low confidence)
  * invoices that are not yet due, so the watchdog has to actually filter

Every buyer also gets a history of PAST paid invoices whose delays follow a
hidden persona, so the score engine has real behaviour to score. The persona
tag itself is written to sim/hidden_personas.json and never appears in
data/seed/ -- the agent has to infer what kind of payer it is dealing with from
payment history alone, exactly as it would in production.

Money is integer paise throughout. Dates are datetime.date. All randomness
comes from --seed, and the output carries no wall-clock timestamp, so the same
seed produces byte-identical files.

    python data/generate.py --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

# Allow running this file directly as a script as well as importing it, by
# putting the repo root on the path when there is no enclosing package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.money import enable_unicode_output, format_inr

SCHEMA_VERSION = 2      # 2: buyers carry the inflow signals (Phase 1)
DEFAULT_SEED = 42

#: Day 0 of the simulated world. Every date in the dataset is placed relative
#: to this, never to the real clock, so the data does not rot.
SIMULATION_START = date(2026, 8, 24)

N_BUYERS = 20
N_CURRENT_INVOICES = 100
N_LOW_CONFIDENCE_BUYERS = 3
N_PARTIALLY_PAID = 12
N_NOT_YET_DUE = 10
MIN_CURRENT_PER_BUYER = 2

MIN_HISTORY = 3
MAX_HISTORY = 15

AMOUNT_MIN_PAISE = 800_000            # Rs 8,000
AMOUNT_MAX_PAISE = 120_000_000        # Rs 12,00,000
AMOUNT_STEP_PAISE = 10_000            # round every amount to the nearest Rs 100

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED_DIR = ROOT / "data" / "seed"
DEFAULT_PERSONA_PATH = ROOT / "sim" / "hidden_personas.json"
LEGAL_CONFIG_PATH = ROOT / "config" / "legal.yaml"

#: Fixed counts, then shuffled. A pure random draw could hand us zero deadbeats
#: and silently kill the Samadhaan demo.
PERSONA_COUNTS: tuple[tuple[str, int], ...] = (
    ("forgetful", 5),
    ("cash_tight", 5),
    ("habitual_delayer", 4),
    ("disputer", 3),
    ("deadbeat", 3),
)


@dataclass(frozen=True)
class Persona:
    """How a persona behaved in the PAST -- this is what the score engine sees.

    The last three fields shape the buyer's money coming IN rather than their
    payment behaviour going out. They exist so the two-axis score
    (engine/ability_willingness.py) has something real to read: a buyer who
    cannot pay and a buyer who will not pay look identical in payment history
    alone, and telling them apart is the whole point of the ability axis.

    The correlation is deliberate and one-directional -- the persona shapes
    the numbers, and only the NUMBERS reach the buyer record. No module under
    engine/ ever sees the tag that produced them, exactly as it never sees
    the tag behind a delay pattern today (tests/test_sim_isolation.py).
    """

    delay_min: int              # days past the statutory due date
    delay_max: int
    on_time_chance: float       # chance of paying on or before the due date
    broken_promise_chance: float
    dispute_chance: float
    inflow_drift: float         # month-on-month drift in money coming in
    inflow_volatility: float    # how lumpy that inflow is, month to month
    failed_payment_chance: float  # per-attempt chance of a bounced payment


#: Note which personas are healthy but unwilling. habitual_delayer and
#: disputer both pay late, but neither is short of money -- their inflow is
#: flat and steady, so ability stays high while willingness falls. That is
#: what puts them in the "can pay but won't" quadrant instead of lumping them
#: in with cash_tight, which is the entire distinction this phase adds.
PERSONA_BEHAVIOUR: dict[str, Persona] = {
    "forgetful": Persona(0, 8, 0.45, 0.05, 0.00, 0.010, 0.06, 0.02),
    "cash_tight": Persona(10, 30, 0.10, 0.25, 0.02, -0.075, 0.22, 0.35),
    "habitual_delayer": Persona(25, 60, 0.02, 0.45, 0.05, 0.005, 0.09, 0.06),
    "disputer": Persona(5, 25, 0.20, 0.10, 0.35, 0.000, 0.08, 0.05),
    "deadbeat": Persona(60, 150, 0.00, 0.55, 0.15, -0.110, 0.26, 0.45),
}

#: How many months of inflow history a buyer carries, and the size of a
#: typical month by profile. A corporate's monthly inflow dwarfs a small
#: trader's, so the SAME invoice is a routine payment for one and a genuinely
#: hard ask for the other -- which is exactly what the ability axis's
#: invoice-to-capacity ratio is there to notice.
INFLOW_MONTHS_MIN = 6
INFLOW_MONTHS_MAX = 12
INFLOW_BASE_PAISE: dict[str, tuple[int, int]] = {
    "corporate": (600_000_000, 3_000_000_000),      # Rs 60L - Rs 3Cr a month
    "small_trader": (60_000_000, 400_000_000),      # Rs 6L - Rs 40L a month
}
#: Recent payment attempts a failed-payment count is drawn over.
FAILED_PAYMENT_ATTEMPTS = 6
#: An inflow series never collapses below this fraction of where it started:
#: a buyer still trading with us has not gone to zero, and a zero month would
#: make the capacity ratio meaningless rather than merely bad.
INFLOW_FLOOR_FRACTION = 0.15

#: name, profile, city, state, GST state code, contact person, sector
BUYER_SEEDS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    ("Sundaram Auto Components Pvt Ltd", "corporate", "Coimbatore", "Tamil Nadu", "33", "Ramesh Iyer", "auto"),
    ("Meridian Logistics India Pvt Ltd", "corporate", "Navi Mumbai", "Maharashtra", "27", "Farid Shaikh", "logistics"),
    ("Kaveri Textiles Ltd", "corporate", "Tiruppur", "Tamil Nadu", "33", "Lakshmi Narayanan", "textiles"),
    ("Northstar Engineering Pvt Ltd", "corporate", "Pune", "Maharashtra", "27", "Sneha Kulkarni", "engineering"),
    ("Bharat Polymers Ltd", "corporate", "Vadodara", "Gujarat", "24", "Nilesh Patel", "polymers"),
    ("Vistara Consumer Goods Pvt Ltd", "corporate", "Bengaluru", "Karnataka", "29", "Arun Prakash", "fmcg"),
    ("Deccan Electricals Ltd", "corporate", "Hyderabad", "Telangana", "36", "Padmaja Rao", "electricals"),
    ("Anand Precision Works Pvt Ltd", "corporate", "Gurugram", "Haryana", "06", "Vikas Ahuja", "precision"),
    ("Orbit Retail Ventures Pvt Ltd", "corporate", "New Delhi", "Delhi", "07", "Tanvi Bhalla", "retail"),
    ("Verma Hardware Stores", "small_trader", "Kanpur", "Uttar Pradesh", "09", "Suresh Verma", "hardware"),
    ("Sharma Trading Co", "small_trader", "Ludhiana", "Punjab", "03", "Gopal Sharma", "general"),
    ("New Ganesh Enterprises", "small_trader", "Nagpur", "Maharashtra", "27", "Mangesh Deshmukh", "general"),
    ("Ravi Steel Traders", "small_trader", "Raipur", "Chhattisgarh", "22", "Ravi Agrawal", "steel"),
    ("Jain Packaging Mart", "small_trader", "Indore", "Madhya Pradesh", "23", "Mahesh Jain", "packaging"),
    ("Krishna Electricals", "small_trader", "Vijayawada", "Andhra Pradesh", "37", "Krishna Murthy", "electricals"),
    ("Modern Tools and Spares", "small_trader", "Rajkot", "Gujarat", "24", "Jignesh Thakkar", "tools"),
    ("Balaji Agencies", "small_trader", "Belagavi", "Karnataka", "29", "Shrikant Patil", "general"),
    ("Sethi Brothers", "small_trader", "Jalandhar", "Punjab", "03", "Harpreet Sethi", "hardware"),
    ("Om Sai Distributors", "small_trader", "Nashik", "Maharashtra", "27", "Sandeep Pawar", "fmcg"),
    ("Fatima Traders", "small_trader", "Kozhikode", "Kerala", "32", "Abdul Rahman", "general"),
)

#: What each sector actually buys: item, unit, and a plausible per-unit rate
#: band in paise. The quantity on an invoice is derived from its amount and the
#: rate, so "Rs 9,600" never comes with "810 cases" attached -- a description
#: that contradicts its own total is worse than no description at all.
#:
#: Item names never repeat the unit word, so nothing reads "480 rolls pallet
#: wrap rolls".
GOODS: dict[str, tuple[tuple[str, str, int, int], ...]] = {
    "auto": (
        ("brake pads", "sets", 45_000, 120_000),
        ("clutch plates", "units", 30_000, 90_000),
        ("wheel bearings", "units", 12_000, 60_000),
    ),
    "logistics": (
        ("pallet wrap", "rolls", 9_000, 25_000),
        ("corrugated cartons", "units", 1_800, 4_500),
        ("strapping", "coils", 45_000, 120_000),
    ),
    "textiles": (
        ("cotton yarn 30s", "kg", 21_000, 32_000),
        ("dyed cotton fabric", "metres", 6_000, 18_000),
        ("polyester blend fabric", "metres", 4_500, 12_000),
    ),
    "engineering": (
        ("CNC machined flanges", "units", 35_000, 150_000),
        ("mild steel brackets", "units", 4_000, 18_000),
        ("gearbox housings", "units", 120_000, 600_000),
    ),
    "polymers": (
        ("HDPE granules", "kg", 9_500, 13_000),
        ("PP copolymer", "kg", 8_500, 14_000),
        ("masterbatch pigment", "kg", 18_000, 60_000),
    ),
    "fmcg": (
        ("detergent refill pouches", "cases", 35_000, 90_000),
        ("packaged snack cartons", "cases", 40_000, 110_000),
        ("shampoo sachets", "cases", 50_000, 140_000),
    ),
    "electricals": (
        ("copper wire 1.5 sqmm", "coils", 90_000, 220_000),
        ("LED panel lights", "units", 18_000, 65_000),
        ("MCB distribution boards", "units", 45_000, 250_000),
    ),
    "precision": (
        ("ground steel shafts", "units", 18_000, 90_000),
        ("precision bushings", "units", 2_500, 14_000),
        ("hardened dowel pins", "units", 600, 3_000),
    ),
    "retail": (
        ("stainless steel cookware", "units", 25_000, 120_000),
        ("plastic storage bins", "units", 8_000, 42_000),
        ("assorted household goods", "cases", 30_000, 90_000),
    ),
    "hardware": (
        ("galvanised hinges", "units", 2_000, 11_000),
        ("cement screws", "boxes", 12_000, 50_000),
        ("PVC pipe fittings", "units", 2_500, 16_000),
    ),
    "steel": (
        ("MS angles 50x50", "kg", 5_200, 7_200),
        ("TMT bars 12mm", "kg", 4_800, 6_800),
        ("GI sheets", "bundles", 90_000, 260_000),
    ),
    "packaging": (
        ("corrugated boxes 5-ply", "units", 2_200, 6_000),
        ("BOPP tape", "rolls", 2_800, 7_500),
        ("shrink film", "rolls", 60_000, 180_000),
    ),
    "tools": (
        ("drill bits", "sets", 15_000, 70_000),
        ("torque wrenches", "units", 90_000, 450_000),
        ("abrasive cutting discs", "units", 1_800, 7_000),
    ),
    "general": (
        ("assorted trading stock", "cases", 30_000, 120_000),
        ("mixed consignment", "cases", 25_000, 100_000),
        ("general merchandise", "cases", 20_000, 80_000),
    ),
}

#: Dispute notes quote the goods, because a dispute is always about something
#: specific. Formatted with the invoice description.
DISPUTE_NOTES: tuple[str, ...] = (
    "Buyer reports transit damage on part of the {goods} and wants a credit note before paying.",
    "Buyer disputes the quantity of {goods} received against the delivery challan.",
    "Buyer says the rate billed for the {goods} does not match the purchase order.",
    "Buyer has raised a quality complaint on the {goods} and asked for a joint inspection.",
)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _load_statutory_terms() -> tuple[int, int]:
    """Read the 15/45 day rule from config/legal.yaml.

    The data factory places dates, it does not do law math -- but the two have
    to agree, and legal numbers live in config, never in code.
    """
    legal = yaml.safe_load(LEGAL_CONFIG_PATH.read_text(encoding="utf-8"))
    return int(legal["no_agreement_days"]), int(legal["max_agreement_days"])


def _slug(name: str) -> str:
    """Company name to a domain-ish slug: Kaveri Textiles Ltd -> kaveri-textiles."""
    dropped = {"pvt", "ltd", "co", "and"}
    cleaned = "".join(c if c.isalnum() else " " for c in name).lower()
    words = [w for w in cleaned.split() if w not in dropped]
    return "-".join(words[:3])


def _email(contact_name: str, company: str) -> str:
    """A guaranteed-undeliverable address on the RFC 2606 reserved .invalid TLD."""
    local = ".".join(part.lower() for part in contact_name.split())
    return f"{local}@{_slug(company)}.example.invalid"


def _gstin(rng: random.Random, state_code: str) -> str:
    """A format-shaped but entirely synthetic GSTIN."""
    letters = "".join(rng.choice("ABCDEFGHIJKLMNPQRSTUVWXYZ") for _ in range(5))
    digits = "".join(rng.choice("0123456789") for _ in range(4))
    entity = rng.choice("ABCDEFGHJ")
    check = rng.choice("ABCDEFGHJKLMNP")
    return f"{state_code}{letters}{digits}{entity}1Z{check}"


def _amount(rng: random.Random, profile: str) -> int:
    """Log-uniform amount in paise: many small invoices, few large ones."""
    low, high = AMOUNT_MIN_PAISE, AMOUNT_MAX_PAISE
    if profile == "corporate":
        low = AMOUNT_MIN_PAISE * 5          # corporates rarely place tiny orders
    exponent = rng.uniform(0.0, 1.0)
    raw = low * (high / low) ** exponent
    return _round_paise(raw)


def _terms(rng: random.Random) -> tuple[bool, int | None]:
    """Pick the contractual terms. About 30% have no written agreement at all.

    Of those that do, roughly a fifth are 60 or 90 days -- void above the
    statutory ceiling, which is exactly what the law engine exists to catch.
    """
    if rng.random() < 0.30:
        return False, None
    roll = rng.random()
    if roll < 0.30:
        return True, 30
    if roll < 0.70:
        return True, 45
    if roll < 0.90:
        return True, 60
    return True, 90


def _statutory_term_days(written: bool, agreed: int | None, no_agreement: int, ceiling: int) -> int:
    """Days from acceptance to the statutory due date under Section 15."""
    if not written or agreed is None:
        return no_agreement
    return min(agreed, ceiling)


def _round_paise(value: float) -> int:
    """Round to the nearest Rs 100. Real invoices are not priced to the paisa."""
    return max(AMOUNT_STEP_PAISE, int(round(value / AMOUNT_STEP_PAISE)) * AMOUNT_STEP_PAISE)


def _description(rng: random.Random, sector: str, amount_paise: int) -> str:
    """What was actually sold, e.g. 4200 kg HDPE granules, batch B-2214.

    The quantity is derived from the invoice amount at a plausible per-unit
    rate, so the line and the total tell the same story.
    """
    item, unit, rate_low, rate_high = rng.choice(GOODS[sector])
    rate = rng.randint(rate_low, rate_high)
    quantity = max(1, round(amount_paise / rate))
    if quantity > 500:
        quantity = (quantity // 10) * 10
    elif quantity > 100:
        quantity = (quantity // 5) * 5
    text = f"{quantity} {unit} {item}"
    if rng.random() < 0.40:
        text += f", batch B-{rng.randint(1000, 9999)}"
    return text


def _goods_label(description: str, sector: str) -> str:
    """The bare item name out of a description, for quoting in a dispute note."""
    for item, _unit, _rate_low, _rate_high in GOODS[sector]:
        if item in description:
            return item
    return "consignment"


def _po_number(rng: random.Random, issue: date, written_agreement: bool) -> str | None:
    """A purchase order reference in the Indian financial-year format.

    Not every order has one -- plenty of MSME trade runs on a phone call, and
    an invoice with no written agreement is the likeliest to have no PO either.
    That gap is part of why the supplier has no leverage without the Act.
    """
    if rng.random() < (0.20 if written_agreement else 0.55):
        return None
    fy_start = issue.year if issue.month >= 4 else issue.year - 1
    fy = f"{fy_start % 100:02d}-{(fy_start + 1) % 100:02d}"
    return f"PO/{fy}/{rng.randint(1, 99999):05d}"


def _iso(day: date) -> str:
    return day.isoformat()


def _blank_invoice() -> dict[str, Any]:
    """An invoice with its keys in display order; values filled in by the caller."""
    return {
        "invoice_id": "",
        "buyer_id": "",
        "cohort": "",
        "description": "",
        "po_number": None,
        "amount_paise": 0,
        "currency": "INR",
        "issue_date": "",
        "acceptance_date": "",
        "written_agreement": False,
        "agreed_days": None,
        "agreed_due_date": None,
        "status": "open",
        "partial_payments": [],
        "amount_paid_paise": 0,
        "paid_date": None,
        "disputed": False,
        "dispute_note": None,
        "promise_broken": False,
    }


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------

def _assign_personas(rng: random.Random) -> list[str]:
    """Fixed counts, then shuffled, so every persona is guaranteed present."""
    personas: list[str] = []
    for tag, count in PERSONA_COUNTS:
        personas.extend([tag] * count)
    rng.shuffle(personas)
    return personas


def _build_buyers(rng: random.Random, low_confidence: list[int]) -> list[dict[str, Any]]:
    """Build the 20 buyer records. Seed order is shuffled so profiles interleave."""
    order = list(range(N_BUYERS))
    rng.shuffle(order)

    buyers: list[dict[str, Any]] = []
    for position, seed_index in enumerate(order):
        name, profile, city, state, state_code, contact, sector = BUYER_SEEDS[seed_index]
        hinglish_chance = 0.10 if profile == "corporate" else 0.75
        email_chance = 0.85 if profile == "corporate" else 0.30
        # A buyer we have barely traded with has a short relationship.
        if position in low_confidence:
            age_days = rng.randint(60, 200)
        else:
            age_days = rng.randint(240, 900)
        buyers.append(
            {
                "buyer_id": f"BUY-{position + 1:02d}",
                "name": name,
                "profile": profile,
                "sector": sector,
                "language_pref": "hinglish" if rng.random() < hinglish_chance else "english",
                "contact_name": contact,
                "contact_email": _email(contact, name),
                "contact_phone": f"+91-90000-{position + 1:05d}",
                "city": city,
                "state": state,
                "gstin": _gstin(rng, state_code),
                "relationship_since": _iso(SIMULATION_START - timedelta(days=age_days)),
                "preferred_channel": "email" if rng.random() < email_chance else "whatsapp",
                "opted_out": False,
            }
        )
    return buyers


def _inflow_series(rng: random.Random, base: int, behaviour: Persona, months: int) -> list[int]:
    """One buyer's monthly money-in, oldest first, most-recent LAST.

    A level that drifts by the persona's drift each month and wobbles by its
    volatility. Oldest first so a declining series reads the way a human
    reads a bank statement -- and so "the last three months" is a slice off
    the end, not the front.
    """
    series: list[int] = []
    level = float(base)
    floor = base * INFLOW_FLOOR_FRACTION
    for _month in range(months):
        wobble = rng.uniform(-behaviour.inflow_volatility, behaviour.inflow_volatility)
        level = max(floor, level * (1.0 + behaviour.inflow_drift + wobble))
        series.append(_round_paise(level))
    return series


def _add_inflow_signals(seed: int, buyers: list[dict[str, Any]], personas: list[str]) -> None:
    """Attach the synthetic money-in signals to each buyer record, in place.

    Runs on its OWN RNG stream, derived from the seed but separate from the
    one that built the world. That is the same discipline _malformed_invoices()
    follows and for the same reason: drawing these from the shared stream
    would shift every subsequent draw, silently rewriting every invoice,
    every delay and every headline number in report/out/. Same seed, same
    world -- adding a field must not change the world it is added to.
    """
    rng = random.Random(f"{seed}:inflow")
    for index, buyer in enumerate(buyers):
        behaviour = PERSONA_BEHAVIOUR[personas[index]]
        low, high = INFLOW_BASE_PAISE[buyer["profile"]]
        base = rng.randint(low, high)
        months = rng.randint(INFLOW_MONTHS_MIN, INFLOW_MONTHS_MAX)
        buyer["monthly_inflow_paise"] = _inflow_series(rng, base, behaviour, months)
        buyer["failed_payment_count"] = sum(
            1 for _attempt in range(FAILED_PAYMENT_ATTEMPTS)
            if rng.random() < behaviour.failed_payment_chance
        )


def _build_history(
    rng: random.Random,
    buyers: list[dict[str, Any]],
    personas: list[str],
    low_confidence: list[int],
    no_agreement: int,
    ceiling: int,
) -> list[dict[str, Any]]:
    """Past PAID invoices, one batch per buyer, delays shaped by the persona."""
    invoices: list[dict[str, Any]] = []

    for index, buyer in enumerate(buyers):
        behaviour = PERSONA_BEHAVIOUR[personas[index]]
        if index in low_confidence:
            count = rng.randint(1, 2)
        else:
            count = rng.randint(MIN_HISTORY, MAX_HISTORY)
        started = date.fromisoformat(buyer["relationship_since"])
        span = max(30, (SIMULATION_START - started).days)
        # Drift lets a buyer get better or worse over time, so the score engine
        # has a real trend to report rather than a flat average.
        drift = rng.choice([-0.3, 0.0, 0.0, 0.4])

        for slot in range(count):
            written, agreed = _terms(rng)
            term = _statutory_term_days(written, agreed, no_agreement, ceiling)

            if rng.random() < behaviour.on_time_chance:
                delay = -rng.randint(0, min(5, term - 1))
            else:
                raw_delay = rng.randint(behaviour.delay_min, behaviour.delay_max)
                progress = slot / max(1, count - 1)
                delay = max(1, int(round(raw_delay * (1 + drift * progress))))

            # Space the batch across the relationship, oldest first.
            target_days_ago = int(span * (count - slot) / (count + 1))
            jitter = rng.randint(-10, 10)
            days_ago = max(term + delay + 1, target_days_ago + jitter)

            acceptance = SIMULATION_START - timedelta(days=days_ago)
            issue = acceptance - timedelta(days=rng.randint(0, 6))
            statutory_due = acceptance + timedelta(days=term)
            paid = statutory_due + timedelta(days=delay)
            amount = _amount(rng, buyer["profile"])

            invoice = _blank_invoice()
            invoice.update(
                buyer_id=buyer["buyer_id"],
                cohort="history",
                description=_description(rng, buyer["sector"], amount),
                po_number=_po_number(rng, issue, written),
                amount_paise=amount,
                issue_date=_iso(issue),
                acceptance_date=_iso(acceptance),
                written_agreement=written,
                agreed_days=agreed,
                agreed_due_date=_iso(issue + timedelta(days=agreed)) if agreed else None,
                status="paid",
                amount_paid_paise=amount,
                paid_date=_iso(paid),
                promise_broken=rng.random() < behaviour.broken_promise_chance,
            )

            if rng.random() < behaviour.dispute_chance:
                # A dispute raised and later settled. A historical fact for the
                # score engine, not an open dispute for the brain.
                invoice["disputed"] = True
                note = rng.choice(DISPUTE_NOTES)
                goods = _goods_label(invoice["description"], buyer["sector"])
                invoice["dispute_note"] = note.format(goods=goods)

            if rng.random() < 0.15:
                first_amount = _round_paise(amount * rng.uniform(0.3, 0.6))
                first_date = statutory_due - timedelta(days=rng.randint(1, max(2, term // 2)))
                invoice["partial_payments"] = [
                    {"date": _iso(first_date), "amount_paise": first_amount},
                    {"date": _iso(paid), "amount_paise": amount - first_amount},
                ]
            else:
                invoice["partial_payments"] = [{"date": _iso(paid), "amount_paise": amount}]

            invoices.append(invoice)

    return invoices


def _overdue_targets(rng: random.Random) -> list[int]:
    """Days overdue as of SIMULATION_START, one per current invoice.

    Negative means not yet due -- the watchdog must leave those alone.
    """
    targets = [-rng.randint(1, 20) for _ in range(N_NOT_YET_DUE)]
    while len(targets) < N_CURRENT_INVOICES:
        targets.append(int(rng.triangular(1, 150, 25)))
    rng.shuffle(targets)
    return targets


def _build_current(
    rng: random.Random,
    buyers: list[dict[str, Any]],
    no_agreement: int,
    ceiling: int,
) -> list[dict[str, Any]]:
    """Exactly 100 open invoices, unevenly spread -- concentration is realistic."""
    counts = [MIN_CURRENT_PER_BUYER] * N_BUYERS
    weights = [rng.uniform(0.5, 4.0) for _ in range(N_BUYERS)]
    for _ in range(N_CURRENT_INVOICES - MIN_CURRENT_PER_BUYER * N_BUYERS):
        picked = rng.choices(range(N_BUYERS), weights=weights, k=1)[0]
        counts[picked] += 1

    targets = _overdue_targets(rng)
    invoices: list[dict[str, Any]] = []

    for index, buyer in enumerate(buyers):
        for _ in range(counts[index]):
            days_overdue = targets.pop()
            written, agreed = _terms(rng)
            term = _statutory_term_days(written, agreed, no_agreement, ceiling)

            statutory_due = SIMULATION_START - timedelta(days=days_overdue)
            acceptance = statutory_due - timedelta(days=term)
            issue = acceptance - timedelta(days=rng.randint(0, 6))
            amount = _amount(rng, buyer["profile"])

            invoice = _blank_invoice()
            invoice.update(
                buyer_id=buyer["buyer_id"],
                cohort="current",
                description=_description(rng, buyer["sector"], amount),
                po_number=_po_number(rng, issue, written),
                amount_paise=amount,
                issue_date=_iso(issue),
                acceptance_date=_iso(acceptance),
                written_agreement=written,
                agreed_days=agreed,
                agreed_due_date=_iso(issue + timedelta(days=agreed)) if agreed else None,
                status="open",
            )
            invoices.append(invoice)

    return invoices


def _apply_mess(
    rng: random.Random,
    current: list[dict[str, Any]],
    buyers: list[dict[str, Any]],
    personas: list[str],
    no_agreement: int,
    ceiling: int,
) -> None:
    """Place the dispute, the partial payments and the opt-out on purpose."""
    persona_of = {buyers[i]["buyer_id"]: personas[i] for i in range(N_BUYERS)}
    sector_of = {buyer["buyer_id"]: buyer["sector"] for buyer in buyers}
    overdue = [
        inv for inv in current
        if statutory_due_date(inv, no_agreement, ceiling) <= SIMULATION_START
    ]

    # Exactly one pre-disputed invoice, on a buyer who disputes things.
    candidates = [inv for inv in overdue if persona_of[inv["buyer_id"]] == "disputer"]
    disputed = rng.choice(candidates if candidates else overdue)
    disputed["status"] = "disputed"
    disputed["disputed"] = True
    goods = _goods_label(disputed["description"], sector_of[disputed["buyer_id"]])
    disputed["dispute_note"] = rng.choice(DISPUTE_NOTES).format(goods=goods)

    # A handful of invoices where some money came in but not all of it.
    remaining = [inv for inv in overdue if inv is not disputed]
    for invoice in rng.sample(remaining, N_PARTIALLY_PAID):
        amount = invoice["amount_paise"]
        paid = _round_paise(amount * rng.uniform(0.25, 0.65))
        paid = min(paid, amount - AMOUNT_STEP_PAISE)
        due = statutory_due_date(invoice, no_agreement, ceiling)
        window = max(1, (SIMULATION_START - due).days)
        when = due + timedelta(days=rng.randint(0, window - 1)) if window > 1 else due
        invoice["status"] = "partially_paid"
        invoice["partial_payments"] = [{"date": _iso(when), "amount_paise": paid}]
        invoice["amount_paid_paise"] = paid

    # One buyer has told us to stop contacting them. The stop rule must hold.
    opt_out_pool = [i for i in range(N_BUYERS) if personas[i] != "disputer"]
    buyers[rng.choice(opt_out_pool)]["opted_out"] = True


#: One buyer, reused by every malformed fixture below. Any real buyer_id
#: works -- these invoices are excluded from the watchdog queue before
#: anything ever needs the buyer's own attributes (engine/validate.py).
_MALFORMED_BUYER_ID = "BUY-01"


def _malformed_invoices() -> list[dict[str, Any]]:
    """Six deliberately malformed current invoices, one per docs/edge_cases.md
    case that engine/validate.py exists to catch (TC-045, TC-049, TC-050,
    TC-051, TC-053, TC-054).

    Purely additive -- appended after _assign_ids(), with their own fixed IDs,
    so they never affect the normal invoices' numbering or the RNG stream
    that produced them. cohort="current" so they flow through the same
    pipeline (watchdog, the simulator's exceptions list) as any other current
    invoice; only their content marks them as invalid, not a special field.
    """
    normal_issue = _iso(SIMULATION_START - timedelta(days=90))

    def fixture(tc: str, **overrides: Any) -> dict[str, Any]:
        invoice = _blank_invoice()
        invoice.update(
            invoice_id=f"INV-MALFORMED-{tc}",
            buyer_id=_MALFORMED_BUYER_ID,
            cohort="current",
            description=f"malformed fixture invoice for docs/edge_cases.md {tc}",
            amount_paise=5_000_000,
            issue_date=normal_issue,
            acceptance_date=normal_issue,
            status="open",
        )
        invoice.update(overrides)
        return invoice

    future_issue = _iso(SIMULATION_START + timedelta(days=5))
    return [
        fixture("TC-045", acceptance_date=None),
        fixture("TC-049", written_agreement=True, agreed_days="whenever possible"),
        fixture("TC-050", issue_date=future_issue, acceptance_date=future_issue),
        fixture(
            "TC-051",
            issue_date=_iso(SIMULATION_START - timedelta(days=30)),
            acceptance_date=_iso(SIMULATION_START - timedelta(days=35)),
        ),
        fixture("TC-053", amount_paise=0),
        fixture("TC-054", amount_paise=-500_000),
    ]


def _assign_ids(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort chronologically and number the invoices the way a ledger would."""
    invoices.sort(key=lambda inv: (inv["acceptance_date"], inv["buyer_id"]))
    counters: dict[str, int] = {}
    for invoice in invoices:
        year = invoice["acceptance_date"][:4]
        counters[year] = counters.get(year, 0) + 1
        invoice["invoice_id"] = f"INV-{year}-{counters[year]:04d}"
    return invoices


def statutory_due_date(invoice: dict[str, Any], no_agreement: int, ceiling: int) -> date:
    """Statutory due date for an invoice, from the config-supplied terms.

    engine/law.py remains the authority for law math; this exists so the data
    factory can place dates that agree with it.
    """
    term = _statutory_term_days(
        invoice["written_agreement"], invoice["agreed_days"], no_agreement, ceiling
    )
    return date.fromisoformat(invoice["acceptance_date"]) + timedelta(days=term)


def generate(seed: int, with_malformed: bool = False) -> dict[str, Any]:
    """Build the whole test world reproducibly from one seed.

    with_malformed appends six deliberately malformed current invoices (see
    _malformed_invoices()) AFTER the RNG-driven world is fully built and
    numbered -- so with_malformed=False (the default) produces byte-identical
    output to before this flag existed, and with_malformed=True never
    perturbs the RNG stream that produces the other 100 current invoices.
    """
    no_agreement, ceiling = _load_statutory_terms()
    rng = random.Random(seed)

    personas = _assign_personas(rng)
    low_confidence = sorted(rng.sample(range(N_BUYERS), N_LOW_CONFIDENCE_BUYERS))
    buyers = _build_buyers(rng, low_confidence)
    history = _build_history(rng, buyers, personas, low_confidence, no_agreement, ceiling)
    # Deliberately last, on its own RNG stream -- see _add_inflow_signals().
    _add_inflow_signals(seed, buyers, personas)
    current = _build_current(rng, buyers, no_agreement, ceiling)
    _apply_mess(rng, current, buyers, personas, no_agreement, ceiling)
    invoices = _assign_ids(history + current)

    malformed = _malformed_invoices() if with_malformed else []
    invoices = invoices + malformed

    return {
        "buyers": {
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "seed": seed,
                "buyer_count": len(buyers),
                "simulation_start": _iso(SIMULATION_START),
            },
            "buyers": buyers,
        },
        "invoices": {
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "seed": seed,
                "simulation_start": _iso(SIMULATION_START),
                "current_invoice_count": len(current) + len(malformed),
                "history_invoice_count": len(history),
                # Self-describing on disk: nothing else distinguishes a
                # malformed seed-42 dataset from a plain one except the
                # INV-MALFORMED- ids themselves. data/store.load_meta() is
                # what every entry point already calls to read this file's
                # header, so this is visible for free wherever that is.
                "with_malformed": with_malformed,
            },
            "invoices": invoices,
        },
        "personas": {
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "seed": seed,
                "warning": (
                    "Simulator only. No module under engine/ may read this file. "
                    "The agent must infer buyer behaviour from payment history."
                ),
            },
            "personas": {buyers[i]["buyer_id"]: personas[i] for i in range(N_BUYERS)},
        },
    }


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write pretty JSON with LF endings, so the bytes match on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _display_path(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise (--out-dir may point anywhere)."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def ensure_dataset(seed: int) -> bool:
    """Make sure the world on disk was actually built with this seed.

    Checking mere existence is not enough: main.py and sim/run_sim.py both
    used to skip regeneration whenever *a* dataset was present, so asking for
    a different --seed silently kept reusing whatever was already on disk.
    That would have made a "three different seeds" experiment test the same
    invoices three times. This regenerates whenever the file is missing or
    its own recorded seed does not match.

    Returns:
        True if the dataset was (re)generated, False if it already matched.
    """
    from data import store

    if store.dataset_exists():
        try:
            if store.load_meta().get("seed") == seed:
                return False
        except (store.DatasetMissing, KeyError):
            pass

    world = generate(seed)
    _write_json(store.BUYERS_PATH, world["buyers"])
    _write_json(store.INVOICES_PATH, world["invoices"])
    _write_json(DEFAULT_PERSONA_PATH, world["personas"])
    return True


def print_summary(world: dict[str, Any], paths: dict[str, Path]) -> None:
    """A sanity table. One fact per line, no emoji."""
    no_agreement, ceiling = _load_statutory_terms()
    symbol = enable_unicode_output()
    buyers = world["buyers"]["buyers"]
    invoices = world["invoices"]["invoices"]
    # Malformed fixtures (--with-malformed) are deliberately unfit for the law
    # math this summary itself uses (statutory_due_date, agreed_days > ceiling)
    # -- they are reported separately below, never folded into these stats.
    malformed = [inv for inv in invoices if inv["invoice_id"].startswith("INV-MALFORMED-")]
    current = [inv for inv in invoices if inv["cohort"] == "current" and inv not in malformed]
    history = [inv for inv in invoices if inv["cohort"] == "history"]

    history_per_buyer: dict[str, int] = {}
    for invoice in history:
        key = invoice["buyer_id"]
        history_per_buyer[key] = history_per_buyer.get(key, 0) + 1

    not_yet_due = [
        inv for inv in current
        if statutory_due_date(inv, no_agreement, ceiling) > SIMULATION_START
    ]
    outstanding = sum(inv["amount_paise"] - inv["amount_paid_paise"] for inv in current)
    overdue_outstanding = sum(
        inv["amount_paise"] - inv["amount_paid_paise"]
        for inv in current
        if statutory_due_date(inv, no_agreement, ceiling) <= SIMULATION_START
    )
    history_counts = [history_per_buyer.get(b["buyer_id"], 0) for b in buyers]

    def row(label: str, value: Any) -> None:
        print(f"  {label:<26} {value:>16}")

    print(f"dataset summary (seed {world['buyers']['meta']['seed']})")
    print("buyers")
    row("total", len(buyers))
    row("corporate", sum(1 for b in buyers if b["profile"] == "corporate"))
    row("small traders", sum(1 for b in buyers if b["profile"] == "small_trader"))
    row("prefer hinglish", sum(1 for b in buyers if b["language_pref"] == "hinglish"))
    row("opted out", sum(1 for b in buyers if b["opted_out"]))
    row("low confidence (<3 paid)", sum(1 for c in history_counts if c < 3))
    row("history per buyer", f"{min(history_counts)} to {max(history_counts)}")
    print("invoices")
    row("total records", len(invoices))
    row("history (paid)", len(history))
    row("current", len(current))
    row("  open", sum(1 for inv in current if inv["status"] == "open"))
    row("  partially paid", sum(1 for inv in current if inv["status"] == "partially_paid"))
    row("  disputed", sum(1 for inv in current if inv["status"] == "disputed"))
    row("  not yet due", len(not_yet_due))
    row("  overdue", len(current) - len(not_yet_due))
    if malformed:
        row("  malformed (--with-malformed)", len(malformed))
    print("money (current invoices)")
    row("outstanding", format_inr(outstanding, symbol))
    row("of which overdue", format_inr(overdue_outstanding, symbol))
    row("largest invoice", format_inr(max(inv["amount_paise"] for inv in current), symbol))
    row("smallest invoice", format_inr(min(inv["amount_paise"] for inv in current), symbol))
    print("messy by design (current invoices)")
    row("no written agreement", sum(1 for inv in current if not inv["written_agreement"]))
    row("agreed terms over 45d", sum(1 for inv in current if (inv["agreed_days"] or 0) > ceiling))
    row("with partial payments", sum(1 for inv in current if inv["partial_payments"]))
    if malformed:
        print("malformed fixtures (excluded from the queue by engine/validate.py)")
        for inv in malformed:
            row(inv["invoice_id"], "")
    print("files written")
    for label, path in paths.items():
        print(f"  {label:<26} {_display_path(path):>16}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic buyers and invoices.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default: 42)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_SEED_DIR, help="where buyers/invoices go")
    parser.add_argument("--persona-out", type=Path, default=DEFAULT_PERSONA_PATH, help="hidden persona file")
    parser.add_argument(
        "--with-malformed", action="store_true",
        help="add six deliberately malformed current invoices, one per docs/edge_cases.md "
             "TC-045/049/050/051/053/054, so engine/validate.py's rejection is reachable "
             "from real data on disk and not only from unit-test fixtures (default: off; "
             "the default seed-42 dataset is unaffected by this flag's existence)",
    )
    args = parser.parse_args()

    world = generate(args.seed, with_malformed=args.with_malformed)
    paths = {
        "buyers": args.out_dir / "buyers.json",
        "invoices": args.out_dir / "invoices.json",
        "hidden personas": args.persona_out,
    }
    _write_json(paths["buyers"], world["buyers"])
    _write_json(paths["invoices"], world["invoices"])
    _write_json(paths["hidden personas"], world["personas"])
    print_summary(world, paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
