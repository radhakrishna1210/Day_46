"""Guard: the agent must never read sim/hidden_personas.json.

The whole point of the simulator's persona table is that the agent infers
buyer behaviour from payment history, exactly as it would in production --
the personas exist to drive the fake world, not to be consulted by it.

Mirrors the AST-scan technique in tests/test_no_legal_constants.py: read the
executable source of every module that must NOT reach the hidden personas,
and fail if the literal string ever appears in it. data/generate.py is the
one file allowed to name it, and only because it WRITES the file -- it is
scoped down to a single check that the reference stays confined to the write
call, so a decision-making addition there would still be caught.

report/build_report.py is deliberately NOT in this list: it runs strictly
after both simulated runs are already finished, reading only results.json,
and cannot feed anything back into a decision. Its Methodology section
names sim/hidden_personas.json and tests/test_sim_isolation.py by filename
on purpose -- to tell a reader exactly which guardrail backs which claim --
and test_report_only_documents_the_guardrail_it_does_not_read_the_file
below checks that mention stays confined to that documentation string.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Everything the agent's decision pipeline runs through. None of it may know
#: a persona exists. report/ is excluded on purpose -- see the module
#: docstring -- and checked separately, more narrowly, below.
FORBIDDEN_FILES = sorted(
    [p for p in (ROOT / "engine").glob("*.py")]
    + [p for p in (ROOT / "data").glob("*.py") if p.name != "generate.py"]
    + [ROOT / "main.py"]
)

MARKER = "hidden_personas"


def _string_and_name_literals(path: Path) -> list[tuple[int, str]]:
    """Every string constant and every identifier, so an import or an f-string

    reference is caught the same way a bare string would be.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((node.lineno, node.value))
        elif isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            found.append((node.lineno, node.attr))
    return found


@pytest.mark.parametrize("path", FORBIDDEN_FILES, ids=lambda p: p.name)
def test_the_agent_never_names_hidden_personas(path: Path) -> None:
    offenders = [
        f"line {line}: {text!r}"
        for line, text in _string_and_name_literals(path)
        if MARKER in text
    ]
    assert not offenders, (
        f"{path.name} references {MARKER!r}: {offenders}. The agent must infer "
        f"buyer behaviour from payment history, never read the simulator's "
        f"hidden persona file."
    )


def test_data_generate_only_writes_the_file_it_does_not_read_it_back() -> None:
    """The one exempt file: scoped so it may only ever WRITE the persona file."""
    tree = ast.parse((ROOT / "data" / "generate.py").read_text(encoding="utf-8"))
    read_functions = {"load_hidden_personas", "load", "loads", "read_text", "open"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name in read_functions:
                args_text = ast.dump(node)
                if MARKER in args_text:
                    offenders.append((node.lineno, name))
    assert not offenders, (
        f"data/generate.py reads {MARKER!r} back with: {offenders}. It should "
        f"only ever write this file."
    )


def test_report_only_documents_the_guardrail_it_does_not_read_the_file() -> None:
    """report/build_report.py may name the persona file in prose, describing

    the guardrail for a reader -- it must never actually open, import from,
    or otherwise reach it. Only a documentation string may carry the marker.
    """
    tree = ast.parse((ROOT / "report" / "build_report.py").read_text(encoding="utf-8"))
    functional_offenders = [
        f"line {node.lineno}: {node.id if isinstance(node, ast.Name) else node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
        and MARKER in (node.id if isinstance(node, ast.Name) else node.attr)
    ]
    assert not functional_offenders, (
        f"build_report.py reaches for {MARKER!r} in code, not just prose: "
        f"{functional_offenders}"
    )
    body = (ROOT / "report" / "build_report.py").read_text(encoding="utf-8")
    assert MARKER in body, (
        "the guardrail claim naming this file went missing from build_report.py's "
        "Methodology data -- update this test if that section was intentionally reworded"
    )


def test_the_guard_would_actually_catch_something() -> None:
    """A guard that never fires proves nothing. Prove it fires."""
    tree = ast.parse("from sim import personas\npersona = personas.load_hidden_personas()\n")
    found = [
        node.value if isinstance(node, ast.Constant) else getattr(node, "id", getattr(node, "attr", ""))
        for node in ast.walk(tree)
        if isinstance(node, (ast.Constant, ast.Name, ast.Attribute))
    ]
    assert any(MARKER in str(v) for v in found)


def test_sim_is_where_the_marker_is_allowed_to_live() -> None:
    """Sanity check on the exemption itself: the marker really does exist in

    sim/, so this guard is testing something real, not an already-dead path.
    """
    sim_sources = "\n".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "sim").glob("*.py")
    )
    assert MARKER in sim_sources
