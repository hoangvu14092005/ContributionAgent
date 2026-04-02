"""Repository conventions extraction.

Automatically detects and extracts coding conventions from a repository
to ensure generated code matches the existing style.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contribai.core.models import Repository
    from contribai.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class RepoConventions:
    """Extracted conventions from a repository."""

    # Code style
    naming_convention: str  # snake_case, camelCase, PascalCase
    indentation: str  # "2 spaces", "4 spaces", "tabs"
    quote_style: str  # single, double, mixed
    line_length: int  # max line length

    # Patterns
    has_type_hints: bool  # Python type hints usage
    has_docstrings: bool  # Docstring presence
    docstring_style: str  # Google, NumPy, reStructuredText, None

    # Confidence
    confidence: float  # 0.0-1.0

    def to_prompt_context(self) -> str:
        """Convert conventions to prompt context."""
        confidence_note = ""
        if self.confidence < 0.7:
            confidence_note = (
                f"\nNOTE: Convention detection confidence is {self.confidence:.0%}. "
                "Use conservative defaults if unsure."
            )

        return f"""
Repository Coding Conventions:{confidence_note}

Style Rules (MUST FOLLOW EXACTLY):
- Naming: {self.naming_convention}
- Indentation: {self.indentation}
- Quotes: {self.quote_style}
- Max line length: {self.line_length}
- Type hints: {"Required" if self.has_type_hints else "Optional"}
- Docstrings: {self.docstring_style if self.has_docstrings else "Not required"}

CRITICAL: Your generated code MUST match these conventions exactly.
Any style mismatch will result in rejection.
"""

    @classmethod
    def extract_from_files(
        cls, repo: Repository, files: dict[str, str]
    ) -> RepoConventions:
        """Extract conventions by analyzing code files.

        Args:
            repo: Repository metadata
            files: Dict of file_path -> content

        Returns:
            RepoConventions with detected patterns
        """
        # Filter to code files only
        code_files = {
            path: content
            for path, content in files.items()
            if cls._is_code_file(path, repo.language)
        }

        if not code_files:
            logger.warning(f"No code files found for {repo.full_name}")
            return cls._default_conventions(repo.language)

        # Detect patterns
        naming = cls._detect_naming_convention(code_files)
        indentation = cls._detect_indentation(code_files)
        quotes = cls._detect_quote_style(code_files)
        line_length = cls._detect_line_length(code_files)

        # Python-specific
        has_type_hints = False
        has_docstrings = False
        docstring_style = "None"

        if repo.language.lower() == "python":
            has_type_hints = cls._detect_type_hints(code_files)
            has_docstrings = cls._detect_docstrings(code_files)
            if has_docstrings:
                docstring_style = cls._detect_docstring_style(code_files)

        # Calculate confidence based on sample size
        total_lines = sum(len(content.split("\n")) for content in code_files.values())
        confidence = min(total_lines / 500, 1.0)  # Full confidence at 500+ lines

        return cls(
            naming_convention=naming,
            indentation=indentation,
            quote_style=quotes,
            line_length=line_length,
            has_type_hints=has_type_hints,
            has_docstrings=has_docstrings,
            docstring_style=docstring_style,
            confidence=confidence,
        )

    @staticmethod
    def _is_code_file(path: str, language: str) -> bool:
        """Check if file is a code file."""
        code_extensions = {
            "python": [".py"],
            "javascript": [".js", ".jsx"],
            "typescript": [".ts", ".tsx"],
            "go": [".go"],
            "rust": [".rs"],
            "java": [".java"],
            "kotlin": [".kt"],
        }

        lang_lower = language.lower()
        extensions = code_extensions.get(lang_lower, [])

        return any(path.endswith(ext) for ext in extensions)

    @staticmethod
    def _detect_naming_convention(files: dict[str, str]) -> str:
        """Detect naming convention from code samples."""
        snake_case_count = 0
        camel_case_count = 0
        pascal_case_count = 0

        for content in files.values():
            # Count function/variable patterns
            snake_case_count += len(re.findall(r"\b[a-z]+_[a-z_]+\b", content))
            camel_case_count += len(re.findall(r"\b[a-z]+[A-Z][a-zA-Z]+\b", content))
            pascal_case_count += len(re.findall(r"\b[A-Z][a-z]+[A-Z][a-zA-Z]+\b", content))

        # Return most common
        if snake_case_count > camel_case_count and snake_case_count > pascal_case_count:
            return "snake_case"
        elif camel_case_count > pascal_case_count:
            return "camelCase"
        else:
            return "PascalCase"

    @staticmethod
    def _detect_indentation(files: dict[str, str]) -> str:
        """Detect indentation style."""
        spaces_2 = 0
        spaces_4 = 0
        tabs = 0

        for content in files.values():
            lines = content.split("\n")
            for line in lines:
                if line.startswith("  ") and not line.startswith("    "):
                    spaces_2 += 1
                elif line.startswith("    "):
                    spaces_4 += 1
                elif line.startswith("\t"):
                    tabs += 1

        # Return most common
        if tabs > spaces_2 and tabs > spaces_4:
            return "tabs"
        elif spaces_2 > spaces_4:
            return "2 spaces"
        else:
            return "4 spaces"

    @staticmethod
    def _detect_quote_style(files: dict[str, str]) -> str:
        """Detect quote style preference."""
        single_quotes = 0
        double_quotes = 0

        for content in files.values():
            # Count string literals
            single_quotes += len(re.findall(r"'[^']*'", content))
            double_quotes += len(re.findall(r'"[^"]*"', content))

        if single_quotes > double_quotes * 1.5:
            return "single"
        elif double_quotes > single_quotes * 1.5:
            return "double"
        else:
            return "mixed"

    @staticmethod
    def _detect_line_length(files: dict[str, str]) -> int:
        """Detect typical max line length."""
        all_lengths = []

        for content in files.values():
            lines = content.split("\n")
            lengths = [len(line) for line in lines if line.strip()]
            all_lengths.extend(lengths)

        if not all_lengths:
            return 100

        # Use 95th percentile as max line length
        all_lengths.sort()
        p95_index = int(len(all_lengths) * 0.95)
        p95_length = all_lengths[p95_index]

        # Round to common values
        if p95_length <= 80:
            return 80
        elif p95_length <= 100:
            return 100
        elif p95_length <= 120:
            return 120
        else:
            return 120  # Cap at 120

    @staticmethod
    def _detect_type_hints(files: dict[str, str]) -> bool:
        """Detect if Python type hints are used."""
        hint_count = 0
        function_count = 0

        for content in files.values():
            # Count functions with type hints
            hint_count += len(re.findall(r"def \w+\([^)]*:\s*\w+", content))
            # Count all functions
            function_count += len(re.findall(r"def \w+\(", content))

        if function_count == 0:
            return False

        # If >50% of functions have type hints, consider it required
        return hint_count / function_count > 0.5

    @staticmethod
    def _detect_docstrings(files: dict[str, str]) -> bool:
        """Detect if docstrings are commonly used."""
        docstring_count = 0
        function_count = 0

        for content in files.values():
            # Count docstrings (triple quotes after def)
            docstring_count += len(re.findall(r'def \w+\([^)]*\):[^"\']*["\']{{3}}', content))
            # Count all functions
            function_count += len(re.findall(r"def \w+\(", content))

        if function_count == 0:
            return False

        # If >30% of functions have docstrings, consider it expected
        return docstring_count / function_count > 0.3

    @staticmethod
    def _detect_docstring_style(files: dict[str, str]) -> str:
        """Detect docstring style (Google, NumPy, etc)."""
        google_count = 0
        numpy_count = 0

        for content in files.values():
            # Google style: Args:, Returns:, Raises:
            google_count += len(re.findall(r"Args:|Returns:|Raises:", content))
            # NumPy style: Parameters, Returns with dashes
            numpy_count += len(re.findall(r"Parameters\n\s*-+|Returns\n\s*-+", content))

        if google_count > numpy_count:
            return "Google"
        elif numpy_count > 0:
            return "NumPy"
        else:
            return "Basic"

    @classmethod
    def _default_conventions(cls, language: str) -> RepoConventions:
        """Return default conventions for a language."""
        defaults = {
            "python": cls(
                naming_convention="snake_case",
                indentation="4 spaces",
                quote_style="double",
                line_length=100,
                has_type_hints=False,
                has_docstrings=False,
                docstring_style="None",
                confidence=0.3,
            ),
            "javascript": cls(
                naming_convention="camelCase",
                indentation="2 spaces",
                quote_style="single",
                line_length=100,
                has_type_hints=False,
                has_docstrings=False,
                docstring_style="None",
                confidence=0.3,
            ),
            "typescript": cls(
                naming_convention="camelCase",
                indentation="2 spaces",
                quote_style="single",
                line_length=100,
                has_type_hints=True,
                has_docstrings=False,
                docstring_style="None",
                confidence=0.3,
            ),
        }

        return defaults.get(
            language.lower(),
            cls(
                naming_convention="snake_case",
                indentation="4 spaces",
                quote_style="double",
                line_length=100,
                has_type_hints=False,
                has_docstrings=False,
                docstring_style="None",
                confidence=0.3,
            ),
        )
