"""Strip ``<thinking>`` blocks that small models leak into output.

Some small models — particularly ones fine-tuned on Anthropic
transcripts — emit ``<thinking>...</thinking>`` blocks in plain
output instead of routing them through the provider's reasoning
channel. The user shouldn't see these; this module strips them.

Public surface:

* :func:`strip_thinking` — remove ``<thinking>`` and a couple of
  related sentinel tags from a string.
* :func:`has_thinking` — predicate exposed for tests / metrics.

Stdlib-only, regex-based, idempotent.
"""
from __future__ import annotations

import re
from typing import Final

__all__ = [
    "THINKING_TAGS",
    "has_thinking",
    "strip_thinking",
]


#: Tags we treat as "internal monologue, hide from the user". The
#: list is conservative — only tags that small open-weight models
#: have been observed to emit in the wild.
THINKING_TAGS: Final[tuple[str, ...]] = (
    "thinking",
    "scratchpad",
    "reasoning",
)


_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"<\s*(" + "|".join(THINKING_TAGS) + r")\s*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Catch unclosed thinking blocks too — small models sometimes
# forget the closing tag entirely. We trim from the opening tag to
# the next double newline (paragraph break) or end-of-string.
_UNCLOSED_RE: Final[re.Pattern[str]] = re.compile(
    r"<\s*(" + "|".join(THINKING_TAGS) + r")\s*>.*?(?=\n\s*\n|$)",
    re.IGNORECASE | re.DOTALL,
)


def has_thinking(text: str) -> bool:
    """Return ``True`` when ``text`` contains a thinking-style tag."""
    if not text:
        return False
    if _TAG_RE.search(text):
        return True
    return bool(_UNCLOSED_RE.search(text))


def strip_thinking(text: str) -> str:
    """Remove thinking blocks from ``text``.

    Strips closed tags first, then unclosed ones, then collapses any
    runs of three-or-more blank lines that the removal might have
    left behind.

    Idempotent: applying this twice returns the same result as once.
    """
    if not text:
        return text

    cleaned = _TAG_RE.sub("", text)
    cleaned = _UNCLOSED_RE.sub("", cleaned)
    # Collapse runs of blank lines left by the strip.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip("\n")
