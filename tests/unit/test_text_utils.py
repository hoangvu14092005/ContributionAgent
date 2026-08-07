"""Tests for contribai.core.text_utils.

Consolidated (Layer A) from 4 inline copies scattered across analyzer.py, engine.py,
patrol.py, and solver.py. The tests below prove the consolidated helper preserves
the behavior of each prior inline implementation.

NOTE: We construct `` tags at runtime via `chr(60) + 'think>'` (or
``<`` Unicode escapes) so the angle-bracket syntax is not stripped
by HTML processors that walk our source files.
"""

from __future__ import annotations

from contribai.core.text_utils import strip_think_blocks

# Build the two tags once at module load. Using ``<`` lets us put the
# literal characters in a docstring/comment without triggering HTML stripping
# in tooling; the runtime value is exactly `` and ``.
OPEN_TAG = "<think>"
CLOSE_TAG = "</think>"


def _wrap_think(body: str = "reasoning here") -> str:
    """Helper: produce a complete ``...`` block as a string."""
    return f"{OPEN_TAG}{body}{CLOSE_TAG}"


class TestStripThinkBlocks:
    """`strip_think_blocks(text: str) -> str` consolidates four inline copies."""

    def test_no_blocks_returns_text_unchanged(self):
        assert strip_think_blocks("hello world") == "hello world"

    def test_empty_string_returns_empty(self):
        assert strip_think_blocks("") == ""

    def test_whitespace_only_returns_empty(self):
        # Mirrors the original `.strip()` call after re.sub at all 4 sites.
        assert strip_think_blocks("   \n  ") == ""

    def test_complete_block_at_start_is_removed(self):
        text = f"{_wrap_think()}\nnow the real response"
        result = strip_think_blocks(text)
        assert "reasoning here" not in result
        assert result == "now the real response"

    def test_complete_block_in_middle_is_removed(self):
        text = f"before{_wrap_think('INNER')}between\nafter"
        result = strip_think_blocks(text)
        assert "INNER" not in result
        assert "before" in result
        assert "between" in result
        assert "after" in result

    def test_multiple_complete_blocks_are_removed(self):
        # Two consecutive blocks at start; both should be stripped.
        text = f"{_wrap_think('first')}{_wrap_think('second')}\nreal answer"
        result = strip_think_blocks(text)
        assert "first" not in result
        assert "second" not in result
        assert result == "real answer"

    def test_opening_only_block_at_start_is_removed(self):
        # `` tag without a matching closing tag should be stripped too.
        text = f"{OPEN_TAG}thinking but never closed\nthe rest of the response"
        result = strip_think_blocks(text)
        assert OPEN_TAG not in result
        assert "the rest of the response" in result

    def test_case_insensitive_tag_match(self):
        # Original engine.py / analyzer.py use re.IGNORECASE — verify carried over.
        text = "<Think>reasoning</THINK>\nactual payload"
        result = strip_think_blocks(text)
        assert "reasoning" not in result
        assert result == "actual payload"

    def test_multiline_block_body(self):
        block = f"{OPEN_TAG}\n  line 1\n  line 2\n  line 3\n{CLOSE_TAG}"
        text = f"{block}\nreal output"
        result = strip_think_blocks(text)
        assert "line 1" not in result
        assert "line 2" not in result
        assert "line 3" not in result
        assert result == "real output"

    def test_surrounding_whitespace_is_trimmed(self):
        text = f"   \n{OPEN_TAG}reasoning{CLOSE_TAG}\n   result\n   "
        result = strip_think_blocks(text)
        assert not result.startswith(" ")
        assert not result.startswith("\n")
        assert not result.endswith("\n")
        assert result == "result"

    def test_unicode_in_response_preserved(self):
        text = f"{_wrap_think('thinking in english')}\nphản hồi tiếng Việt"
        result = strip_think_blocks(text)
        assert result == "phản hồi tiếng Việt"

    def test_json_payload_preserved_intact(self):
        # The engine.py _extract_json use-case: result must be parseable JSON.
        text = f'{_wrap_think()}\n{{"changes": [{{"path": "x.py"}}]}}'
        result = strip_think_blocks(text)
        assert result == '{"changes": [{"path": "x.py"}]}'
        import json

        parsed = json.loads(result)
        assert parsed["changes"][0]["path"] == "x.py"

    def test_block_with_attributes_inside_is_still_stripped(self):
        # `` is unusual but the base case still strips.
        attr_open = OPEN_TAG[:-1] + ' lang="en">'
        text = f"{attr_open}reasoning{CLOSE_TAG}\nresult"
        result = strip_think_blocks(text)
        assert "reasoning" not in result
        assert result == "result"

    def test_only_thinking_block_returns_empty(self):
        assert strip_think_blocks(_wrap_think("only reasoning")) == ""

    def test_passthrough_to_yaml_payload(self):
        # patrol.py / solver.py use case.
        text = f"{_wrap_think()}\n```yaml\nfoo: bar\n```"
        result = strip_think_blocks(text)
        assert result == "```yaml\nfoo: bar\n```"


class TestBehavioralParity:
    """Spot-checks that the consolidated helper matches each of the 4 original sites."""

    def test_analyzer_pattern_parity(self):
        # analyzer.py originally applied both re.sub patterns + .strip() in order.
        text = f"{_wrap_think()}\nactual"
        assert strip_think_blocks(text) == "actual"

    def test_engine_response_stripper_parity(self):
        # engine.py originally called .strip() BEFORE re.sub. Our helper does
        # it AFTER. Equivalent for normal inputs (re.sub never produces
        # trailing whitespace in this pattern).
        text = f"  {_wrap_think('r')}foo  "
        result = strip_think_blocks(text)
        assert "foo" in result

    def test_extract_json_parity(self):
        # engine.py:_extract_json originally stripped leading/trailing/newlines
        # then ran both patterns.
        text = f'{_wrap_think()}\n{{"k": "v"}}'
        result = strip_think_blocks(text)
        assert result.startswith("{") and result.endswith("}")

    def test_patrol_parser_parity(self):
        # patrol.py originally stripped both patterns. Our helper should
        # produce the same intermediate payload.
        text = f"{_wrap_think()}\nyaml: stuff\nmore: lines"
        result = strip_think_blocks(text)
        assert result == "yaml: stuff\nmore: lines"

    def test_solver_parser_parity(self):
        # solver.py:_parse_multi_file_response originally stripped both patterns.
        text = f"{_wrap_think()}\n---FILE---\nfoo\n---FILE---\nbar"
        result = strip_think_blocks(text)
        assert result.startswith("---FILE---")
        assert "foo" in result and "bar" in result
