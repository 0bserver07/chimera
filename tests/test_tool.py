from chimera.core.tool import BaseTool, tool
from chimera.env.local import LocalEnvironment
from chimera.types import ToolResult


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a message"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, args: dict, env) -> ToolResult:
        return ToolResult(output=args["message"])


def test_tool_instance():
    t = EchoTool()
    assert t.name == "echo"
    assert t.description == "Echo a message"


def test_tool_execute():
    t = EchoTool()
    result = t.execute({"message": "hello"}, env=None)
    assert result.output == "hello"
    assert result.success


def test_tool_to_schema():
    t = EchoTool()
    schema = t.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"


def test_tool_to_anthropic_schema():
    t = EchoTool()
    schema = t.to_anthropic_schema()
    assert schema["name"] == "echo"
    assert "input_schema" in schema


def test_tool_decorator():
    @tool(name="greet", description="Greet someone", parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    })
    def greet(args, env):
        return ToolResult(output=f"Hello {args['name']}")

    assert greet.name == "greet"
    result = greet.execute({"name": "World"}, env=None)
    assert result.output == "Hello World"
