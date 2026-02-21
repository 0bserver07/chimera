from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.tool import BaseTool
from chimera.providers.base import Provider, Response
from chimera.types import AgentResult, Message, ToolCall, ToolResult


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


class FailingTool(BaseTool):
    name = "fail"
    description = "Always fails"
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, args, env):
        return ToolResult(output="", error="Something went wrong")


# --- Tests ---


def test_react_no_tool_calls():
    """Provider returns a plain text response -- loop completes in 1 step."""
    provider = MockProvider([
        Response(content="Hello!", tool_calls=[], usage={}),
    ])
    loop = ReAct(max_steps=10)
    context = Context(system="You are helpful.")
    context.add(Message.user("Hi"))

    result = loop.run(provider, [], context, env=None)

    assert result.success is True
    assert result.output == "Hello!"
    assert result.steps == 1
    assert result.tool_calls_total == 0


def test_react_one_tool_call_then_done():
    """Provider makes one tool call, then returns a final text response."""
    provider = MockProvider([
        Response(
            content="Let me echo that.",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "hello"})],
            usage={},
        ),
        Response(content="Done! The echo said hello.", tool_calls=[], usage={}),
    ])
    loop = ReAct(max_steps=10)
    context = Context(system="You are helpful.")
    context.add(Message.user("Echo hello"))

    result = loop.run(provider, [EchoTool()], context, env=None)

    assert result.success is True
    assert result.steps == 2
    assert result.tool_calls_total == 1
    assert "Done" in result.output


def test_react_multiple_tool_calls():
    """Provider makes two tool calls in separate steps."""
    provider = MockProvider([
        Response(
            content="",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "first"})],
            usage={},
        ),
        Response(
            content="",
            tool_calls=[ToolCall(id="tc2", name="echo", arguments={"message": "second"})],
            usage={},
        ),
        Response(content="All done.", tool_calls=[], usage={}),
    ])
    loop = ReAct(max_steps=10)
    context = Context()
    context.add(Message.user("Echo twice"))

    result = loop.run(provider, [EchoTool()], context, env=None)

    assert result.success is True
    assert result.steps == 3
    assert result.tool_calls_total == 2


def test_react_multiple_tool_calls_in_single_response():
    """Provider returns multiple tool calls in a single response."""
    provider = MockProvider([
        Response(
            content="",
            tool_calls=[
                ToolCall(id="tc1", name="echo", arguments={"message": "a"}),
                ToolCall(id="tc2", name="echo", arguments={"message": "b"}),
            ],
            usage={},
        ),
        Response(content="Done with both.", tool_calls=[], usage={}),
    ])
    loop = ReAct(max_steps=10)
    context = Context()
    context.add(Message.user("Echo a and b"))

    result = loop.run(provider, [EchoTool()], context, env=None)

    assert result.success is True
    assert result.steps == 2
    assert result.tool_calls_total == 2


def test_react_unknown_tool():
    """Provider calls a tool that doesn't exist -- error message added to context."""
    provider = MockProvider([
        Response(
            content="",
            tool_calls=[ToolCall(id="tc1", name="nonexistent", arguments={})],
            usage={},
        ),
        Response(content="I see the error.", tool_calls=[], usage={}),
    ])
    loop = ReAct(max_steps=10)
    context = Context()
    context.add(Message.user("Do something"))

    result = loop.run(provider, [EchoTool()], context, env=None)

    assert result.success is True
    assert result.steps == 2
    assert result.tool_calls_total == 1
    # Verify the error message was added to context
    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert any("unknown tool" in m.content.lower() for m in tool_msgs)


def test_react_tool_failure():
    """Provider calls a tool that returns an error."""
    provider = MockProvider([
        Response(
            content="",
            tool_calls=[ToolCall(id="tc1", name="fail", arguments={})],
            usage={},
        ),
        Response(content="I see it failed.", tool_calls=[], usage={}),
    ])
    loop = ReAct(max_steps=10)
    context = Context()
    context.add(Message.user("Try the failing tool"))

    result = loop.run(provider, [FailingTool()], context, env=None)

    assert result.success is True
    # Verify the error was fed back to context
    tool_msgs = [m for m in context.messages if m.role == "tool"]
    assert any("error" in m.content.lower() for m in tool_msgs)


def test_react_max_steps_reached():
    """Provider keeps making tool calls until max_steps is hit."""
    # Create a provider that always returns tool calls
    infinite_responses = [
        Response(
            content="",
            tool_calls=[ToolCall(id=f"tc{i}", name="echo", arguments={"message": f"step{i}"})],
            usage={},
        )
        for i in range(20)
    ]
    provider = MockProvider(infinite_responses)
    loop = ReAct(max_steps=3)
    context = Context()
    context.add(Message.user("Loop forever"))

    result = loop.run(provider, [EchoTool()], context, env=None)

    assert result.success is False
    assert result.error == "Max steps reached"
    assert result.steps == 3
    assert result.tool_calls_total == 3


def test_react_context_messages_accumulated():
    """Verify that context accumulates all messages from the loop."""
    provider = MockProvider([
        Response(
            content="Calling echo",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "test"})],
            usage={},
        ),
        Response(content="All done.", tool_calls=[], usage={}),
    ])
    loop = ReAct(max_steps=10)
    context = Context(system="System prompt")
    context.add(Message.user("Do echo"))

    loop.run(provider, [EchoTool()], context, env=None)

    # user + assistant(tool_call) + tool_result + assistant(final)
    assert len(context.messages) == 4
    assert context.messages[0].role == "user"
    assert context.messages[1].role == "assistant"
    assert context.messages[2].role == "tool"
    assert context.messages[3].role == "assistant"
