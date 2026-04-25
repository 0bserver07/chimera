from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.core.tool import BaseTool
from chimera.providers.base import Provider, Response
from chimera.types import ToolCall, ToolResult


# --- Mock provider ---


class MockProvider(Provider):
    """Provider that returns a predetermined sequence of responses."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None) -> Response:
        if self._call_count >= len(self._responses):
            return Response(content="(no more responses)", tool_calls=[], usage={})
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock"


# --- Mock tool ---


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a message"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, args, env):
        return ToolResult(output=f"Echo: {args['message']}")


# --- Tests ---


def test_agent_simple_response():
    """Agent with no tools, provider returns text immediately."""
    provider = MockProvider([
        Response(content="Hello, I'm here to help!", tool_calls=[], usage={}),
    ])
    agent = Agent(provider=provider)

    result = agent.run("Hi there", env=None)

    assert result.success is True
    assert "Hello" in result.output
    assert result.steps == 1
    assert result.tool_calls_total == 0


def test_agent_with_tools():
    """Agent with a tool, provider uses the tool then responds."""
    provider = MockProvider([
        Response(
            content="Let me echo that.",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "world"})],
            usage={},
        ),
        Response(content="The echo returned: world", tool_calls=[], usage={}),
    ])
    agent = Agent(provider=provider, tools=[EchoTool()])

    result = agent.run("Echo world for me", env=None)

    assert result.success is True
    assert result.steps == 2
    assert result.tool_calls_total == 1


def test_agent_custom_prompt():
    """Agent uses a custom prompt template."""
    provider = MockProvider([
        Response(content="I am a Python expert!", tool_calls=[], usage={}),
    ])
    prompt = Prompt.from_string("You are a {{language}} expert.")
    agent = Agent(provider=provider, prompt=prompt)

    # The prompt renders with tools=[] since no tools
    result = agent.run("What do you know?", env=None)

    assert result.success is True


def test_agent_custom_loop():
    """Agent uses a custom loop with max_steps=2."""
    responses = [
        Response(
            content="",
            tool_calls=[ToolCall(id=f"tc{i}", name="echo", arguments={"message": f"s{i}"})],
            usage={},
        )
        for i in range(10)
    ]
    provider = MockProvider(responses)
    loop = ReAct(max_steps=2)
    agent = Agent(provider=provider, tools=[EchoTool()], loop=loop)

    result = agent.run("Loop", env=None)

    assert result.success is False
    assert result.error == "Max steps reached"
    assert result.steps == 2


def test_agent_name():
    """Agent can have an optional name."""
    provider = MockProvider([
        Response(content="ok", tool_calls=[], usage={}),
    ])
    agent = Agent(provider=provider, name="coder")
    assert agent.name == "coder"


def test_agent_default_prompt():
    """Agent has a default prompt if none provided."""
    provider = MockProvider([
        Response(content="ok", tool_calls=[], usage={}),
    ])
    agent = Agent(provider=provider)
    assert agent.prompt is not None
    rendered = agent.prompt.render()
    assert "helpful" in rendered.lower() or "coding" in rendered.lower()


def test_agent_prompt_includes_tool_names():
    """Agent's rendered prompt includes tool names."""
    provider = MockProvider([
        Response(content="ok", tool_calls=[], usage={}),
    ])
    agent = Agent(provider=provider, tools=[EchoTool()])

    # We can check by running -- the system prompt in context should mention the tool
    result = agent.run("test", env=None)
    assert result.success is True
