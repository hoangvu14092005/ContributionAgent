"""Skill loader for .agents/ markdown files.

Parses YAML frontmatter from agent/workflow/knowledge markdown files
so they are programmatically discoverable via ``contribai skills``.

Inspired by Haystack's :class:`SkillToolset` progressive-disclosure pattern:
the loader returns lightweight metadata on ``list_all`` so callers can decide
which full ``Skill`` body to load.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Default location of the .agents/ directory.
DEFAULT_AGENTS_DIR = Path(__file__).resolve().parents[2] / ".agents"

# Subdirectories inside .agents/ that contain skill markdown files.
SKILL_SUBDIRS: tuple[str, ...] = ("agents", "workflows", "knowledge")

# Markdown frontmatter delimiters.
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL,
)


@dataclass(frozen=True)
class SkillInput:
    """A single declared input parameter for a skill."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True


@dataclass(frozen=True)
class Skill:
    """A single skill parsed from a markdown file."""

    name: str
    description: str
    file_path: Path
    category: str  # one of "agent", "workflow", "knowledge"
    trigger: str | None = None
    inputs: tuple[SkillInput, ...] = field(default_factory=tuple)
    extra: dict = field(default_factory=dict)
    body: str = ""

    @property
    def has_trigger(self) -> bool:
        return bool(self.trigger)

    def to_dict(self) -> dict:
        """Serialize for CLI / logging output."""
        try:
            rel = str(self.file_path.relative_to(self.file_path.parents[2]))
        except (ValueError, IndexError):
            rel = str(self.file_path)
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "trigger": self.trigger,
            "file": rel,
            "inputs": [
                {"name": i.name, "type": i.type, "required": i.required} for i in self.inputs
            ],
        }


class SkillParseError(ValueError):
    """Raised when a markdown file has malformed frontmatter."""


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown file into (frontmatter_dict, body).

    Returns empty dict + full text if no frontmatter is present.
    Raises :class:`SkillParseError` on malformed YAML.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fm_raw, body = match.group(1), match.group(2)
    try:
        data = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError as exc:
        raise SkillParseError(f"Invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillParseError(f"Frontmatter must be a mapping, got {type(data).__name__}")
    return data, body


def _category_from_path(path: Path, agents_dir: Path) -> str | None:
    """Map a file path to its skill category, or None if not under agents_dir."""
    try:
        rel = path.resolve().relative_to(agents_dir.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    top = parts[0]
    if top == "agents":
        return "agent"
    if top == "workflows":
        return "workflow"
    if top == "knowledge":
        return "knowledge"
    return None


def _parse_inputs(raw: object) -> tuple[SkillInput, ...]:
    """Normalize the frontmatter ``inputs`` field into :class:`SkillInput` tuples."""
    if raw is None:
        return ()
    if isinstance(raw, dict):
        # legacy: name -> description
        items = []
        for name, val in raw.items():
            if isinstance(val, str):
                items.append(SkillInput(name=name, description=val))
            elif isinstance(val, dict):
                items.append(
                    SkillInput(
                        name=name,
                        type=val.get("type", "string"),
                        description=val.get("description", ""),
                        required=val.get("required", True),
                    )
                )
        return tuple(items)
    if isinstance(raw, list):
        items = []
        for entry in raw:
            if isinstance(entry, str):
                items.append(SkillInput(name=entry))
            elif isinstance(entry, dict):
                items.append(
                    SkillInput(
                        name=entry.get("name", ""),
                        type=entry.get("type", "string"),
                        description=entry.get("description", ""),
                        required=entry.get("required", True),
                    )
                )
        return tuple(items)
    return ()


class SkillLoader:
    """Loader that discovers and parses skills under ``.agents/``.

    Usage::

        loader = SkillLoader()  # uses default .agents/ location
        for skill in loader.list_all():
            print(skill.name, skill.trigger)
        skill = loader.find_by_trigger("/address_pr_comments")
        if skill:
            print(skill.body)
    """

    def __init__(self, agents_dir: Path | str | None = None) -> None:
        self._agents_dir = Path(agents_dir) if agents_dir else DEFAULT_AGENTS_DIR

    @property
    def agents_dir(self) -> Path:
        return self._agents_dir

    def _iter_files(self) -> list[Path]:
        if not self._agents_dir.exists():
            return []
        results: list[Path] = []
        for sub in SKILL_SUBDIRS:
            sub_dir = self._agents_dir / sub
            if sub_dir.exists():
                results.extend(sorted(sub_dir.glob("*.md")))
        return results

    def list_all(self) -> list[Skill]:
        """Return all skills. Skips files that fail to parse (logs a warning)."""
        skills: list[Skill] = []
        for path in self._iter_files():
            skill = self._parse_file(path)
            if skill is not None:
                skills.append(skill)
        return skills

    def find_by_trigger(self, trigger: str) -> Skill | None:
        """Find a skill whose trigger matches (case-insensitive, with or without leading ``/``)."""
        if not trigger:
            return None
        needle = trigger.lstrip("/").lower()
        for skill in self.list_all():
            if not skill.trigger:
                continue
            if skill.trigger.lstrip("/").lower() == needle:
                return skill
        return None

    def find_by_name(self, name: str) -> Skill | None:
        """Find a skill by its file stem."""
        for skill in self.list_all():
            if skill.file_path.stem == name:
                return skill
        return None

    def search(self, query: str) -> list[Skill]:
        """Keyword search across name, description, and trigger."""
        q = query.lower().strip()
        if not q:
            return self.list_all()
        results: list[Skill] = []
        for skill in self.list_all():
            haystack = " ".join(
                [
                    skill.name,
                    skill.description,
                    skill.trigger or "",
                    skill.body[:500],
                ]
            ).lower()
            if q in haystack:
                results.append(skill)
        return results

    def categories(self) -> dict[str, int]:
        """Return count of skills per category."""
        counts: dict[str, int] = {"agent": 0, "workflow": 0, "knowledge": 0}
        for skill in self.list_all():
            counts[skill.category] = counts.get(skill.category, 0) + 1
        return counts

    def _parse_file(self, path: Path) -> Skill | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return None

        try:
            fm, body = _parse_frontmatter(text)
        except SkillParseError as exc:
            logger.warning("Skipping %s: %s", path, exc)
            return None

        category = _category_from_path(path, self._agents_dir)
        if category is None:
            return None

        name = str(fm.get("name") or path.stem)
        description = str(fm.get("description") or "")
        trigger_raw = fm.get("trigger")
        trigger = str(trigger_raw) if trigger_raw else None
        inputs = _parse_inputs(fm.get("inputs"))

        # collect extra metadata we don't model in fields
        reserved = {"name", "description", "trigger", "inputs"}
        extra = {k: v for k, v in fm.items() if k not in reserved}

        return Skill(
            name=name,
            description=description,
            file_path=path,
            category=category,
            trigger=trigger,
            inputs=inputs,
            extra=extra,
            body=body.strip(),
        )


def load_default_loader() -> SkillLoader:
    """Return a loader pointing at the project's default ``.agents/`` directory."""
    return SkillLoader(DEFAULT_AGENTS_DIR)
