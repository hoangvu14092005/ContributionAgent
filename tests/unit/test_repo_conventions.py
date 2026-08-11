"""Tests for contribai.analysis.repo_conventions.

Layer A — closes the test gap for `repo_conventions.py` (was 0% coverage, 332 lines).

Covers:

- `to_prompt_context()` (with low confidence and high confidence branches)
- `extract_from_files()` for at least one Python sample + a non-Python sample
- The `_default_conventions()` factory for each supported language
- Detection accuracy: snake_case / camelCase / PascalCase, 4 spaces / 2 spaces / tabs,
  single / double / mixed quotes, line length, type hints, docstring style
- The "no code files" warning path
"""

from __future__ import annotations

import pytest

from contribai.analysis.repo_conventions import RepoConventions
from contribai.core.models import Repository

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def repo_python() -> Repository:
    return Repository(
        owner="acme",
        name="snakey",
        full_name="acme/snakey",
        description="",
        default_branch="main",
        stars=0,
        language="python",
        url="",
    )


@pytest.fixture
def repo_javascript() -> Repository:
    return Repository(
        owner="acme",
        name="cammy",
        full_name="acme/cammy",
        description="",
        default_branch="main",
        stars=0,
        language="JavaScript",  # uppercase to verify .lower() handling
        url="",
    )


@pytest.fixture
def snake_python_files() -> dict[str, str]:
    """Realistic snake_case Python with 4 spaces + double quotes + type hints."""
    return {
        "src/foo.py": (
            "from typing import List\n"
            "\n"
            "\n"
            "def compute_sum(values: List[int]) -> int:\n"
            '    """Compute the sum of values."""\n'
            "    total: int = 0\n"
            "    for v in values:\n"
            "        if v > 0:\n"
            "            total += v\n"
            "    return total\n"
        ),
        "src/bar.py": (
            "\n"
            "def helper(x: int) -> int:\n"
            '    """Helper.\n'
            "\n"
            "    Args:\n"
            "        x: An int.\n"
            "\n"
            "    Returns:\n"
            "        The doubled value.\n"
            '    """\n'
            "    return x * 2\n"
        ),
    }


@pytest.fixture
def camel_js_files() -> dict[str, str]:
    """Realistic camelCase JS with 2 spaces + single quotes."""
    return {
        "index.js": (
            "function computeSum(values) {\n"
            "  return values.reduce((acc, v) => acc + v, 0);\n"
            "}\n"
            "\n"
            "const helper = (x) => x * 2;\n"
            "\n"
            "module.exports = { computeSum, helper };\n"
        ),
    }


# ── Construction & accessors ────────────────────────────────────────────────


class TestRepoConventionsConstruction:
    def test_dataclass_attributes(self):
        c = RepoConventions(
            naming_convention="snake_case",
            indentation="4 spaces",
            quote_style="double",
            line_length=100,
            has_type_hints=True,
            has_docstrings=True,
            docstring_style="Google",
            confidence=1.0,
        )
        assert c.naming_convention == "snake_case"
        assert c.indentation == "4 spaces"
        assert c.quote_style == "double"
        assert c.line_length == 100
        assert c.has_type_hints is True
        assert c.has_docstrings is True
        assert c.docstring_style == "Google"
        assert c.confidence == 1.0

    def test_to_prompt_context_high_confidence(self):
        c = RepoConventions(
            naming_convention="snake_case",
            indentation="4 spaces",
            quote_style="double",
            line_length=100,
            has_type_hints=True,
            has_docstrings=True,
            docstring_style="Google",
            confidence=0.95,
        )
        ctx = c.to_prompt_context()
        assert "Repository Coding Conventions" in ctx
        assert "snake_case" in ctx
        assert "4 spaces" in ctx
        assert "double" in ctx
        assert "Max line length: 100" in ctx
        # No warning at high confidence.
        assert "confidence" not in ctx.lower() or "NOTE" not in ctx

    def test_to_prompt_context_low_confidence_has_warning(self):
        c = RepoConventions(
            naming_convention="snake_case",
            indentation="4 spaces",
            quote_style="double",
            line_length=100,
            has_type_hints=False,
            has_docstrings=False,
            docstring_style="None",
            confidence=0.4,
        )
        ctx = c.to_prompt_context()
        assert "NOTE" in ctx
        assert "0.4" in ctx or "40%" in ctx
        assert "Use conservative defaults" in ctx

    def test_to_prompt_context_renders_required_optional(self):
        c_with = RepoConventions(
            naming_convention="snake_case",
            indentation="4 spaces",
            quote_style="double",
            line_length=100,
            has_type_hints=True,
            has_docstrings=True,
            docstring_style="Google",
            confidence=1.0,
        )
        c_without = RepoConventions(
            naming_convention="snake_case",
            indentation="4 spaces",
            quote_style="double",
            line_length=100,
            has_type_hints=False,
            has_docstrings=False,
            docstring_style="None",
            confidence=1.0,
        )
        assert "Required" in c_with.to_prompt_context()
        assert "Optional" in c_without.to_prompt_context()
        assert "Not required" in c_without.to_prompt_context()


# ── Defaults per language ───────────────────────────────────────────────────


class TestDefaultConventions:
    def test_python_defaults(self):
        c = RepoConventions._default_conventions("python")
        assert c.naming_convention == "snake_case"
        assert c.indentation == "4 spaces"
        assert c.quote_style == "double"
        assert c.line_length == 100
        assert c.has_type_hints is False
        assert c.has_docstrings is False
        assert c.docstring_style == "None"
        assert c.confidence == 0.3

    def test_javascript_defaults(self):
        c = RepoConventions._default_conventions("javascript")
        assert c.naming_convention == "camelCase"
        assert c.indentation == "2 spaces"
        assert c.quote_style == "single"

    def test_typescript_defaults_have_type_hints(self):
        c = RepoConventions._default_conventions("typescript")
        assert c.has_type_hints is True
        assert c.indentation == "2 spaces"

    def test_unknown_language_falls_back_to_python_defaults(self):
        c = RepoConventions._default_conventions("rust-unknown")
        assert c.naming_convention == "snake_case"


# ── extract_from_files ──────────────────────────────────────────────────────


class TestExtractFromFiles:
    def test_no_code_files_returns_defaults(self, repo_python, caplog):
        # Pass only a README.md; repo_conventions filters to code files.
        files = {"README.md": "Hello world"}
        result = RepoConventions.extract_from_files(repo_python, files)
        # Falls back to Python defaults.
        assert result.naming_convention == "snake_case"
        assert result.confidence == 0.3

    def test_extracts_snake_python_conventions(self, repo_python, snake_python_files):
        c = RepoConventions.extract_from_files(repo_python, snake_python_files)
        assert c.naming_convention == "snake_case"
        assert c.indentation == "4 spaces"
        assert c.quote_style in ("double", "mixed")  # double quotes dominate
        assert c.line_length in (80, 100)
        assert c.has_type_hints is True
        assert c.has_docstrings is True
        assert c.docstring_style in ("Google", "Basic", "None")  # depending on regex match

    def test_extracts_camel_javascript_conventions(self, repo_javascript, camel_js_files):
        c = RepoConventions.extract_from_files(repo_javascript, camel_js_files)
        assert c.naming_convention == "camelCase"
        assert c.indentation == "2 spaces"
        # JS has no Python-specific checks.
        assert c.has_type_hints is False
        assert c.has_docstrings is False

    def test_confidence_grows_with_sample_size(self, repo_python):
        tiny = {"src/foo.py": "x = 1\n"}
        huge = {f"src/f{i}.py": "\n".join(["x = 1"] * 60) for i in range(20)}
        # 60 * 20 = 1200 lines → confidence capped at 1.0.
        c_tiny = RepoConventions.extract_from_files(repo_python, tiny)
        c_huge = RepoConventions.extract_from_files(repo_python, huge)
        assert c_tiny.confidence < c_huge.confidence
        assert c_huge.confidence == 1.0

    def test_python_repo_with_no_functions_still_returns_valid_conventions(self, repo_python):
        # Edge case: Python file with no `def` lines. should not crash.
        files = {"empty.py": "x = 1\ny = 2\n"}
        c = RepoConventions.extract_from_files(repo_python, files)
        assert isinstance(c, RepoConventions)
        # No functions means has_type_hints should fall back to False (no evidence).
        assert c.has_type_hints is False
        assert c.has_docstrings is False


# ── Detection accuracy for individual rules ─────────────────────────────────


class TestIndividualDetections:
    def test_naming_snake(self):
        files = {"f.py": "def my_func():\n    var_one = 1\n"}
        assert RepoConventions._detect_naming_convention(files) == "snake_case"

    def test_naming_camel(self):
        files = {"f.js": "function myFunc() {\n  var oneVar = 1;\n}\n"}
        assert RepoConventions._detect_naming_convention(files) == "camelCase"

    def test_indentation_4_spaces(self):
        files = {"f.py": "def f():\n    return 1\n"}
        # 4-space indent wins over 2-space.
        assert RepoConventions._detect_indentation(files) == "4 spaces"

    def test_indentation_2_spaces(self):
        files = {"f.js": "function f() {\n  return 1;\n}\n"}
        assert RepoConventions._detect_indentation(files) == "2 spaces"

    def test_indentation_tabs(self):
        files = {"f.go": "func f() {\n\treturn 1\n}\n"}
        assert RepoConventions._detect_indentation(files) == "tabs"

    def test_quote_style_double(self):
        files = {"f.py": 'x = "a"\ny = "b"\nz = "c"\n'}
        assert RepoConventions._detect_quote_style(files) == "double"

    def test_quote_style_single(self):
        files = {"f.js": "var x = 'a';\nvar y = 'b';\nvar z = 'c';\n"}
        assert RepoConventions._detect_quote_style(files) == "single"

    def test_quote_style_mixed(self):
        files = {"f.py": "x = \"a\"\nvar y = 'b'\n"}
        # Roughly equal → mixed.
        assert RepoConventions._detect_quote_style(files) == "mixed"

    def test_line_length_capped_at_120(self):
        # Make every line ~125 chars long.
        long_line = "x = '" + ("a" * 125) + "'"
        files = {"f.py": long_line + "\n"}
        assert RepoConventions._detect_line_length(files) == 120

    def test_line_length_short(self):
        files = {"f.py": "x = 1\n" * 20}
        assert RepoConventions._detect_line_length(files) == 80

    def test_type_hints_majority_required(self):
        files = {"f.py": "def a(x: int) -> int:\n    return x\n" * 10}
        assert RepoConventions._detect_type_hints(files) is True

    def test_type_hints_rare_optional(self):
        files = {"f.py": "def a(x):\n    return x\n" * 10}
        assert RepoConventions._detect_type_hints(files) is False

    def test_docstring_style_google(self):
        # Multiple Google-style markers; should win.
        content = "\n".join(
            [
                "def a(x):",
                '    """Summary.',
                "",
                "    Args:",
                "        x: Something.",
                "",
                "    Returns:",
                "        The value.",
                '    """',
                "    return x",
            ]
        )
        files = {"f.py": content * 5}
        assert RepoConventions._detect_docstring_style(files) == "Google"

    def test_docstring_style_numpy(self):
        content = "\n".join(
            [
                "def a(x):",
                '    """Summary.',
                "",
                "    Parameters",
                "    ----------",
                "    x : int",
                "        Something.",
                "",
                "    Returns",
                "    -------",
                "        The value.",
                '    """',
                "    return x",
            ]
        )
        files = {"f.py": content * 5}
        assert RepoConventions._detect_docstring_style(files) == "NumPy"

    def test_docstring_style_basic(self):
        # Has docstrings but no Google / NumPy markers.
        files = {"f.py": "def a(x):\n    '''Short doc.'''\n    return x\n" * 5}
        assert RepoConventions._detect_docstring_style(files) == "Basic"
