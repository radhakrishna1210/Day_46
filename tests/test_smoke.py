"""Day 1 smoke test: the skeleton holds together.

It checks three things and nothing more: every module imports, the mock LLM is
deterministic, and `python main.py --seed 42` runs the whole pipeline and exits
cleanly. Real behaviour gets real tests as each block lands (engine/law.py and
engine/score.py first -- money math is not allowed to be untested).
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MODULES = [
    "data.generate",
    "engine.audit",
    "engine.brain",
    "engine.channels",
    "engine.law",
    "engine.llm",
    "engine.promises",
    "engine.score",
    "engine.watchdog",
    "engine.writer",
    "report.build_report",
    "sim.personas",
    "sim.run_sim",
    "main",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    assert importlib.import_module(name) is not None


def test_config_files_parse() -> None:
    yaml = pytest.importorskip("yaml")
    for filename in ("rules.yaml", "legal.yaml"):
        loaded = yaml.safe_load((ROOT / "config" / filename).read_text(encoding="utf-8"))
        assert isinstance(loaded, dict) and loaded, f"{filename} is empty or not a mapping"


def test_stop_limits_are_in_config_not_code() -> None:
    yaml = pytest.importorskip("yaml")
    rules = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
    assert rules["stop_rules"]["max_per_rung"] == 3
    assert rules["stop_rules"]["max_total"] == 5


def test_mock_llm_is_deterministic() -> None:
    from engine.llm import llm

    first = llm("chase invoice 204", purpose="draft_message")
    second = llm("chase invoice 204", purpose="draft_message")
    assert first == second
    assert first.startswith("[mock:draft_message:")


def test_llm_rejects_unknown_purpose() -> None:
    from engine.llm import llm

    with pytest.raises(ValueError):
        llm("anything", purpose="not_a_real_purpose")


def test_main_runs_clean_and_prints_every_step() -> None:
    result = subprocess.run(
        [sys.executable, "main.py", "--seed", "42"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    from main import PIPELINE

    for number in range(1, len(PIPELINE) + 1):
        assert f"step {number}:" in result.stdout
