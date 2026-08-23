"""Guard: no legal constant and no legal prose may live outside config/legal.yaml.

Non-negotiable #3. A number like 45 or a sentence citing Section 16 baked into
Python is a number nobody will re-verify when the law or the RBI rate moves.
This test reads the executable code -- docstrings and comments excluded, since
explaining the law in a docstring is exactly where that explanation belongs --
and fails if any statutory value or citation appears in it.

If this test fails, the fix is to move the value into config/legal.yaml and read
it through engine.config, never to add an exception here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
LEGAL_CONFIG = ROOT / "config" / "legal.yaml"

_CONFIG = yaml.safe_load(LEGAL_CONFIG.read_text(encoding="utf-8"))

#: Rates, as fractions. These are unmistakable -- 0.055 or 0.75 appearing
#: anywhere in this codebase is a statutory figure and nothing else -- so they
#: are banned repo-wide.
FORBIDDEN_RATES: set[float] = {
    float(_CONFIG["rbi_bank_rate"]),                             # 0.055
    float(_CONFIG["buyer_tax_rate"]),                            # 0.30
    float(_CONFIG["samadhaan"]["challenge_predeposit_share"]),   # 0.75
}

#: Day counts. 15, 45 and 30 are ordinary small integers that legitimately show
#: up as row limits and window sizes elsewhere, so they are only banned in the
#: one module allowed to do law math. Any other module needing them has to go
#: through engine.law, which is the point.
FORBIDDEN_DAY_COUNTS: set[float] = {
    float(_CONFIG["no_agreement_days"]),          # 15
    float(_CONFIG["max_agreement_days"]),         # 45
    float(_CONFIG["partial_month_day_basis"]),    # 30
}
LAW_MODULE = ROOT / "engine" / "law.py"

#: Citations and statute names that belong in config templates, not in code.
FORBIDDEN_PROSE = re.compile(
    r"MSMED|Income-tax Act|Samadhaan|43B|37\(2\)|Section\s+(15|16|19|22|23|37|43)",
)

SOURCE_FILES = sorted(
    [p for p in (ROOT / "engine").glob("*.py")]
    + [p for p in (ROOT / "sim").glob("*.py")]
    + [p for p in (ROOT / "report").glob("*.py")]
    + [ROOT / "main.py"]
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of the Constant nodes that are docstrings, so they can be skipped."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                found.add(id(first.value))
    return found


def _numeric_literals(path: Path) -> list[tuple[int, float]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    return [
        (node.lineno, float(node.value))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and id(node) not in skip
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ]


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_no_statutory_rate_anywhere_in_executable_code(path: Path) -> None:
    offenders = [
        f"line {line}: {value!r}"
        for line, value in _numeric_literals(path)
        if value in FORBIDDEN_RATES
    ]
    assert not offenders, (
        f"{path.name} hardcodes a statutory rate: {offenders}. "
        f"Read it from config/legal.yaml via engine.config instead."
    )


def test_no_statutory_day_count_in_the_law_engine() -> None:
    """The law engine must derive every window from config, never restate one."""
    offenders = [
        f"line {line}: {value!r}"
        for line, value in _numeric_literals(LAW_MODULE)
        if value in FORBIDDEN_DAY_COUNTS
    ]
    assert not offenders, (
        f"engine/law.py hardcodes a statutory window: {offenders}. "
        f"Read it from config/legal.yaml instead."
    )


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_no_legal_prose_in_executable_code(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    offenders = [
        f"line {node.lineno}: {node.value[:60]!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and id(node) not in skip
        and isinstance(node.value, str)
        and FORBIDDEN_PROSE.search(node.value)
    ]
    assert not offenders, (
        f"{path.name} contains legal prose: {offenders}. "
        f"Put the sentence in the facts block of config/legal.yaml instead."
    )


def test_the_guard_would_actually_catch_something() -> None:
    """A test that never fails is not a guard. Prove it fires."""
    tree = ast.parse("CEILING = 45\nNOTE = 'Section 16, MSMED Act 2006'\n")
    numbers = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, int)
    ]
    prose = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    assert any(float(v) in FORBIDDEN_DAY_COUNTS for v in numbers)
    assert any(FORBIDDEN_PROSE.search(v) for v in prose)


def test_every_statutory_value_is_actually_present_in_config() -> None:
    """The forbidden lists are derived from config, so config must define them all."""
    assert FORBIDDEN_RATES == {0.055, 0.30, 0.75}
    assert FORBIDDEN_DAY_COUNTS == {15.0, 45.0, 30.0}
    assert _CONFIG["retrieved_on"] == "2026-08-23"
    assert "facts" in _CONFIG and len(_CONFIG["facts"]) >= 7


# --- one statement per legal position ------------------------------------
# The refactor that introduced config/legal.yaml `clauses` collapsed three
# statements of the Section 16 rate, two of the Section 19 pre-deposit and two
# of the Section 15 ceiling onto one each. These tests keep it that way: two
# wordings of the same section is how a message and a draft end up disagreeing
# in front of a buyer.

def _config_body() -> str:
    """legal.yaml with comment lines removed -- comments quote the Act on purpose."""
    raw = LEGAL_CONFIG.read_text(encoding="utf-8")
    return "\n".join(line for line in raw.splitlines() if not line.strip().startswith("#"))


@pytest.mark.parametrize("phrase", [
    "three times the RBI Bank Rate",
    "ceiling set by Section 15",
])
def test_each_legal_position_is_worded_exactly_once(phrase: str) -> None:
    occurrences = _config_body().count(phrase)
    assert occurrences == 1, (
        f"{phrase!r} appears {occurrences} times in legal.yaml. A legal position "
        f"belongs in the clauses block once; everything else composes from it."
    )


def test_the_clauses_block_is_where_those_statements_live() -> None:
    clauses = _CONFIG["clauses"]
    assert "three times the RBI Bank Rate" in clauses["section_16_rate"]
    assert "ceiling set by Section 15" in clauses["section_15_ceiling"]
    assert "Section 19 of the MSMED Act" in clauses["section_19_predeposit_formal"]
    assert "Section 19 of the MSMED Act" in clauses["section_19_predeposit_plain"]


def test_every_clause_reference_names_a_real_clause() -> None:
    """A typo in a template would silently drop the legal position from a message."""
    import re
    known = set(_CONFIG["clauses"])
    for block in ("facts", "reference_text"):
        for key, template in _CONFIG[block].items():
            for reference in re.findall(r"\{([a-z_0-9]+)\}", str(template)):
                if reference.startswith("section_") and reference in known:
                    continue
                assert reference not in {f"{k}_MISSING" for k in known}, key
    # and the composing templates really do reference them
    composed = " ".join(str(v) for v in _CONFIG["facts"].values())
    composed += " ".join(str(v) for v in _CONFIG["reference_text"].values())
    for clause in known:
        assert "{" + clause + "}" in composed, f"clause {clause} is never used"


def test_section_19_is_split_by_audience_but_shares_one_figure() -> None:
    """Two wordings are allowed where two audiences read them. One number is not.

    The draft addresses the buyer as the Respondent; a message addresses them
    directly. Both must take the percentage from the same config value, so the
    phrasing can differ and the figure cannot.
    """
    clauses = _CONFIG["clauses"]
    formal = clauses["section_19_predeposit_formal"]
    plain = clauses["section_19_predeposit_plain"]

    assert formal != plain, "the split exists to word this per audience"
    assert "Respondent" in formal and "Respondent" not in plain
    assert "{predeposit_pct}" in formal
    assert "{predeposit_pct}" in plain

    # neither may hardcode the number
    share = _CONFIG["samadhaan"]["challenge_predeposit_share"]
    for text in (formal, plain):
        assert str(int(share * 100)) not in text
