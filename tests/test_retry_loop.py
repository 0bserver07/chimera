"""Tests for chimera.core.loops.retry — RetryLoop with scoring."""

from unittest.mock import MagicMock

from chimera.core.context import Context
from chimera.core.loops.retry import RetryAttempt, RetryLoop
from chimera.types import AgentResult, Message


def _mock_inner(*results: AgentResult):
    """Create a mock inner loop that returns sequential results."""
    inner = MagicMock()
    inner.run = MagicMock(side_effect=list(results))
    return inner


def test_retry_succeeds_first_try():
    result = AgentResult(output="done", steps=1, tool_calls_total=0, cost=0.01, success=True)
    loop = RetryLoop(inner=_mock_inner(result), max_retries=3)
    ctx = Context(system="test")
    ctx.add(Message.user("do it"))
    out = loop.run(MagicMock(), [], ctx, None)
    assert out.success
    assert out.output == "done"
    assert len(loop.attempts) == 1
    assert loop.attempts[0].score == 1.0


def test_retry_on_failure():
    fail = AgentResult(output="error", steps=1, tool_calls_total=0, cost=0.01, success=False)
    success = AgentResult(output="done", steps=1, tool_calls_total=0, cost=0.01, success=True)
    loop = RetryLoop(inner=_mock_inner(fail, success), max_retries=3)
    ctx = Context(system="test")
    ctx.add(Message.user("do it"))
    out = loop.run(MagicMock(), [], ctx, None)
    assert out.success
    assert out.output == "done"
    assert len(loop.attempts) == 2
    assert loop.attempts[0].score == 0.0
    assert loop.attempts[1].score == 1.0


def test_retry_picks_best():
    r1 = AgentResult(output="bad", steps=1, tool_calls_total=0, cost=0.01, success=False)
    r2 = AgentResult(output="ok", steps=1, tool_calls_total=0, cost=0.01, success=False)
    r3 = AgentResult(output="good", steps=1, tool_calls_total=0, cost=0.01, success=False)

    def scorer(r: AgentResult) -> float:
        return {"bad": 0.2, "ok": 0.5, "good": 0.8}.get(r.output, 0.0)

    loop = RetryLoop(inner=_mock_inner(r1, r2, r3), max_retries=3, scorer=scorer)
    ctx = Context(system="test")
    ctx.add(Message.user("do it"))
    out = loop.run(MagicMock(), [], ctx, None)
    assert out.output == "good"
    assert len(loop.attempts) == 3


def test_max_retries_respected():
    fail = AgentResult(output="fail", steps=1, tool_calls_total=0, cost=0.01, success=False)
    loop = RetryLoop(inner=_mock_inner(fail, fail), max_retries=2)
    ctx = Context(system="test")
    ctx.add(Message.user("do it"))
    out = loop.run(MagicMock(), [], ctx, None)
    assert not out.success
    assert len(loop.attempts) == 2


def test_custom_scorer_with_threshold():
    r = AgentResult(output="partial", steps=3, tool_calls_total=2, cost=0.05, success=True)
    loop = RetryLoop(
        inner=_mock_inner(r),
        scorer=lambda r: 0.7,
        success_threshold=0.5,
    )
    ctx = Context(system="test")
    ctx.add(Message.user("do it"))
    out = loop.run(MagicMock(), [], ctx, None)
    assert out.success
    assert loop.attempts[0].score == 0.7
    # Stopped after 1 attempt because 0.7 >= 0.5
    assert len(loop.attempts) == 1


def test_threshold_not_met_retries_all():
    r1 = AgentResult(output="a", steps=1, tool_calls_total=0, cost=0.01, success=True)
    r2 = AgentResult(output="b", steps=1, tool_calls_total=0, cost=0.01, success=True)
    r3 = AgentResult(output="c", steps=1, tool_calls_total=0, cost=0.01, success=True)

    loop = RetryLoop(
        inner=_mock_inner(r1, r2, r3),
        scorer=lambda r: 0.3,
        success_threshold=0.9,
        max_retries=3,
    )
    ctx = Context(system="test")
    ctx.add(Message.user("do it"))
    loop.run(MagicMock(), [], ctx, None)
    # All 3 attempts should run since 0.3 < 0.9
    assert len(loop.attempts) == 3


def test_retry_context_includes_feedback():
    """Second attempt should receive a context with retry feedback."""
    fail = AgentResult(output="first try failed", steps=1, tool_calls_total=0, cost=0.01, success=False)
    success = AgentResult(output="done", steps=1, tool_calls_total=0, cost=0.01, success=True)

    inner = _mock_inner(fail, success)
    loop = RetryLoop(inner=inner, max_retries=3)
    ctx = Context(system="test system")
    ctx.add(Message.user("original task"))
    loop.run(MagicMock(), [], ctx, None)

    # The second call should have a fresh context with retry info
    assert inner.run.call_count == 2
    second_call_ctx = inner.run.call_args_list[1][0][2]  # third positional arg is context
    msgs = second_call_ctx.to_messages()
    # Should have system + original user msg + retry feedback
    assert msgs[0].role == "system"
    assert any("Previous attempt" in m.content and "0%" in m.content for m in msgs if m.role == "user")


def test_attempt_dataclass():
    result = AgentResult(output="x", steps=1, tool_calls_total=0, cost=0.0, success=True)
    attempt = RetryAttempt(attempt=1, result=result, score=0.95)
    assert attempt.attempt == 1
    assert attempt.score == 0.95
    assert attempt.result is result


def test_iter_steps_delegates():
    """iter_steps should delegate to the inner loop."""
    inner = MagicMock()
    sentinel = object()
    inner.iter_steps.return_value = sentinel
    loop = RetryLoop(inner=inner)
    ctx = Context(system="test")
    provider = MagicMock()
    result = loop.iter_steps(provider, [], ctx, None)
    assert result is sentinel
    inner.iter_steps.assert_called_once_with(provider, [], ctx, None)


def test_attempts_cleared_between_runs():
    r1 = AgentResult(output="a", steps=1, tool_calls_total=0, cost=0.01, success=True)
    r2 = AgentResult(output="b", steps=1, tool_calls_total=0, cost=0.01, success=True)
    inner = MagicMock()
    inner.run = MagicMock(side_effect=[r1, r2])
    loop = RetryLoop(inner=inner, max_retries=1)
    ctx = Context(system="test")
    ctx.add(Message.user("do it"))

    loop.run(MagicMock(), [], ctx, None)
    assert len(loop.attempts) == 1

    loop.run(MagicMock(), [], ctx, None)
    assert len(loop.attempts) == 1  # cleared, not accumulated


def test_best_from_middle_attempt():
    """Best attempt might not be the last one."""
    r1 = AgentResult(output="meh", steps=1, tool_calls_total=0, cost=0.01, success=False)
    r2 = AgentResult(output="great", steps=1, tool_calls_total=0, cost=0.01, success=False)
    r3 = AgentResult(output="ok", steps=1, tool_calls_total=0, cost=0.01, success=False)

    scores = {"meh": 0.1, "great": 0.9, "ok": 0.5}
    loop = RetryLoop(
        inner=_mock_inner(r1, r2, r3),
        scorer=lambda r: scores.get(r.output, 0.0),
        max_retries=3,
        success_threshold=1.0,  # never satisfied
    )
    ctx = Context(system="test")
    ctx.add(Message.user("do it"))
    out = loop.run(MagicMock(), [], ctx, None)
    assert out.output == "great"
