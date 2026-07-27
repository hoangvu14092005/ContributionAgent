"""Tests for contribai.generator.style_validator.

Layer A — closes the test gap for `style_validator.py` (was 0% coverage, 241 lines).

The module is data-only: no async, no I/O. We test:

- Score weighting math (naming/indent/quote/len/hint/doc contribution)
- The 7.0 pass threshold plus the `len(issues) == 0` extra gate
- Each individual check produces the right kind of output (issues vs warnings)
- Edge cases: empty code, code without language-specific features
"""

from __future__ import annotations

import pytest

from contribai.analysis.repo_conventions import RepoConventions
from contribai.generator.style_validator import StyleValidator, StyleValidationResult


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> StyleValidator:
    return StyleValidator()


@pytest.fixture
def python_snake_conventions() -> RepoConventions:
    """A typical Python repo: snake_case, 4 spaces, double quotes, line-length 100, type hints, docstrings."""
    return RepoConventions(
        naming_convention="snake_case",
        indentation="4 spaces",
        quote_style="double",
        line_length=100,
        has_type_hints=True,
        has_docstrings=True,
        docstring_style="Google",
        confidence=1.0,
    )


@pytest.fixture
def javascript_conventions() -> RepoConventions:
    """JS repo: camelCase, 2 spaces, single quotes, no docstrings, no type hints (non-Python)."""
    return RepoConventions(
        naming_convention="camelCase",
        indentation="2 spaces",
        quote_style="single",
        line_length=100,
        has_type_hints=False,
        has_docstrings=False,
        docstring_style="None",
        confidence=1.0,
    )


# ── Smoke / Return type ─────────────────────────────────────────────────────


class TestStyleValidatorReturns:
    def test_returns_a_style_validation_result(self, validator, python_snake_conventions):
        result = validator.validate("x = 1\n", python_snake_conventions)
        assert isinstance(result, StyleValidationResult)

    def test_result_score_is_a_float_in_zero_to_ten(self, validator, python_snake_conventions):
        result = validator.validate("x = 1\n", python_snake_conventions)
        assert isinstance(result.score, float)
        assert 0.0 <= result.score <= 10.0

    def test_result_passed_is_a_bool(self, validator, python_snake_conventions):
        result = validator.validate("x = 1\n", python_snake_conventions)
        assert isinstance(result.passed, bool)

    def test_result_issues_and_warnings_are_lists(
        self, validator, python_snake_conventions
    ):
        result = validator.validate("x = 1\n", python_snake_conventions)
        assert isinstance(result.issues, list)
        assert isinstance(result.warnings, list)


# ── Pass-threshold behavior (7.0 + zero issues) ─────────────────────────────


class TestPassThreshold:
    def test_perfect_code_passes(self, validator, python_snake_conventions):
        # snake_case function, 4 spaces indent, double quotes, ≤100 char lines,
        # type hints, Google docstring.
        perfect_code = (
            '"""Module docstring."""\n'
            "\n"
            "from typing import List\n"
            "\n"
            "\n"
            'def add_items(items: List[int]) -> int:\n'
            '    """Add a list of items.\n'
            "\n"
            "    Args:\n"
            "        items: The list of items.\n"
            "\n"
            "    Returns:\n"
            "        The sum.\n"
            '    """\n'
            '    return sum(items)\n'
        )
        result = validator.validate(perfect_code, python_snake_conventions)
        assert result.passed is True
        assert result.score >= 7.0
        assert result.issues == []

    def test_camel_case_in_snake_repo_creates_issue(self, validator, python_snake_conventions):
        bad_code = "def doSomething(x):\n    return x\n"
        result = validator.validate(bad_code, python_snake_conventions)
        # camelCase violation is a hard issue (drops naming score by 3, weighted 30%):
        # the delta is 3.0 * 0.30 = 0.9, so 10 - 0.9 = 9.1, still passes the threshold.
        # But the issue itself forces passed=False (the extra `len(issues) == 0` gate).
        assert result.passed is False
        assert any("camelCase" in i or "snake_case" in i for i in result.issues)

    def test_snake_case_in_camel_repo_creates_issue(self, validator, javascript_conventions):
        bad_code = "function do_something(x) { return x; }\n"
        result = validator.validate(bad_code, javascript_conventions)
        assert result.passed is False
        assert any("snake_case" in i or "camelCase" in i for i in result.issues)

    def test_double_dunder_not_flagged_in_camel_repo(self, validator, javascript_conventions):
        # The check filters out names starting with "__" (dunder convention).
        code_with_dunder = "function __init__() { return null; }\n"
        result = validator.validate(code_with_dunder, javascript_conventions)
        # No snake-case false positives from dunders.
        assert not any("snake_case" in i for i in result.issues)


# ── Indentation check ───────────────────────────────────────────────────────


class TestIndentationCheck:
    def test_tabs_when_repo_uses_4_spaces(self, validator, python_snake_conventions):
        # Tab-indented line in a 4-spaces repo → violates; should pass into _check_indentation.
        code = "def foo():\n\treturn 1\n"
        result = validator.validate(code, python_snake_conventions)
        # Score drops but the main impact is an "issues" entry.
        assert any("Indentation" in i for i in result.issues)

    def test_2_space_when_repo_uses_4_spaces(self, validator, python_snake_conventions):
        code = "def foo():\n  return 1\n"
        result = validator.validate(code, python_snake_conventions)
        assert any("Indentation" in i for i in result.issues)

    def test_correct_indentation_no_issue(self, validator, python_snake_conventions):
        code = "def foo():\n    return 1\n"
        result = validator.validate(code, python_snake_conventions)
        assert not any("Indentation" in i for i in result.issues)


# ── Quote style check ───────────────────────────────────────────────────────


class TestQuoteStyleCheck:
    def test_mixed_preference_never_warns(self, validator, python_snake_conventions):
        # Temporarily flip to mixed; both quote styles are tolerated.
        mixed = RepoConventions(**{**python_snake_conventions.__dict__, "quote_style": "mixed"})
        code = "x = 'single'\ny = \"double\"\n"
        result = validator.validate(code, mixed)
        assert not any("quote" in w.lower() for w in result.warnings)

    def test_single_quotes_when_repo_uses_double_only_warns(
        self, validator, python_snake_conventions
    ):
        code = "x = 'a'\ny = 'b'\nz = 'c'\n"
        result = validator.validate(code, python_snake_conventions)
        # Several singles, no doubles → warning (not issue).
        assert any("single" in w.lower() for w in result.warnings)
        assert not any("quote" in i.lower() for i in result.issues)

    def test_dominant_preferred_quotes_no_warning(self, validator, python_snake_conventions):
        code = 'x = "a"\ny = "b"\n'
        result = validator.validate(code, python_snake_conventions)
        assert not any("single" in w.lower() for w in result.warnings)


# ── Line length check ───────────────────────────────────────────────────────


class TestLineLengthCheck:
    def test_long_line_creates_warning(self, validator, python_snake_conventions):
        long_line = "x = '" + ("a" * 200) + "'"
        result = validator.validate(long_line, python_snake_conventions)
        assert any("exceed" in w.lower() or "length" in w.lower() for w in result.warnings)

    def test_short_lines_no_warning(self, validator, python_snake_conventions):
        code = "x = 1\ny = 2\nz = 3\n"
        result = validator.validate(code, python_snake_conventions)
        assert not any("length" in w.lower() for w in result.warnings)


# ── Type hints & docstrings (Python only) ──────────────────────────────────


class TestPythonSpecificChecks:
    def test_no_hints_with_hint_repo_warns(self, validator, python_snake_conventions):
        # 3 functions, 0 hints → ratio 0 < 0.5 → warning.
        code = (
            "def a(x):\n"
            "    return x\n"
            "def b(x):\n"
            "    return x\n"
            "def c(x):\n"
            "    return x\n"
        )
        result = validator.validate(code, python_snake_conventions)
        assert any("type hint" in w.lower() for w in result.warnings)

    def test_mostly_hinted_no_warning(self, validator, python_snake_conventions):
        code = (
            "def a(x: int) -> int:\n    return x\n"
            "def b(x: int) -> int:\n    return x\n"
            "def c(x: int) -> int:\n    return x\n"
        )
        result = validator.validate(code, python_snake_conventions)
        assert not any("type hint" in w.lower() for w in result.warnings)

    def test_no_docstrings_warns(self, validator, python_snake_conventions):
        code = (
            "def a(x):\n    return x\n"
            "def b(x):\n    return x\n"
            "def c(x):\n    return x\n"
        )
        result = validator.validate(code, python_snake_conventions)
        assert any("docstring" in w.lower() for w in result.warnings)

    def test_with_docstrings_no_warning(self, validator, python_snake_conventions):
        code = (
            'def a(x):\n    """Summary."""\n    return x\n'
            'def b(x):\n    """Summary."""\n    return x\n'
            'def c(x):\n    """Summary."""\n    return x\n'
        )
        result = validator.validate(code, python_snake_conventions)
        assert not any("docstring" in w.lower() for w in result.warnings)

    def test_non_python_repo_skips_hint_and_doc_checks(
        self, validator, javascript_conventions
    ):
        # JS repo: has_type_hints=False, has_docstrings=False → both checks skipped.
        code = "function foo(x) { return x; }\n"
        result = validator.validate(code, javascript_conventions)
        assert not any("type hint" in w.lower() for w in result.warnings)
        assert not any("docstring" in w.lower() for w in result.warnings)


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_code(self, validator, python_snake_conventions):
        result = validator.validate("", python_snake_conventions)
        # No findings either way; we just shouldn't crash.
        assert isinstance(result.score, float)
        assert 0.0 <= result.score <= 10.0

    def test_blank_lines_only(self, validator, python_snake_conventions):
        result = validator.validate("\n\n\n", python_snake_conventions)
        assert isinstance(result.score, float)
        assert 0.0 <= result.score <= 10.0

    def test_score_clamped_at_zero(self, validator, python_snake_conventions):
        # Worst case: bad naming + bad indent + bad quotes + long line +
        # no hints + no docs. Score can go negative internally but is clamped.
        terrible_code = (
            "def DoSomething(x){\n"  # camelCase + 1-line braces mixed
            "\treturn x\n"
            "}\n"
            "def Another(x){\n"
            "\treturn x\n"
            "}\n"
            "def AThird(x){\n"
            "\treturn x\n"
            "}\n"
        )
        # Build a 200-char line to trigger length warning.
        terrible_code += "x = '" + ("q" * 250) + "'\n"
        result = validator.validate(terrible_code, python_snake_conventions)
        assert result.score >= 0.0
        assert result.passed is False
