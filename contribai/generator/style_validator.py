"""Style validation for generated code.

Validates that generated code matches repository conventions
before submission.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contribai.analysis.repo_conventions import RepoConventions

logger = logging.getLogger(__name__)


@dataclass
class StyleValidationResult:
    """Result of style validation."""

    passed: bool
    score: float  # 0.0-10.0
    issues: list[str]
    warnings: list[str]


class StyleValidator:
    """Validate generated code style against repo conventions."""

    def validate(
        self, generated_code: str, conventions: RepoConventions
    ) -> StyleValidationResult:
        """Validate generated code style.

        Args:
            generated_code: The generated code to validate
            conventions: Repository conventions to check against

        Returns:
            StyleValidationResult with score and issues
        """
        score = 10.0
        issues = []
        warnings = []

        # 1. Check naming convention
        naming_score, naming_issues = self._check_naming(generated_code, conventions)
        score -= (10.0 - naming_score) * 0.3  # 30% weight
        issues.extend(naming_issues)

        # 2. Check indentation
        indent_score, indent_issues = self._check_indentation(generated_code, conventions)
        score -= (10.0 - indent_score) * 0.25  # 25% weight
        issues.extend(indent_issues)

        # 3. Check quote style
        quote_score, quote_warnings = self._check_quotes(generated_code, conventions)
        score -= (10.0 - quote_score) * 0.15  # 15% weight
        warnings.extend(quote_warnings)

        # 4. Check line length
        length_score, length_warnings = self._check_line_length(generated_code, conventions)
        score -= (10.0 - length_score) * 0.15  # 15% weight
        warnings.extend(length_warnings)

        # 5. Check type hints (Python)
        if conventions.has_type_hints:
            hint_score, hint_warnings = self._check_type_hints(generated_code)
            score -= (10.0 - hint_score) * 0.10  # 10% weight
            warnings.extend(hint_warnings)

        # 6. Check docstrings (Python)
        if conventions.has_docstrings:
            doc_score, doc_warnings = self._check_docstrings(generated_code, conventions)
            score -= (10.0 - doc_score) * 0.05  # 5% weight
            warnings.extend(doc_warnings)

        passed = score >= 7.0 and len(issues) == 0

        return StyleValidationResult(
            passed=passed, score=max(0.0, score), issues=issues, warnings=warnings
        )

    def _check_naming(
        self, code: str, conventions: RepoConventions
    ) -> tuple[float, list[str]]:
        """Check naming convention compliance."""
        issues = []
        score = 10.0

        if conventions.naming_convention == "snake_case":
            # Check for camelCase violations
            camel_case_matches = re.findall(r"\b[a-z]+[A-Z][a-zA-Z]+\b", code)
            if camel_case_matches:
                issues.append(
                    f"Uses camelCase but repo uses snake_case: {camel_case_matches[:3]}"
                )
                score -= 3.0

        elif conventions.naming_convention == "camelCase":
            # Check for snake_case violations
            snake_case_matches = re.findall(r"\b[a-z]+_[a-z_]+\b", code)
            # Filter out common patterns like __init__, __name__
            snake_case_matches = [m for m in snake_case_matches if not m.startswith("__")]
            if snake_case_matches:
                issues.append(
                    f"Uses snake_case but repo uses camelCase: {snake_case_matches[:3]}"
                )
                score -= 3.0

        return max(0.0, score), issues

    def _check_indentation(
        self, code: str, conventions: RepoConventions
    ) -> tuple[float, list[str]]:
        """Check indentation style compliance."""
        issues = []
        score = 10.0

        lines = code.split("\n")
        indent_violations = 0

        for i, line in enumerate(lines, 1):
            if not line or not line[0].isspace():
                continue

            if conventions.indentation == "tabs":
                if line.startswith(" "):
                    indent_violations += 1
            elif conventions.indentation == "2 spaces":
                if line.startswith("    ") or line.startswith("\t"):
                    indent_violations += 1
            elif conventions.indentation == "4 spaces":
                if line.startswith("  ") and not line.startswith("    "):
                    indent_violations += 1
                elif line.startswith("\t"):
                    indent_violations += 1

        if indent_violations > 0:
            issues.append(
                f"Indentation mismatch: {indent_violations} lines use wrong indentation "
                f"(expected {conventions.indentation})"
            )
            score -= min(5.0, indent_violations * 0.5)

        return max(0.0, score), issues

    def _check_quotes(
        self, code: str, conventions: RepoConventions
    ) -> tuple[float, list[str]]:
        """Check quote style compliance."""
        warnings = []
        score = 10.0

        if conventions.quote_style == "mixed":
            return score, warnings  # No preference

        single_quotes = len(re.findall(r"'[^']*'", code))
        double_quotes = len(re.findall(r'"[^"]*"', code))

        if conventions.quote_style == "single" and double_quotes > single_quotes:
            warnings.append(
                f"Prefers single quotes but code uses {double_quotes} double quotes "
                f"vs {single_quotes} single quotes"
            )
            score -= 2.0

        elif conventions.quote_style == "double" and single_quotes > double_quotes:
            warnings.append(
                f"Prefers double quotes but code uses {single_quotes} single quotes "
                f"vs {double_quotes} double quotes"
            )
            score -= 2.0

        return max(0.0, score), warnings

    def _check_line_length(
        self, code: str, conventions: RepoConventions
    ) -> tuple[float, list[str]]:
        """Check line length compliance."""
        warnings = []
        score = 10.0

        lines = code.split("\n")
        long_lines = [
            (i + 1, len(line))
            for i, line in enumerate(lines)
            if len(line) > conventions.line_length
        ]

        if long_lines:
            warnings.append(
                f"{len(long_lines)} lines exceed max length {conventions.line_length}: "
                f"lines {[ln for ln, _ in long_lines[:3]]}"
            )
            score -= min(3.0, len(long_lines) * 0.5)

        return max(0.0, score), warnings

    def _check_type_hints(self, code: str) -> tuple[float, list[str]]:
        """Check type hint usage (Python)."""
        warnings = []
        score = 10.0

        # Count functions with and without type hints
        functions_with_hints = len(re.findall(r"def \w+\([^)]*:\s*\w+", code))
        total_functions = len(re.findall(r"def \w+\(", code))

        if total_functions > 0:
            hint_ratio = functions_with_hints / total_functions
            if hint_ratio < 0.5:
                warnings.append(
                    f"Repo uses type hints but only {hint_ratio:.0%} of functions have them"
                )
                score -= 2.0

        return max(0.0, score), warnings

    def _check_docstrings(
        self, code: str, conventions: RepoConventions
    ) -> tuple[float, list[str]]:
        """Check docstring usage (Python)."""
        warnings = []
        score = 10.0

        # Count functions with docstrings
        functions_with_docs = len(re.findall(r'def \w+\([^)]*\):[^"\']*["\']{{3}}', code))
        total_functions = len(re.findall(r"def \w+\(", code))

        if total_functions > 0:
            doc_ratio = functions_with_docs / total_functions
            if doc_ratio < 0.3:
                warnings.append(
                    f"Repo uses docstrings but only {doc_ratio:.0%} of functions have them"
                )
                score -= 1.0

        return max(0.0, score), warnings
