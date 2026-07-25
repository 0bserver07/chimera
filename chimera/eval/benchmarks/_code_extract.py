"""Shared fence extraction for code-grading benchmark adapters.

Chat models wrap solutions in Markdown ``` fences surrounded by prose;
executing that raw text raises ``SyntaxError`` and grades a correct solution
as 0%. Every adapter that executes ``agent_output`` as Python must normalize
through :func:`extract_code` first. (This gap was caught by the first full
agent × benchmark grid: the columns whose adapters lacked extraction scored a
uniform 0% across all 13 agents while fence-aware columns passed.)
"""

from __future__ import annotations

import re

__all__ = ["CODE_FENCE", "extract_code"]

#: ``` / ```python / ```py fenced block matcher.
#:
#: Only horizontal whitespace (``[^\S\n]*``) is consumed after the info string,
#: then at most one newline. A greedy ``\s*`` here would swallow the first
#: line's *indentation* along with the newline, silently dedenting the block —
#: harmless for a whole module (whose first line starts at column 0) but fatal
#: for a completion-shaped answer, where ``    return x`` becomes an
#: ``IndentationError`` and the correct solution grades as a miss.
CODE_FENCE = re.compile(
    r"```(?:python|py)?[^\S\n]*\n?(.*?)```", re.DOTALL | re.IGNORECASE
)


def extract_code(output: str) -> str:
    """Return executable Python from a model response.

    Concatenates the fenced block(s) when present, otherwise assumes the
    response is already bare source.

    Args:
        output: Raw model/agent output, possibly markdown-fenced with prose.

    Returns:
        The fenced code joined by blank lines, or *output* unchanged.
    """
    blocks = CODE_FENCE.findall(output)
    if blocks:
        return "\n\n".join(block.strip("\n") for block in blocks)
    return output
