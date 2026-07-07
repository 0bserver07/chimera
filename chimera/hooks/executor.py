"""HookExecutor — run hooks and merge their results."""
from __future__ import annotations

import asyncio
import fnmatch
import inspect
import json
import os
from typing import TYPE_CHECKING, Any

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
    from chimera.permissions.audit import AuditLog


class HookExecutor:
    """Executes hooks against input data and merges results.

    Supports command hooks (shell subprocess), function hooks (Python
    callables), and prompt hooks (LLM-evaluated prompts via a callback).
    """

    def __init__(
        self,
        prompt_evaluator: Any = None,
        *,
        cwd: str | None = None,
        extra_env: dict[str, str] | None = None,
        audit_log: AuditLog | None = None,
    ) -> None:
        """Initialize the executor.

        Args:
            prompt_evaluator: Optional async callable that takes a prompt
                string and returns a dict with ``ok`` (bool) and optional
                ``reason`` (str).  Used by prompt hooks.
            cwd: Default working directory for command hooks. Individual
                ``CommandHook.cwd`` values take precedence. ``None`` =
                inherit the parent process's cwd.
            extra_env: Default environment variables merged into every
                command hook subprocess (on top of ``os.environ`` and
                the auto-injected ``HOOK_*`` vars). Individual
                ``CommandHook.extra_env`` values take precedence.
            audit_log: Optional :class:`AuditLog` to record one entry
                per command hook fired (decision = "hook_allowed",
                "hook_blocked", or "hook_failed").
        """
        self._prompt_evaluator = prompt_evaluator
        self._cwd = cwd
        self._extra_env = dict(extra_env) if extra_env else {}
        self._audit_log = audit_log

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

        The subprocess inherits ``os.environ`` plus per-executor and
        per-hook ``extra_env`` values, plus auto-injected ``HOOK_*``
        variables (``HOOK_EVENT``, ``HOOK_TOOL_NAME``, ``HOOK_TOOL_INPUT``,
        ``HOOK_TOOL_OUTPUT``, ``HOOK_TOOL_IS_ERROR``). Working directory
        defaults to the executor's ``cwd``, overridable by ``hook.cwd``.

        Stdout is parsed as JSON; recognised keys (per CC contract):
            - ``continue``: bool → ``continue_execution``
            - ``systemMessage``: str
            - ``hookSpecificOutput.permissionDecision``: str
            - ``hookSpecificOutput.permissionDecisionReason``: str
            - ``hookSpecificOutput.updatedInput``: dict
            - ``hookSpecificOutput.additionalContext``: str

        Exit codes:
            0 — allow (continue)
            2 — block (continue_execution=False, stderr → reason)
            other — allow, but surface stderr as system_message
        """
        env = self._build_env(hook, input_data)
        cwd = hook.cwd or self._cwd

        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )

            input_json = input_data.to_json().encode()
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_json),
                timeout=hook.timeout if hook.timeout > 0 else None,
            )

            stderr_text = stderr.decode().strip() if stderr else ""
            stdout_text = stdout.decode().strip() if stdout else ""
            # Prefer stderr for diagnostics, but fall back to stdout — some
            # hook scripts report errors on stdout only.
            msg = stderr_text or stdout_text

            # Parse JSON stdout (CC contract). Falls back to plain text.
            parsed = self._parse_stdout_json(stdout_text)

            if proc.returncode == 0:
                output = self._merge_parsed(
                    HookOutput(continue_execution=True), parsed,
                )
                self._record_audit(hook, input_data, "hook_allowed", "")
                return output
            elif proc.returncode == 2:
                output = self._merge_parsed(
                    HookOutput(
                        continue_execution=False,
                        reason=msg or "Blocked by hook",
                    ),
                    parsed,
                )
                # Force the block — JSON cannot un-deny an exit-2.
                output.continue_execution = False
                self._record_audit(
                    hook, input_data, "hook_blocked", output.reason or "",
                )
                return output
            else:
                output = self._merge_parsed(
                    HookOutput(
                        continue_execution=True,
                        system_message=msg
                        or f"Hook exited with code {proc.returncode}",
                    ),
                    parsed,
                )
                self._record_audit(
                    hook, input_data, "hook_failed",
                    f"exit={proc.returncode}",
                )
                return output

        except asyncio.TimeoutError:
            self._record_audit(
                hook, input_data, "hook_failed",
                f"timeout after {hook.timeout}s",
            )
            return HookOutput(
                continue_execution=True,
                system_message=f"Hook command timed out after {hook.timeout}s: {hook.command}",
            )
        except Exception as exc:
            self._record_audit(hook, input_data, "hook_failed", str(exc))
            return HookOutput(
                continue_execution=True,
                system_message=f"Hook command error: {exc}",
            )

    # ------------------------------------------------------------------
    # Subprocess helpers
    # ------------------------------------------------------------------

    def _build_env(
        self, hook: CommandHook, input_data: HookInput,
    ) -> dict[str, str]:
        """Build the subprocess environment for a command hook.

        Layered (later wins): ``os.environ`` < executor ``extra_env`` <
        hook ``extra_env`` < auto-injected ``HOOK_*`` vars.
        """
        env: dict[str, str] = dict(os.environ)
        env.update(self._extra_env)
        if hook.extra_env:
            env.update(hook.extra_env)

        event_val = (
            input_data.event.value
            if hasattr(input_data.event, "value")
            else str(input_data.event)
        )
        env["HOOK_EVENT"] = event_val
        env["HOOK_TOOL_NAME"] = input_data.tool_name or ""
        env["HOOK_TOOL_INPUT"] = (
            json.dumps(input_data.tool_input) if input_data.tool_input else ""
        )
        env["HOOK_TOOL_OUTPUT"] = input_data.tool_output or ""
        env["HOOK_TOOL_IS_ERROR"] = "1" if input_data.tool_error else "0"
        return env

    @staticmethod
    def _parse_stdout_json(stdout_text: str) -> dict[str, Any] | None:
        """Try to parse hook stdout as JSON. Returns ``None`` on failure."""
        if not stdout_text:
            return None
        try:
            data = json.loads(stdout_text)
        except (json.JSONDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _merge_parsed(
        base: HookOutput, parsed: dict[str, Any] | None,
    ) -> HookOutput:
        """Apply parsed JSON fields to *base*, returning a new HookOutput."""
        if not parsed:
            return base

        if "continue" in parsed and isinstance(parsed["continue"], bool):
            base.continue_execution = parsed["continue"]
        if "systemMessage" in parsed and isinstance(parsed["systemMessage"], str):
            base.system_message = parsed["systemMessage"]
        if "decision" in parsed and isinstance(parsed["decision"], str):
            base.decision = parsed["decision"]
        if "reason" in parsed and isinstance(parsed["reason"], str):
            base.reason = parsed["reason"]
        if "suppressOutput" in parsed and isinstance(parsed["suppressOutput"], bool):
            base.suppress_output = parsed["suppressOutput"]

        hso = parsed.get("hookSpecificOutput")
        if isinstance(hso, dict):
            pd = hso.get("permissionDecision")
            if isinstance(pd, str):
                base.permission_decision = pd
            pdr = hso.get("permissionDecisionReason")
            if isinstance(pdr, str):
                base.permission_decision_reason = pdr
            ui = hso.get("updatedInput")
            if isinstance(ui, dict):
                base.updated_input = ui
            ac = hso.get("additionalContext")
            if isinstance(ac, str):
                base.additional_context = ac
        return base

    def _record_audit(
        self,
        hook: CommandHook,
        input_data: HookInput,
        decision: str,
        reason: str,
    ) -> None:
        """Append one structured entry per fired hook to the audit log."""
        if self._audit_log is None:
            return
        try:
            self._audit_log.record(
                tool_name=input_data.tool_name or "",
                arguments={
                    "hook_command": hook.command,
                    "hook_event": (
                        input_data.event.value
                        if hasattr(input_data.event, "value")
                        else str(input_data.event)
                    ),
                },
                decision=decision,
                reason=reason,
            )
        except Exception:  # pragma: no cover - audit must never break a hook
            pass

    # ------------------------------------------------------------------
    # Function hooks
    # ------------------------------------------------------------------

    async def _execute_function(
        self,
        hook: FunctionHook,
        input_data: HookInput,
        abort: AbortSignal | None,
    ) -> HookOutput:
        """Call a Python callback with timeout.

        Two calling conventions are supported, selected by
        ``hook.receives_input``:

        * Legacy (``False``, the default): the callback is invoked as
          ``callback(messages, abort_signal)`` where ``messages`` is
          ``input_data.messages or []`` and ``abort_signal`` is ``None``
          (abort is managed externally).
        * Ergonomic (``True``, used by :meth:`HookEmitter.on`): the callback
          is invoked as ``callback(input_data)`` so it can read the full
          :class:`HookInput` payload (event, tool name/input/output, ...).

        The callback may be sync or ``async`` in either convention. A
        returned :class:`HookOutput` is propagated (so a subscriber can, for
        example, veto a ``PreToolUse`` by returning
        ``HookOutput(continue_execution=False)``); any other return value
        yields a default :class:`HookOutput`.
        """
        try:
            call_args: tuple[Any, ...]
            if hook.receives_input:
                call_args = (input_data,)
            else:
                # abort is managed externally; pass None
                call_args = (input_data.messages or [], None)

            if inspect.iscoroutinefunction(hook.callback):
                coro = hook.callback(*call_args)
            else:
                coro = asyncio.get_event_loop().run_in_executor(
                    None, hook.callback, *call_args,
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

        Two filters apply, both optional:
        - ``matcher.events`` (None = match all events; otherwise the input's
          event must be in the list, compared via ``HookEvent.value``).
        - ``matcher.matcher`` (None = match all tool names; otherwise an
          fnmatch pattern against ``input_data.tool_name``).
        """
        # Event filter (None means "match all events").
        if matcher.events is not None:
            event_val = (
                input_data.event.value
                if hasattr(input_data.event, "value")
                else str(input_data.event)
            )
            if event_val not in matcher.events:
                return False
        # Tool-name filter (None means "match all tools").
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
            permission_decision=new.permission_decision or base.permission_decision,
            permission_decision_reason=(
                new.permission_decision_reason or base.permission_decision_reason
            ),
        )
