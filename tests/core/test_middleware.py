"""Tests for chimera.core.middleware — loop middleware system and safety net."""
from __future__ import annotations

from unittest.mock import MagicMock

from chimera.core.middleware import (
    LoggingMiddleware,
    LoopMiddleware,
    MiddlewareChain,
    SafetyNetMiddleware,
)
from chimera.core.context import Context
from chimera.types import AgentResult, Message


def test_middleware_chain_before_model():
    class AddMessage(LoopMiddleware):
        def before_model(self, context, tools):
            context.add(Message.user("injected"))
            return context

    chain = MiddlewareChain([AddMessage()])
    ctx = Context(system="test")
    result = chain.run_before_model(ctx, [])
    assert any("injected" in m.content for m in result.messages)


def test_middleware_chain_after_model():
    class ModifyResponse(LoopMiddleware):
        def after_model(self, response, context):
            response.content = response.content + " [modified]"
            return response

    chain = MiddlewareChain([ModifyResponse()])
    resp = MagicMock()
    resp.content = "original"
    result = chain.run_after_model(resp, Context())
    assert "[modified]" in result.content


def test_middleware_chain_after_agent():
    class TagResult(LoopMiddleware):
        def after_agent(self, result, env):
            result.output = result.output + " [tagged]"
            return result

    chain = MiddlewareChain([TagResult()])
    result = AgentResult(output="done", steps=1, tool_calls_total=0, cost=0, success=True)
    tagged = chain.run_after_agent(result, None)
    assert "[tagged]" in tagged.output


def test_multiple_middleware_chain():
    class A(LoopMiddleware):
        def before_model(self, ctx, tools):
            ctx.add(Message.user("A"))
            return ctx

    class B(LoopMiddleware):
        def before_model(self, ctx, tools):
            ctx.add(Message.user("B"))
            return ctx

    chain = MiddlewareChain([A(), B()])
    ctx = Context()
    chain.run_before_model(ctx, [])
    contents = [m.content for m in ctx.messages]
    assert "A" in contents
    assert "B" in contents


def test_logging_middleware():
    logger = LoggingMiddleware()
    ctx = Context()
    logger.before_model(ctx, [])
    resp = MagicMock()
    resp.content = "hi"
    logger.after_model(resp, ctx)
    result = AgentResult(output="ok", steps=1, tool_calls_total=0, cost=0, success=True)
    logger.after_agent(result, None)
    assert len(logger.calls) == 3


def test_safety_net_no_env():
    safety = SafetyNetMiddleware()
    result = AgentResult(output="ok", steps=1, tool_calls_total=0, cost=0, success=True)
    safety.after_agent(result, None)
    assert not safety.auto_committed


def test_safety_net_no_changes():
    safety = SafetyNetMiddleware()
    env = MagicMock()
    env.run_command.return_value = MagicMock(stdout="")
    result = AgentResult(output="ok", steps=1, tool_calls_total=0, cost=0, success=True)
    safety.after_agent(result, env)
    assert not safety.auto_committed


def test_safety_net_auto_commits():
    safety = SafetyNetMiddleware(commit_message="auto")
    env = MagicMock()
    env.run_command.side_effect = [
        MagicMock(stdout="M file.py\n"),  # git status
        MagicMock(stdout=""),  # git add
        MagicMock(stdout=""),  # git commit
    ]
    result = AgentResult(output="ok", steps=1, tool_calls_total=0, cost=0, success=True)
    safety.after_agent(result, env)
    assert safety.auto_committed


def test_empty_chain():
    chain = MiddlewareChain()
    ctx = Context()
    assert chain.run_before_model(ctx, []) is ctx


def test_default_middleware_noop():
    mw = LoopMiddleware()
    ctx = Context()
    assert mw.before_model(ctx, []) is ctx


def test_middleware_chain_add():
    chain = MiddlewareChain()
    assert len(chain.middleware) == 0
    mw = LoggingMiddleware()
    chain.add(mw)
    assert len(chain.middleware) == 1


def test_safety_net_disabled():
    safety = SafetyNetMiddleware(auto_commit=False)
    env = MagicMock()
    result = AgentResult(output="ok", steps=1, tool_calls_total=0, cost=0, success=True)
    safety.after_agent(result, env)
    assert not safety.auto_committed
    env.run_command.assert_not_called()


def test_safety_net_exception_handling():
    safety = SafetyNetMiddleware()
    env = MagicMock()
    env.run_command.side_effect = RuntimeError("not a git repo")
    result = AgentResult(output="ok", steps=1, tool_calls_total=0, cost=0, success=True)
    out = safety.after_agent(result, env)
    assert not safety.auto_committed
    assert out is result
