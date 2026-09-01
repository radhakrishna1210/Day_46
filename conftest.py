"""Put the repo root on sys.path so tests import engine/, sim/, report/ from anywhere."""

import sys
from pathlib import Path

import pytest

ROOT = str(Path(__file__).parent.resolve())
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="session", autouse=True)
def _outcomes_file_out_of_the_way(tmp_path_factory):
    """Keep the test suite from writing audit/outcomes.jsonl.

    Half of the fix for a real bug, recorded in engine/outcomes.py's FILE
    LIFECYCLE note: the suite calls run_agent()/run_baseline() dozens of
    times, and every one of those writes outcome rows. Left pointed at the
    production file they stack into it -- and the result looks exactly like a
    genuine --compare until someone sums it and gets a number 5x too big,
    which is precisely what happened.

    The other half is that truncation is now explicit and lives in
    sim/run_sim.py's main(), which the suite never calls. Either fix alone
    leaves a hole: without this the suite still appends to the real artifact,
    and without the explicit start_file() the arms of one --compare would
    clobber each other.

    Session-scoped and autouse, so no individual test has to remember. Tests
    that care about file behaviour pass their own tmp_path explicitly and are
    unaffected by this.

    engine/audit.py is deliberately NOT redirected the same way: existing
    tests read audit.entries() off the real path on purpose, and changing
    that is a separate decision from this one.
    """
    from engine import outcomes

    original = outcomes.OUTCOMES_PATH
    outcomes.OUTCOMES_PATH = tmp_path_factory.mktemp("outcomes") / "outcomes.jsonl"
    try:
        yield outcomes.OUTCOMES_PATH
    finally:
        outcomes.OUTCOMES_PATH = original
