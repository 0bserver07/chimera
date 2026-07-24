"""Word-level inline diff highlighting (R-REN-10).

``chimera.tui.worddiff`` is stdlib-only (difflib + re), so the pairing and
span logic runs in CI's no-``tui``-extra posture; the rendering half needs
rich and is guarded.
"""
from __future__ import annotations

import pytest

from chimera.tui.worddiff import (
    MIN_RATIO,
    common_leading_ws,
    pair_runs,
    tokenize,
    word_spans,
)


def _changed(spans) -> list[str]:
    return [span.text for span in spans if span.changed]


def _text(spans) -> str:
    return "".join(span.text for span in spans)


# -- tokenizing -----------------------------------------------------------
def test_tokenize_is_lossless():
    for line in ("def f(x):  # note", "    return a+b", "", "  ", "héllo wörld"):
        assert "".join(tokenize(line)) == line


def test_tokenize_splits_words_space_and_punctuation():
    assert tokenize("a.b c") == ["a", ".", "b", " ", "c"]


# -- pairing --------------------------------------------------------------
def test_runs_pair_index_wise_when_balanced():
    assert pair_runs(["a", "b"], ["x", "y"]) == [("a", "x"), ("b", "y")]


def test_unbalanced_runs_do_not_pair():
    assert pair_runs(["a", "b", "c"], ["x"]) is None
    assert pair_runs([], ["x"]) is None
    assert pair_runs(["a"], []) is None


# -- word spans -----------------------------------------------------------
def test_only_the_changed_tokens_are_flagged():
    old, new = word_spans("total = price * qty", "total = price * count")
    assert _text(old) == "total = price * qty"
    assert _text(new) == "total = price * count"
    assert _changed(old) == ["qty"]
    assert _changed(new) == ["count"]


def test_indentation_is_never_highlighted():
    old, new = word_spans("    return a", "        return b")
    # The shared leading whitespace opens an unchanged run in both lines...
    assert old[0].text.startswith("    ") and not old[0].changed
    assert new[0].text.startswith("    ") and not new[0].changed
    # ...and no highlighted span is whitespace-only, so re-indents never flash.
    assert all(span.text.strip() for span in old if span.changed)
    assert all(span.text.strip() for span in new if span.changed)
    assert _changed(old) == ["a"]
    assert _changed(new) == ["b"]


def test_common_leading_ws_counts_only_shared_whitespace():
    assert common_leading_ws("    a", "        b") == 4
    assert common_leading_ws("\t x", "\t y") == 2
    assert common_leading_ws("a", "    a") == 0      # no shared indent
    assert common_leading_ws("  ab", "  ax") == 2    # stops at the first non-space


def test_pure_reindentation_highlights_nothing():
    old, new = word_spans("  x = 1", "      x = 1")
    assert _changed(old) == [] and _changed(new) == []


def test_dissimilar_lines_are_skipped_entirely():
    assert word_spans("from chimera import Agent", "  # unrelated comment,") is None
    assert word_spans("aaaa", "bbbb") is None


def test_identical_and_blank_pairs_are_skipped():
    assert word_spans("same", "same") is None
    assert word_spans("", "new line") is None
    assert word_spans("old line", "   ") is None


def test_min_ratio_is_tunable():
    # A pair just under the default floor pairs when the floor is lowered.
    old_line, new_line = "alpha beta gamma", "alpha zeta omega delta"
    assert word_spans(old_line, new_line, min_ratio=0.9) is None
    assert word_spans(old_line, new_line, min_ratio=0.1) is not None
    assert 0.0 < MIN_RATIO < 1.0


def test_spans_cover_the_whole_line_and_merge():
    old, new = word_spans("a = f(1, 2)", "a = f(1, 3)")
    assert _text(old) == "a = f(1, 2)" and _text(new) == "a = f(1, 3)"
    # adjacent same-flag runs are merged, so the unchanged prefix is one span
    assert old[0].text == "a = f(1, " and not old[0].changed


# -- rendering ------------------------------------------------------------
pytest.importorskip("rich")

from chimera.tui.results import render_diff, word_diff_lines  # noqa: E402
from chimera.tui.theme import BUILTIN_THEMES, Palette  # noqa: E402


def test_word_diff_lines_emits_removals_then_additions():
    lines = word_diff_lines(["total = qty"], ["total = count"])
    assert lines is not None
    assert [line.plain for line in lines] == ["-total = qty", "+total = count"]


def test_word_diff_lines_declines_unpairable_runs():
    assert word_diff_lines(["a", "b"], ["x"]) is None
    assert word_diff_lines(["completely different"], ["nothing alike here!!"]) is None


def test_render_diff_highlights_only_the_changed_token():
    lines = render_diff("@@ -1 +1 @@\n-total = price * qty\n+total = price * count")
    removal = next(line for line in lines if line.plain.startswith("-"))
    highlighted = [
        removal.plain[span.start:span.end]
        for span in removal.spans
        if "reverse" in str(span.style)
    ]
    assert highlighted == ["qty"]


def test_render_diff_falls_back_to_plain_lines_when_unbalanced():
    lines = render_diff("@@ -1 +1 @@\n-a = 1\n-b = 2\n+c = 3")
    bodies = [line.plain for line in lines if line.plain.startswith(("-", "+"))]
    assert bodies == ["-a = 1", "-b = 2", "+c = 3"]
    # no word highlighting on an unpairable run
    assert not any(
        "reverse" in str(span.style) for line in lines for span in line.spans
    )


def test_render_diff_keeps_file_headers_and_hunks_intact():
    lines = render_diff(
        "diff --git a/f b/f\nindex 1..2 100644\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n ctx"
    )
    assert [line.plain for line in lines[:5]] == [
        "diff --git a/f b/f", "index 1..2 100644", "--- a/f", "+++ b/f", "@@ -1 +1 @@",
    ]
    assert lines[-1].plain == " ctx"


def test_render_diff_handles_multiple_runs_in_one_hunk():
    lines = render_diff("@@\n-a = 1\n+a = 2\n ctx\n-b = 1\n+b = 2")
    assert [line.plain for line in lines] == [
        "@@", "-a = 1", "+a = 2", " ctx", "-b = 1", "+b = 2",
    ]


def test_render_diff_follows_the_theme():
    palette = Palette(BUILTIN_THEMES["chimera"], mode="dark", depth="truecolor")
    lines = render_diff("@@\n-x = qty\n+x = count", palette=palette)
    styles = {str(span.style) for line in lines for span in line.spans}
    assert "reverse #8fd67a" in styles or "reverse #f07178" in styles
