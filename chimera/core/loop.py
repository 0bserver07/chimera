from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Iterator, TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import (
    LoopBreak,
    async_execute_tool_calls_incremental,
    execute_tool_calls_incremental,
)
from chimera.env.base import Environment
from chimera.providers.base import Provider, Response, StreamEvent
from chimera.providers.cost import calculate_cost
from chimera.providers.cost_tracker import CostLimitExceeded
from chimera.types import AgentResult, Message, StepResult, ToolCall

if TYPE_CHECKING:
    from chimera.core.abort import AbortSignal
    from chimera.core.cancellation import CancellationToken
    from chimera.core.loop_config import LoopConfig
    from chimera.core.loop_events import LoopEvent
    from chimera.hooks.events import HookEvent as _HookEvent
    from chimera.streaming.base import StreamHandler


def _fire_loop_hook(
    config: "LoopConfig | None",
    event: "_HookEvent",
    **kwargs: object,
) -> None:
    """Fire a loop-lifecycle hook synchronously, never raising.

    Used to emit SessionStart / SessionEnd / Stop / StopFailure /
    UserPromptSubmit / Notification at the canonical points of
    :class:`ReAct.iter_steps` and :meth:`ReAct.async_iter_steps`.

    The emitter is reached via ``config.hook_emitter``. When no emitter
    is configured the call is a cheap no-op so the legacy
    zero-config posture is preserved. Exceptions raised by hooks are
    swallowed because lifecycle emission must never break the loop.
    """
    if config is None or config.hook_emitter is None:
        return
    if not getattr(config.hook_emitter, "active", True):
        return
    try:
        config.hook_emitter.emit_sync(event, **kwargs)
    except Exception:
        # Hook failures must never break the agent loop.
        pass


async def _fire_loop_hook_async(
    config: "LoopConfig | None",
    event: "_HookEvent",
    **kwargs: object,
) -> None:
    """Async sibling of :func:`_fire_loop_hook` for ``async_iter_steps``."""
    if config is None or config.hook_emitter is None:
        return
    if not getattr(config.hook_emitter, "active", True):
        return
    try:
        await config.hook_emitter.emit(event, **kwargs)
    except Exception:
        pass


def _last_user_prompt(context: "Context") -> str | None:
    """Return the most recent user message text from *context*, if any."""
    try:
        for msg in reversed(list(context.to_messages())):
            role = getattr(msg, "role", None)
            if role == "user":
                content = getattr(msg, "content", None)
                if isinstance(content, str):
                    return content
                # If content is a list of blocks (Anthropic-style), join text parts.
                if isinstance(content, list):
                    parts: list[str] = []
                    for block in content:
                        if isinstance(block, dict):
                            text = block.get("text") or block.get("content")
                            if isinstance(text, str):
                                parts.append(text)
                    if parts:
                        return "\n".join(parts)
        return None
    except Exception:
        return None


class ReAct:
    """ReAct loop: Reason -> Act (tool call) -> Observe (tool result) -> repeat.

    Iterates until the provider returns a response with no tool calls,
    or max_steps is reached.
    """

    def __init__(
        self,
        max_steps: int = 50,
        config: LoopConfig | None = None,
    ) -> None:
        self.max_steps = max_steps
        # Safety-by-default: when no LoopConfig is supplied we materialise
        # one so that :class:`~chimera.permissions.presets.Interactive`
        # permissions and :class:`~chimera.secrets.RedactionMiddleware`
        # are active from the first tool call.  Callers who want the
        # old zero-config posture can pass ``LoopConfig(yolo_mode=True)``
        # or export ``CHIMERA_UNSAFE=1``.
        #
        # The lookup is cheap (one env-var read + one object allocation)
        # and benchmark-negligible vs. the ~100ms of a provider request.
        if config is None:
            from chimera.core.loop_config import LoopConfig as _LC
            config = _LC()
        self.config = config

    def iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> Generator[StepResult, None, AgentResult]:
        """Yield one :class:`StepResult` per LLM turn.

        When a permission check returns ASK, the yielded step carries a
        :attr:`~StepResult.pending_approval`.  The consumer must call
        :meth:`~PendingApproval.approve` or :meth:`~PendingApproval.deny`
        before iterating further.

        The :class:`AgentResult` is the generator return value (accessible
        via ``StopIteration.value``).
        """
        from chimera.core.middleware import MiddlewareChain

        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        event_bus = self.config.event_bus if self.config else None
        handler: StreamHandler | None = self.config.handler if self.config else None
        wire = self.config.wire if self.config else None
        mw_chain = MiddlewareChain(self.config.middleware if self.config else None)

        if wire:
            from chimera.wire.types import TurnBegin
            wire.send(TurnBegin(turn_id=id(context)))

        if self.config and self.config.event_bus:
            from chimera.events.types import AgentStartEvent
            self.config.event_bus.publish(AgentStartEvent(max_steps=self.max_steps))

        # -- Deliver next-turn messages: queued for "whatever run comes next",
        # they survive a cancelled run (unlike steering/follow-up) and are
        # injected once, at the start of this run.
        if self.config and self.config.message_queues:
            drain_next = getattr(self.config.message_queues, "drain_next_turn", None)
            if callable(drain_next):
                for msg in drain_next():
                    context.add(msg)

        # -- Lifecycle hooks: SessionStart + UserPromptSubmit --
        from chimera.hooks.events import HookEvent as _HE
        _fire_loop_hook(self.config, _HE.SESSION_START)
        _user_prompt = _last_user_prompt(context)
        if _user_prompt is not None:
            _fire_loop_hook(
                self.config, _HE.USER_PROMPT_SUBMIT, user_prompt=_user_prompt,
            )

        for _ in range(self.max_steps):
            # -- Cancellation check --
            if self.config and self.config.cancellation:
                from chimera.core.cancellation import OperationCancelled
                try:
                    self.config.cancellation.check()
                except OperationCancelled:
                    if self.config and self.config.event_bus:
                        from chimera.events.types import CancellationEvent
                        self.config.event_bus.publish(CancellationEvent(at_step=steps))
                    _fire_loop_hook(
                        self.config, _HE.STOP_FAILURE,
                        tool_error="Cancelled",
                    )
                    _fire_loop_hook(self.config, _HE.SESSION_END)
                    yield StepResult(
                        message=Message.assistant("Operation cancelled"),
                        done=True, step=steps, cost=0.0,
                    )
                    return AgentResult(
                        output="Operation cancelled", steps=steps,
                        tool_calls_total=total_tool_calls, cost=total_cost,
                        success=False, error="Cancelled",
                    )

            steps += 1

            if self.config and self.config.event_bus:
                from chimera.events.types import TurnStartEvent
                self.config.event_bus.publish(TurnStartEvent(turn_number=steps))

            # -- Instruction anchor: re-inject key instructions periodically --
            if self.config and self.config.instruction_anchor:
                anchor = self.config.instruction_anchor
                if anchor.should_inject(steps, context.to_messages()):
                    context.add(Message.user(anchor.get_injection()))

            # -- Learning injector: inject proven error-fix patterns --
            if self.config and self.config.learning_injector:
                try:
                    injections = self.config.learning_injector.get_injections(
                        context.to_messages(),
                    )
                    for inj in injections:
                        context.add(Message.user(inj))
                except Exception:
                    pass  # Learning is best-effort

            if wire:
                from chimera.wire.types import StepBegin
                wire.send(StepBegin(step=steps))

            context = mw_chain.run_before_model(context, tools)

            if self.config and self.config.event_bus:
                from chimera.events.types import ModelRequestEvent
                self.config.event_bus.publish(ModelRequestEvent(
                    model=provider.model_name,
                    message_count=len(context.to_messages()),
                    tool_count=len(schemas) if schemas else 0,
                ))

            if handler:
                handler.on_step_start(steps)
                events = provider.stream(
                    context.to_messages(), tools=schemas if schemas else None,
                )
                cancel = self.config.cancellation if self.config else None
                if self.config and self.config.event_bus:
                    from chimera.events.types import StreamStartEvent
                    self.config.event_bus.publish(StreamStartEvent(model=provider.model_name))
                response = self._accumulate_stream(events, handler, cancellation=cancel)
                if self.config and self.config.event_bus:
                    from chimera.events.types import StreamEndEvent
                    self.config.event_bus.publish(StreamEndEvent(
                        total_tokens=response.usage.get("input_tokens", 0) + response.usage.get("output_tokens", 0),
                    ))
            else:
                response = provider.complete(
                    context.to_messages(), tools=schemas if schemas else None,
                )

            response = mw_chain.run_after_model(response, context)

            if self.config and self.config.event_bus:
                from chimera.events.types import ModelResponseEvent
                self.config.event_bus.publish(ModelResponseEvent(
                    model=provider.model_name,
                    content_length=len(response.content),
                    tool_calls_count=len(response.tool_calls),
                    input_tokens=response.usage.get("input_tokens", 0),
                    output_tokens=response.usage.get("output_tokens", 0),
                ))

            step_cost = calculate_cost(provider.model_name, response.usage)
            total_cost += step_cost

            # Cost tracking
            if self.config and self.config.cost_tracker:
                try:
                    self.config.cost_tracker.record(step_cost, model=provider.model_name)
                except CostLimitExceeded:
                    if handler:
                        handler.on_done()
                    if wire:
                        from chimera.wire.types import StepEnd
                        wire.send(StepEnd(step=steps))
                    _fire_loop_hook(
                        self.config, _HE.STOP_FAILURE,
                        tool_error="Cost limit exceeded",
                    )
                    _fire_loop_hook(self.config, _HE.SESSION_END)
                    yield StepResult(
                        message=Message.assistant(response.content),
                        done=True,
                        step=steps,
                        cost=step_cost,
                    )
                    if wire:
                        from chimera.wire.types import TurnEnd
                        wire.send(TurnEnd(turn_id=id(context), steps=steps, output=response.content))
                    return AgentResult(
                        output=response.content,
                        steps=steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error="Cost limit exceeded",
                    )

            context.add(
                Message.assistant(response.content, tool_calls=response.tool_calls),
            )

            if not response.has_tool_calls:
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                if self.config and self.config.event_bus:
                    from chimera.events.types import TurnEndEvent
                    self.config.event_bus.publish(TurnEndEvent(
                        turn_number=steps,
                        tool_calls_count=len(response.tool_calls),
                    ))
                if handler:
                    handler.on_step_end(steps)
                    handler.on_done()
                if wire:
                    from chimera.wire.types import StepEnd
                    wire.send(StepEnd(step=steps))
                # -- Lifecycle hooks: Stop + Notification + SessionEnd --
                _fire_loop_hook(self.config, _HE.STOP)
                _fire_loop_hook(
                    self.config, _HE.NOTIFICATION,
                    tool_output=response.content,
                )
                _fire_loop_hook(self.config, _HE.SESSION_END)
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=[],
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                if wire:
                    from chimera.wire.types import TurnEnd
                    wire.send(TurnEnd(turn_id=id(context), steps=steps, output=response.content))
                if self.config and self.config.event_bus:
                    from chimera.events.types import AgentEndEvent
                    self.config.event_bus.publish(AgentEndEvent(
                        steps=steps, success=True, total_cost=total_cost,
                    ))
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )

            # Execute tool calls incrementally (pauses on ASK)
            try:
                exec_result = execute_tool_calls_incremental(
                    response.tool_calls, tool_map, context, env, self.config,
                )
            except LoopBreak:
                if handler:
                    handler.on_step_end(steps)
                    handler.on_done()
                if wire:
                    from chimera.wire.types import StepEnd
                    wire.send(StepEnd(step=steps))
                _fire_loop_hook(
                    self.config, _HE.STOP_FAILURE,
                    tool_error="Loop detected",
                )
                _fire_loop_hook(self.config, _HE.SESSION_END)
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                if wire:
                    from chimera.wire.types import TurnEnd
                    wire.send(TurnEnd(turn_id=id(context), steps=steps, output=response.content))
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls + len(response.tool_calls),
                    cost=total_cost,
                    success=False,
                    error="Loop detected",
                )

            total_tool_calls += exec_result.executed

            # Emit tool events to handler
            if handler:
                for tc_idx, tc in enumerate(response.tool_calls):
                    handler.on_tool_start(tc.name, tc.id)
                    if tc_idx < len(exec_result.results):
                        tr = exec_result.results[tc_idx]
                        content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"
                        handler.on_tool_end(tc.id, content[:500])

            if exec_result.pending is not None:
                # Pause: yield step with pending approval
                step = StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                    pending_approval=exec_result.pending,
                )
                yield step

                # Consumer must have called approve() or deny() by now
                pa = exec_result.pending
                if pa.approved:
                    # Execute the approved call + remaining
                    remaining = [pa.tool_call] + exec_result.remaining
                    try:
                        extra = execute_tool_calls_incremental(
                            remaining, tool_map, context, env, None,
                        )
                    except LoopBreak:
                        if wire:
                            from chimera.wire.types import StepEnd, TurnEnd
                            wire.send(StepEnd(step=steps))
                            wire.send(TurnEnd(turn_id=id(context), steps=steps, output=response.content))
                        return AgentResult(
                            output=response.content,
                            steps=steps,
                            tool_calls_total=total_tool_calls,
                            cost=total_cost,
                            success=False,
                            error="Loop detected",
                        )
                    total_tool_calls += extra.executed
                else:
                    # Denied — add denial message to context
                    context.add(
                        Message.tool(pa.tool_call.id, pa.denial_message),
                    )
            else:
                # All tool calls executed normally
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                if self.config and self.config.event_bus:
                    from chimera.events.types import TurnEndEvent
                    self.config.event_bus.publish(TurnEndEvent(
                        turn_number=steps,
                        tool_calls_count=len(response.tool_calls),
                    ))
                if wire:
                    from chimera.wire.types import StepEnd
                    wire.send(StepEnd(step=steps))
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                )

            # -- Drain steering messages --
            if self.config and self.config.message_queues:
                for msg in self.config.message_queues.drain_steering():
                    context.add(msg)
                    if self.config.event_bus:
                        from chimera.events.types import SteeringEvent
                        self.config.event_bus.publish(SteeringEvent(content=msg.content if hasattr(msg, "content") else ""))

            if handler:
                handler.on_step_end(steps)

        # Max steps
        if self.config and self.config.event_bus:
            from chimera.events.types import ErrorEvent
            self.config.event_bus.publish(
                ErrorEvent(error="max steps reached", recoverable=False),
            )
        if handler:
            handler.on_done()
        if wire:
            from chimera.wire.types import StepEnd
            wire.send(StepEnd(step=steps))
        _fire_loop_hook(
            self.config, _HE.STOP_FAILURE, tool_error="Max steps reached",
        )
        _fire_loop_hook(self.config, _HE.SESSION_END)
        yield StepResult(
            message=Message.assistant("Max steps reached"),
            tool_calls=[],
            done=True,
            step=steps,
            cost=0.0,
        )
        if wire:
            from chimera.wire.types import TurnEnd
            wire.send(TurnEnd(turn_id=id(context), steps=steps, output="Max steps reached"))
        if self.config and self.config.event_bus:
            from chimera.events.types import AgentEndEvent
            self.config.event_bus.publish(AgentEndEvent(
                steps=steps, success=False, total_cost=total_cost,
            ))
        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=False,
            error="Max steps reached",
        )

    @staticmethod
    def _accumulate_stream(
        events: Iterator[StreamEvent],
        handler: StreamHandler | None,
        cancellation: CancellationToken | None = None,
    ) -> Response:
        """Consume an iterator of stream events into a single Response.

        While iterating, each event is forwarded to *handler* (if given)
        via :meth:`StreamHandler.handle_event`.
        """
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        current_tool_call: ToolCall | None = None
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        for event in events:
            # Check cancellation between stream chunks for sub-second abort
            if cancellation and cancellation.is_cancelled:
                break

            if handler:
                handler.handle_event(event)

            if event.type == "text_delta":
                content_parts.append(event.content)
            elif event.type == "tool_call_start":
                current_tool_call = event.tool_call
            elif event.type == "tool_call_delta":
                pass
            elif event.type == "tool_call_complete":
                if event.tool_call is not None:
                    tool_calls.append(event.tool_call)
                current_tool_call = None
            elif event.type == "done":
                if current_tool_call is not None:
                    tool_calls.append(current_tool_call)
                    current_tool_call = None
                if event.tool_call and event.tool_call not in tool_calls:
                    tool_calls.append(event.tool_call)
                if event.usage:
                    usage = event.usage

        # Safety: flush if the stream ended without done/complete
        if current_tool_call is not None:
            tool_calls.append(current_tool_call)

        return Response(
            content="".join(content_parts),
            tool_calls=tool_calls,
            usage=usage,
        )

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the loop to completion, auto-denying ASK permissions."""
        from chimera.core.middleware import MiddlewareChain

        result = drain_steps(self.iter_steps(provider, tools, context, env))
        mw_chain = MiddlewareChain(self.config.middleware if self.config else None)
        result = mw_chain.run_after_agent(result, env)
        return result


    async def async_iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AsyncGenerator[StepResult, None]:
        """Async version of :meth:`iter_steps`.

        Uses ``async_complete`` for LLM calls and
        ``async_execute_tool_calls_incremental`` for concurrent tool
        execution.

        The :class:`AgentResult` is stored on ``self._async_result``
        because async generators cannot use ``return <value>``.
        Use :func:`async_drain_steps` to consume and retrieve it.
        """
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        event_bus = self.config.event_bus if self.config else None
        # WHY (W5 finishing-touch): mirror the sync iter_steps handler dispatch
        # so MinkStreamHandler emits the ▶ tool markers for `chimera mink -p`
        # (which goes through async_run → async_iter_steps, not iter_steps).
        handler: StreamHandler | None = self.config.handler if self.config else None

        # -- Lifecycle hooks: SessionStart + UserPromptSubmit --
        from chimera.hooks.events import HookEvent as _HE
        await _fire_loop_hook_async(self.config, _HE.SESSION_START)
        _user_prompt = _last_user_prompt(context)
        if _user_prompt is not None:
            await _fire_loop_hook_async(
                self.config, _HE.USER_PROMPT_SUBMIT, user_prompt=_user_prompt,
            )

        for _ in range(self.max_steps):
            # -- Cancellation check --
            if self.config and self.config.cancellation:
                from chimera.core.cancellation import OperationCancelled
                try:
                    self.config.cancellation.check()
                except OperationCancelled:
                    await _fire_loop_hook_async(
                        self.config, _HE.STOP_FAILURE, tool_error="Cancelled",
                    )
                    await _fire_loop_hook_async(self.config, _HE.SESSION_END)
                    yield StepResult(
                        message=Message.assistant("Operation cancelled"),
                        done=True, step=steps, cost=0.0,
                    )
                    self._async_result = AgentResult(
                        output="Operation cancelled", steps=steps,
                        tool_calls_total=total_tool_calls, cost=total_cost,
                        success=False, error="Cancelled",
                    )
                    return

            steps += 1

            response = await provider.async_complete(
                context.to_messages(), tools=schemas if schemas else None,
            )

            step_cost = calculate_cost(provider.model_name, response.usage)
            total_cost += step_cost

            # Cost tracking
            if self.config and self.config.cost_tracker:
                try:
                    self.config.cost_tracker.record(step_cost, model=provider.model_name)
                except CostLimitExceeded:
                    await _fire_loop_hook_async(
                        self.config, _HE.STOP_FAILURE,
                        tool_error="Cost limit exceeded",
                    )
                    await _fire_loop_hook_async(self.config, _HE.SESSION_END)
                    yield StepResult(
                        message=Message.assistant(response.content),
                        done=True,
                        step=steps,
                        cost=step_cost,
                    )
                    self._async_result = AgentResult(
                        output=response.content,
                        steps=steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost,
                        success=False,
                        error="Cost limit exceeded",
                    )
                    return

            context.add(
                Message.assistant(response.content, tool_calls=response.tool_calls),
            )

            if not response.has_tool_calls:
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                # -- Lifecycle hooks: Stop + Notification + SessionEnd --
                await _fire_loop_hook_async(self.config, _HE.STOP)
                await _fire_loop_hook_async(
                    self.config, _HE.NOTIFICATION,
                    tool_output=response.content,
                )
                await _fire_loop_hook_async(self.config, _HE.SESSION_END)
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=[],
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                self._async_result = AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )
                return

            # Execute tool calls concurrently
            try:
                exec_result = await async_execute_tool_calls_incremental(
                    response.tool_calls, tool_map, context, env, self.config,
                )
            except LoopBreak:
                await _fire_loop_hook_async(
                    self.config, _HE.STOP_FAILURE, tool_error="Loop detected",
                )
                await _fire_loop_hook_async(self.config, _HE.SESSION_END)
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                self._async_result = AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls + len(response.tool_calls),
                    cost=total_cost,
                    success=False,
                    error="Loop detected",
                )
                return

            total_tool_calls += exec_result.executed

            # WHY (W5 finishing-touch): emit tool start/end to the handler so
            # the rich TUI renders the ▶ collapsed tool block on async runs.
            if handler:
                for tc_idx, tc in enumerate(response.tool_calls):
                    handler.on_tool_start(tc.name, tc.id)
                    if tc_idx < len(exec_result.results):
                        tr = exec_result.results[tc_idx]
                        content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"
                        handler.on_tool_end(tc.id, content[:500])

            if exec_result.pending is not None:
                step = StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                    pending_approval=exec_result.pending,
                )
                yield step

                pa = exec_result.pending
                if pa.approved:
                    remaining = [pa.tool_call] + exec_result.remaining
                    try:
                        extra = await async_execute_tool_calls_incremental(
                            remaining, tool_map, context, env, None,
                        )
                    except LoopBreak:
                        self._async_result = AgentResult(
                            output=response.content,
                            steps=steps,
                            tool_calls_total=total_tool_calls,
                            cost=total_cost,
                            success=False,
                            error="Loop detected",
                        )
                        return
                    total_tool_calls += extra.executed
                else:
                    context.add(
                        Message.tool(pa.tool_call.id, pa.denial_message),
                    )
            else:
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                )

            # -- Drain steering messages --
            if self.config and self.config.message_queues:
                for msg in self.config.message_queues.drain_steering():
                    context.add(msg)

            # -- LLM condensation (M11) --
            # Fire SummaryCompaction.compact() every N steps when both
            # ``condensation`` and ``condense_every_n_steps`` are set on
            # the LoopConfig. This wires SWE-bench Verified's
            # ``should_condense`` contract (every-N-steps trigger) into
            # the actual loop so the conversation window stays focused
            # over long Verified runs (default budget = 500 steps).
            if (
                self.config
                and self.config.condensation is not None
                and self.config.condense_every_n_steps
                and self.config.condense_every_n_steps > 0
                and steps > 0
                and steps % self.config.condense_every_n_steps == 0
            ):
                # The provider's context window is the natural budget.
                # Falls back to a generous default when missing.
                budget = getattr(provider, "context_window", 200_000) or 200_000
                compacted = self.config.condensation.compact(
                    list(context.messages), budget,
                )
                context.messages = compacted

        # Max steps
        await _fire_loop_hook_async(
            self.config, _HE.STOP_FAILURE, tool_error="Max steps reached",
        )
        await _fire_loop_hook_async(self.config, _HE.SESSION_END)
        yield StepResult(
            message=Message.assistant("Max steps reached"),
            tool_calls=[],
            done=True,
            step=steps,
            cost=0.0,
        )
        self._async_result = AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=False,
            error="Max steps reached",
        )

    async def async_run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the loop to completion using async provider calls.

        Uses :meth:`async_iter_steps` internally.  Any pending ASK
        permissions are auto-denied (same as :meth:`run`).
        """
        return await async_drain_steps(
            self.async_iter_steps(provider, tools, context, env)
        )

    async def async_run_events(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
        *,
        abort_signal: AbortSignal | None = None,
    ) -> AsyncGenerator[LoopEvent, None]:
        """Run the loop yielding LoopEvents via AgentLoop.

        This is the new streaming interface. Existing run()/iter_steps()
        remain unchanged for backwards compatibility.
        """
        from chimera.core.agent_loop import AgentLoop
        loop = AgentLoop()
        async for event in loop.run(
            messages=context.to_messages(),
            tools=tools,
            provider=provider,
            system_prompt=context.system or "",
            max_turns=self.max_steps,
            abort_signal=abort_signal,
        ):
            yield event


def drain_steps(
    gen: Generator[StepResult, None, AgentResult],
) -> AgentResult:
    """Consume an ``iter_steps`` generator to completion.

    Any :attr:`~StepResult.pending_approval` is automatically denied.
    Returns the :class:`AgentResult` from the generator.
    """
    result: AgentResult | None = None
    try:
        while True:
            step = next(gen)
            if step.pending_approval:
                step.pending_approval.deny("Auto-denied by drain_steps")
    except StopIteration as e:
        result = e.value
    if result is None:
        # Generator ended without returning; shouldn't happen, but be safe
        result = AgentResult(
            output="", steps=0, tool_calls_total=0, cost=0.0, success=False,
            error="Generator ended unexpectedly",
        )
    return result


async def async_drain_steps(
    gen: AsyncGenerator[StepResult, None],
) -> AgentResult:
    """Consume an ``async_iter_steps`` generator to completion.

    Any :attr:`~StepResult.pending_approval` is automatically denied.
    Retrieves the :class:`AgentResult` from the ``_async_result``
    attribute set by :meth:`ReAct.async_iter_steps`.
    """
    owner: ReAct | None = None
    # Access the underlying ReAct instance from the generator
    ag_frame = getattr(gen, "ag_frame", None)
    if ag_frame is not None:
        local_vars = ag_frame.f_locals
        owner = local_vars.get("self")

    async for step in gen:
        if step.pending_approval:
            step.pending_approval.deny("Auto-denied by async_drain_steps")

    if owner is not None and hasattr(owner, "_async_result"):
        return owner._async_result

    return AgentResult(
        output="", steps=0, tool_calls_total=0, cost=0.0, success=False,
        error="Generator ended unexpectedly",
    )
