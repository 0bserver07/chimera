"""Word-level inline diff highlighting (R-REN-10).

A unified diff that colors whole lines red/green makes you *read* both lines to
find the one identifier that changed. Pairing the removal and addition runs and
highlighting only the changed tokens makes the edit visible at a glance.

The rules this implements, all from the spec:

- **Pair runs, then word-diff each pair.** A removal run and the addition run
  that follows it pair index-wise only when they have the same length; an
  unbalanced run (3 removed, 1 added) has no honest pairing, so it renders as
  plain line colors.
- **Skip when similarity is low.** Two lines that merely share a comma are not
  an edit of each other — below :data:`MIN_RATIO` the pair renders plain, since
  highlighting near-everything is worse than highlighting nothing.
- **Never highlight indentation.** The common leading whitespace of the pair is
  emitted as unchanged, so a re-indent never lights up the whole line.

Stdlib-only (:mod:`difflib` + :mod:`re`) and widget-free: the frontend turns
:class:`Span` runs into styled text with the ``diff.add-word`` /
``diff.remove-word`` theme slots.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

__all__ = [
    "MIN_RATIO",
    "Span",
    "common_leading_ws",
    "pair_runs",
    "tokenize",
    "word_spans",
]

#: Below this similarity ratio a pair is not an edit of each other and the
#: word diff is skipped entirely (spec: "skip entirely when similarity is low").
MIN_RATIO = 0.4

#: Words, whitespace runs, and single punctuation characters. Lossless:
#: ``"".join(tokenize(s)) == s`` for any input.
_TOKEN_RE = re.compile(r"\w+|\s+|[^\w\s]")


@dataclass(frozen=True)
class Span:
    """One run of a diff line, flagged as changed or unchanged.

    Args:
        text: The literal text of the run.
        changed: True when this run differs from the paired line — the
            frontend inverse-highlights exactly these.
    """

    text: str
    changed: bool


def tokenize(line: str) -> list[str]:
    """Split a line into word / whitespace / punctuation tokens (lossless).

    Args:
        line: Any single line of text.

    Returns:
        Tokens whose concatenation is exactly *line*.
    """
    return _TOKEN_RE.findall(line)


def common_leading_ws(old: str, new: str) -> int:
    """Length of the leading whitespace both lines share.

    Args:
        old: The removal line (without its ``-`` marker).
        new: The addition line (without its ``+`` marker).

    Returns:
        The number of leading characters that are whitespace in both lines and
        equal — never highlighted, so indentation stays quiet.
    """
    limit = min(len(old), len(new))
    i = 0
    while i < limit and old[i] == new[i] and old[i] in " \t":
        i += 1
    return i


def _merge(spans: list[Span]) -> list[Span]:
    """Collapse adjacent spans that share a flag (and drop empty ones)."""
    out: list[Span] = []
    for span in spans:
        if not span.text:
            continue
        if out and out[-1].changed == span.changed:
            out[-1] = Span(out[-1].text + span.text, span.changed)
        else:
            out.append(span)
    return out


def word_spans(
    old: str, new: str, *, min_ratio: float = MIN_RATIO,
) -> tuple[list[Span], list[Span]] | None:
    """Word-diff one removal/addition pair (R-REN-10).

    Args:
        old: The removed line's text (no ``-`` marker).
        new: The added line's text (no ``+`` marker).
        min_ratio: Similarity floor; below it the pair is not treated as an
            edit and ``None`` is returned.

    Returns:
        ``(old_spans, new_spans)`` covering each line completely, with only
        the changed tokens flagged — or ``None`` when the pair is identical,
        one side is blank, or the two are too dissimilar to pair honestly.
    """
    if old == new or not old.strip() or not new.strip():
        return None
    indent = common_leading_ws(old, new)
    old_body, new_body = old[indent:], new[indent:]
    if SequenceMatcher(None, old_body, new_body).ratio() < min_ratio:
        return None
    old_tokens, new_tokens = tokenize(old_body), tokenize(new_body)
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    prefix = [Span(old[:indent], False)] if indent else []
    old_spans: list[Span] = list(prefix)
    new_spans: list[Span] = list(prefix)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_text = "".join(old_tokens[i1:i2])
        new_text = "".join(new_tokens[j1:j2])
        changed = tag != "equal"
        # A changed run made only of whitespace carries no information — mark
        # it unchanged so trailing/inner spacing edits do not flash.
        old_spans.append(Span(old_text, changed and bool(old_text.strip())))
        new_spans.append(Span(new_text, changed and bool(new_text.strip())))
    return _merge(old_spans), _merge(new_spans)


def pair_runs(removes: list[str], adds: list[str]) -> list[tuple[str, str]] | None:
    """Pair a removal run with the addition run that follows it.

    Args:
        removes: Consecutive removed lines (no markers).
        adds: The consecutive added lines that follow them.

    Returns:
        Index-wise ``(old, new)`` pairs when the runs are the same non-zero
        length, else ``None`` — an unbalanced run has no honest pairing, so
        the caller falls back to plain line colors.
    """
    if not removes or len(removes) != len(adds):
        return None
    return list(zip(removes, adds))
