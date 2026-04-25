from chimera.core.context import Context
from chimera.core.prompt import Prompt
from chimera.types import Message, ToolCall


# --- Context tests ---


def test_context_empty():
    ctx = Context()
    assert ctx.messages == []
    assert len(ctx) == 0


def test_context_add_message():
    ctx = Context()
    ctx.add(Message.user("hello"))
    assert len(ctx) == 1
    assert ctx.messages[0].content == "hello"


def test_context_add_multiple():
    ctx = Context()
    ctx.add(Message.user("q1"))
    ctx.add(Message.assistant("a1"))
    assert len(ctx) == 2


def test_context_to_messages_no_system():
    ctx = Context()
    ctx.add(Message.user("hello"))
    msgs = ctx.to_messages()
    assert len(msgs) == 1
    assert msgs[0].role == "user"


def test_context_to_messages_with_system():
    ctx = Context(system="You are helpful.")
    ctx.add(Message.user("hello"))
    msgs = ctx.to_messages()
    assert len(msgs) == 2
    assert msgs[0].role == "system"
    assert msgs[0].content == "You are helpful."
    assert msgs[1].role == "user"


def test_context_preserves_tool_calls():
    ctx = Context()
    tc = ToolCall(id="1", name="read", arguments={"path": "x"})
    ctx.add(Message.assistant("thinking", tool_calls=[tc]))
    ctx.add(Message.tool("1", "file contents"))
    assert len(ctx) == 2
    assert ctx.messages[0].tool_calls[0].name == "read"
    assert ctx.messages[1].call_id == "1"


# --- Prompt tests ---


def test_prompt_from_string():
    p = Prompt.from_string("You are a coder.")
    assert "coder" in p.render()


def test_prompt_from_string_with_tools():
    p = Prompt.from_string("You are a coder.")
    rendered = p.render(tools=["read_file", "write_file"])
    assert "read_file" in rendered
    assert "write_file" in rendered


def test_prompt_from_file(tmp_path):
    f = tmp_path / "prompt.txt"
    f.write_text("You are a {{role}}.")
    p = Prompt.from_file(str(f))
    rendered = p.render(role="tester")
    assert "tester" in rendered
    assert "{{" not in rendered


def test_prompt_template_substitution():
    p = Prompt.from_string("Hello {{name}}, you are a {{role}}.")
    rendered = p.render(name="Alice", role="developer")
    assert rendered == "Hello Alice, you are a developer."


def test_prompt_unmatched_placeholders_preserved():
    p = Prompt.from_string("Hello {{name}}, age {{age}}.")
    rendered = p.render(name="Bob")
    assert "Bob" in rendered
    assert "{{age}}" in rendered


def test_prompt_no_tools():
    p = Prompt.from_string("You are helpful.")
    rendered = p.render()
    assert rendered == "You are helpful."
    assert "Available tools" not in rendered


def test_prompt_empty_tools():
    p = Prompt.from_string("You are helpful.")
    rendered = p.render(tools=[])
    assert "Available tools" not in rendered
