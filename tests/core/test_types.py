from chimera.types import (
    Message,
    ToolCall,
    ToolResult,
    CommandResult,
    TestResult,
    StepResult,
    AgentResult,
)


def test_message_user():
    msg = Message.user("hello")
    assert msg.role == "user"
    assert msg.content == "hello"


def test_message_assistant():
    msg = Message.assistant(
        "hi", tool_calls=[ToolCall(id="1", name="read", arguments={"path": "x"})]
    )
    assert msg.role == "assistant"
    assert len(msg.tool_calls) == 1


def test_message_tool():
    msg = Message.tool(call_id="1", content="file contents")
    assert msg.role == "tool"
    assert msg.call_id == "1"


def test_tool_result():
    r = ToolResult(output="hello", error=None, metadata={"lines": 5})
    assert r.output == "hello"
    assert r.success is True


def test_tool_result_error():
    r = ToolResult(output="", error="not found")
    assert r.success is False


def test_command_result():
    r = CommandResult(stdout="ok", stderr="", exit_code=0)
    assert r.success is True


def test_command_result_failure():
    r = CommandResult(stdout="", stderr="error", exit_code=1)
    assert r.success is False


def test_test_result():
    r = TestResult(passed=8, failed=2, errors=0, output="...")
    assert r.total == 10
    assert r.pass_rate == 0.8
    assert r.all_passed is False


def test_test_result_all_pass():
    r = TestResult(passed=5, failed=0, errors=0, output="ok")
    assert r.all_passed is True
    assert r.pass_rate == 1.0


def test_step_result():
    r = StepResult(
        message=Message.assistant("done"),
        tool_calls=[],
        done=True,
    )
    assert r.done is True


def test_agent_result():
    r = AgentResult(
        output="completed",
        steps=5,
        tool_calls_total=12,
        cost=0.05,
        success=True,
    )
    assert r.success is True
