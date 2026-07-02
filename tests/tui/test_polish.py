"""Tests for the Phase-3 polish set: reasoning display (§13.4), the thinking
event path, the per-lane sidebar (§13.7), and richer diffs (§13.8)."""
from types import SimpleNamespace

import pytest

pytest.importorskip("rich")

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.render import LaneTranscript, plain  # noqa: E402
from chimera.tui.results import split_diff_files, split_rows  # noqa: E402
from chimera.types import ToolCall  # noqa: E402


# -- 13.4: thinking event path -------------------------------------------
def test_anthropic_mapper_surfaces_thinking_delta():
    from chimera.providers.anthropic import AnthropicProvider

    event = SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="thinking_delta", thinking="let me reason"),
    )
    out = list(AnthropicProvider._map_anthropic_event(event, None, None, ""))
    assert len(out) == 1
    assert out[0].type == "thinking_delta"
    assert out[0].content == "let me reason"


@pytest.mark.asyncio
async def test_agent_loop_forwards_thinking_chunks():
    from chimera.core.agent_loop import AgentLoop
    from chimera.providers.base import StreamEvent
    from chimera.types import Message

    class FakeProvider:
        model_name = "glm-5.2"

        async def async_stream(self, messages, tools=None, **kw):
            yield StreamEvent(type="thinking_delta", content="pondering… ")
            yield StreamEvent(type="thinking_delta", content="done pondering")
            yield StreamEvent(type="text_delta", content="the answer")
            yield StreamEvent(type="done", usage={"input_tokens": 1, "output_tokens": 2})

    events = []
    async for ev in AgentLoop().run(
        messages=[Message.user("q")],
        tools=[],
        provider=FakeProvider(),
        system_prompt="s",
        max_turns=1,
        stream=True,
    ):
        events.append(ev)

    thinking = [e for e in events if e.type == LoopEventType.thinking_chunk]
    assert [t.data for t in thinking] == ["pondering… ", "done pondering"]
    # reasoning never leaks into the assistant content
    assistant = next(e for e in events if e.type == LoopEventType.assistant)
    assert "pondering" not in (getattr(assistant.data, "content", "") or "")


# -- 13.4: render side ----------------------------------------------------
def _think_ev(text):
    return LoopEvent(LoopEventType.thinking_chunk, text, 0)


def _assistant_ev(text):
    return LoopEvent(LoopEventType.assistant, SimpleNamespace(content=text), 0)


def test_lane_transcript_collapses_reasoning_by_default():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_think_ev("step 1… "))
    t.handle(_think_ev("step 2"))
    assert sink == []  # buffered until a boundary
    t.handle(_assistant_ev("answer"))
    texts = [plain(r) for r in sink]
    assert any("reasoning hidden (14 chars)" in p for p in texts)
    assert any("answer" in p for p in texts)
    assert not any("step 1" in p for p in texts)  # collapsed
    # reveal_last prints the hidden block on demand
    assert t.reveal_last() is True
    assert any("step 1" in plain(r) for r in sink)


def test_lane_transcript_shows_reasoning_when_toggled():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.show_reasoning = True
    t.handle(_think_ev("visible thought"))
    t.handle(_assistant_ev("answer"))
    assert any("∴ visible thought" in plain(r) for r in sink)


# -- markdown rendering (display sinks only) --------------------------------
def test_assistant_prose_renders_as_markdown_in_display_sink():
    from rich.markdown import Markdown

    sink: list = []
    t = LaneTranscript(sink.append)  # display sink: markdown on by default
    t.handle(LoopEvent(LoopEventType.assistant_chunk, "# Title\n**bold**", 0))
    t.handle(_assistant_ev("x"))
    assert any(isinstance(r, Markdown) for r in sink)
    assert any("# Title" in plain(r) for r in sink)  # source recoverable


def test_tool_output_stays_literal_even_with_markdown_on():
    from rich.markdown import Markdown

    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(LoopEvent(
        LoopEventType.tool_result,
        (ToolCall("1", "read_file", {}), SimpleNamespace(output="**raw file**", success=True)),
        0,
    ))
    assert not any(isinstance(r, Markdown) for r in sink)
    assert any("**raw file**" in plain(r) for r in sink)


def test_persisted_transcript_stays_plain_markdown_source():
    lane = Lane(LaneConfig("A", "A", "glm-5.2"), driver=SimpleNamespace())
    lane.record(LoopEvent(LoopEventType.assistant_chunk, "## Head\n`code`", 0))
    lane.record(_assistant_ev("x"))
    text = lane.transcript_text()
    assert "## Head" in text and "`code`" in text  # raw source, not objects


def test_lane_transcript_commit_flushes_pending_reasoning():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_think_ev("tail thought"))
    t.commit()
    assert any("reasoning hidden" in getattr(r, "plain", "") for r in sink)
    assert t.reveal_last() is True


def test_lane_record_keeps_reasoning_out_of_persisted_transcript():
    lane = Lane(LaneConfig("A", "A", "glm-5.2"), driver=SimpleNamespace())
    lane.record(_think_ev("secret reasoning"))
    lane.record(_assistant_ev("public answer"))
    text = lane.transcript_text()
    assert "public answer" in text
    assert "secret reasoning" not in text


# -- 13.7: lane tool log ---------------------------------------------------
def test_lane_tool_log_tracks_calls_and_outcomes():
    lane = Lane(LaneConfig("A", "A", "glm-5.2"), driver=SimpleNamespace())
    lane.record(LoopEvent(LoopEventType.tool_use, ToolCall("1", "read_file", {}), 0))
    assert lane.tool_log == [("read_file", None)]  # in flight
    lane.record(LoopEvent(
        LoopEventType.tool_result,
        (ToolCall("1", "read_file", {}), SimpleNamespace(output="x", success=True)), 0,
    ))
    assert lane.tool_log == [("read_file", True)]
    lane.record(LoopEvent(LoopEventType.tool_use, ToolCall("2", "bash", {}), 0))
    lane.record(LoopEvent(
        LoopEventType.tool_result,
        (ToolCall("2", "bash", {}), SimpleNamespace(output="err", success=False)), 0,
    ))
    assert lane.tool_log == [("read_file", True), ("bash", False)]


@pytest.mark.asyncio
async def test_sidebar_toggle_and_narrow_autohide():
    pytest.importorskip("textual")
    from chimera.tui.cohort import Cohort
    from chimera.tui.multiplex import MultiplexApp

    lanes = [
        Lane(LaneConfig("A", "m1", "m1"), SimpleNamespace(history=[]), None),
        Lane(LaneConfig("B", "m2", "m2"), SimpleNamespace(history=[]), None),
    ]
    lanes[0].tool_log.append(("read_file", True))
    co = Cohort(lanes, task="x")
    app = MultiplexApp(co)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert app.query_one("#sidebar").display is False  # off by default
        app.action_toggle_sidebar()
        await pilot.pause()
        assert app.query_one("#sidebar").display is True   # wide terminal: shown

    app2 = MultiplexApp(Cohort(
        [Lane(LaneConfig("A", "m1", "m1"), SimpleNamespace(history=[]), None)], task="x",
    ))
    async with app2.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        app2.action_toggle_sidebar()
        await pilot.pause()
        assert app2.query_one("#sidebar").display is False  # narrow: auto-hidden


# -- 13.8: diff helpers ------------------------------------------------------
DIFF = """diff --git a/calc.py b/calc.py
index 000..111 100644
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+print(1)
"""


def test_split_diff_files_by_git_header():
    files = split_diff_files(DIFF)
    assert [f[0] for f in files] == ["calc.py", "new.py"]
    assert "return a + b" in files[0][1]
    assert "print(1)" in files[1][1]


def test_split_diff_files_non_git_fallback():
    files = split_diff_files("just a summary\nmodified: f.txt")
    assert len(files) == 1 and files[0][0] == "(changes)"


def test_split_rows_pairs_changes():
    file_diff = split_diff_files(DIFF)[0][1]
    rows = split_rows(file_diff)
    kinds = [r[0] for r in rows]
    assert "meta" in kinds and "ctx" in kinds and "change" in kinds
    change = next(r for r in rows if r[0] == "change")
    assert change[1] == "    return a - b"   # old on the left
    assert change[2] == "    return a + b"   # new on the right


def test_split_rows_unbalanced_runs():
    rows = split_rows("@@ -1 +1,2 @@\n-old\n+new1\n+new2")
    changes = [r for r in rows if r[0] == "change"]
    assert changes == [("change", "old", "new1"), ("change", "", "new2")]


# -- 13.8: screen mode/file navigation ---------------------------------------
@pytest.mark.asyncio
async def test_results_screen_file_nav_and_split_toggle():
    pytest.importorskip("textual")
    from textual.app import App
    from textual.widgets import RichLog

    from chimera.tui.cohort import Cohort
    from chimera.tui.results import ResultsScreen

    ws = SimpleNamespace(path="/tmp/x", strategy="worktree", base_commit="abc",
                         branch="b", diff=lambda: DIFF)
    lane = Lane(LaneConfig("A", "glm-5.2", "glm-5.2"), SimpleNamespace(history=[]), ws)
    lane.on_turn_begin()
    lane.record(LoopEvent(
        LoopEventType.result,
        SimpleNamespace(reason="completed", turn_count=1, cost_usd=0.001,
                        usage={}, messages=[]), 0,
    ))
    lane.on_turn_end(order=1)
    co = Cohort([lane], task="x")

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(ResultsScreen(co))

    app = _Host()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ResultsScreen)
        assert screen._file_idx == 0
        header = str(screen.query_one("#diff-header").render())
        assert "file 1/2: calc.py" in header and "[unified]" in header

        screen.action_next_file()
        await pilot.pause()
        header = str(screen.query_one("#diff-header").render())
        assert "file 2/2: new.py" in header

        screen.action_toggle_split()
        await pilot.pause()
        header = str(screen.query_one("#diff-header").render())
        assert "[split]" in header
        assert screen.query_one("#diff-body", RichLog).lines
