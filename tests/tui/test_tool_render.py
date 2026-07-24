"""Per-tool call rendering (R-REN-5).

``chimera.tui.tool_render`` is stdlib-only, so the dispatch table runs in CI's
no-``tui``-extra posture; the rendering half needs rich and is guarded.
"""
from __future__ import annotations

import pytest

from chimera.tui.tool_render import (
    DEFAULT_RENDERER,
    TOOL_RENDERERS,
    call_row,
    is_block_tool,
    renderer_for,
    short_value,
    summarize_args,
    tool_verb,
)


# -- dispatch table -------------------------------------------------------
def test_each_class_has_its_own_glyph():
    icons = {
        name: renderer_for(name).icon
        for name in ("bash", "read", "write", "search", "delegate", "web_fetch", "think")
    }
    assert icons["bash"] == "$"
    assert icons["read"] == "→"
    assert icons["write"] == "←"
    assert icons["search"] == "✱"
    assert icons["delegate"] == "↳"
    assert len(set(icons.values())) == len(icons), "glyphs must be distinguishable"


def test_unknown_tools_keep_the_historical_glyph_and_preview():
    assert renderer_for("totally_unknown") is DEFAULT_RENDERER
    icon, verb, summary = call_row("totally_unknown", {"a": 1, "b": 2, "c": 3, "d": 4})
    assert icon == "⚙"
    assert verb == "totally_unknown"
    assert summary == "a=1, b=2, c=3"  # first three, exactly as before


def test_identity_is_never_lost_inside_a_class():
    # test/git/bash share the shell glyph but keep their own names.
    assert {tool_verb(n) for n in ("bash", "test", "git")} == {"bash", "test", "git"}
    assert {renderer_for(n).icon for n in ("bash", "test", "git")} == {"$"}


def test_block_classification():
    assert is_block_tool("bash") and is_block_tool("delegate") and is_block_tool("web_fetch")
    assert not is_block_tool("read")
    assert not is_block_tool("search")
    assert not is_block_tool("edit")
    assert not is_block_tool("unknown_tool")


def test_mcp_names_lose_the_plumbing_and_resolve_on_the_tool_half():
    assert tool_verb("mcp__github__search") == "search"
    assert renderer_for("mcp__github__search").icon == "✱"
    assert summarize_args("mcp__github__search", {"pattern": "todo"}) == '[github] "todo"'
    assert tool_verb("mcp__server") == "server"


# -- argument summaries ---------------------------------------------------
def test_shell_summary_is_the_command():
    assert summarize_args("bash", {"command": "pytest -q"}) == "pytest -q"
    assert summarize_args("test", {"cmd": "make check"}) == "make check"
    # newlines collapse; long commands truncate
    assert "\n" not in summarize_args("bash", {"command": "a\nb"})
    assert summarize_args("bash", {"command": "x" * 200}).endswith("…")


def test_read_summary_carries_the_line_range():
    assert summarize_args("read", {"path": "a/b.py"}) == "a/b.py"
    assert summarize_args("read", {"path": "a.py", "start_line": 12, "end_line": 40}) == "a.py:12-40"
    assert summarize_args("read", {"file_path": "a.py", "offset": 5}) == "a.py:5"


def test_search_summary_quotes_the_pattern_and_names_the_scope():
    assert summarize_args("search", {"pattern": "TODO"}) == '"TODO"'
    assert summarize_args("search", {"query": "TODO", "path": "chimera/"}) == '"TODO" in chimera/'
    assert summarize_args("list_files", {"path": "src"}) == "src"


def test_delegate_summary_names_the_agent_and_the_task():
    assert summarize_args("delegate", {"agent": "explore", "task": "map the repo"}) == (
        "explore · map the repo"
    )
    assert summarize_args("task", {"prompt": "do it"}) == "do it"


def test_summaries_fall_back_to_the_generic_preview():
    # A shell-class tool called with an unexpected shape still says something.
    assert summarize_args("bash", {"weird": "shape"}) == "weird=shape"
    assert summarize_args("read", {}) == ""
    assert summarize_args("read", None) == ""


def test_short_value():
    assert short_value("abc") == "abc"
    assert short_value("x" * 50) == "x" * 39 + "…"
    assert short_value("a\nb") == "a b"


def test_registry_is_consistent():
    for name, renderer in TOOL_RENDERERS.items():
        assert renderer.icon, name
        assert renderer.summary in (
            "shell", "path", "search", "delegate", "web", "text", "default",
        ), name


# -- rendering ------------------------------------------------------------
pytest.importorskip("rich")

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.render import LaneTranscript, format_event  # noqa: E402
from chimera.types import ToolCall  # noqa: E402


class _Res:
    def __init__(self, output, success=True):
        self.output = output
        self.success = success


def _use(name, args):
    return LoopEvent(LoopEventType.tool_use, ToolCall(id="1", name=name, arguments=args), 0)


def _result(name, output, success=True):
    return LoopEvent(
        LoopEventType.tool_result,
        (ToolCall(id="1", name=name, arguments={}), _Res(output, success)),
        0,
    )


def test_persistence_path_is_byte_identical_by_default():
    # format_event's default is the historical generic row: Lane.record relies
    # on it, so a persisted transcript never changes shape.
    out = format_event(_use("bash", {"command": "ls"}), [])
    assert out[0].plain == "⚙ bash(command=ls)"


def test_display_path_uses_the_per_tool_grammar():
    out = format_event(_use("bash", {"command": "pytest -q"}), [], tool_grammar=True)
    assert out[0].plain == "$ bash pytest -q"
    out = format_event(_use("read", {"path": "a.py", "start_line": 1, "end_line": 9}),
                       [], tool_grammar=True)
    assert out[0].plain == "→ read a.py:1-9"


def test_transcript_turns_the_grammar_on_by_default():
    sink: list = []
    LaneTranscript(sink.append, markdown=False).handle(_use("search", {"pattern": "x"}))
    assert sink[0].plain == '✱ search "x"'
    sink.clear()
    LaneTranscript(sink.append, markdown=False, tool_grammar=False).handle(
        _use("search", {"pattern": "x"}),
    )
    assert sink[0].plain == "⚙ search(pattern=x)"


def test_block_tools_render_their_output_as_a_card():
    out = format_event(_result("bash", "line one\nline two"), [], tool_grammar=True)
    assert out[0].plain == "│ line one\n│ line two"


def test_inline_tools_render_output_without_a_card():
    out = format_event(_result("read", "line one\nline two"), [], tool_grammar=True)
    assert out[0].plain == "line one\nline two"


def test_card_keeps_the_elision_marker_and_its_affordance():
    body = "\n".join(f"line {i}" for i in range(60))
    out = format_event(
        _result("bash", body), [], tool_grammar=True, elide=True, expand_hint="ctrl+x",
    )
    lines = out[0].plain.split("\n")
    assert all(line.startswith("│ ") for line in lines)
    assert any("expands" in line for line in lines)
    assert lines[0] == "│ line 0" and lines[-1] == "│ line 59"


def test_failed_card_uses_the_error_slot():
    out = format_event(_result("bash", "boom", success=False), [], tool_grammar=True)
    styles = {str(span.style) for span in out[0].spans}
    assert "red" in styles
