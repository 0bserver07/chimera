"""Integration tests against a real LLM backend.

All tests skip when ANTHROPIC_AUTH_TOKEN is not set.

Run with:
    ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic" \
    ANTHROPIC_AUTH_TOKEN="..." \
    ANTHROPIC_MODEL="glm-5" \
    python -m pytest tests/test_integration_live.py -v
"""
from __future__ import annotations

import os

import pytest

from chimera.core.agent import Agent
from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Response
from chimera.providers.factory import create_provider
from chimera.tools.think import ThinkTool
from chimera.tools.dmail import DMailTool
from chimera.types import Message

_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
_MODEL = os.environ.get("ANTHROPIC_MODEL", "glm-5")

pytestmark = pytest.mark.skipif(
    not _TOKEN,
    reason="ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY not set",
)


@pytest.fixture(scope="module")
def provider():
    return create_provider(model=_MODEL)


@pytest.fixture
def env(tmp_path):
    e = LocalEnvironment(workdir=str(tmp_path))
    e.setup()
    yield e
    e.cleanup()


# -- Provider-level tests ---------------------------------------------------


def test_provider_text_completion(provider):
    """Provider returns a non-empty text response."""
    messages = [Message.user("What is 2+2? Answer with just the number.")]
    response = provider.complete(messages, max_tokens=50)
    assert isinstance(response, Response)
    assert response.content.strip() != ""
    assert "4" in response.content


def test_provider_tool_use(provider):
    """Provider correctly calls a tool when given tool schemas."""
    tools = [{
        "name": "calculator",
        "description": "Evaluate a math expression",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "The expression to evaluate"},
            },
            "required": ["expression"],
        },
    }]
    messages = [Message.user("What is 17 * 23? Use the calculator tool.")]
    response = provider.complete(messages, tools=tools, max_tokens=200)
    assert isinstance(response, Response)
    assert response.has_tool_calls
    tc = response.tool_calls[0]
    assert tc.name == "calculator"
    assert "expression" in tc.arguments


def test_provider_multi_turn(provider):
    """Provider handles multi-turn conversation correctly."""
    messages = [
        Message.user("My name is Alice."),
    ]
    r1 = provider.complete(messages, max_tokens=100)
    assert isinstance(r1, Response)

    messages.append(Message.assistant(r1.content))
    messages.append(Message.user("What is my name?"))
    r2 = provider.complete(messages, max_tokens=100)
    assert "Alice" in r2.content


# -- Agent-level tests -------------------------------------------------------


def test_agent_simple_response(provider):
    """Agent returns a coherent response for a simple question."""
    agent = Agent(provider=provider, loop=ReAct(max_steps=3))
    result = agent.run("What is the capital of France? Answer in one word.", env=None)
    assert result.success
    assert "Paris" in result.output


def test_agent_file_create(provider, env):
    """Agent creates a file using tools and it appears on disk."""
    agent = Agent(
        provider=provider,
        tools=list(DEFAULT_TOOLS),
        loop=ReAct(max_steps=5),
    )
    result = agent.run(
        "Create a file called hello.py with the content: print('hello world')",
        env=env,
    )
    assert result.success
    content = env.read_file("hello.py")
    assert "hello" in content.lower()


def test_agent_bash_command(provider, env):
    """Agent runs a shell command and returns the result."""
    agent = Agent(
        provider=provider,
        tools=list(DEFAULT_TOOLS),
        loop=ReAct(max_steps=5),
    )
    result = agent.run("Run 'echo chimera_test_123' in bash and tell me the output.", env=env)
    assert result.success
    assert "chimera_test_123" in result.output


def test_agent_cost_tracking(provider):
    """Agent run returns non-negative cost and step count."""
    agent = Agent(provider=provider, loop=ReAct(max_steps=3))
    result = agent.run("Say hello.", env=None)
    assert result.success
    assert result.cost >= 0
    assert result.steps >= 1


# -- Tool-specific tests -----------------------------------------------------


def test_think_tool_no_side_effects(provider):
    """ThinkTool records thought but has no external side effects."""
    think = ThinkTool()
    result = think.execute({"thought": "Let me reason about this..."}, env=None)
    assert result.output == "Thought recorded."
    assert result.metadata["thought"] == "Let me reason about this..."
    assert result.error is None


def test_dmail_context_rewind():
    """DMailTool correctly rewinds context to a checkpoint."""
    ctx = Context(system="You are helpful.")
    dmail = DMailTool()
    dmail.bind_context(ctx)

    # Build conversation
    ctx.add(Message.user("Step 1"))
    cp0 = dmail.create_checkpoint()  # checkpoint 0 at index 1
    ctx.add(Message.assistant("Response 1"))
    ctx.add(Message.user("Step 2 - lots of noise"))
    ctx.add(Message.assistant("Response 2 - more noise"))
    assert len(ctx.messages) == 4

    # Send D-Mail
    result = dmail.execute({
        "action": "send",
        "checkpoint_id": cp0,
        "message": "Skip the noise. The answer from step 1 was X.",
    })
    assert result.error is None

    # Context should be: Step 1 + D-Mail
    assert len(ctx.messages) == 2
    assert ctx.messages[0].content == "Step 1"
    assert "D-Mail" in ctx.messages[1].content
    assert "answer from step 1 was X" in ctx.messages[1].content


# -- Composition tests -------------------------------------------------------


def test_pipeline_two_agents(provider):
    """Two-agent Pipeline completes successfully."""
    from chimera.composition import Pipeline

    agent1 = Agent(provider=provider, loop=ReAct(max_steps=3), name="summarizer")
    agent2 = Agent(provider=provider, loop=ReAct(max_steps=3), name="translator")

    pipe = Pipeline([agent1, agent2])
    result = pipe.run("First, say 'hello world'. Then translate the previous output to French.", env=None)
    assert result.success


def test_ensemble_two_agents(provider):
    """Ensemble runs two agents and returns results."""
    from chimera.composition import Ensemble

    agent1 = Agent(provider=provider, loop=ReAct(max_steps=2), name="a")
    agent2 = Agent(provider=provider, loop=ReAct(max_steps=2), name="b")

    ensemble = Ensemble([agent1, agent2])
    results = ensemble.run("What is 3+5?", env=None)
    assert len(results) == 2
    assert all(r.success for r in results)
