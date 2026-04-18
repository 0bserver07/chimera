"""Tests for code enforcements #130-#133.

Covers:
- #130: Read-before-write guard on EditFileTool
- #131: Action nudge when model returns text-only
- #132: Auto-continue when no edits made
- #133: Error context injection on tool failure
"""
from __future__ import annotations


import pytest

from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEventType
from chimera.core.tool import BaseTool
from chimera.providers.base import Response
from chimera.tools.edit import EditFileTool
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal mock provider yielding canned responses in order."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.model_name = "mock"

    async def async_complete(self, messages, tools=None, **kwargs):
        if self._idx >= len(self._responses):
            return Response(content="(exhausted)", tool_calls=[], usage={})
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class EchoTool(BaseTool):
    name = "echo"
    description = "echoes"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    is_concurrency_safe = True

    def execute(self, args, env):
        return ToolResult(output=args.get("text", ""))

    async def async_execute(self, args, env):
        return ToolResult(output=args.get("text", ""))


class FakeEditTool(BaseTool):
    """A mock edit_file tool that always succeeds."""

    name = "edit_file"
    description = "edit a file"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
    }
    is_concurrency_safe = False

    def execute(self, args, env):
        return ToolResult(output=f"Edited {args.get('file_path', '')}")

    async def async_execute(self, args, env):
        return ToolResult(output=f"Edited {args.get('file_path', '')}")


class FailingEditTool(BaseTool):
    """A mock edit_file tool that always fails."""

    name = "edit_file"
    description = "edit a file"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
    }
    is_concurrency_safe = False

    def execute(self, args, env):
        return ToolResult(output="", error="String not found in file.py")

    async def async_execute(self, args, env):
        return ToolResult(output="", error="String not found in file.py")


class FailingBashTool(BaseTool):
    """A mock bash tool that always fails."""

    name = "bash"
    description = "run a command"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}
    is_concurrency_safe = False

    def execute(self, args, env):
        return ToolResult(output="", error="command not found: foobar")

    async def async_execute(self, args, env):
        return ToolResult(output="", error="command not found: foobar")


# ===========================================================================
# #130: Read-before-write guard
# ===========================================================================


class TestReadBeforeWriteGuard:
    """EditFileTool should reject edits to files not previously read."""

    def setup_method(self):
        EditFileTool.reset_read_tracking()
        EditFileTool.set_enforce_read_before_write(True)

    def teardown_method(self):
        EditFileTool.reset_read_tracking()
        EditFileTool.set_enforce_read_before_write(False)

    def test_edit_rejects_unread_file(self, tmp_path):
        """Editing a file that was never read should be rejected."""
        f = tmp_path / "code.py"
        f.write_text("hello world")

        from chimera.core.operations import LocalReadOps, LocalWriteOps

        read_ops = LocalReadOps(cwd=str(tmp_path))
        write_ops = LocalWriteOps(cwd=str(tmp_path))
        tool = EditFileTool(read_ops=read_ops, write_ops=write_ops)
        result = tool.execute(
            {"path": "code.py", "old_string": "hello", "new_string": "goodbye"},
            env=None,
        )
        assert not result.success
        assert "must read" in result.error.lower()

    def test_edit_allows_after_read(self, tmp_path):
        """After marking a file as read, edit should proceed normally."""
        f = tmp_path / "code.py"
        f.write_text("hello world")

        from chimera.core.operations import LocalReadOps, LocalWriteOps

        read_ops = LocalReadOps(cwd=str(tmp_path))
        write_ops = LocalWriteOps(cwd=str(tmp_path))

        # Simulate the read tool marking the file
        EditFileTool.mark_file_read(str(tmp_path / "code.py"))

        tool = EditFileTool(read_ops=read_ops, write_ops=write_ops)
        result = tool.execute(
            {"path": "code.py", "old_string": "hello", "new_string": "goodbye"},
            env=None,
        )
        assert result.success
        assert f.read_text() == "goodbye world"

    def test_reset_clears_tracking(self, tmp_path):
        """reset_read_tracking should clear all tracked files."""
        EditFileTool.mark_file_read("/some/file.py")
        assert EditFileTool.was_file_read("/some/file.py")
        EditFileTool.reset_read_tracking()
        assert not EditFileTool.was_file_read("/some/file.py")

    def test_guard_disabled_by_default(self, tmp_path):
        """When enforcement is off, editing unread files works."""
        EditFileTool.set_enforce_read_before_write(False)
        f = tmp_path / "code.py"
        f.write_text("hello world")

        from chimera.core.operations import LocalReadOps, LocalWriteOps

        read_ops = LocalReadOps(cwd=str(tmp_path))
        write_ops = LocalWriteOps(cwd=str(tmp_path))
        tool = EditFileTool(read_ops=read_ops, write_ops=write_ops)
        result = tool.execute(
            {"path": "code.py", "old_string": "hello", "new_string": "goodbye"},
            env=None,
        )
        assert result.success

    def test_read_file_tool_marks_file(self, tmp_path):
        """ReadFileTool.execute() should mark the file as read."""
        f = tmp_path / "code.py"
        f.write_text("hello world")

        from chimera.core.operations import LocalReadOps
        from chimera.tools.read import ReadFileTool

        read_ops = LocalReadOps(cwd=str(tmp_path))
        tool = ReadFileTool(ops=read_ops)
        result = tool.execute({"path": "code.py"}, env=None)
        assert result.success

        # The file should now be marked as read
        resolved = str((tmp_path / "code.py").resolve())
        assert EditFileTool.was_file_read(resolved)

    def test_cached_read_tool_marks_file(self, tmp_path):
        """CachedReadTool.execute() should mark the file as read."""
        f = tmp_path / "code.py"
        f.write_text("hello world")

        from chimera.tools.cached_read import CachedReadTool

        tool = CachedReadTool()
        result = tool.execute({"path": str(f)}, env=None)
        assert result.success

        resolved = str(f.resolve())
        assert EditFileTool.was_file_read(resolved)


# ===========================================================================
# #131: Action nudge
# ===========================================================================


class TestActionNudge:
    """Model returns text-only responses; the loop should nudge it to act."""

    @pytest.mark.asyncio
    async def test_action_nudge_on_text_only(self):
        """When model returns text without tools, a nudge is injected."""
        responses = [
            # First: text only, should trigger nudge
            Response(content="I should edit the file...", tool_calls=[], usage={}),
            # Second: still text only after nudge, should trigger second nudge
            Response(content="Let me think more...", tool_calls=[], usage={}),
            # Third: after 2 nudges, should be allowed to complete
            Response(content="I'm done thinking.", tool_calls=[], usage={}),
        ]
        provider = MockProvider(responses)
        loop = AgentLoop()
        events = []
        async for event in loop.run(
            messages=[Message.user("Edit the file")],
            tools=[EchoTool(), FakeEditTool()],
            provider=provider,
            system_prompt="test",
            enable_action_nudge=True,
        ):
            events.append(event)

        result_event = next(e for e in events if e.type == LoopEventType.result)
        assert result_event.data.reason == "completed"
        # Provider should have been called 3 times (original + 2 nudge retries)
        assert provider._idx == 3

    @pytest.mark.asyncio
    async def test_action_nudge_max_2(self):
        """After 2 nudges, agent is allowed to complete without tools."""
        responses = [
            Response(content="Thinking 1...", tool_calls=[], usage={}),
            Response(content="Thinking 2...", tool_calls=[], usage={}),
            Response(content="Done.", tool_calls=[], usage={}),
        ]
        provider = MockProvider(responses)
        loop = AgentLoop()
        events = []
        async for event in loop.run(
            messages=[Message.user("Do something")],
            tools=[EchoTool(), FakeEditTool()],
            provider=provider,
            system_prompt="test",
            enable_action_nudge=True,
        ):
            events.append(event)

        result_event = next(e for e in events if e.type == LoopEventType.result)
        assert result_event.data.reason == "completed"
        # All 3 responses consumed: 2 nudges + final allowed completion
        assert provider._idx == 3

    @pytest.mark.asyncio
    async def test_no_nudge_when_tools_used(self):
        """When the model uses a tool, no nudge should be issued after."""
        responses = [
            Response(
                content="Using tool",
                tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
                usage={},
            ),
            Response(content="Done!", tool_calls=[], usage={}),
        ]
        provider = MockProvider(responses)
        loop = AgentLoop()
        events = []
        async for event in loop.run(
            messages=[Message.user("Do it")],
            tools=[EchoTool(), FakeEditTool()],
            provider=provider,
            system_prompt="test",
            enable_action_nudge=True,
        ):
            events.append(event)

        result_event = next(e for e in events if e.type == LoopEventType.result)
        assert result_event.data.reason == "completed"
        # Only 2 responses: tool call + completion
        assert provider._idx == 2

    @pytest.mark.asyncio
    async def test_nudge_disabled(self):
        """When enable_action_nudge=False, text-only completes immediately."""
        responses = [
            Response(content="Just text.", tool_calls=[], usage={}),
        ]
        provider = MockProvider(responses)
        loop = AgentLoop()
        events = []
        async for event in loop.run(
            messages=[Message.user("Do it")],
            tools=[EchoTool(), FakeEditTool()],
            provider=provider,
            system_prompt="test",
            enable_action_nudge=False,
        ):
            events.append(event)

        result_event = next(e for e in events if e.type == LoopEventType.result)
        assert result_event.data.reason == "completed"
        assert provider._idx == 1


# ===========================================================================
# #132: Auto-continue when no edits
# ===========================================================================


class TestAutoContinueNoEdits:
    """Model completes but hasn't made any file edits; loop should continue."""

    @pytest.mark.asyncio
    async def test_auto_continue_when_no_edits(self):
        """Model uses echo tool (not edit) then tries to complete -> nudge."""
        responses = [
            # Turn 1: use a non-edit tool
            Response(
                content="Reading...",
                tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
                usage={},
            ),
            # Turn 2: try to complete without edits -> should be nudged
            Response(content="I think I'm done.", tool_calls=[], usage={}),
            # Turn 3: after nudge, actually edit
            Response(
                content="OK editing",
                tool_calls=[ToolCall(id="t2", name="edit_file", arguments={
                    "file_path": "test.py", "old_string": "a", "new_string": "b",
                })],
                usage={},
            ),
            Response(content="Now I'm done.", tool_calls=[], usage={}),
        ]
        provider = MockProvider(responses)
        loop = AgentLoop()
        events = []
        async for event in loop.run(
            messages=[Message.user("Edit the file")],
            tools=[EchoTool(), FakeEditTool()],
            provider=provider,
            system_prompt="test",
            enable_auto_continue=True,
            enable_action_nudge=True,
        ):
            events.append(event)

        result_event = next(e for e in events if e.type == LoopEventType.result)
        assert result_event.data.reason == "completed"
        # Should have consumed all 4 responses
        assert provider._idx == 4

    @pytest.mark.asyncio
    async def test_auto_continue_disabled(self):
        """When disabled, model completes immediately even without edits."""
        responses = [
            Response(
                content="Reading",
                tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
                usage={},
            ),
            Response(content="Done without edits.", tool_calls=[], usage={}),
        ]
        provider = MockProvider(responses)
        loop = AgentLoop()
        events = []
        async for event in loop.run(
            messages=[Message.user("Do it")],
            tools=[EchoTool()],
            provider=provider,
            system_prompt="test",
            enable_auto_continue=False,
            enable_action_nudge=False,
        ):
            events.append(event)

        result_event = next(e for e in events if e.type == LoopEventType.result)
        assert result_event.data.reason == "completed"
        assert provider._idx == 2


# ===========================================================================
# #133: Error context injection
# ===========================================================================


class TestErrorContextInjection:
    """Tool failures should inject diagnostic messages."""

    @pytest.mark.asyncio
    async def test_error_injection_on_edit_failure(self):
        """Edit tool failure injects 're-read the file' guidance."""
        responses = [
            Response(
                content="Editing...",
                tool_calls=[ToolCall(id="t1", name="edit_file", arguments={
                    "file_path": "test.py", "old_string": "x", "new_string": "y",
                })],
                usage={},
            ),
            # After error injection, model completes
            Response(content="OK, I see the error.", tool_calls=[], usage={}),
        ]
        provider = MockProvider(responses)

        # Capture what messages the provider sees
        captured_messages: list[list[Message]] = []
        original_complete = provider.async_complete

        async def capturing_complete(messages, tools=None, **kwargs):
            captured_messages.append(list(messages))
            return await original_complete(messages, tools=tools, **kwargs)

        provider.async_complete = capturing_complete

        loop = AgentLoop()
        events = []
        async for event in loop.run(
            messages=[Message.user("Edit test.py")],
            tools=[FailingEditTool()],
            provider=provider,
            system_prompt="test",
            enable_action_nudge=False,
            enable_auto_continue=False,
        ):
            events.append(event)

        # The second call should have an error context message injected
        assert len(captured_messages) == 2
        second_call_msgs = captured_messages[1]
        user_msgs = [m for m in second_call_msgs if m.role == "user"]
        assert any("re-read the file" in m.content.lower() for m in user_msgs)

    @pytest.mark.asyncio
    async def test_error_injection_on_bash_failure(self):
        """Bash tool failure injects 'diagnose the issue' guidance."""
        responses = [
            Response(
                content="Running...",
                tool_calls=[ToolCall(id="t1", name="bash", arguments={
                    "command": "foobar",
                })],
                usage={},
            ),
            Response(content="OK, I see the error.", tool_calls=[], usage={}),
        ]
        provider = MockProvider(responses)

        captured_messages: list[list[Message]] = []
        original_complete = provider.async_complete

        async def capturing_complete(messages, tools=None, **kwargs):
            captured_messages.append(list(messages))
            return await original_complete(messages, tools=tools, **kwargs)

        provider.async_complete = capturing_complete

        loop = AgentLoop()
        events = []
        async for event in loop.run(
            messages=[Message.user("Run foobar")],
            tools=[FailingBashTool()],
            provider=provider,
            system_prompt="test",
            enable_action_nudge=False,
            enable_auto_continue=False,
        ):
            events.append(event)

        assert len(captured_messages) == 2
        second_call_msgs = captured_messages[1]
        user_msgs = [m for m in second_call_msgs if m.role == "user"]
        assert any("diagnose" in m.content.lower() for m in user_msgs)
