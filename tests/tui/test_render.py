"""Behavioral tests for the shared renderer's Phase-1 streaming discipline.

R-REN-6 progressive block commitment through :class:`LaneTranscript`, R-REN-7
normalization at the renderable boundary, and the R-FOLD-2/3 display/persist
split for tool output (display elides, ``Lane.record`` keeps full output).
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("rich")

from rich.markdown import Markdown  # noqa: E402
from rich.text import Text  # noqa: E402

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.markdown_stream import live_tail_view  # noqa: E402
from chimera.tui.render import (  # noqa: E402
    LaneTranscript,
    assistant_renderable,
    format_event,
    plain,
)
from chimera.types import ToolCall  # noqa: E402


def _chunk(text):
    return LoopEvent(LoopEventType.assistant_chunk, text, 0)


def _assistant(content=""):
    return LoopEvent(LoopEventType.assistant, SimpleNamespace(content=content), 0)


def _think(text):
    return LoopEvent(LoopEventType.thinking_chunk, text, 0)


def _tool_result(name, output, ok=True):
    return LoopEvent(
        LoopEventType.tool_result,
        (ToolCall("1", name, {}), SimpleNamespace(output=output, success=ok)),
        0,
    )


def _markups(sink):
    return [r.markup for r in sink if isinstance(r, Markdown)]


DOC = "First paragraph.\n\nSecond paragraph.\n\n```py\nx = 1\n```\nFinal tail line"


# -- R-REN-6: progressive commitment -----------------------------------------
def test_completed_blocks_reach_sink_before_turn_end():
    sink: list = []
    t = LaneTranscript(sink.append)
    for i in range(0, len(DOC), 5):
        t.handle(_chunk(DOC[i : i + 5]))
    committed_early = _markups(sink)
    assert len(committed_early) >= 3  # two paragraphs + the closed fence
    t.handle(_assistant(DOC))
    assert "".join(plain(r) for r in sink) == DOC


def test_streamed_source_equals_batch_render_source():
    stream_sink: list = []
    t = LaneTranscript(stream_sink.append)
    for i in range(0, len(DOC), 7):
        t.handle(_chunk(DOC[i : i + 7]))
    t.commit()

    batch_sink: list = []
    LaneTranscript(batch_sink.append).handle(_assistant(DOC))

    assert "".join(plain(r) for r in stream_sink) == DOC
    assert [plain(r) for r in batch_sink] == [DOC]


def test_no_duplicate_when_stream_fully_committed():
    # every block completes before the assistant event; its content fallback
    # must not re-render the message
    doc = "alpha\n\nbeta\n\n"
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_chunk(doc))
    assert _markups(sink) == ["alpha\n\n", "beta\n\n"]
    t.handle(_assistant(doc))
    assert _markups(sink) == ["alpha\n\n", "beta\n\n"]
    assert "".join(plain(r) for r in sink) == doc


def test_blank_spacer_between_committed_blocks():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_chunk("alpha\n\nbeta\n\n"))
    kinds = [type(r) for r in sink]
    assert kinds == [Markdown, Text, Markdown]
    assert plain(sink[1]) == ""  # spacer is invisible to the source invariant


def test_unclosed_fence_streams_nothing_until_commit():
    sink: list = []
    t = LaneTranscript(sink.append)
    for piece in ("```py\n", "a = 1\n", "b = 2\n"):
        t.handle(_chunk(piece))
    assert sink == []  # a fence that never closes stays live
    t.commit()
    assert _markups(sink) == ["```py\na = 1\nb = 2\n"]


def test_chunk_boundary_mid_fence_marker():
    sink: list = []
    t = LaneTranscript(sink.append)
    for piece in ("Intro\n\n``", "`py\nx = 1\n``", "`\nAfter.\n\n"):
        t.handle(_chunk(piece))
    t.handle(_assistant())
    assert _markups(sink) == ["Intro\n\n", "```py\nx = 1\n```\n", "After.\n\n"]


def test_table_rows_held_until_complete():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_chunk("| a | b |\n"))
    t.handle(_chunk("| - | - |\n"))
    t.handle(_chunk("| 1 | 2 |\n"))
    assert sink == []  # a new row reshapes columns: hold the whole table
    t.handle(_chunk("\ndone\n\n"))
    assert _markups(sink) == ["| a | b |\n| - | - |\n| 1 | 2 |\n\n", "done\n\n"]


def test_interleaved_thinking_and_assistant_chunks():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_think("step 1"))
    t.handle(_chunk("part one\n\n"))
    t.handle(_think("step 2"))
    t.handle(_chunk("part two\n\n"))
    t.commit()
    texts = [plain(r) for r in sink]
    assert sum("thought for" in x for x in texts) == 2  # the R-FOLD-1 traces
    assert _markups(sink) == ["part one\n\n", "part two\n\n"]
    assert t.reveal_last() is True


def test_state_resets_between_streams():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_chunk("first turn\n\n"))
    t.handle(_assistant())
    t.handle(_chunk("second turn\n\n"))
    t.handle(_assistant())
    assert _markups(sink) == ["first turn\n\n", "second turn\n\n"]
    assert t.live_tail == ""


def test_live_tail_property_exposes_uncommitted_text():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_chunk("done\n\n```py\ncode\n``"))
    assert _markups(sink) == ["done\n\n"]
    assert t.live_tail == "```py\ncode\n``"
    # the pane layer renders it shrink-free via live_tail_view
    assert live_tail_view(t.live_tail) == "```py\ncode\n"


# -- R-REN-7: normalization at the renderable boundary ------------------------
def test_nested_fences_normalized_for_display():
    nested = "```markdown\n```python\ncode\n```\n```"
    r = assistant_renderable(nested, markdown=True)
    assert isinstance(r, Markdown)
    assert r.markup == "````markdown\n```python\ncode\n```\n````"


def test_plain_renderable_keeps_original_source():
    nested = "```markdown\n```python\ncode\n```\n```"
    r = assistant_renderable(nested, markdown=False)
    assert isinstance(r, Text)
    assert plain(r) == nested  # persistence path: never rewritten


# -- R-FOLD-2/3: tool output display elision vs full persistence --------------
def _long_output(lines=200):
    return "\n".join(f"line {i:04d} " + "x" * 20 for i in range(lines))


def test_display_sink_elides_tool_output_head_and_tail():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_tool_result("bash", _long_output(100)))
    [r] = sink
    text = plain(r)
    assert text.startswith("line 0000")
    assert text.endswith("line 0099 " + "x" * 20)
    assert "… +85 lines …" in text  # shell class: 10 head + 5 tail
    assert "line 0050" not in text


def test_elision_marker_is_dim_and_output_keeps_status_color():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_tool_result("bash", _long_output(100), ok=False))
    [r] = sink
    styles = {str(span.style) for span in r.spans}
    assert "dim" in styles
    assert "red" in styles


def test_quiet_tools_elide_tighter_than_shell():
    sink_a: list = []
    sink_b: list = []
    LaneTranscript(sink_a.append).handle(_tool_result("bash", _long_output(100)))
    LaneTranscript(sink_b.append).handle(_tool_result("search", _long_output(100)))
    assert len(plain(sink_b[0])) < len(plain(sink_a[0]))
    assert "… +95 lines …" in plain(sink_b[0])  # quiet class: 3 head + 2 tail


def test_short_tool_output_untouched_in_display():
    sink: list = []
    t = LaneTranscript(sink.append)
    t.handle(_tool_result("bash", "ok\ndone"))
    assert plain(sink[0]) == "ok\ndone"


def test_format_event_default_keeps_full_output():
    # persistence callers do not opt in to elision: full text, no marker
    out = format_event(_tool_result("bash", _long_output(200)), [])
    assert plain(out[0]) == _long_output(200)


def test_elide_is_a_mutable_display_toggle():
    # R-FOLD-2's global expand toggle flips this attribute live; it applies
    # to tool results rendered afterwards (committed output re-renders with
    # the transcript overlay, R-FOLD-7, a later wave).
    sink: list = []
    t = LaneTranscript(sink.append)
    assert t.elide is True  # collapsed by default, same as before
    t.elide = False
    t.handle(_tool_result("bash", _long_output(100)))
    assert plain(sink[0]) == _long_output(100)  # expanded: full output
    t.elide = True
    t.handle(_tool_result("bash", _long_output(100)))
    assert "… +85 lines …" in plain(sink[1])    # collapsed again


def test_expand_hint_names_the_injected_key_on_the_marker():
    # The frontend injects its currently-bound expand key (R-KEY-3); the
    # renderer never hardcodes one.
    sink: list = []
    t = LaneTranscript(sink.append, expand_hint="ctrl+x")
    t.handle(_tool_result("bash", _long_output(100)))
    assert "… +85 lines … (ctrl+x expands)" in plain(sink[0])


def test_no_expand_hint_keeps_the_bare_marker():
    sink: list = []
    t = LaneTranscript(sink.append)  # no hint injected
    t.handle(_tool_result("bash", _long_output(100)))
    text = plain(sink[0])
    assert "… +85 lines …" in text and "expands" not in text


def test_format_event_expand_hint_only_applies_when_eliding():
    out = format_event(
        _tool_result("bash", _long_output(200)), [], expand_hint="ctrl+x",
    )
    assert "expands" not in plain(out[0])  # elide off: no marker, no hint


def test_lane_record_persists_full_tool_output():
    # the recorded transcript is the session record: display caps never apply
    lane = Lane(LaneConfig("A", "A", "glm-5.2"), driver=SimpleNamespace())
    lane.record(_tool_result("bash", _long_output(200)))
    text = lane.transcript_text()
    assert "line 0100" in text  # middle intact (old fixed chop cut this)
    assert "line 0199" in text
    assert "…" not in text


def test_lane_record_stream_text_identical_to_before():
    # Lane.record still accumulates chunks and drains once at the assistant
    # event — recorded text is unchanged by progressive display commitment
    lane = Lane(LaneConfig("A", "A", "glm-5.2"), driver=SimpleNamespace())
    for i in range(0, len(DOC), 5):
        lane.record(_chunk(DOC[i : i + 5]))
    lane.record(_assistant(DOC))
    assert lane.transcript_text() == DOC
