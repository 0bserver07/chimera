"""Tests for chimera.hooks.executor — HookExecutor."""
from __future__ import annotations

import asyncio
import sys

import pytest

from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import (
    CommandHook,
    FunctionHook,
    HookInput,
    HookMatcher,
    HookOutput,
    PromptHook,
)


@pytest.fixture
def executor():
    return HookExecutor()


@pytest.fixture
def sample_input():
    return HookInput(
        event="PreToolUse",
        session_id="s1",
        tool_name="bash",
        tool_input={"command": "ls"},
    )


# ---------------------------------------------------------------------------
# Command hook: exit 0 (allow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_hook_exit_0_allows(executor, sample_input):
    hook = CommandHook(command=f"{sys.executable} -c \"import sys; sys.exit(0)\"")
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is True


# ---------------------------------------------------------------------------
# Command hook: exit 2 (block)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_hook_exit_2_blocks(executor, sample_input):
    hook = CommandHook(
        command=f"{sys.executable} -c \"import sys; sys.stderr.write('blocked'); sys.exit(2)\"",
    )
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is False
    assert result.reason == "blocked"


# ---------------------------------------------------------------------------
# Command hook: other exit codes pass stderr to user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_hook_exit_1_passes_stderr(executor, sample_input):
    hook = CommandHook(
        command=f"{sys.executable} -c \"import sys; sys.stderr.write('oops'); sys.exit(1)\"",
    )
    matcher = HookMatcher(hooks=[hook])

    # Non-0, non-2 exit => continue_execution stays True but
    # the error is surfaced as system_message
    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is True
    assert result.system_message is not None
    assert "oops" in result.system_message


# ---------------------------------------------------------------------------
# Function hook: allow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_function_hook_allow(executor, sample_input):
    def allow_hook(messages, abort_signal):
        return HookOutput(continue_execution=True)

    hook = FunctionHook(callback=allow_hook)
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is True


# ---------------------------------------------------------------------------
# Function hook: block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_function_hook_block(executor, sample_input):
    def block_hook(messages, abort_signal):
        return HookOutput(continue_execution=False, reason="denied")

    hook = FunctionHook(callback=block_hook)
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is False
    assert result.reason == "denied"


# ---------------------------------------------------------------------------
# Function hook: timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_function_hook_timeout(executor, sample_input):
    async def slow_hook(messages, abort_signal):
        await asyncio.sleep(10)
        return HookOutput()

    hook = FunctionHook(callback=slow_hook, timeout=0)  # instant timeout
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    # On timeout, execution should continue but with an error message
    assert result.continue_execution is True
    assert result.system_message is not None


# ---------------------------------------------------------------------------
# Matcher filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matcher_filters_by_tool_name(executor):
    """Matcher 'Write' should not fire for tool_name='bash'."""
    called = []

    def tracking_hook(messages, abort_signal):
        called.append(True)
        return HookOutput()

    hook = FunctionHook(callback=tracking_hook)
    matcher = HookMatcher(hooks=[hook], matcher="Write")

    inp = HookInput(event="PreToolUse", session_id="s1", tool_name="bash")
    result = await executor.execute(HookEvent.PRE_TOOL_USE, inp, [matcher])

    assert result.continue_execution is True
    assert len(called) == 0  # hook was not called


@pytest.mark.asyncio
async def test_matcher_none_matches_all(executor):
    """Matcher=None should fire for any tool."""
    called = []

    def tracking_hook(messages, abort_signal):
        called.append(True)
        return HookOutput()

    hook = FunctionHook(callback=tracking_hook)
    matcher = HookMatcher(hooks=[hook], matcher=None)

    inp = HookInput(event="PreToolUse", session_id="s1", tool_name="anything")
    await executor.execute(HookEvent.PRE_TOOL_USE, inp, [matcher])

    assert len(called) == 1


@pytest.mark.asyncio
async def test_matcher_fnmatch_glob(executor):
    """Matcher supports fnmatch-style patterns."""
    called = []

    def tracking_hook(messages, abort_signal):
        called.append(True)
        return HookOutput()

    hook = FunctionHook(callback=tracking_hook)
    matcher = HookMatcher(hooks=[hook], matcher="bash*")

    inp = HookInput(event="PreToolUse", session_id="s1", tool_name="bash_tool")
    await executor.execute(HookEvent.PRE_TOOL_USE, inp, [matcher])

    assert len(called) == 1


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_block_wins(executor, sample_input):
    """If one hook blocks and another allows, the result is blocked."""
    def allow_hook(messages, abort_signal):
        return HookOutput(continue_execution=True)

    def block_hook(messages, abort_signal):
        return HookOutput(continue_execution=False, stop_reason="nope")

    m1 = HookMatcher(hooks=[FunctionHook(callback=allow_hook)])
    m2 = HookMatcher(hooks=[FunctionHook(callback=block_hook)])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [m1, m2],
    )
    assert result.continue_execution is False
    assert result.stop_reason == "nope"


# ---------------------------------------------------------------------------
# Short-circuit on continue_execution=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_circuit_on_block(executor, sample_input):
    """After a hook returns continue_execution=False, later matchers are skipped."""
    call_order = []

    def block_hook(messages, abort_signal):
        call_order.append("block")
        return HookOutput(continue_execution=False, stop_reason="blocked")

    def after_hook(messages, abort_signal):
        call_order.append("after")
        return HookOutput()

    m1 = HookMatcher(hooks=[FunctionHook(callback=block_hook)])
    m2 = HookMatcher(hooks=[FunctionHook(callback=after_hook)])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [m1, m2],
    )
    assert result.continue_execution is False
    assert call_order == ["block"]


# ---------------------------------------------------------------------------
# Prompt hook: evaluator allows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_hook_allows(sample_input):
    async def evaluator(prompt_text):
        return {"ok": True}

    executor = HookExecutor(prompt_evaluator=evaluator)
    hook = PromptHook(prompt="Is this safe? $ARGUMENTS")
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is True


# ---------------------------------------------------------------------------
# Prompt hook: evaluator denies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_hook_denies(sample_input):
    async def evaluator(prompt_text):
        return {"ok": False, "reason": "Dangerous operation"}

    executor = HookExecutor(prompt_evaluator=evaluator)
    hook = PromptHook(prompt="Is this safe? $ARGUMENTS")
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is False
    assert result.stop_reason == "Dangerous operation"


# ---------------------------------------------------------------------------
# Prompt hook: evaluator timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_hook_timeout(sample_input):
    async def slow_evaluator(prompt_text):
        await asyncio.sleep(10)
        return {"ok": True}

    executor = HookExecutor(prompt_evaluator=slow_evaluator)
    hook = PromptHook(prompt="Check $ARGUMENTS", timeout=0)
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is True
    assert result.system_message is not None
    assert "timed out" in result.system_message


# ---------------------------------------------------------------------------
# Prompt hook: no evaluator = allow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_hook_no_evaluator_allows(sample_input):
    executor = HookExecutor()  # no prompt_evaluator
    hook = PromptHook(prompt="Check $ARGUMENTS")
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is True


# ---------------------------------------------------------------------------
# Prompt hook: evaluator error = allow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_hook_evaluator_error_allows(sample_input):
    async def broken_evaluator(prompt_text):
        raise RuntimeError("evaluator crashed")

    executor = HookExecutor(prompt_evaluator=broken_evaluator)
    hook = PromptHook(prompt="Check $ARGUMENTS")
    matcher = HookMatcher(hooks=[hook])

    result = await executor.execute(
        HookEvent.PRE_TOOL_USE, sample_input, [matcher],
    )
    assert result.continue_execution is True


# ---------------------------------------------------------------------------
# Prompt hook: $ARGUMENTS substitution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_hook_substitutes_arguments(sample_input):
    received_prompts = []

    async def capture_evaluator(prompt_text):
        received_prompts.append(prompt_text)
        return {"ok": True}

    executor = HookExecutor(prompt_evaluator=capture_evaluator)
    hook = PromptHook(prompt="Is this safe? $ARGUMENTS")
    matcher = HookMatcher(hooks=[hook])

    await executor.execute(HookEvent.PRE_TOOL_USE, sample_input, [matcher])

    assert len(received_prompts) == 1
    # $ARGUMENTS should have been replaced with the input JSON
    assert "$ARGUMENTS" not in received_prompts[0]
    assert "bash" in received_prompts[0]  # tool_name from sample_input
