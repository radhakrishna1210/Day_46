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
    "data.store",
    "engine.audit",
    "engine.config",
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


# --- live mode, exercised without spending anything ----------------------

def test_live_mode_builds_the_right_request(monkeypatch) -> None:
    """The request shape is checked with a fake SDK, so no credits are spent.

    Three things must hold: the model is the one config names for that purpose,
    the key is passed explicitly rather than left to an ambient credential, and
    effort is omitted where the model would reject it.
    """
    import sys
    import types

    from engine import llm
    from engine.config import rules

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            block = types.SimpleNamespace(type="text", text="drafted")
            usage = types.SimpleNamespace(input_tokens=11, output_tokens=22)
            return types.SimpleNamespace(content=[block], usage=usage)

    class FakeAnthropic:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic",
                        types.SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("LLM_MODE", "live")

    assert llm.llm("draft this", purpose="draft_message") == "drafted"
    config = rules()["llm"]
    assert captured["model"] == config["models"]["draft_message"]
    assert captured["max_tokens"] == config["max_tokens"]["draft_message"]
    assert captured["api_key"] == "sk-test-not-a-real-key"
    assert captured["output_config"] == {"effort": "medium"}
    assert llm.last_usage["input_tokens"] == 11


def test_live_mode_omits_effort_where_the_model_rejects_it(monkeypatch) -> None:
    """Haiku 4.5 returns an error for output_config.effort, so it must not be sent."""
    import sys
    import types

    from engine import llm

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="{}")],
                usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(
        Anthropic=lambda api_key=None: types.SimpleNamespace(messages=FakeMessages())))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODE", "live")

    llm.llm("classify this", purpose="parse_reply")
    assert "output_config" not in captured
    assert captured["model"] == "claude-haiku-4-5"


def test_live_mode_refuses_without_a_key(monkeypatch) -> None:
    from engine import llm

    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.llm("anything", purpose="draft_message")
