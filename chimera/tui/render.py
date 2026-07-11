"""Shared ``LoopEvent`` → transcript rendering for the Chimera TUIs.

Both the single-agent TUI and the multiplexer render an :class:`AgentDriver`
event stream into a scrolling transcript that must *look* identical. This module
holds that rendering as a small, sink-agnostic helper so the two frontends can't
drift.

The single-agent :class:`~chimera.tui.app.ChimeraTUI` predates this module and
keeps its own inline copy (it is intentionally left untouched — Phase 2 is
purely additive); the multiplexer's per-lane panes and the persisted transcript
both go through :class:`LaneTranscript` here.

Design notes:

- ``assistant_chunk`` text streams through incremental block commitment
  (R-REN-6): completed top-level markdown blocks flush to the sink the moment
  they finish and only the live tail stays buffered. This keeps the original
  anti-flicker rule (§5.2) intact — the sink is append-only, so nothing is
  written until it can never change — while long answers appear progressively
  instead of all at turn end. ``format_event`` itself keeps the accumulate-
  then-drain contract, so persistence callers record identical text.
- Nested fences are normalized before markdown rendering (R-REN-7).
- Tool output display uses head+tail elision with per-tool-class caps
  (R-FOLD-2). Display-only: persistence callers get the full output
  (R-FOLD-3) because ``elide`` defaults off.
- ``tool_progress`` is intentionally dropped: it is ephemeral and must never be
  persisted (§3.1 ephemerality guarantee).
- Dynamic agent/tool text is wrapped in :class:`rich.text.Text` so markup-
  significant characters render literally (§5.2 content safety).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.markdown import Markdown
from rich.text import Text

from chimera.core.loop_events import LoopEventType
from chimera.tui.markdown_stream import (
    caps_for_tool,
    elide_middle,
    normalize_nested_fences,
    split_complete_blocks,
)

__all__ = ["LaneTranscript", "format_event", "assistant_renderable", "plain", "short"]


def short(value: Any, limit: int = 40) -> str:
    """Collapse a value to a single truncated line for an argument preview."""
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def plain(renderable: Any) -> str:
    """Best-effort plain-text of a renderable (for the persisted transcript)."""
    if hasattr(renderable, "plain"):
        return str(renderable.plain)
    if isinstance(renderable, Markdown):
        return str(renderable.markup)  # the original source text
    return str(renderable)


def assistant_renderable(text: str, *, markdown: bool = False) -> Any:
    """Assistant prose as a renderable: rich ``Markdown`` when *markdown* is on
    (headers, bold, syntax-highlighted code fences), plain ``Text`` otherwise.

    Only assistant prose is ever markdown-rendered — tool output, user echo,
    and reasoning stay literal (§5.2 content safety: file contents and command
    output must never be reinterpreted). Fences nested inside fences are
    normalized first so inner markers render as content (R-REN-7). Falls back
    to plain text if parsing fails.
    """
    if markdown:
        try:
            return Markdown(normalize_nested_fences(text))
        except Exception:  # noqa: BLE001 - display fallback, never fail a turn
            return Text(text)
    return Text(text)


def format_event(
    ev: Any, chunks: list[str], *, markdown: bool = False, elide: bool = False,
) -> list[Any]:
    """Render one loop event to zero or more renderables, in order.

    ``chunks`` is the caller's streaming-text accumulator: this appends
    ``assistant_chunk`` fragments to it and drains it (committing one assistant
    item) when the block completes. Returning a list keeps the function pure and
    testable — the caller decides where the renderables go. With ``markdown``
    on, committed assistant prose renders as rich Markdown (display sinks);
    persistence callers keep the default plain text.

    ``elide`` applies head+tail elision with per-tool-class caps to tool output
    (R-FOLD-2). It is display-only and defaults off so persistence callers
    (``Lane.record``) keep the full output in the session record (R-FOLD-3).
    """
    t = ev.type
    out: list[Any] = []
    if t == LoopEventType.assistant_chunk:
        chunks.append(str(ev.data))
    elif t == LoopEventType.assistant:
        if chunks:
            out.append(assistant_renderable("".join(chunks), markdown=markdown))
            chunks.clear()
        else:
            content = getattr(ev.data, "content", "") or ""
            if content.strip():
                out.append(assistant_renderable(content, markdown=markdown))
    elif t == LoopEventType.tool_use:
        tc = ev.data
        args = getattr(tc, "arguments", {}) or {}
        preview = ", ".join(f"{k}={short(v)}" for k, v in list(args.items())[:3])
        out.append(Text.assemble(
            ("⚙ ", "yellow"), (str(getattr(tc, "name", "?")), "bold yellow"),
            (f"({preview})", "dim"),
        ))
    elif t == LoopEventType.tool_result:
        call, result = ev.data if isinstance(ev.data, tuple) else (None, ev.data)
        text = (getattr(result, "output", "") or "").rstrip()
        if text:
            ok = getattr(result, "success", True)
            style = "green" if ok else "red"
            marker = ""
            if elide:
                caps = caps_for_tool(str(getattr(call, "name", "") or ""))
                head, marker, tail = elide_middle(text, caps)
            if marker:
                styled = Text()
                styled.append(head, style=style)
                styled.append("\n")
                styled.append(marker, style="dim")
                if tail:
                    styled.append("\n")
                    styled.append(tail, style=style)
                out.append(styled)
            else:
                out.append(Text(text, style=style))
    elif t == LoopEventType.error:
        out.append(Text(f"error: {ev.data}", style="red"))
    elif t == LoopEventType.compact_boundary:
        out.append(Text("… [context compacted]", style="dim"))
    elif t == LoopEventType.result:
        r = ev.data
        reason = getattr(r, "reason", "") or ""
        out.append(Text(
            f"· {getattr(r, 'turn_count', 0)} steps · "
            f"${float(getattr(r, 'cost_usd', 0) or 0):.4f}"
            + (f" · {reason}" if reason else ""),
            style="dim",
        ))
    elif t == LoopEventType.system and ev.data:
        out.append(Text(str(ev.data), style="dim"))
    return out


class LaneTranscript:
    """Stateful adapter: feed it events, it writes renderables to a sink.

    ``sink`` is any callable taking one renderable — e.g. ``RichLog.write`` for a
    live pane, or ``list.append`` in a test. Streaming assistant text is
    accumulated internally so the sink never has to know about the commit rule:
    completed top-level markdown blocks commit as they finish (R-REN-6) and only
    the live tail — exposed as :attr:`live_tail` for frontends that render a
    live region — stays buffered until the block ends.

    Reasoning (``thinking_chunk``) is accumulated separately and committed as a
    dim block when the next non-thinking content arrives (§13.4). Default is
    **collapsed**: a one-line marker with the size; set :attr:`show_reasoning`
    (the frontends bind a toggle key) to render the text, and
    :meth:`reveal_last` prints the most recent hidden block on demand.
    """

    def __init__(self, sink: Callable[[Any], object], *, markdown: bool = True) -> None:
        self._sink = sink
        self._chunks: list[str] = []
        self._think: list[str] = []
        self._last_thinking = ""
        self._streamed = False  # saw assistant_chunk since the last flush
        self._blocks_in_stream = 0  # committed blocks since the last flush
        self.show_reasoning = False
        self.markdown = markdown

    @property
    def live_tail(self) -> str:
        """Uncommitted streaming text — the live tail of the current block.

        Frontends that render a live region should display it via
        :func:`chimera.tui.markdown_stream.live_tail_view`, which trims a
        partial closing fence so the region never shrinks (R-REN-6). The tail
        is display-state only; it flushes to the sink at block end.
        """
        return "".join(self._chunks)

    def handle(self, ev: Any) -> None:
        """Render one event, writing any produced renderables to the sink."""
        t = getattr(ev, "type", None)
        if t == LoopEventType.thinking_chunk:
            self._think.append(str(ev.data))
            return
        if self._think:
            self._commit_thinking()
        if t == LoopEventType.assistant_chunk:
            self._streamed = True
            self._chunks.append(str(ev.data))
            blocks, tail = split_complete_blocks("".join(self._chunks))
            if blocks:
                self._chunks[:] = [tail] if tail else []
                for block in blocks:
                    self._commit_block(block)
            return
        if t == LoopEventType.assistant:
            self._flush_stream(fallback=ev.data)
            return
        for renderable in format_event(ev, self._chunks, markdown=self.markdown, elide=True):
            self._sink(renderable)

    def _commit_block(self, text: str) -> None:
        """Write one completed assistant block, with a blank line between blocks.

        A single markdown render separates its blocks with one blank line;
        committing block-by-block would lose it, so a blank spacer renderable
        precedes every block after the first. The spacer's plain text is empty,
        keeping concatenated sink output equal to the streamed source.
        """
        if self._blocks_in_stream:
            self._sink(Text(""))
        self._sink(assistant_renderable(text, markdown=self.markdown))
        self._blocks_in_stream += 1

    def _flush_stream(self, fallback: Any = None) -> None:
        """Flush the live tail; fall back to full message content when nothing
        streamed (non-streaming providers emit only the ``assistant`` event)."""
        if self._chunks:
            self._commit_block("".join(self._chunks))
            self._chunks.clear()
        elif not self._streamed and fallback is not None:
            content = getattr(fallback, "content", "") or ""
            if content.strip():
                self._commit_block(content)
        self._streamed = False
        self._blocks_in_stream = 0

    def _commit_thinking(self) -> None:
        block = "".join(self._think).strip()
        self._think.clear()
        if not block:
            return
        self._last_thinking = block
        if self.show_reasoning:
            self._sink(Text(f"∴ {block}", style="dim italic"))
        else:
            self._sink(Text(
                f"∴ reasoning hidden ({len(block)} chars) — toggle to show",
                style="dim",
            ))

    def reveal_last(self) -> bool:
        """Print the most recent reasoning block; True if there was one."""
        if not self._last_thinking:
            return False
        self._sink(Text(f"∴ {self._last_thinking}", style="dim italic"))
        return True

    def commit(self) -> None:
        """Flush any un-committed streaming text (call on turn end / cancel)."""
        if self._think:
            self._commit_thinking()
        self._flush_stream()

    def note(self, text: str, style: str = "dim") -> None:
        """Write a frontend-originated line (steer marker, echo, etc.)."""
        self._sink(Text(text, style=style))
