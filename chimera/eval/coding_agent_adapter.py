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

    Tracks the latest assistant text as the output, counts ``tool_use`` events,
    and reads final cost / turn-count (and the authoritative final assistant
    message) from the terminal ``result`` event.

    Args:
        events: The async iterator returned by ``CodingAgent.run(task)``.

    Returns:
        An :class:`~chimera.types.AgentResult` the Harness can grade.
    """
    from chimera.core.loop_events import LoopEventType

    output = ""
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
                output = text
        elif etype == LoopEventType.error:
            error = str(event.data)
        elif etype == LoopEventType.result:
            res = event.data
            cost = float(getattr(res, "cost_usd", 0.0) or 0.0)
            steps = int(getattr(res, "turn_count", 0) or 0)
            final = _last_assistant_text(getattr(res, "messages", None) or [])
            if final:
                output = final

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
