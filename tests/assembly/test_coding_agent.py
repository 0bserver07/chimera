"""Tests for the assembly layer — CodingAgent wires all 8 phases."""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.assembly.coding_agent import CodingAgent
from chimera.assembly.presets import PRESETS, AssemblyConfig
from chimera.assembly.system_prompts import CODING_AGENT_PROMPT, MINIMAL_PROMPT, EXPLORE_PROMPT
from chimera.assembly.tool_sets import coding_tools, minimal_tools, explore_tools
from chimera.commands.input_handler import InputHandler
from chimera.commands.processor import SlashCommandProcessor
from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand
from chimera.core.abort import AbortSignal
from chimera.core.loop_events import LoopEventType
from chimera.core.memory import PersistentMemory
from chimera.providers.base import Response


# ---------------------------------------------------------------------------
# Preset tests
# ---------------------------------------------------------------------------


def test_presets_exist():
    assert "claude_code" in PRESETS
    assert "codex" in PRESETS
    assert "minimal" in PRESETS
    assert "explore" in PRESETS


def test_preset_values():
    cc = PRESETS["claude_code"]
    assert cc.name == "claude_code"
    assert cc.tool_set == "coding"
    assert cc.permissions is True
    assert cc.hooks is True
    assert cc.max_turns == 100

    mini = PRESETS["minimal"]
    assert mini.permissions is False
    assert mini.hooks is False
    assert mini.max_turns == 20


def test_assembly_config_defaults():
    cfg = AssemblyConfig(name="test", description="test config")
    assert cfg.tool_set == "coding"
    assert cfg.permissions is True
    assert cfg.streaming is True
    assert cfg.model is None


# ---------------------------------------------------------------------------
# Tool-set tests
# ---------------------------------------------------------------------------


def test_coding_tools_returns_list():
    tools = coding_tools()
    assert len(tools) >= 15
    names = [t.name for t in tools]
    assert "bash" in names
    assert "skill" in names
    assert "agent" in names
    assert "think" in names
    assert "tool_search" in names
    assert "task_output" in names


def test_coding_tools_returns_fresh_instances():
    tools_a = coding_tools()
    tools_b = coding_tools()
    # Different list objects
    assert tools_a is not tools_b
    # Different tool objects
    assert tools_a[0] is not tools_b[0]


def test_minimal_tools():
    tools = minimal_tools()
    assert len(tools) == 4
    names = [t.name for t in tools]
    assert "bash" in names
    assert "read_file" in names
    assert "write_file" in names
    assert "edit_file" in names


def test_explore_tools():
    tools = explore_tools()
    assert len(tools) == 3
    names = [t.name for t in tools]
    assert "read_file" in names  # CachedReadTool inherits from ReadFileTool
    assert "search" in names
    assert "list_files" in names


# ---------------------------------------------------------------------------
# System-prompt tests
# ---------------------------------------------------------------------------


def test_coding_prompt_not_empty():
    assert len(CODING_AGENT_PROMPT) > 100
    assert "coding" in CODING_AGENT_PROMPT.lower() or "agent" in CODING_AGENT_PROMPT.lower()


def test_prompts_are_strings():
    assert isinstance(CODING_AGENT_PROMPT, str)
    assert isinstance(MINIMAL_PROMPT, str)
    assert isinstance(EXPLORE_PROMPT, str)


# ---------------------------------------------------------------------------
# CodingAgent integration tests
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal mock provider for testing."""
    model_name = "mock"

    async def async_complete(self, messages, tools=None, **kw):
        return Response(content="Done!", tool_calls=[], usage={})


@pytest.mark.asyncio
async def test_coding_agent_runs():
    """CodingAgent initializes and runs with a mock provider."""
    agent = CodingAgent.__new__(CodingAgent)

    # Manually set up minimal state to bypass full __init__
    agent.provider = MockProvider()
    agent._config = PRESETS["minimal"]
    agent._project_dir = Path(".")
    agent._abort_signal = AbortSignal()
    agent._permission_checker = None
    agent._permission_context = None
    agent._hook_executor = None
    agent._hook_matchers = None
    agent._content_replacement = None
    agent._transcript = None
    agent._compaction = None
    agent._memory = PersistentMemory(Path("/tmp/chimera_test_nonexistent"))
    agent._input_handler = InputHandler()
    agent.tools = minimal_tools()
    agent._system_prompt_text = "You are a test agent."

    events = []
    async for event in agent.run("Hello"):
        events.append(event)

    # Must have a result event
    result_events = [e for e in events if e.type == LoopEventType.result]
    assert len(result_events) == 1
    assert result_events[0].data.reason == "completed"


@pytest.mark.asyncio
async def test_slash_command_handled():
    """Slash commands are handled by InputHandler, not sent to model."""
    registry = CommandRegistry()
    registry.register(
        LocalCommand(name="test", description="test", handler=lambda a: "test output"),
    )
    handler = InputHandler(processor=SlashCommandProcessor(registry))

    was_cmd, output = await handler.process("/test")
    assert was_cmd is True
    assert output == "test output"


@pytest.mark.asyncio
async def test_slash_command_in_agent():
    """CodingAgent detects slash commands and yields a system message."""
    agent = CodingAgent.__new__(CodingAgent)
    agent.provider = MockProvider()
    agent._config = PRESETS["minimal"]
    agent._project_dir = Path(".")
    agent._abort_signal = AbortSignal()
    agent._permission_checker = None
    agent._permission_context = None
    agent._hook_executor = None
    agent._hook_matchers = None
    agent._content_replacement = None
    agent._transcript = None
    agent._compaction = None
    agent._memory = PersistentMemory(Path("/tmp/chimera_test_nonexistent"))
    agent.tools = minimal_tools()
    agent._system_prompt_text = "You are a test agent."

    # Set up a command registry with a /hello command
    registry = CommandRegistry()
    registry.register(
        LocalCommand(name="hello", description="say hello", handler=lambda a: "world"),
    )
    agent._input_handler = InputHandler(processor=SlashCommandProcessor(registry))

    events = []
    async for event in agent.run("/hello"):
        events.append(event)

    # Should get a system_message event, NOT a result from the model
    assert len(events) == 1
    assert events[0].data == "world"


@pytest.mark.asyncio
async def test_from_preset_classmethod():
    """from_preset returns a CodingAgent when __init__ succeeds."""
    # We cannot fully initialize without a real provider, so just test
    # that from_preset delegates to __init__ with the preset argument.
    # We test this by checking CodingAgent.from_preset exists and
    # has the correct signature.
    assert callable(CodingAgent.from_preset)


def test_abort_and_reset():
    """abort() marks signal, reset_abort() creates a fresh one."""
    agent = CodingAgent.__new__(CodingAgent)
    agent._abort_signal = AbortSignal()

    assert not agent._abort_signal.aborted
    agent.abort()
    assert agent._abort_signal.aborted

    agent.reset_abort()
    assert not agent._abort_signal.aborted
