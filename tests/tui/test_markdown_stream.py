"""Torture tests for the pure stream-shaping helpers (spec §13).

Covers R-REN-6 block-boundary detection (fences with char+length tracking,
blank-line-terminated runs, table/list holdback, chunk boundaries mid-marker),
R-REN-7 nested-fence normalization, and R-FOLD-2 head+tail elision. Everything
here is terminal-free: the module under test is stdlib-only.
"""
from __future__ import annotations

from chimera.tui.markdown_stream import (
    DEFAULT_CAPS,
    QUIET_CAPS,
    SHELL_CAPS,
    ElisionCaps,
    caps_for_tool,
    elide_middle,
    live_tail_view,
    normalize_nested_fences,
    split_complete_blocks,
)


# -- R-REN-6: split_complete_blocks — basics --------------------------------
def test_empty_buffer():
    assert split_complete_blocks("") == ([], "")


def test_incomplete_line_stays_in_tail():
    assert split_complete_blocks("hello") == ([], "hello")


def test_paragraph_commits_at_blank_line():
    assert split_complete_blocks("para one\n\n") == (["para one\n\n"], "")


def test_paragraph_without_blank_stays_live():
    # It could still grow (lazy continuation) or become a setext heading.
    assert split_complete_blocks("para one\n") == ([], "para one\n")


def test_multiple_paragraphs():
    blocks, tail = split_complete_blocks("a\n\nb\n\nc")
    assert blocks == ["a\n\n", "b\n\n"]
    assert tail == "c"


def test_heading_and_prose_commit_together():
    blocks, tail = split_complete_blocks("# Title\nbody line\n\nnext")
    assert blocks == ["# Title\nbody line\n\n"]
    assert tail == "next"


def test_leading_blank_lines_attach_to_next_block():
    blocks, tail = split_complete_blocks("\n\npara\n\n")
    assert blocks == ["\n\npara\n\n"]
    assert tail == ""


def test_crlf_lines():
    blocks, tail = split_complete_blocks("a\r\n\r\nb")
    assert blocks == ["a\r\n\r\n"]
    assert tail == "b"


# -- R-REN-6: fences ---------------------------------------------------------
def test_closed_fence_commits_immediately_without_blank():
    blocks, tail = split_complete_blocks("```py\ncode\n```\nnext")
    assert blocks == ["```py\ncode\n```\n"]
    assert tail == "next"


def test_tilde_fence():
    blocks, tail = split_complete_blocks("~~~\nx\n~~~\n")
    assert blocks == ["~~~\nx\n~~~\n"]
    assert tail == ""


def test_fence_char_mismatch_does_not_close():
    # backticks inside a tilde fence are content
    blocks, tail = split_complete_blocks("~~~\n```\nstill inside\n~~~\n")
    assert blocks == ["~~~\n```\nstill inside\n~~~\n"]
    assert tail == ""


def test_shorter_inner_fence_does_not_close():
    blocks, tail = split_complete_blocks("````\n```\ninside\n````\n")
    assert blocks == ["````\n```\ninside\n````\n"]
    assert tail == ""


def test_longer_close_run_closes():
    # closing run must be >= the opener, per the flat fence grammar
    blocks, tail = split_complete_blocks("```\nx\n````\nrest\n\n")
    assert blocks == ["```\nx\n````\n", "rest\n\n"]
    assert tail == ""


def test_close_candidate_with_info_string_is_content():
    blocks, tail = split_complete_blocks("```\nx\n``` py\n")
    assert blocks == []
    assert tail == "```\nx\n``` py\n"


def test_unclosed_fence_stays_entirely_live():
    blocks, tail = split_complete_blocks("```\nline\nline\nline\n")
    assert blocks == []
    assert tail == "```\nline\nline\nline\n"


def test_backtick_run_with_backtick_info_is_not_a_fence():
    blocks, tail = split_complete_blocks("``` `x` ```\n\n")
    assert blocks == ["``` `x` ```\n\n"]  # a paragraph, blank-terminated
    assert tail == ""


def test_fence_opener_completes_pending_paragraph():
    blocks, tail = split_complete_blocks("intro\n```py\ncode\n```\n")
    assert blocks == ["intro\n", "```py\ncode\n```\n"]
    assert tail == ""


def test_indented_four_spaces_is_not_a_fence_opener():
    blocks, tail = split_complete_blocks("    ```\n    code\n\nplain\n")
    assert blocks == ["    ```\n    code\n\n"]  # an indented-code run
    assert tail == "plain\n"


# -- R-REN-6: tables ---------------------------------------------------------
def test_table_rows_held_until_non_table_line():
    rows = "| a | b |\n| - | - |\n| 1 | 2 |\n"
    assert split_complete_blocks(rows) == ([], rows)


def test_table_commits_when_prose_arrives():
    blocks, tail = split_complete_blocks("| a |\n| - |\ndone\n")
    assert blocks == ["| a |\n| - |\n"]
    assert tail == "done\n"


def test_table_commits_at_blank_line():
    blocks, tail = split_complete_blocks("| a |\n| - |\n\nafter")
    assert blocks == ["| a |\n| - |\n\n"]
    assert tail == "after"


# -- R-REN-6: lists and indented code ----------------------------------------
def test_list_holds_across_blank_lines():
    buf = "- one\n\n- two\n"
    assert split_complete_blocks(buf) == ([], buf)


def test_list_commits_on_non_continuation_after_blank():
    blocks, tail = split_complete_blocks("- one\n\n- two\n\nplain\n")
    assert blocks == ["- one\n\n- two\n\n"]
    assert tail == "plain\n"


def test_ordered_list_holds_numbering_together():
    blocks, tail = split_complete_blocks("1. one\n\n2. two\n\nplain\n")
    assert blocks == ["1. one\n\n2. two\n\n"]
    assert tail == "plain\n"


def test_indented_continuation_stays_in_list():
    blocks, tail = split_complete_blocks("- item\n\n  continuation\n\nplain\n")
    assert blocks == ["- item\n\n  continuation\n\n"]
    assert tail == "plain\n"


def test_nested_fence_inside_list_swallows_blanks():
    buf = "- item\n  ```\nraw\n\nraw2\n  ```\n\nnext para\n\n"
    blocks, tail = split_complete_blocks(buf)
    assert blocks == ["- item\n  ```\nraw\n\nraw2\n  ```\n\n", "next para\n\n"]
    assert tail == ""


def test_column_zero_fence_ends_a_held_list():
    blocks, tail = split_complete_blocks("- item\n\n```\ncode\n```\n")
    assert blocks == ["- item\n\n", "```\ncode\n```\n"]
    assert tail == ""


def test_indented_code_holds_across_blank_lines():
    blocks, tail = split_complete_blocks("    a\n\n    b\n\nplain\n")
    assert blocks == ["    a\n\n    b\n\n"]
    assert tail == "plain\n"


# -- R-REN-6: invariants over a torture corpus -------------------------------
_TORTURE_DOC = (
    "# Report\n"
    "Intro line one\nintro line two\n"
    "\n"
    "```python\ndef f():\n    return '```'\n```\n"
    "A paragraph after the fence.\n"
    "\n"
    "- item one\n"
    "\n"
    "- item two\n"
    "  with continuation\n"
    "\n"
    "closing list paragraph\n"
    "\n"
    "| col a | col b |\n"
    "| ----- | ----- |\n"
    "| 1     | 2     |\n"
    "\n"
    "~~~text\ntilde fence with ``` inside\n~~~\n"
    "final live tail"
)

_CORPUS = [
    "",
    "x",
    "a\n\nb\n\nc\n",
    "```\nnever closes\n",
    "| a |\n| - |\n| 1 |\n",
    "- 1\n\n- 2\n",
    "\n\n\n",
    "``\n`py\nnot a fence\n\n",
    _TORTURE_DOC,
]


def test_concatenation_invariant_over_corpus():
    for buf in _CORPUS:
        blocks, tail = split_complete_blocks(buf)
        assert "".join(blocks) + tail == buf, f"lossy split for {buf!r}"


def _stream(doc: str, size: int) -> tuple[list[str], str]:
    """Feed *doc* in *size*-char chunks, re-buffering only the tail each time."""
    committed: list[str] = []
    buf = ""
    for i in range(0, len(doc), size):
        buf += doc[i : i + size]
        blocks, buf = split_complete_blocks(buf)
        committed.extend(blocks)
    return committed, buf


def test_chunking_invariance_all_cut_points():
    # Any two-chunk split — including cuts landing mid-fence-marker — must
    # commit exactly the blocks a one-shot split commits (prefix stability).
    one_shot = split_complete_blocks(_TORTURE_DOC)
    for cut in range(1, len(_TORTURE_DOC)):
        head, rest = _TORTURE_DOC[:cut], _TORTURE_DOC[cut:]
        blocks_a, tail = split_complete_blocks(head)
        blocks_b, tail = split_complete_blocks(tail + rest)
        assert blocks_a + blocks_b == one_shot[0], f"cut at {cut} diverged"
        assert tail == one_shot[1]


def test_chunking_invariance_char_by_char_and_stride():
    one_shot = split_complete_blocks(_TORTURE_DOC)
    for size in (1, 3, 7):
        assert _stream(_TORTURE_DOC, size) == one_shot


# -- R-REN-6: live_tail_view --------------------------------------------------
def test_live_tail_view_passthrough_without_fence():
    assert live_tail_view("plain tail") == "plain tail"
    assert live_tail_view("") == ""


def test_live_tail_view_trims_partial_close():
    assert live_tail_view("```py\ncode\n``") == "```py\ncode\n"


def test_live_tail_view_trims_full_length_unterminated_close():
    assert live_tail_view("```py\ncode\n```") == "```py\ncode\n"


def test_live_tail_view_trims_indented_partial_close():
    assert live_tail_view("```py\ncode\n  ``") == "```py\ncode\n"


def test_live_tail_view_keeps_code_fragments():
    assert live_tail_view("```py\ncode\nx =") == "```py\ncode\nx ="


def test_live_tail_view_keeps_mismatched_char_fragment():
    # backticks inside a tilde fence can never close it: they are content
    assert live_tail_view("~~~\ncode\n``") == "~~~\ncode\n``"
    assert live_tail_view("~~~\ncode\n~~") == "~~~\ncode\n"


def test_live_tail_view_no_trim_after_fence_closed():
    assert live_tail_view("```\nx\n```\n``") == "```\nx\n```\n``"


# -- R-REN-7: normalize_nested_fences -----------------------------------------
def test_normalize_plain_text_returns_same_object():
    text = "just prose\n\nwith paragraphs"
    assert normalize_nested_fences(text) is text


def test_normalize_simple_fence_unchanged():
    text = "```py\nx = 1\n```"
    assert normalize_nested_fences(text) is text


def test_normalize_sequential_fences_unchanged():
    text = "```py\na\n```\n\n```js\nb\n```"
    assert normalize_nested_fences(text) is text


def test_normalize_upgrades_nested_backticks():
    text = "```markdown\n```python\ncode\n```\n```"
    assert normalize_nested_fences(text) == "````markdown\n```python\ncode\n```\n````"


def test_normalize_upgrades_dangling_outer():
    # the intended outer close never arrived; the opener still upgrades so the
    # whole body renders as content instead of leaking out of the fence
    text = "```markdown\n```python\ncode\n```\n"
    assert normalize_nested_fences(text) == "````markdown\n```python\ncode\n```\n"


def test_normalize_tilde_outer_with_backtick_inner_unchanged():
    text = "~~~\n```python\nx\n```\n~~~"
    assert normalize_nested_fences(text) is text


def test_normalize_upgrades_nested_tildes():
    text = "~~~md\n~~~python\nc\n~~~\n~~~"
    assert normalize_nested_fences(text) == "~~~~md\n~~~python\nc\n~~~\n~~~~"


def test_normalize_preserves_indent_and_info():
    text = "  ```markdown note\n```python\nx\n```\n  ```"
    out = normalize_nested_fences(text)
    assert out.split("\n")[0] == "  ````markdown note"
    assert out.split("\n")[-1] == "  ````"


def test_normalize_bare_nested_open():
    text = "```\n```inner\nx\n```\n```"
    assert normalize_nested_fences(text) == "````\n```inner\nx\n```\n````"


def test_normalize_ignores_mid_line_backticks():
    text = "Use ``` for fences.\n\nAnd `code` inline."
    assert normalize_nested_fences(text) is text


def test_normalize_leaves_already_long_outer_alone():
    text = "````md\n```py\nx\n```\n````"
    assert normalize_nested_fences(text) is text


# -- R-FOLD-2: elide_middle ----------------------------------------------------
def test_elide_under_caps_unchanged():
    assert elide_middle("a\nb\nc", DEFAULT_CAPS) == ("a\nb\nc", "", "")


def test_elide_boundary_prefers_showing_over_marker():
    # head + tail + 1 lines: a marker would replace exactly one line — show it
    caps = ElisionCaps(head_lines=2, tail_lines=1, max_chars=1000)
    text = "a\nb\nc\nd"
    assert elide_middle(text, caps) == (text, "", "")


def test_elide_line_counts_and_marker():
    text = "\n".join(f"line {i}" for i in range(50))
    head, marker, tail = elide_middle(text, SHELL_CAPS)
    assert head == "\n".join(f"line {i}" for i in range(10))
    assert tail == "\n".join(f"line {i}" for i in range(45, 50))
    assert marker == "… +35 lines …"


def test_elide_head_is_prefix_and_tail_is_suffix():
    text = "\n".join(f"row {i} content" for i in range(200))
    head, marker, tail = elide_middle(text, DEFAULT_CAPS)
    assert text.startswith(head)
    assert text.endswith(tail)
    assert marker


def test_elide_char_cap_binds_on_giant_single_line():
    text = "x" * 50_000
    head, marker, tail = elide_middle(text, DEFAULT_CAPS)
    assert len(head) + len(tail) <= DEFAULT_CAPS.max_chars
    assert marker == f"… +{50_000 - len(head) - len(tail)} chars …"
    assert text.startswith(head)
    assert text.endswith(tail)


def test_elide_char_cap_with_few_huge_lines():
    text = ("a" * 3000) + "\n" + ("b" * 3000)
    head, marker, tail = elide_middle(text, DEFAULT_CAPS)
    assert len(head) + len(tail) <= DEFAULT_CAPS.max_chars
    assert "chars …" in marker
    assert text.startswith(head)
    assert text.endswith(tail)


def test_elide_dual_caps_line_cut_then_char_shrink():
    # 30 lines of 300 chars: the line cap trips first, then whole lines are
    # shed to meet the char budget — the marker still names hidden lines
    text = "\n".join(("x" * 300) for _ in range(30))
    head, marker, tail = elide_middle(text, DEFAULT_CAPS)
    assert len(head) + len(tail) <= DEFAULT_CAPS.max_chars
    assert marker.endswith("lines …")
    assert text.startswith(head)
    assert text.endswith(tail)


def test_elide_ansi_laden_output():
    line = "\x1b[31mred error text\x1b[0m padding padding padding"
    text = "\n".join(f"{i} {line}" for i in range(100))
    head, marker, tail = elide_middle(text, SHELL_CAPS)
    assert text.startswith(head)
    assert text.endswith(tail)
    assert marker == "… +85 lines …"


def test_caps_for_tool_classes():
    assert caps_for_tool("bash") is SHELL_CAPS
    assert caps_for_tool("test") is SHELL_CAPS
    assert caps_for_tool("search") is QUIET_CAPS
    assert caps_for_tool("read") is QUIET_CAPS
    assert caps_for_tool("unknown_tool") is DEFAULT_CAPS
