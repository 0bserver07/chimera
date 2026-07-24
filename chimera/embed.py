"""Embed a Chimera coding agent in your own program — the stable SDK surface.

This module is *the* supported way to put a working coding agent inside any
Python application: a script, a bot, a web service, a custom frontend. It
wraps the assembled agent stack (:class:`~chimera.assembly.coding_agent.CodingAgent`
driven through :class:`~chimera.assembly.driver.AgentDriver` — the same seam
both Chimera TUIs run on) behind three names, all re-exported from the
package root:

- :func:`run_agent` — one blocking call: task in, :class:`TurnResult` out.
- :class:`AgentSession` — a stateful session: stream typed events, steer
  mid-turn, queue follow-ups, cancel, inspect cost/history, shut down cleanly.
- :class:`TurnResult` — what a blocking turn returns (final text + turn stats).

Stability:
    This surface is semver-stable within the 0.9.x line — additions only, no
    renames, no signature breaks. Code written against it keeps working across
    0.9 patch releases.

Example:
    One real coding turn, streamed::

        import asyncio
        import chimera

        async def main() -> None:
            with chimera.AgentSession(model="glm-5.2", project_dir="scratch") as s:
                async for ev in s.send("create fib.py with a fibonacci(n) function"):
                    line = chimera.render_event(ev)
                    if line:
                        print(line, end="")
                print(f"\\ncost: ${s.total_cost:.4f}")

        asyncio.run(main())

    Or blocking, in one line::

        result = chimera.run_agent("create fib.py ...", model="glm-5.2",
                                   project_dir="scratch")
        print(result.text, result.cost_usd)

The full walkthrough (credentials, model swapping, steering, custom
frontends) is ``docs/guides/embed.md``; the event vocabulary and TUI-grade
rendering guidance live in ``docs/building-a-tui.md``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chimera.assembly.driver import AgentDriver
from chimera.core.loop_events import LoopEventType

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

__all__ = ["AgentSession", "TurnResult", "run_agent"]


@dataclass
class TurnResult:
    """Outcome of one blocking agent turn.

    Returned by :meth:`AgentSession.run`, :meth:`AgentSession.run_async`, and
    :func:`run_agent`. Fields mirror the loop's terminal ``result`` event plus
    the extracted final assistant text.

    Attributes:
        text: The final assistant message of the turn ("" if the turn produced
            no assistant text, e.g. it was cancelled before the model replied).
        cost_usd: Dollar cost of this turn (0.0 for unpriced models).
        steps: Number of loop turns (model calls) the task took.
        reason: Why the loop stopped — ``"completed"``, ``"max_turns"``,
            ``"loop_detected"``, or ``"aborted_..."``.
        duration_ms: Wall-clock duration of the turn in milliseconds.
        usage: Raw token-usage dict from the provider (may be empty).
    """

    text: str
    cost_usd: float
    steps: int
    reason: str
    duration_ms: float
    usage: dict[str, Any] = field(default_factory=dict)


def _final_assistant_text(messages: list[Any]) -> str:
    """Return the last non-empty assistant message content in *messages*."""
    for msg in reversed(messages):
        if isinstance(msg, dict):
            role, content = msg.get("role"), msg.get("content")
        else:
            role = getattr(msg, "role", None)
            content = getattr(msg, "content", None)
        if role == "assistant" and isinstance(content, str) and content.strip():
            return content
    return ""


class AgentSession(AgentDriver):
    """A coding-agent session your application owns — the embed surface.

    ``AgentSession`` *is an* :class:`~chimera.assembly.driver.AgentDriver`
    (the seam Chimera's own TUIs drive), extended with a blocking turn API
    and clean shutdown. Everything a frontend needs funnels through this one
    object; no knowledge of the 8-layer internals is required.

    Three tiers of use:

    1. **One-liner** — :func:`run_agent` (module-level; constructs a session,
       runs one turn, closes it).
    2. **Configured** — construct ``AgentSession`` directly with a model,
       preset, working directory, and any ``CodingAgent`` kwargs
       (``max_turns=``, ``provider=``, ``extra_tools=``,
       ``permission_callback=``, ...).
    3. **Subclassable** — subclass ``AgentSession`` (or drop down to
       ``AgentDriver`` / ``CodingAgent``) to change behavior; inject a custom
       ``provider=`` to control the wire.

    The session API (inherited surface documented here for one-stop reading):

    - ``async for ev in session.send(text)`` — run one turn, streaming typed
      :class:`~chimera.core.loop_events.LoopEvent` objects.
    - ``session.run(text)`` / ``await session.run_async(text)`` — blocking
      convenience; drains the stream and returns a :class:`TurnResult`.
    - ``session.steer(text)`` — inject a message mid-turn (between tool turns).
    - ``session.queue_follow_up(text)`` — queue a message for after the agent
      would otherwise stop.
    - ``session.cancel()`` — cooperatively abort the current turn.
    - ``session.clear()`` — forget the conversation.
    - ``session.close()`` — shut down; also via ``with AgentSession(...) as s:``.
    - state: ``model``, ``tools``, ``total_cost``, ``turn_count``, ``history``,
      ``context_window``.

    Conversation history persists across ``send()``/``run()`` calls, so
    successive turns are one continuing conversation until :meth:`clear`.

    Args:
        model: Model id (``"glm-5.2"``, ``"glm-5.2[1m]"``,
            ``"claude-sonnet-4-..."``, ``"modal-endpoint/..."``, ...). Provider
            selection and credentials follow
            :func:`chimera.providers.factory.create_provider`.
        project_dir: Directory the agent's file/shell tools operate in.
            Defaults to the current working directory.
        preset: Assembly preset — ``"coding_agent"`` (default), ``"codex"``,
            ``"minimal"``, ``"explore"``, ...
        interactive: ``True`` (default) suits chat-style embedding: the
            autonomous keep-going nudges are off. Pass ``False`` for
            unattended runs that should push themselves to completion.
        **agent_kwargs: Forwarded to
            :class:`~chimera.assembly.coding_agent.CodingAgent` (e.g.
            ``max_turns=``, ``provider=``, ``extra_tools=``,
            ``permission_callback=``, ``tools_override=``).

    Stability:
        Semver-stable within 0.9.x — additions only, no renames, no signature
        breaks.
    """

    _closed: bool = False

    # -- blocking turn API ----------------------------------------------
    async def run_async(self, task: str) -> TurnResult:
        """Run one turn to completion and return its :class:`TurnResult`.

        The async twin of :meth:`run` for callers already inside an event
        loop. Drains :meth:`send` internally; use :meth:`send` directly when
        you want the per-event stream instead.

        Args:
            task: The instruction for this turn.

        Returns:
            The turn's final text and stats.
        """
        result_data: Any = None
        last_response_text = ""
        async for ev in self.send(task):
            if ev.type == LoopEventType.assistant:
                content = getattr(ev.data, "content", None)
                if isinstance(content, str) and content.strip():
                    last_response_text = content
            elif ev.type == LoopEventType.result:
                result_data = ev.data
        # The last streamed assistant response IS the final text; the loop's
        # terminal message list stops just short of it on normal completion,
        # so it is only the fallback (covers adapter loops without assistant
        # events).
        messages = list(getattr(result_data, "messages", None) or [])
        return TurnResult(
            text=last_response_text or _final_assistant_text(messages),
            cost_usd=float(getattr(result_data, "cost_usd", 0.0) or 0.0),
            steps=int(getattr(result_data, "turn_count", 0) or 0),
            reason=str(getattr(result_data, "reason", "unknown")),
            duration_ms=float(getattr(result_data, "duration_ms", 0.0) or 0.0),
            usage=dict(getattr(result_data, "usage", None) or {}),
        )

    def run(self, task: str) -> TurnResult:
        """Run one turn, blocking until it finishes.

        Args:
            task: The instruction for this turn.

        Returns:
            The turn's final text and stats.

        Raises:
            RuntimeError: If called from inside a running event loop — use
                ``await session.run_async(task)`` (or stream
                ``session.send(task)``) there instead.
        """
        in_loop = True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            in_loop = False
        if in_loop:
            raise RuntimeError(
                "AgentSession.run() blocks and cannot be called from a running "
                "event loop; use 'await session.run_async(task)' or stream "
                "'session.send(task)' instead.",
            )
        return asyncio.run(self.run_async(task))

    # -- shutdown -------------------------------------------------------
    def close(self) -> None:
        """Shut the session down: cancel any in-flight turn, release resources.

        Idempotent. After ``close()`` the session should not be reused —
        construct a new one. Provider connections are closed best-effort
        (providers without a ``close()`` need no teardown).
        """
        if self._closed:
            return
        self._closed = True
        try:
            self.cancel()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            pass
        provider_close = getattr(getattr(self.agent, "provider", None), "close", None)
        if callable(provider_close):
            try:
                provider_close()
            except Exception:  # noqa: BLE001 - shutdown must never raise
                pass

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` has been called on this session."""
        return self._closed

    def __enter__(self) -> AgentSession:
        """Enter a ``with`` block; the session closes itself on exit."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the session when the ``with`` block exits."""
        self.close()


def run_agent(
    task: str,
    *,
    model: str = "glm-5.2",
    project_dir: str | Path | None = None,
    preset: str = "coding_agent",
    **agent_kwargs: Any,
) -> TurnResult:
    """Run one unattended coding-agent turn and block until it finishes.

    The tier-1 convenience: constructs an :class:`AgentSession`, runs *task*
    to completion, closes the session, and returns the :class:`TurnResult`.
    Unattended posture by default (``interactive=False`` — the agent pushes
    itself to finish); pass ``interactive=True`` to override.

    Example::

        import chimera

        result = chimera.run_agent(
            "create fib.py with a fibonacci(n) function",
            model="glm-5.2",
            project_dir="scratch",
        )
        print(result.text)
        print(f"${result.cost_usd:.4f} in {result.steps} step(s)")

    Args:
        task: The instruction for the turn.
        model: Model id (see :class:`AgentSession`).
        project_dir: Directory the agent's tools operate in (default: CWD).
        preset: Assembly preset (default ``"coding_agent"``).
        **agent_kwargs: Forwarded to :class:`AgentSession` /
            :class:`~chimera.assembly.coding_agent.CodingAgent`
            (e.g. ``max_turns=``, ``provider=``).

    Returns:
        The turn's final text and stats.

    Stability:
        Semver-stable within 0.9.x — additions only, no renames, no signature
        breaks.
    """
    agent_kwargs.setdefault("interactive", False)
    session = AgentSession(
        model=model, project_dir=project_dir, preset=preset, **agent_kwargs,
    )
    try:
        return session.run(task)
    finally:
        session.close()
