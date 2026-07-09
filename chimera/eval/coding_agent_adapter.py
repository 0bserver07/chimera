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


def _accumulate_usage(total: dict[str, int], usage: Any) -> None:
    """Add a per-turn ``usage`` dict's token counts into *total* in place.

    Only the counters :func:`~chimera.providers.cost.calculate_cost` reads
    (``input_tokens`` / ``output_tokens``) are summed; anything else is ignored.
    Non-dict / missing payloads (e.g. a plain-string assistant event in a test)
    are skipped so the caller never has to guard the shape.
    """
    if not isinstance(usage, dict):
        return
    for key in ("input_tokens", "output_tokens"):
        val = usage.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            total[key] = total.get(key, 0) + val


async def aggregate_events(
    events: AsyncIterator[LoopEvent], model: str | None = None,
) -> AgentResult:
    """Fold a CodingAgent ``LoopEvent`` stream into a Harness ``AgentResult``.

    Counts ``tool_use`` events, reads final turn-count from the terminal
    ``result`` event, and — crucially — takes the graded output from the
    **stream's** last ``assistant`` event rather than the result event's message
    list. AgentLoop emits an ``assistant`` event for every turn (including the
    terminal no-tool-call turn that carries the answer), so the last such event
    is the agent's true final message. The result event's ``messages`` are a
    weaker source: the loop's completion branch yields that list *without*
    appending the final assistant turn, so its last assistant entry is a stale
    pre-tool preamble. Preferring it (the previous behavior) clobbered the real
    answer and scored correct solutions 0% on answer-graded benchmarks.

    **Cost survives the error path.** Cost is taken from the terminal ``result``
    event when one arrives, but that event fires only on a *clean* exit
    (completion / loop-detected / abort / max-turns). An unrecoverable provider
    error mid-run raises straight through the loop, emitting no ``result`` — so
    sourcing cost solely from it reported ``$0.00`` for runs that had already
    made real, billable calls. To close that gap this also sums each turn's
    token usage off the per-turn ``assistant`` events and, when no ``result``
    arrives, recovers cost via :func:`~chimera.providers.cost.calculate_cost`
    (requires *model*). The raising iterator is caught so those counts are not
    lost with the exception.

    Args:
        events: The async iterator returned by ``CodingAgent.run(task)``.
        model: Model id used to price the summed per-turn usage on the
            no-``result``-event (error) path. ``None`` disables that fallback —
            the terminal result event, when present, remains authoritative.

    Returns:
        An :class:`~chimera.types.AgentResult` the Harness can grade.
    """
    from chimera.core.loop_events import LoopEventType
    from chimera.providers.cost import calculate_cost
    from chimera.tools.submit import SUBMIT_TOOL_NAME

    submitted_output = ""  # answer the agent handed to the `submit` tool
    streamed_output = ""  # last non-empty assistant text seen on the stream
    result_output = ""  # last-assistant text recovered from the terminal result
    result_cost: float | None = None  # cost off the terminal `result` event
    streamed_usage: dict[str, int] = {}  # per-turn usage summed off `assistant`
    steps = 0
    turns_seen = 0
    tool_calls = 0
    error: str | None = None

    try:
        async for event in events:
            etype = event.type
            if etype == LoopEventType.tool_use:
                tool_calls += 1
                # Deterministic finish-tool path: when the agent calls `submit`,
                # its `answer` argument IS the final answer — no prose scraping.
                # Last submit wins if the agent (incorrectly) calls it twice.
                if getattr(event.data, "name", None) == SUBMIT_TOOL_NAME:
                    args = getattr(event.data, "arguments", None) or {}
                    answer = args.get("answer")
                    if isinstance(answer, str) and answer.strip():
                        submitted_output = answer
            elif etype == LoopEventType.assistant:
                turns_seen += 1
                text = _text_of(event.data)
                if text:
                    streamed_output = text
                # Every assistant event carries that turn's provider Response,
                # whose `usage` is the only cost record that outlives an error.
                _accumulate_usage(streamed_usage, getattr(event.data, "usage", None))
            elif etype == LoopEventType.error:
                error = str(event.data)
            elif etype == LoopEventType.result:
                res = event.data
                result_cost = float(getattr(res, "cost_usd", 0.0) or 0.0)
                steps = int(getattr(res, "turn_count", 0) or 0)
                result_output = _last_assistant_text(getattr(res, "messages", None) or [])
    except Exception as exc:  # noqa: BLE001 — a mid-run loop failure must not void cost
        # The loop raised before emitting a terminal `result` (e.g. an
        # unrecoverable provider error). Record it and fall through: the cost of
        # the calls that DID complete is recovered from `streamed_usage` below,
        # instead of being discarded with the exception.
        if error is None:
            error = f"{type(exc).__name__}: {exc}"

    # Cost precedence: the terminal `result` event's tally is authoritative when
    # present; otherwise (the loop raised, or emitted no result) recover it from
    # the summed per-turn usage so billable calls are never counted as free.
    if result_cost is not None:
        cost = result_cost
    elif model and streamed_usage:
        cost = calculate_cost(model, streamed_usage)
    else:
        cost = 0.0

    # A raising loop emits no `result`, so fall back to the number of assistant
    # turns observed rather than reporting 0 steps against a non-zero cost.
    if not steps:
        steps = turns_seen

    # Precedence: a structured `submit` answer beats everything (it is the
    # agent's explicit, deterministic final answer); otherwise the stream's
    # final assistant text (see the docstring for why the result event's
    # message list is unreliable); the message list is the last resort for
    # loop types that emit a terminal result but no per-turn assistant events.
    output = submitted_output or streamed_output or result_output

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
        use_submit_tool: Opt-in deterministic finish tool. When ``True``, the
            agent gets the :class:`~chimera.tools.submit.SubmitTool` plus a
            one-line instruction, and :func:`aggregate_events` reads the final
            answer from the submit call's ``answer`` argument instead of
            scraping prose. Default ``False`` — zero behavior change.
    """

    #: Instruction appended to the task when the submit tool is injected.
    _SUBMIT_INSTRUCTION = (
        "\n\nWhen the task is complete, call the `submit` tool exactly once "
        "with your complete final answer (the full solution, verbatim) as the "
        "'answer' argument."
    )

    def __init__(
        self,
        provider: Any,
        *,
        preset: str = "coding_agent",
        enable_nudges: bool = True,
        use_submit_tool: bool = False,
        max_turns: int | None = None,
    ) -> None:
        self._provider = provider
        self._preset = preset
        self._enable_nudges = enable_nudges
        self._use_submit_tool = use_submit_tool
        self._max_turns = max_turns

    def set_max_turns(self, max_turns: int | None) -> None:
        """Set the loop's turn ceiling (``None`` restores the preset default).

        The assembled :class:`~chimera.assembly.coding_agent.CodingAgent` loop
        takes no :class:`~chimera.core.loop_config.LoopConfig`, so its only
        budget lever is this constructor ceiling. The budgeted matrix path
        (:class:`~chimera.eval.runners.in_process.InProcessRunner`) calls this
        to align the ceiling with ``budget.max_llm_calls`` — turn parity for
        the single-argument preset factories that cannot receive a full
        enforcer.
        """
        self._max_turns = max_turns

    def run(self, task: str, env: Any) -> AgentResult:
        """Run the CodingAgent on *task*, rooted at ``env.workdir``.

        A per-task failure is captured as an unsuccessful result rather than
        raised, so one bad task cannot abort a whole sweep.
        """
        from chimera.assembly.coding_agent import CodingAgent

        project_dir = getattr(env, "workdir", None)
        try:
            extra_tools: list[Any] | None = None
            if self._use_submit_tool:
                from chimera.tools.submit import SubmitTool

                extra_tools = [SubmitTool()]
                task = task + self._SUBMIT_INSTRUCTION
            agent_kwargs: dict[str, Any] = dict(
                provider=self._provider,
                project_dir=str(project_dir) if project_dir else ".",
                preset=self._preset,
                enable_nudges=self._enable_nudges,
                extra_tools=extra_tools,
            )
            # Only forward max_turns when set, so the preset's own ceiling
            # stays the default (CodingAgent uses a sentinel to tell "unset"
            # from an explicit None).
            if self._max_turns is not None:
                agent_kwargs["max_turns"] = self._max_turns
            agent = CodingAgent(**agent_kwargs)
            model = getattr(self._provider, "model_name", None)
            return asyncio.run(aggregate_events(agent.run(task), model=model))
        except Exception as exc:  # noqa: BLE001 - isolate one task's failure
            return AgentResult(
                output="",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=False,
                error=str(exc),
            )
