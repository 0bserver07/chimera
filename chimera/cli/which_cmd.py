"""``chimera which`` -- recommend the right CLI codename for a task.

Given a free-form task description, score each of the 7 coding-agent CLIs
by how many of its associated keywords appear in the description. Print
the top-k suggestions so a new user can pick a CLI without grepping
``chimera agents`` or the README.

Heuristic only: bag-of-words tokenisation (split on non-alpha,
lowercased) intersected against a static keyword-to-codename map. No
LLM, no network, no provider imports -- stdlib only.

The 7 codenames + their indicative keywords are kept inline here so the
recommender stays light and circular-import-free. Add new keywords as
the per-CLI postures evolve; keep the map small enough that token
overlap stays a meaningful signal.

Usage::

    chimera which --task "I want a TUI panel for coding"
    chimera which --task "spin up a small local llama model" --top-k 1
    chimera which --task "headless rpc agent" --output json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
from typing import Mapping, cast

__all__ = [
    "KEYWORD_MAP",
    "Recommendation",
    "add_subparser",
    "recommend",
    "run",
    "tokenize",
]


# ---------------------------------------------------------------------------
# Keyword map
# ---------------------------------------------------------------------------

# WHY: ordered by codename canonical order so identical-score ties resolve
# in the documented sequence (mink, otter, ferret, weasel, shrew, stoat,
# badger). All keywords are lowercase and alpha-only so they tokenize
# back to themselves under :func:`tokenize`.
#
# Multi-word phrases (e.g. ``low-resource``, ``json-stdio``,
# ``multi-session``) are stored with hyphens for human readability but
# matched as their split tokens against the user's task. We expand them
# below in :data:`_EXPANDED_KEYWORD_MAP`.
KEYWORD_MAP: Mapping[str, tuple[str, ...]] = {
    "mink": ("tui", "textual", "ide", "panel", "gui"),
    "shrew": (
        "small",
        "local",
        "ollama",
        "mini",
        "tiny",
        "low-resource",
        "llama",
        "qwen",
    ),
    "stoat": ("shell", "bash", "command", "repl", "terminal", "/shell"),
    "ferret": (
        "sandbox",
        "docker",
        "isolate",
        "jail",
        "security",
        "untrusted",
    ),
    "badger": (
        "strict",
        "parity",
        "validate",
        "golden",
        "deterministic",
    ),
    "weasel": (
        "rpc",
        "json-stdio",
        "headless",
        "programmatic",
        "api",
    ),
    "otter": (
        "server",
        "http",
        "multi-session",
        "multi-user",
        "port",
        "daemon",
    ),
}


# ---------------------------------------------------------------------------
# Tokenisation + scoring
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[a-z]+")


def tokenize(text: str) -> list[str]:
    """Return the lowercased alpha-only tokens of ``text``.

    Splits on every non-letter character and drops empty fragments. The
    same tokenizer is used for both the user task and the stored
    keywords so matching is symmetric.

    Examples:
        >>> tokenize("Spin up a TUI/IDE panel")
        ['spin', 'up', 'a', 'tui', 'ide', 'panel']
        >>> tokenize("low-resource llama.cpp")
        ['low', 'resource', 'llama', 'cpp']
    """
    return _TOKEN_RE.findall(text.lower())


def _expand_keyword(raw: str) -> tuple[str, ...]:
    """Tokenise a stored keyword the same way we tokenise user input.

    ``low-resource`` -> ``("low", "resource")``; ``/shell`` -> ``("shell",)``.
    Returned as a tuple so downstream code can quickly check membership.
    """
    return tuple(tokenize(raw))


# Pre-expanded form: codename -> tuple of (raw_keyword, expanded_tokens).
# Computed once at import so :func:`recommend` stays O(K) per task.
_EXPANDED_KEYWORD_MAP: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    codename: tuple((kw, _expand_keyword(kw)) for kw in keywords)
    for codename, keywords in KEYWORD_MAP.items()
}


@dataclasses.dataclass(frozen=True)
class Recommendation:
    """One line in the ``chimera which`` output.

    Attributes:
        name: Canonical codename (mink, otter, ferret, weasel, shrew,
            stoat, badger).
        score: Number of distinct keywords that matched. Higher is more
            confident.
        rationale: The raw keywords (as stored in :data:`KEYWORD_MAP`)
            that triggered the score, in the order they appear in the
            map. Empty list when ``score == 0``.
    """

    name: str
    score: int
    rationale: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "score": self.score,
            "rationale": list(self.rationale),
        }


def recommend(task: str, top_k: int = 3) -> list[Recommendation]:
    """Score every codename against ``task`` and return the top ``top_k``.

    Scoring rule: for each codename, count how many of its keywords have
    every expanded token present in the user's task tokens. A keyword
    contributes at most 1 to the score (no double-counting if it appears
    twice in the task).

    Empty or whitespace-only task: returns an empty list (nothing to
    recommend with zero signal).

    Zero-score case: when no keywords match, the function still returns
    ``top_k`` entries with ``score=0`` and empty rationales, ordered by
    canonical codename order. This keeps the output shape stable for
    downstream tooling. Callers that want to suppress empties can filter
    on ``score > 0``.

    Args:
        task: Free-form description of what the user wants to do.
        top_k: How many recommendations to return. Clamped to ``[0,
            len(KEYWORD_MAP)]``. ``top_k=0`` returns an empty list.

    Returns:
        Recommendations sorted by score (descending), then by canonical
        codename order (ascending). Ties are broken by the canonical
        order so the output is fully deterministic.
    """
    if top_k <= 0:
        return []
    task_tokens = set(tokenize(task))
    if not task_tokens:
        return []

    # Stable codename order = insertion order of KEYWORD_MAP.
    codename_order = list(KEYWORD_MAP.keys())

    scored: list[Recommendation] = []
    for idx, codename in enumerate(codename_order):
        matched: list[str] = []
        for raw_kw, expanded in _EXPANDED_KEYWORD_MAP[codename]:
            if expanded and all(tok in task_tokens for tok in expanded):
                matched.append(raw_kw)
        scored.append(
            Recommendation(
                name=codename,
                score=len(matched),
                rationale=matched,
            )
        )
        # idx kept for clarity; sort key below uses codename order.
        del idx

    # Sort by (-score, canonical_index) so higher scores come first and
    # ties break deterministically.
    canonical_index = {name: i for i, name in enumerate(codename_order)}
    scored.sort(key=lambda r: (-r.score, canonical_index[r.name]))

    clamped = min(top_k, len(scored))
    return scored[:clamped]


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def add_subparser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Register ``chimera which`` on the top-level subparser action.

    Mirrors the late-binding pattern used by :mod:`chimera.cli.config_cmd`
    so a broken module never breaks ``chimera --help``. Returns the
    created parser for tests that want to introspect it.
    """
    parser = subparsers.add_parser(
        "which",
        help=(
            "Recommend a chimera CLI codename for a task description "
            "(heuristic; no LLM)."
        ),
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Free-form description of what you want to do.",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="How many recommendations to return (default: 3).",
    )
    return cast(argparse.ArgumentParser, parser)


def _format_text(task: str, recs: list[Recommendation]) -> str:
    """Render recommendations as a numbered list."""
    lines: list[str] = []
    lines.append(f"chimera which: task={task!r}")
    lines.append("")
    if not recs:
        lines.append("  (no recommendations -- task was empty or had no signal)")
        return "\n".join(lines)
    for i, r in enumerate(recs, start=1):
        if r.rationale:
            matched = ", ".join(r.rationale)
            lines.append(
                f"  {i}. {r.name} (score {r.score}) -- matched: {matched}"
            )
        else:
            lines.append(
                f"  {i}. {r.name} (score {r.score}) -- no keyword match"
            )
    lines.append("")
    lines.append(
        "Run ``chimera agents`` for the full catalogue of CLIs and aliases."
    )
    return "\n".join(lines)


def _format_json(task: str, recs: list[Recommendation]) -> str:
    """Render recommendations as a JSON document."""
    payload = {
        "task": task,
        "recommendations": [r.to_dict() for r in recs],
    }
    return json.dumps(payload, indent=2)


def run(args: argparse.Namespace) -> int:
    """Execute ``chimera which`` and print the result."""
    task = getattr(args, "task", "") or ""
    top_k = int(getattr(args, "top_k", 3) or 0)
    output = getattr(args, "output", "text")
    recs = recommend(task, top_k=top_k)
    if output == "json":
        print(_format_json(task, recs))
    else:
        print(_format_text(task, recs))
    return 0
