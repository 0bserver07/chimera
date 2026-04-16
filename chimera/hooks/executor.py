"""HookExecutor — run hooks and merge their results."""
from __future__ import annotations

import asyncio
import fnmatch
import inspect
from typing import TYPE_CHECKING

from chimera.hooks.hook_types import (
    CommandHook,
    FunctionHook,
    HookInput,
    HookMatcher,
    HookOutput,
    PromptHook,
)

if TYPE_CHECKING:
    from chimera.core.abort import AbortSignal
    from chimera.hooks.events import HookEvent


class HookExecutor:
    """Executes hooks against input data and merges results.

    Supports command hooks (shell subprocess), function hooks (Python
    callables), and prompt hooks (LLM-evaluated prompts via a callback).
    """

    def __init__(self, prompt_evaluator=None):
        """Initialize the executor.

        Args:
            prompt_evaluator: Optional async callable that takes a prompt
                string and returns a dict with ``ok`` (bool) and optional
                ``reason`` (str).  Used by prompt hooks.
        """
        self._prompt_evaluator = prompt_evaluator

    async def execute(
        self,
        event: HookEvent,
        input_data: HookInput,
        matchers: list[HookMatcher],
        abort_signal: AbortSignal | None = None,
    ) -> HookOutput:
        """Run all matching hooks and return the merged output.

        Short-circuits as soon as any hook sets ``continue_execution=False``.
        """
        merged = HookOutput()

        for matcher in matchers:
            if not self._matches(matcher, input_data):
                continue

            for hook in matcher.hooks:
                result = await self._execute_single(hook, input_data, abort_signal)
                merged = self._merge(merged, result)

                if not merged.continue_execution:
                    return merged

        return merged

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _execute_single(
        self,
        hook: CommandHook | PromptHook | FunctionHook,
        input_data: HookInput,
        abort: AbortSignal | None,
    ) -> HookOutput:
        """Dispatch to the type-specific handler."""
        if isinstance(hook, CommandHook):
            return await self._execute_command(hook, input_data, abort)
        elif isinstance(hook, FunctionHook):
            return await self._execute_function(hook, input_data, abort)
        elif isinstance(hook, PromptHook):
            return await self._execute_prompt(hook, input_data, abort)
        else:
            return HookOutput()

    # ------------------------------------------------------------------
    # Command hooks
    # ------------------------------------------------------------------

    async def _execute_command(
        self,
        hook: CommandHook,
        input_data: HookInput,
        abort: AbortSignal | None,
    ) -> HookOutput:
        """Run a shell command with input JSON on stdin.

        Exit codes:
            0 — allow (continue)
            2 — block (continue_execution=False, stderr → reason)
            other — allow, but surface stderr as system_message
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            input_json = input_data.to_json().encode()
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_json),
                timeout=hook.timeout if hook.timeout > 0 else None,
            )

            stderr_text = stderr.decode().strip() if stderr else ""
            stdout_text = stdout.decode().strip() if stdout else ""

            if proc.returncode == 0:
                return HookOutput(continue_execution=True)
            elif proc.returncode == 2:
                return HookOutput(
                    continue_execution=False,
                    reason=stderr_text or "Blocked by hook",
                )
            else:
                return HookOutput(
                    continue_execution=True,
                    system_message=stderr_text or f"Hook exited with code {proc.returncode}",
                )

        except asyncio.TimeoutError:
            return HookOutput(
                continue_execution=True,
                system_message=f"Hook command timed out after {hook.timeout}s: {hook.command}",
            )
        except Exception as exc:
            return HookOutput(
                continue_execution=True,
                system_message=f"Hook command error: {exc}",
            )

    # ------------------------------------------------------------------
    # Function hooks
    # ------------------------------------------------------------------

    async def _execute_function(
        self,
        hook: FunctionHook,
        input_data: HookInput,
        abort: AbortSignal | None,
    ) -> HookOutput:
        """Call a Python callback with timeout."""
        try:
            messages = input_data.messages or []
            abort_signal_arg = None  # abort is managed externally; pass None
            if inspect.iscoroutinefunction(hook.callback):
                coro = hook.callback(messages, abort_signal_arg)
            else:
                coro = asyncio.get_event_loop().run_in_executor(
                    None, hook.callback, messages, abort_signal_arg,
                )

            timeout = hook.timeout if hook.timeout > 0 else 0.001
            result = await asyncio.wait_for(coro, timeout=timeout)

            if isinstance(result, HookOutput):
                return result
            return HookOutput()

        except asyncio.TimeoutError:
            return HookOutput(
                continue_execution=True,
                system_message=f"Function hook timed out: {hook.error_message}",
            )
        except Exception as exc:
            return HookOutput(
                continue_execution=True,
                system_message=f"Function hook error: {exc}",
            )

    # ------------------------------------------------------------------
    # Prompt hooks
    # ------------------------------------------------------------------

    async def _execute_prompt(
        self,
        hook: PromptHook,
        input_data: HookInput,
        abort: AbortSignal | None,
    ) -> HookOutput:
        """Execute LLM prompt hook via the registered evaluator callback.

        1. Substitute ``$ARGUMENTS`` in the prompt with input JSON.
        2. If a ``_prompt_evaluator`` callback is set, call it.
        3. Parse the ``{ok: bool, reason?: str}`` response.
        4. Return a :class:`HookOutput` based on the result.
        """
        prompt_text = hook.prompt.replace("$ARGUMENTS", input_data.to_json())

        if self._prompt_evaluator is not None:
            try:
                timeout = hook.timeout if hook.timeout > 0 else 0.001
                result = await asyncio.wait_for(
                    self._prompt_evaluator(prompt_text),
                    timeout=timeout,
                )
                if isinstance(result, dict):
                    if result.get("ok", True):
                        return HookOutput()
                    else:
                        return HookOutput(
                            continue_execution=False,
                            stop_reason=result.get("reason", "Prompt hook denied"),
                        )
            except asyncio.TimeoutError:
                return HookOutput(
                    system_message=f"Prompt hook timed out after {hook.timeout}s",
                )
            except Exception:
                return HookOutput()  # Errors = allow

        return HookOutput()  # No evaluator = allow

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    @staticmethod
    def _matches(matcher: HookMatcher, input_data: HookInput) -> bool:
        """Check whether this matcher applies to the given input.

        ``None`` matcher matches everything; otherwise uses fnmatch
        against the tool name.
        """
        if matcher.matcher is None:
            return True
        if input_data.tool_name is None:
            return False
        return fnmatch.fnmatch(input_data.tool_name, matcher.matcher)

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    @staticmethod
    def _merge(base: HookOutput, new: HookOutput) -> HookOutput:
        """Merge two HookOutputs. Block wins over allow."""
        return HookOutput(
            continue_execution=base.continue_execution and new.continue_execution,
            suppress_output=base.suppress_output or new.suppress_output,
            stop_reason=new.stop_reason or base.stop_reason,
            decision=new.decision or base.decision,
            reason=new.reason or base.reason,
            system_message=new.system_message or base.system_message,
            additional_context=new.additional_context or base.additional_context,
            updated_input=new.updated_input or base.updated_input,
            retry=base.retry or new.retry,
        )
