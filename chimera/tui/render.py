"""Shared ``LoopEvent`` → transcript rendering for the Chimera TUIs.

Both the single-agent TUI and the multiplexer render an :class:`AgentDriver`
event stream into a scrolling transcript that must *look* identical. This module
holds that rendering as a small, sink-agnostic helper so the two frontends can't
drift.

Since issue #172 the single-agent surface *is* the one-lane multiplexer, so
every live pane and the persisted transcript go through :class:`LaneTranscript`
here. (The deprecated :class:`~chimera.tui.app.ChimeraTUI` shim still carries
its historical inline copy until its scheduled removal; the CLI no longer
constructs it.)

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
- Reasoning accumulation exposes :attr:`LaneTranscript.thinking_active` /
  ``thinking_elapsed`` / ``thinking_chars`` so a pane can render the R-FOLD-1
  heartbeat (:func:`heartbeat_line`) while chunks stream. The heartbeat itself
  is display chrome — it never goes through the sink (R-VIEW-3); only the
  one-line committed trace does, extended with elapsed + size.
- Dynamic agent/tool text is wrapped in :class:`rich.text.Text` so markup-
  significant characters render literally (§5.2 content safety).
"""
from __future__ import annotations

import time
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

__all__ = [
    "LaneTranscript",
    "assistant_renderable",
    "fmt_chars",
    "fmt_elapsed",
    "format_event",
    "heartbeat_line",
    "plain",
    "short",
]

#: Heartbeat pulse frames (R-FOLD-1): constant width 3 so the line never
#: jitters as the animation ticks.
_PULSE_FRAMES = ("·  ", "·· ", "···", " ··", "  ·", "   ")


def short(value: Any, limit: int = 40) -> str:
    """Collapse a value to a single truncated line for an argument preview."""
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def fmt_elapsed(seconds: float) -> str:
    """Compact elapsed-time label: ``5s``, ``42s``, ``2m 03s``."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60:02d}s"


def fmt_chars(n: int) -> str:
    """Honest size label in characters: ``412 chars``, ``~1.2k chars``.

    Chars, not tokens: thinking deltas carry only text — no provider reports
    token counts mid-stream (usage arrives on the ``done`` event, after
    reasoning has ended) — and a fabricated token estimate would break the
    honesty rule (R-VIEW-2).
    """
    if n < 1000:
        return f"{n} chars"
    return f"~{n / 1000:.1f}k chars"


def heartbeat_line(elapsed: float, chars: int, frame: int = 2) -> str:
    """The R-FOLD-1 reasoning heartbeat one-liner (display-only, never persisted).

    ``∴ Thinking ··· 5s · ~1.2k chars · 240 chars/s`` — elapsed since the
    first thinking chunk, accumulated size, and a live rate pulse. The rate
    joins once a full second has elapsed (before that it would just be noise).

    Args:
        elapsed: Seconds since the first thinking chunk of the block.
        chars: Characters of reasoning accumulated so far.
        frame: Animation tick — one app-level timer advances it for every
            pane (the frames cycle; any int is valid).

    Returns:
        The heartbeat line. Ephemeral by contract (R-VIEW-3): show it in a
        live region, never write it to a transcript sink.
    """
    dots = _PULSE_FRAMES[frame % len(_PULSE_FRAMES)]
    line = f"∴ Thinking {dots} {fmt_elapsed(elapsed)} · {fmt_chars(chars)}"
    if elapsed >= 1.0 and chars:
        line += f" · {chars / elapsed:.0f} chars/s"
    return line


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
    expand_hint: str = "",
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
    ``expand_hint`` names the key that expands elided output — the frontend
    injects its *currently bound* expand-toggle key (R-KEY-3), so no key
    string is ever hardcoded here; empty means no affordance to advertise.
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
            head, marker, tail = text, "", ""
            if elide:
                caps = caps_for_tool(str(getattr(call, "name", "") or ""))
                head, marker, tail = elide_middle(text, caps)
                if marker and expand_hint:
                    marker = f"{marker} ({expand_hint} expands)"
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
    **collapsed**: a one-line trace with elapsed + size; set
    :attr:`show_reasoning` (the frontends bind a toggle key) to render the
    text, and :meth:`reveal_last` prints the most recent hidden block on
    demand. While chunks accumulate, :attr:`thinking_active`,
    :attr:`thinking_elapsed` and :attr:`thinking_chars` feed the R-FOLD-1
    heartbeat (see :func:`heartbeat_line`) — the heartbeat is pane chrome and
    is never written to the sink (R-VIEW-3).

    Args:
        sink: Callable receiving each committed renderable.
        markdown: Render committed assistant prose as rich Markdown.
        clock: Monotonic time source for the thinking elapsed/rate figures;
            injectable for tests. Defaults to :func:`time.monotonic`.
    """

    def __init__(
        self,
        sink: Callable[[Any], object],
        *,
        markdown: bool = True,
        clock: Callable[[], float] | None = None,
        expand_hint: str = "",
    ) -> None:
        self._sink = sink
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._chunks: list[str] = []
        self._think: list[str] = []
        self._think_started: float | None = None  # clock() at the first chunk
        self._think_chars = 0
        self._last_thinking = ""
        self._streamed = False  # saw assistant_chunk since the last flush
        self._blocks_in_stream = 0  # committed blocks since the last flush
        self.show_reasoning = False
        self.markdown = markdown
        #: Reveal-affordance suffix on the committed reasoning trace. Both TUI
        #: frontends bind Ctrl+E; embedders with another binding can override.
        self.reveal_hint = "Ctrl+E to show"
        #: Display-only tool-output elision (R-FOLD-2), on by default. Mutable:
        #: the global expand toggle flips it live. Flipping affects tool
        #: results rendered *afterwards* — the sink is append-only, so already
        #: committed output re-renders only via the transcript overlay
        #: (R-FOLD-7, a later wave).
        self.elide = True
        #: Key hint appended to elision markers (``… +37 lines … (<key>
        #: expands)``). Injected by the frontend from its keybinding registry
        #: so the marker always names the currently bound key (R-KEY-3).
        self.expand_hint = expand_hint

    @property
    def live_tail(self) -> str:
        """Uncommitted streaming text — the live tail of the current block.

        Frontends that render a live region should display it via
        :func:`chimera.tui.markdown_stream.live_tail_view`, which trims a
        partial closing fence so the region never shrinks (R-REN-6). The tail
        is display-state only; it flushes to the sink at block end.
        """
        return "".join(self._chunks)

    @property
    def thinking_active(self) -> bool:
        """True while reasoning chunks are accumulating (heartbeat should show)."""
        return self._think_started is not None

    @property
    def thinking_chars(self) -> int:
        """Characters of reasoning accumulated so far (honest size, not tokens)."""
        return self._think_chars

    @property
    def thinking_elapsed(self) -> float:
        """Seconds since the first chunk of the current reasoning block.

        ``0.0`` when no block is accumulating. Monotonic within a block (it
        reads the injected clock), resetting when the block commits.
        """
        if self._think_started is None:
            return 0.0
        return self._clock() - self._think_started

    def handle(self, ev: Any) -> None:
        """Render one event, writing any produced renderables to the sink."""
        t = getattr(ev, "type", None)
        if t == LoopEventType.thinking_chunk:
            if self._think_started is None:
                self._think_started = self._clock()
            piece = str(ev.data)
            self._think.append(piece)
            self._think_chars += len(piece)
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
        for renderable in format_event(
            ev, self._chunks,
            markdown=self.markdown, elide=self.elide, expand_hint=self.expand_hint,
        ):
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
        elapsed = self.thinking_elapsed
        chars = self._think_chars
        self._think_started = None
        self._think_chars = 0
        block = "".join(self._think).strip()
        self._think.clear()
        if not block:
            return
        self._last_thinking = block
        if self.show_reasoning:
            self._sink(Text(f"∴ {block}", style="dim italic"))
        else:
            # The committed R-FOLD-1 trace: elapsed + honest size + the reveal
            # affordance. The animated heartbeat itself never persists.
            self._sink(Text(
                f"∴ thought for {fmt_elapsed(elapsed)} ({fmt_chars(chars)})"
                f" — {self.reveal_hint}",
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
