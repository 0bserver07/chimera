"""Embeddable :class:`Agent` SDK for ``chimera weasel``.

The SDK is the fourth weasel surface — alongside the interactive REPL,
one-shot ``-p`` print mode, and stdio JSON-RPC. It lets library consumers
embed the same minimal harness inside their own Python apps with a single
import:

.. code-block:: python

    from chimera.weasel.sdk import Agent

    agent = Agent(model="glm-5")
    print(agent.run("Summarise the README in 3 bullets.").output)

Design points (see ``research/weasel/SPEC.md``):

* **Thin facade.** The :class:`Agent` wraps :class:`chimera.core.agent.Agent`
  plus a :class:`chimera.sessions.session.Session` for multi-turn
  ``chat()``. It is intentionally light — a delivery vehicle for the core
  primitives, not a parallel framework.
* **Stdlib + chimera at import time.** No provider SDK is touched until
  the user calls a method that needs one — :func:`create_provider` lazy
  resolves through the registry.
* **Default provider chain.** When neither ``provider`` nor ``model`` is
  supplied we delegate to ``chimera.providers.factory.create_provider``
  which itself walks ``$ANTHROPIC_MODEL`` / ``$OPENAI_MODEL`` / explicit
  endpoint env vars.
* **Four entrypoints.**
  ``run`` / ``arun`` (one-shot sync + async),
  ``stream`` / ``astream`` (per-step events),
  ``chat`` (multi-turn convenience that maintains an internal Session).

Trademark hygiene: the live source never names the upstream brand. The
filesystem path ``.weasel/`` is a fact, not a product mention.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chimera.types import AgentResult, ToolCall, ToolResult

if TYPE_CHECKING:
    from chimera.core.tool import BaseTool
    from chimera.providers.base import Provider


__all__ = ["Agent", "Event", "EventType", "AgentResult"]


_DEFAULT_SYSTEM_PROMPT = (
    "You are Weasel, a minimal Chimera coding agent. "
    "Use tools to inspect and modify the user's repo. Be concise."
)


# ---------------------------------------------------------------------------
# Event surface for stream() / astream()
# ---------------------------------------------------------------------------


class EventType:
    """Symbolic event-type constants emitted by :meth:`Agent.stream`.

    Kept as plain strings (rather than an :class:`enum.Enum`) so consumers
    can compare with bare literals — ``event.type == "text"`` — without
    importing the SDK.
    """

    TEXT = "text"
    """Assistant produced a text segment for this step."""

    TOOL_CALL = "tool_call"
    """Assistant requested a tool call (one event per call)."""

    TOOL_RESULT = "tool_result"
    """A tool call returned (one event per result)."""

    STEP = "step"
    """End of one ReAct step (assistant + any tool round-trip)."""

    DONE = "done"
    """Final event of the stream; carries the :class:`AgentResult`."""


@dataclass
class Event:
    """A single streamed event from :meth:`Agent.stream` / :meth:`Agent.astream`.

    Attributes:
        type: One of the constants on :class:`EventType`.
        text: Populated for ``text`` events.
        tool_call: Populated for ``tool_call`` events.
        tool_result: Populated for ``tool_result`` events.
        step: Zero-indexed step ordinal, populated for ``step`` events.
        result: Final :class:`AgentResult`, populated for ``done`` events.
        data: Catch-all for forward-compat fields callers should not rely
            on for stable behaviour.
    """

    type: str
    text: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    step: int = 0
    result: AgentResult | None = None
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SDK Agent facade
# ---------------------------------------------------------------------------


class Agent:
    """Embeddable weasel agent.

    A thin facade over :class:`chimera.core.agent.Agent` that exposes the
    five canonical entrypoints (sync / async one-shot, sync / async
    streaming, multi-turn chat) and pulls sane defaults from weasel's
    provider chain when the caller does not pass an explicit provider.

    Args:
        model: Model identifier (e.g. ``"glm-5"``,
            ``"claude-sonnet-4-20250514"``). Forwarded to
            :func:`chimera.providers.factory.create_provider` when
            ``provider`` is ``None``.
        provider: Pre-built :class:`~chimera.providers.base.Provider`. When
            given, ``model`` is ignored — the caller has already chosen
            the backend.
        tools: Tools the agent may call. Defaults to weasel's
            ``AGENT_TOOLS`` group (read / write / edit / bash / search /
            list_files / test / git / web_fetch / replace_in_file / verify
            …) — i.e. the same tool surface the CLI ships with.
        system_prompt: Optional override for the system prompt. Defaults
            to a short Weasel preamble.
        name: Optional human-readable identifier passed through to the
            underlying core :class:`Agent`.

    Example:
        Quick one-shot::

            from chimera.weasel.sdk import Agent

            agent = Agent(model="glm-5")
            result = agent.run("List the files in this repo.")
            print(result.output)

        Multi-turn::

            agent = Agent()
            agent.chat("What's in this repo?")
            agent.chat("Now summarise README.md")  # remembers prior turn
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        provider: Provider | None = None,
        tools: list[BaseTool] | None = None,
        system_prompt: str | None = None,
        name: str | None = None,
    ) -> None:
        # Lazy-import so that ``import chimera.weasel.sdk`` stays cheap and
        # never forces a provider SDK onto callers who only want the type
        # surface (Event / EventType / AgentResult).
        from chimera.core.agent import Agent as _CoreAgent
        from chimera.core.loop import ReAct
        from chimera.core.prompt import Prompt

        self._model = model
        self._user_supplied_provider = provider is not None

        if provider is None:
            provider = self._default_provider(model)

        if tools is None:
            tools = self._default_tools()

        prompt = Prompt.from_string(system_prompt or _DEFAULT_SYSTEM_PROMPT)
        loop = ReAct()

        self._core = _CoreAgent(
            provider=provider,
            tools=list(tools),
            loop=loop,
            prompt=prompt,
            name=name,
        )

        # Lazily constructed for ``chat()`` so the first ``run()`` doesn't
        # pay the import cost of :class:`Session`.
        self._session: Any | None = None

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def provider(self) -> Provider:
        """The underlying LLM :class:`~chimera.providers.base.Provider`."""
        return self._core.provider

    @property
    def tools(self) -> list[BaseTool]:
        """The tool list bound to this agent."""
        return self._core.tools

    @property
    def model_name(self) -> str:
        """Convenience accessor for the active model identifier."""
        return getattr(self._core.provider, "model_name", self._model or "unknown")

    # ------------------------------------------------------------------
    # One-shot entrypoints
    # ------------------------------------------------------------------

    def run(self, prompt: str) -> AgentResult:
        """Run the agent synchronously on a single prompt.

        Args:
            prompt: Natural-language task for the agent.

        Returns:
            An :class:`AgentResult` with ``output``, ``steps``, ``cost``,
            ``success``, and any ``error``.
        """
        return self._core.run(prompt, env=None)

    async def arun(self, prompt: str) -> AgentResult:
        """Async one-shot. Mirror of :meth:`run`.

        Args:
            prompt: Natural-language task for the agent.

        Returns:
            The :class:`AgentResult` from the async loop.
        """
        return await self._core.async_run(prompt, env=None)

    # ------------------------------------------------------------------
    # Streaming entrypoints
    # ------------------------------------------------------------------

    def stream(self, prompt: str) -> Iterator[Event]:
        """Stream :class:`Event` instances for one task.

        Drives the core loop's :meth:`iter_steps` generator and translates
        each :class:`~chimera.types.StepResult` into a sequence of
        ``text`` → ``tool_call`` → ``tool_result`` → ``step`` events. The
        last event is always ``done`` carrying the :class:`AgentResult`.

        Args:
            prompt: Natural-language task for the agent.

        Yields:
            :class:`Event` objects in execution order.
        """
        from chimera.core.context import Context
        from chimera.core.tool import ContextAwareTool
        from chimera.types import Message

        system = self._core.prompt.render(tools=[t.name for t in self._core.tools])
        context = Context(system=system)
        context.add(Message.user(prompt))
        for t in self._core.tools:
            if isinstance(t, ContextAwareTool):
                t.bind_context(context)

        gen = self._core.loop.iter_steps(
            self._core.provider, self._core.tools, context, None,
        )
        result: AgentResult | None = None
        try:
            while True:
                try:
                    step = next(gen)
                except StopIteration as stop:
                    result = stop.value
                    break

                # Auto-deny pending approvals so the SDK never blocks on
                # interactive confirmation.
                if step.pending_approval is not None and not step.pending_approval.decided:
                    step.pending_approval.deny("Auto-denied by SDK stream()")

                if step.message is not None and step.message.content:
                    yield Event(type=EventType.TEXT, text=step.message.content)

                for call in step.tool_calls:
                    yield Event(type=EventType.TOOL_CALL, tool_call=call)

                for tr in step.tool_results:
                    yield Event(type=EventType.TOOL_RESULT, tool_result=tr)

                yield Event(type=EventType.STEP, step=step.step)
        finally:
            gen.close()

        yield Event(type=EventType.DONE, result=result)

    async def astream(self, prompt: str) -> AsyncIterator[Event]:
        """Async equivalent of :meth:`stream`.

        Uses the loop's :meth:`async_iter_steps` so that providers with a
        true async ``complete`` path get exercised end-to-end.

        Args:
            prompt: Natural-language task for the agent.

        Yields:
            :class:`Event` objects, last one always ``done``.
        """
        from chimera.core.context import Context
        from chimera.core.tool import ContextAwareTool
        from chimera.types import Message

        system = self._core.prompt.render(tools=[t.name for t in self._core.tools])
        context = Context(system=system)
        context.add(Message.user(prompt))
        for t in self._core.tools:
            if isinstance(t, ContextAwareTool):
                t.bind_context(context)

        loop = self._core.loop
        agen = loop.async_iter_steps(
            self._core.provider, self._core.tools, context, None,
        )
        try:
            async for step in agen:
                if step.pending_approval is not None and not step.pending_approval.decided:
                    step.pending_approval.deny("Auto-denied by SDK astream()")

                if step.message is not None and step.message.content:
                    yield Event(type=EventType.TEXT, text=step.message.content)

                for call in step.tool_calls:
                    yield Event(type=EventType.TOOL_CALL, tool_call=call)

                for tr in step.tool_results:
                    yield Event(type=EventType.TOOL_RESULT, tool_result=tr)

                yield Event(type=EventType.STEP, step=step.step)
        finally:
            await agen.aclose()

        # ``async_iter_steps`` stashes the AgentResult on the loop instance.
        result = getattr(loop, "_async_result", None)
        yield Event(type=EventType.DONE, result=result)

    # ------------------------------------------------------------------
    # Multi-turn chat
    # ------------------------------------------------------------------

    def chat(self, message: str) -> str:
        """Multi-turn convenience entrypoint.

        Maintains a single :class:`~chimera.sessions.session.Session`
        instance internally so successive ``chat`` calls see the full
        conversation history.

        Args:
            message: User message for this turn.

        Returns:
            The assistant's text response (``AgentResult.output``).
        """
        if self._session is None:
            from chimera.sessions.session import Session

            self._session = Session(agent=self._core, env=None)
        result = self._session.chat(message)
        return result.output

    def reset_chat(self) -> None:
        """Drop the in-memory chat :class:`Session`.

        Subsequent :meth:`chat` calls start fresh with no prior context.
        """
        self._session = None

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    @staticmethod
    def _default_provider(model: str | None) -> Provider:
        """Resolve the default provider via :func:`create_provider`.

        Lazy-imported so the SDK module never forces a provider SDK at
        ``import`` time.
        """
        from chimera.providers.factory import create_provider

        return create_provider(model=model)

    @staticmethod
    def _default_tools() -> list[BaseTool]:
        """Return weasel's default tool list (a copy of ``AGENT_TOOLS``).

        Lazy-imported because ``chimera.core.tool_group`` triggers concrete
        tool construction the first time ``AGENT_TOOLS`` is read.
        """
        from chimera.core.tool_group import AGENT_TOOLS

        return list(AGENT_TOOLS)
