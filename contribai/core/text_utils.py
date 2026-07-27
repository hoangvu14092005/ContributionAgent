"""Text utilities used across ContribAI.

Centralizes small string-cleaning helpers that previously lived inline in 4 different
modules (DRY violation fixed in Layer A, see `docs/INTEGRATION_PLAN.md`).
"""

from __future__ import annotations

import re

# Match a complete `` block: opening tag (with optional whitespace/newlines)
# followed by anything up to the closing tag. DOTALL so the body can span
# multiple lines, IGNORECASE so variants like `` or `` also match.
_THINK_FULL_RE = re.compile(
    r"<\s*think\b.*?<\s*/\s*think\s*>",
    re.DOTALL | re.IGNORECASE,
)

# Match an *opening* `` tag at the start of the response that doesn't have a
# matching close in the same message. Some models (e.g. MiniMax-M3) sometimes
# produce an unterminated opening that needs to be dropped.
_THINK_OPEN_AT_START_RE = re.compile(
    r"^\s*<\s*think\b[^>]*?>",
    re.DOTALL | re.IGNORECASE,
)


def strip_think_blocks(text: str) -> str:
    """Strip `` reasoning blocks from an LLM response.

    Some LLM providers (notably MiniMax-M3) emit a `` block before the
    real payload. This helper removes both well-formed blocks and a stray
    opening tag at the start of the response.

    Args:
        text: Raw LLM output (may or may not contain `` blocks).

    Returns:
        The input with `` blocks removed and surrounding whitespace
        stripped. The original string is not modified.

    Examples:
        >>> strip_think_blocks("hello world")
        'hello world'
        >>> strip_think_blocks("hi")
        ''
        >>> strip_think_blocks("\\nhi")  # leading newline dropped
        ''
    """
    if not text:
        return text
    text = _THINK_FULL_RE.sub("", text)
    text = _THINK_OPEN_AT_START_RE.sub("", text)
    return text.strip()
