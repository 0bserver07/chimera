"""AgentDriver: a small, TUI-friendly control surface over :class:`CodingAgent`.

A terminal REPL or a full TUI (Textual, Rich, a web frontend, …) drives the
agent through this one object instead of poking at loop internals:

- ``async for ev in driver.send(text)`` — stream a turn's events.
- ``driver.steer(text)`` — inject a message mid-run (between tool turns).
- ``driver.queue_follow_up(text)`` — queue a message for after the agent stops.
- ``driver.cancel()`` — abort the current turn.
- ``driver.clear()`` — forget the conversation.
- state: ``model``, ``tools``, ``total_cost``, ``history``, ``context_window``.

The event vocabulary is :class:`chimera.core.loop_events.LoopEventType`. Use
:func:`render_event` for a no-frills text rendering, or consume the typed
events directly and render them however the UI wants.

Conversation memory and per-turn cost accumulation are handled here, so a UI
only has to render events and collect input.

Example::

    driver = AgentDriver(model="glm-5.2[1m]", project_dir=".")
    async for ev in driver.send("fix the failing test in calc.py"):
        line = render_event(ev)
        if line:
            print(line)
    print(f"cost so far: ${driver.total_cost:.4f}")
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.core.loop_events import LoopEvent, LoopEventType

if TYPE_CHECKING:
    from chimera.assembly.coding_agent import CodingAgent

__all__ = ["AgentDriver", "render_event"]


class AgentDriver:
    """Thin, stateful control surface a REPL/TUI uses to drive a coding agent.

    Args:
        model: Model id (``glm-5.2[1m]``, ``claude-sonnet-4-...``, …).
        project_dir: Working directory the agent operates in. Defaults to CWD.
        preset: Assembly preset (``coding_agent``, ``codex``, ``minimal``, …).
        interactive: When ``True`` (default), the autonomous action/keep-going
            nudges are disabled so conversational turns (questions, acks) do
            not provoke "you didn't use any tools" rambling. Set ``False`` for
            unattended/print-mode runs that should push themselves to finish.
        **agent_kwargs: Forwarded to :class:`CodingAgent` (e.g. ``max_turns``,
            ``provider``, ``tools_override``).
    """

    def __init__(
        self,
        model: str = "glm-5.2",
        project_dir: str | Path | None = None,
        preset: str = "coding_agent",
        *,
        interactive: bool = True,
        **agent_kwargs: Any,
    ) -> None:
        from chimera.assembly.coding_agent import CodingAgent

        agent_kwargs.setdefault("enable_nudges", not interactive)
        self._agent: CodingAgent = CodingAgent(
            model=model, project_dir=project_dir, preset=preset, **agent_kwargs,
        )
        self._total_cost = 0.0
        self._turn_count = 0

    # -- driving --------------------------------------------------------
    async def send(self, text: str) -> AsyncIterator[LoopEvent]:
        """Run one turn, yielding loop events; accrues cost and history."""
        self._agent.reset_abort()
        async for ev in self._agent.run(text):
            if ev.type == LoopEventType.result:
                self._total_cost += float(getattr(ev.data, "cost_usd", 0.0) or 0.0)
                self._turn_count += 1
            yield ev

    def steer(self, text: str) -> None:
        """Inject a steering message delivered between tool turns mid-run."""
        self._agent.steer(text)

    def queue_follow_up(self, text: str) -> None:
        """Queue a message delivered after the agent would otherwise stop."""
        self._agent.queue_follow_up(text)

    def cancel(self) -> None:
        """Abort the current turn (cooperative; takes effect at the next step)."""
        self._agent.abort()

    def clear(self) -> None:
        """Forget the conversation so the next ``send()`` starts fresh."""
        self._agent.clear_history()

    def load_history(self, messages: list[Any]) -> None:
        """Seed the conversation from a saved history (for session resume)."""
        self._agent.load_history(messages)

    # -- state ----------------------------------------------------------
    @property
    def agent(self) -> CodingAgent:
        """The underlying :class:`CodingAgent` (for advanced access)."""
        return self._agent

    @property
    def model(self) -> str:
        return str(getattr(self._agent.provider, "model_name", "unknown"))

    @property
    def tools(self) -> list[Any]:
        return self._agent.tools

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def history(self) -> list[Any]:
        return self._agent.history

    @property
    def context_window(self) -> int | None:
        win = getattr(self._agent.provider, "context_window", None)
        return int(win) if win else None


def render_event(ev: LoopEvent) -> str | None:
    """Render a loop event as a single display line, or ``None`` to skip.

    A convenience for simple text UIs. Streaming text chunks are returned raw
    (no trailing newline) so callers can ``print(..., end="")``; everything
    else is a complete line. Returns ``None`` for events with nothing to show.
    """
    t = ev.type
    if t == LoopEventType.assistant_chunk:
        return str(ev.data)
    if t == LoopEventType.tool_use:
        tc = ev.data
        name = getattr(tc, "name", "?")
        args = getattr(tc, "arguments", {}) or {}
        preview = ", ".join(f"{k}={_short(v)}" for k, v in list(args.items())[:3])
        return f"\n  ⚙ {name}({preview})"
    if t == LoopEventType.tool_result:
        tc, result = ev.data if isinstance(ev.data, tuple) else (None, ev.data)
        name = getattr(tc, "name", "?") if tc else "?"
        out = getattr(result, "output", str(result)) or ""
        ok = getattr(result, "success", True)
        if not out.strip():
            return None
        if len(out) > 1200:
            out = out[:600] + f"\n  … [{len(out) - 1200} chars truncated] …\n" + out[-600:]
        return f"  [{'+' if ok else '!'} {name}] {out}"
    if t == LoopEventType.compact_boundary:
        return "\n  … [context compacted]"
    if t == LoopEventType.error:
        return f"\n  [error] {ev.data}"
    if t == LoopEventType.system:
        return str(ev.data) if ev.data else None
    return None


def _short(value: Any, limit: int = 40) -> str:
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"
