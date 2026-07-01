"""Run an alternative reasoning loop as a ``LoopEvent`` stream (real loop swap).

The multiplexer/TUI renders ``LoopEvent``s, but only ``AgentLoop`` emits them; the
strategy loops in ``chimera/core/loops/`` (plan-execute, reflexion,
tree-of-thought) instead expose
``iter_steps(provider, tools, context, env) -> Generator[StepResult]``. This
adapter bridges the two so a multiplexer lane can race a *genuinely different
reasoning loop* and still stream into its pane.

It runs the strategy loop's **synchronous** generator in a worker thread — so the
loop's blocking provider calls don't stall the asyncio event loop and lanes stay
concurrent — and translates each ``StepResult`` into ``LoopEvent``s over a
thread-safe hop back to the loop. Cancellation is cooperative at step boundaries:
the worker checks the agent's abort signal between steps.
"""
from __future__ import annotations

import importlib
import threading
from collections.abc import AsyncIterator
from typing import Any

from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult

__all__ = ["REAL_LOOPS", "adapt_loop", "is_real_loop"]

# Loop name -> (module, class). Lazy-imported so importing this module stays cheap.
REAL_LOOPS: dict[str, tuple[str, str]] = {
    "plan-execute": ("chimera.core.loops.plan_execute", "PlanAndExecute"),
    "reflexion": ("chimera.core.loops.reflexion", "Reflexion"),
    "tot": ("chimera.core.loops.tree_of_thought", "TreeOfThought"),
}


def is_real_loop(name: str | None) -> bool:
    """True if *name* selects a swappable reasoning loop (vs a posture/default)."""
    return bool(name) and name in REAL_LOOPS


def _aborted(signal: Any) -> bool:
    if signal is None:
        return False
    flag = getattr(signal, "aborted", False)
    try:
        return bool(flag() if callable(flag) else flag)
    except Exception:  # noqa: BLE001
        return False


class _BoundedProvider:
    """Wrap a provider so the strategy loops' non-streaming ``complete()`` uses a
    bounded ``max_tokens``.

    The strategy loops call ``provider.complete()`` without a ``max_tokens``, so
    the provider fills its (possibly very large) default — e.g. glm-5.2's — and
    the Anthropic SDK then rejects the non-streaming request with "streaming is
    required for operations that may take longer than 10 minutes". Capping the
    output keeps the loops on the working non-streaming path; everything else
    (model name, streaming, async methods) delegates unchanged.
    """

    def __init__(self, inner: Any, max_tokens: int = 8192) -> None:
        self._inner = inner
        self._cap = max_tokens

    def complete(
        self,
        messages: Any,
        tools: Any = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        return self._inner.complete(
            messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens or self._cap, **kwargs,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _build_loop(name: str, max_steps: int) -> Any:
    module_path, cls_name = REAL_LOOPS[name]
    cls = getattr(importlib.import_module(module_path), cls_name)
    return cls(max_steps=max_steps)


def _step_to_events(step: Any, turn: int) -> list[LoopEvent]:
    """Translate one strategy-loop StepResult into ordered LoopEvents."""
    events: list[LoopEvent] = []
    message: Any = getattr(step, "message", None)
    if message is not None and getattr(message, "content", None):
        events.append(LoopEvent(LoopEventType.assistant, message, turn))
    calls = list(getattr(step, "tool_calls", []) or [])
    for tc in calls:
        events.append(LoopEvent(LoopEventType.tool_use, tc, turn))
    for i, result in enumerate(getattr(step, "tool_results", []) or []):
        tc = calls[i] if i < len(calls) else None
        events.append(LoopEvent(LoopEventType.tool_result, (tc, result), turn))
    return events


def _result_event(agent_result: Any, messages: list[Any], turn: int, cost: float) -> LoopEvent:
    if agent_result is None:
        reason, total = "completed", cost
        steps = turn
    else:
        success = getattr(agent_result, "success", True)
        reason = "completed" if success else (getattr(agent_result, "error", None) or "error")
        total = float(getattr(agent_result, "cost", 0.0) or 0.0) or cost
        steps = int(getattr(agent_result, "steps", turn) or turn)
    return LoopEvent(
        LoopEventType.result,
        LoopResult(
            reason=reason, messages=messages, usage={},
            cost_usd=total, duration_ms=0.0, turn_count=steps,
        ),
        turn,
    )


async def adapt_loop(
    loop_name: str,
    *,
    provider: Any,
    tools: list[Any],
    system_prompt: str,
    messages: list[Any],
    env: Any = None,
    max_steps: int = 50,
    abort_signal: Any = None,
) -> AsyncIterator[LoopEvent]:
    """Run a strategy loop and yield its progress as ``LoopEvent``s.

    Args mirror what ``CodingAgent`` already has (provider, tools, the assembled
    system prompt, the seed conversation, the workspace env). ``max_steps`` bounds
    the loop; ``abort_signal`` (an ``AbortSignal``) enables step-boundary cancel.
    Ends with exactly one ``result`` event (guaranteed terminal, like AgentLoop).
    """
    import asyncio

    from chimera.core.context import Context

    context = Context(system=system_prompt)
    for message in messages:
        context.add(message)

    provider = _BoundedProvider(provider)  # keep the loops' non-streaming complete() valid
    loop_obj = _build_loop(loop_name, max_steps)
    aio_loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    def emit(event: Any) -> None:
        aio_loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        cost = 0.0
        turn = 0
        try:
            gen = loop_obj.iter_steps(provider, tools, context, env)
            try:
                while True:
                    if _aborted(abort_signal):
                        emit(LoopEvent(
                            LoopEventType.result,
                            LoopResult(
                                reason="cancelled", messages=list(context.messages),
                                usage={}, cost_usd=cost, duration_ms=0.0, turn_count=turn,
                            ),
                            turn,
                        ))
                        gen.close()
                        return
                    step = next(gen)
                    turn = getattr(step, "step", turn) or turn
                    cost += float(getattr(step, "cost", 0.0) or 0.0)
                    for event in _step_to_events(step, turn):
                        emit(event)
            except StopIteration as stop:
                emit(_result_event(stop.value, list(context.messages), turn, cost))
        except Exception as exc:  # noqa: BLE001 - surfaced as an error + terminal result
            emit(LoopEvent(LoopEventType.error, str(exc), 0))
            emit(LoopEvent(
                LoopEventType.result,
                LoopResult(
                    reason="error", messages=list(context.messages),
                    usage={}, cost_usd=cost, duration_ms=0.0, turn_count=turn,
                ),
                turn,
            ))
        finally:
            emit(sentinel)

    threading.Thread(target=worker, daemon=True, name=f"loop-{loop_name}").start()
    while True:
        event = await queue.get()
        if event is sentinel:
            break
        yield event
