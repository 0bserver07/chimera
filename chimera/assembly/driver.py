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
from typing import TYPE_CHECKING, Any, Protocol

from chimera.core.loop_events import LoopEvent, LoopEventType

if TYPE_CHECKING:
    from chimera.assembly.coding_agent import CodingAgent

__all__ = ["AgentDriver", "DriverProtocol", "render_event"]


class DriverProtocol(Protocol):
    """The driver contract a frontend lane drives — the ONLY seam a lane needs.

    :class:`AgentDriver` is the canonical implementation;
    :class:`~chimera.assembly.external_driver.ExternalAgentDriver` (a real
    third-party CLI as a lane, issue #169) and test fakes satisfy it
    structurally. Beyond these requirements, frontends read a few *optional*
    attributes defensively via ``getattr`` — ``context_window``, ``thinking``,
    ``auto_compaction``, ``model``, ``total_cost``, ``turn_count``, ``budget``,
    ``budget_tally`` — so a driver may omit them and the UI degrades honestly
    (hides the gauge rather than fake it).
    """

    def send(self, text: str) -> AsyncIterator[LoopEvent]:
        """Run one turn, yielding events; MUST end with exactly one ``result``."""
        ...

    def steer(self, text: str) -> None:
        """Inject a mid-run message (or note that steering is unsupported)."""
        ...

    def queue_follow_up(self, text: str) -> None:
        """Queue a message for after the turn (or note it is unsupported)."""
        ...

    def cancel(self) -> None:
        """Abort the current turn."""
        ...

    def clear(self) -> None:
        """Forget the conversation."""
        ...

    def load_history(self, messages: list[Any]) -> None:
        """Seed the conversation from a saved history (session resume)."""
        ...

    @property
    def tools(self) -> list[Any]:
        """The driver's tools (objects with a ``name``); may be empty."""
        ...

    @property
    def history(self) -> list[Any]:
        """The conversation as :class:`~chimera.types.Message`-like items."""
        ...


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
            ``provider``, ``tools_override``, or ``interceptors=`` — a
            :class:`~chimera.core.interception.Interceptors` instance whose
            seams can block/mutate provider requests, tool calls, tool
            results, and the outgoing context; merged per turn with chains
            registered by loaded plugins, plugin chains first, host chains
            last).
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

    @property
    def auto_compaction(self) -> bool:
        """Whether the agent loop has auto-compaction armed.

        Surfaced for status displays (the context meter's ``(auto)`` marker);
        reads the agent's compaction integration, which is present exactly
        when the preset enabled compaction.
        """
        return getattr(self._agent, "_compaction", None) is not None

    @property
    def budget(self) -> Any:
        """The lane's :class:`~chimera.core.budget.BudgetSpec`, or ``None``.

        Surfaced for the status line's budget meter and the cohort manifest;
        ``None`` when the lane carries no budget.
        """
        return getattr(self._agent, "budget", None)

    @property
    def budget_tally(self) -> Any:
        """Live budget counters (cost / llm_calls / elapsed), or ``None``.

        The enforcer's mutable tally, so a status display reads live
        consumption during a turn rather than only the last turn-end snapshot.
        """
        return getattr(self._agent, "budget_tally", None)

    def set_budget(self, budget: Any) -> None:
        """Set or clear the lane's run budget mid-session (delegates to the agent).

        Preserves consumption already recorded; takes effect on the next
        :meth:`send`. A no-op for drivers whose agent has no budget support.
        """
        setter = getattr(self._agent, "set_budget", None)
        if setter is not None:
            setter(budget)

    # -- hot-swap seam (/resync) ----------------------------------------
    @property
    def busy(self) -> bool:
        """Whether a turn is currently streaming through this driver."""
        return bool(getattr(self._agent, "_turn_active", False))

    def resync_resources(self) -> Any:
        """Hot-swap plugins / skills / agent definitions from disk, live.

        Delegates to :meth:`CodingAgent.resync_resources` — the seam behind
        ``/resync`` in both frontends and on the embed surface
        (:class:`~chimera.embed.AgentSession` inherits this method).

        Returns:
            The :class:`~chimera.assembly.resync.ResyncReport`; refused (and
            nothing rebound) while a turn is running.
        """
        return self._agent.resync_resources()


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
