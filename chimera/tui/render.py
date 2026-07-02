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

- ``assistant_chunk`` text is accumulated and committed as a single item when
  the block completes (``assistant`` / turn end) — the anti-flicker rule (§5.2).
- ``tool_progress`` is intentionally dropped: it is ephemeral and must never be
  persisted (§3.1 ephemerality guarantee).
- Dynamic agent/tool text is wrapped in :class:`rich.text.Text` so markup-
  significant characters render literally (§5.2 content safety).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.text import Text

from chimera.core.loop_events import LoopEventType

__all__ = ["LaneTranscript", "format_event", "plain", "short"]

_TOOL_OUT_LIMIT = 1500


def short(value: Any, limit: int = 40) -> str:
    """Collapse a value to a single truncated line for an argument preview."""
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def plain(renderable: Any) -> str:
    """Best-effort plain-text of a renderable (for the persisted transcript)."""
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


def format_event(ev: Any, chunks: list[str]) -> list[Any]:
    """Render one loop event to zero or more renderables, in order.

    ``chunks`` is the caller's streaming-text accumulator: this appends
    ``assistant_chunk`` fragments to it and drains it (committing one assistant
    item) when the block completes. Returning a list keeps the function pure and
    testable — the caller decides where the renderables go.
    """
    t = ev.type
    out: list[Any] = []
    if t == LoopEventType.assistant_chunk:
        chunks.append(str(ev.data))
    elif t == LoopEventType.assistant:
        if chunks:
            out.append(Text("".join(chunks)))
            chunks.clear()
        else:
            content = getattr(ev.data, "content", "") or ""
            if content.strip():
                out.append(Text(content))
    elif t == LoopEventType.tool_use:
        tc = ev.data
        args = getattr(tc, "arguments", {}) or {}
        preview = ", ".join(f"{k}={short(v)}" for k, v in list(args.items())[:3])
        out.append(Text.assemble(
            ("⚙ ", "yellow"), (str(getattr(tc, "name", "?")), "bold yellow"),
            (f"({preview})", "dim"),
        ))
    elif t == LoopEventType.tool_result:
        _, result = ev.data if isinstance(ev.data, tuple) else (None, ev.data)
        text = (getattr(result, "output", "") or "").rstrip()
        if text:
            if len(text) > _TOOL_OUT_LIMIT:
                text = text[:800] + "\n… [truncated] …\n" + text[-500:]
            ok = getattr(result, "success", True)
            out.append(Text(text, style="green" if ok else "red"))
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
    accumulated internally so the sink never has to know about the commit rule.

    Reasoning (``thinking_chunk``) is accumulated separately and committed as a
    dim block when the next non-thinking content arrives (§13.4). Default is
    **collapsed**: a one-line marker with the size; set :attr:`show_reasoning`
    (the frontends bind a toggle key) to render the text, and
    :meth:`reveal_last` prints the most recent hidden block on demand.
    """

    def __init__(self, sink: Callable[[Any], object]) -> None:
        self._sink = sink
        self._chunks: list[str] = []
        self._think: list[str] = []
        self._last_thinking = ""
        self.show_reasoning = False

    def handle(self, ev: Any) -> None:
        """Render one event, writing any produced renderables to the sink."""
        if getattr(ev, "type", None) == LoopEventType.thinking_chunk:
            self._think.append(str(ev.data))
            return
        if self._think:
            self._commit_thinking()
        for renderable in format_event(ev, self._chunks):
            self._sink(renderable)

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
        if self._chunks:
            self._sink(Text("".join(self._chunks)))
            self._chunks.clear()

    def note(self, text: str, style: str = "dim") -> None:
        """Write a frontend-originated line (steer marker, echo, etc.)."""
        self._sink(Text(text, style=style))
