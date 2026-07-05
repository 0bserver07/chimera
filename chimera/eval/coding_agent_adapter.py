"""Adapter: run the assembled ``chimera code`` CodingAgent under the eval Harness.

The eval :class:`~chimera.eval.harness.Harness` drives a *synchronous*
``agent.run(prompt, env) -> AgentResult``. The daily-driver
:class:`~chimera.assembly.coding_agent.CodingAgent`, by contrast, exposes an
``async def run(task)`` that *yields* :class:`~chimera.core.loop_events.LoopEvent`
objects and takes its working directory as a constructor ``project_dir`` (it
builds its own tool environment internally, ignoring any env passed to ``run``).

This adapter bridges the two so a benchmark can measure the *real* agent — its
tool loop, nudges, and compaction — instead of a single model completion.

    from chimera.eval.coding_agent_adapter import CodingAgentAdapter
    from chimera.eval.harness import Harness

    agent = CodingAgentAdapter(provider=provider)
    harness = Harness(benchmark, agent, env_factory=make_env)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from chimera.types import AgentResult

if TYPE_CHECKING:
    from chimera.core.loop_events import LoopEvent


def _text_of(data: Any) -> str:
    """Best-effort text from an assistant-event payload or a Message."""
    if isinstance(data, str):
        return data
    for attr in ("content", "text"):
        val = getattr(data, attr, None)
        if isinstance(val, str) and val:
            return val
    return ""


def _last_assistant_text(messages: Any) -> str:
    """Return the last assistant message's text from a message list."""
    try:
        seq = list(messages)
    except TypeError:
        return ""
    for msg in reversed(seq):
        if getattr(msg, "role", None) == "assistant":
            text = _text_of(msg)
            if text:
                return text
    return ""


async def aggregate_events(events: AsyncIterator[LoopEvent]) -> AgentResult:
    """Fold a CodingAgent ``LoopEvent`` stream into a Harness ``AgentResult``.

    Counts ``tool_use`` events, reads final cost / turn-count from the terminal
    ``result`` event, and — crucially — takes the graded output from the
    **stream's** last ``assistant`` event rather than the result event's message
    list. AgentLoop emits an ``assistant`` event for every turn (including the
    terminal no-tool-call turn that carries the answer), so the last such event
    is the agent's true final message. The result event's ``messages`` are a
    weaker source: the loop's completion branch yields that list *without*
    appending the final assistant turn, so its last assistant entry is a stale
    pre-tool preamble. Preferring it (the previous behavior) clobbered the real
    answer and scored correct solutions 0% on answer-graded benchmarks.

    Args:
        events: The async iterator returned by ``CodingAgent.run(task)``.

    Returns:
        An :class:`~chimera.types.AgentResult` the Harness can grade.
    """
    from chimera.core.loop_events import LoopEventType

    streamed_output = ""  # last non-empty assistant text seen on the stream
    result_output = ""  # last-assistant text recovered from the terminal result
    cost = 0.0
    steps = 0
    tool_calls = 0
    error: str | None = None

    async for event in events:
        etype = event.type
        if etype == LoopEventType.tool_use:
            tool_calls += 1
        elif etype == LoopEventType.assistant:
            text = _text_of(event.data)
            if text:
                streamed_output = text
        elif etype == LoopEventType.error:
            error = str(event.data)
        elif etype == LoopEventType.result:
            res = event.data
            cost = float(getattr(res, "cost_usd", 0.0) or 0.0)
            steps = int(getattr(res, "turn_count", 0) or 0)
            result_output = _last_assistant_text(getattr(res, "messages", None) or [])

    # Prefer the stream's final assistant text (see the docstring for why the
    # result event's message list is unreliable). Fall back to the message list
    # only for loop types that emit a terminal result but no per-turn assistant
    # events.
    output = streamed_output or result_output

    return AgentResult(
        output=output,
        steps=steps,
        tool_calls_total=tool_calls,
        cost=cost,
        success=error is None,
        error=error,
    )


class CodingAgentAdapter:
    """Expose the async, event-streaming CodingAgent as a sync Harness agent.

    Args:
        provider: Chimera provider for the agent's model.
        preset: CodingAgent preset (default ``coding_agent``).
        enable_nudges: Keep the action / keep-going nudges on — recommended for
            benchmark runs, per CodingAgent's own guidance.
    """

    def __init__(
        self,
        provider: Any,
        *,
        preset: str = "coding_agent",
        enable_nudges: bool = True,
    ) -> None:
        self._provider = provider
        self._preset = preset
        self._enable_nudges = enable_nudges

    def run(self, task: str, env: Any) -> AgentResult:
        """Run the CodingAgent on *task*, rooted at ``env.workdir``.

        A per-task failure is captured as an unsuccessful result rather than
        raised, so one bad task cannot abort a whole sweep.
        """
        from chimera.assembly.coding_agent import CodingAgent

        project_dir = getattr(env, "workdir", None)
        try:
            agent = CodingAgent(
                provider=self._provider,
                project_dir=str(project_dir) if project_dir else ".",
                preset=self._preset,
                enable_nudges=self._enable_nudges,
            )
            return asyncio.run(aggregate_events(agent.run(task)))
        except Exception as exc:  # noqa: BLE001 - isolate one task's failure
            return AgentResult(
                output="",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=False,
                error=str(exc),
            )
