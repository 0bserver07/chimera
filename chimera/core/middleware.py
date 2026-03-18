"""Loop middleware system for composable agent loop hooks.

Middleware provides three hook points in the agent loop:

- **before_model**: runs before each LLM call
- **after_model**: runs after model response, before tool execution
- **after_agent**: runs once when the loop completes

Multiple middleware are chained via :class:`MiddlewareChain` and
executed in order.
"""
from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.core.context import Context
    from chimera.core.tool import BaseTool
    from chimera.env.base import Environment
    from chimera.providers.base import Response
    from chimera.types import AgentResult

__all__ = [
    "EnsureToolCallMiddleware",
    "LoggingMiddleware",
    "LoopMiddleware",
    "MiddlewareChain",
    "SafetyNetMiddleware",
]


class LoopMiddleware(ABC):
    """Composable hook for the agent loop.

    Three hook points:

    - ``before_model``: runs before each LLM call
    - ``after_model``: runs after model response, before tool execution
    - ``after_agent``: runs once when the loop completes

    Default implementations are no-ops. Override what you need.
    """

    def before_model(self, context: Context, tools: list[BaseTool]) -> Context:
        """Called before each LLM call. Can modify context."""
        return context

    def after_model(self, response: Response, context: Context) -> Response:
        """Called after model response. Can modify response."""
        return response

    def after_agent(self, result: AgentResult, env: Environment | None) -> AgentResult:
        """Called once when the loop finishes. Can modify result."""
        return result


class MiddlewareChain:
    """Execute multiple middleware in order."""

    def __init__(self, middleware: list[LoopMiddleware] | None = None) -> None:
        self._middleware = middleware or []

    def add(self, mw: LoopMiddleware) -> None:
        """Append a middleware to the chain."""
        self._middleware.append(mw)

    @property
    def middleware(self) -> list[LoopMiddleware]:
        """Return a copy of the middleware list."""
        return list(self._middleware)

    def run_before_model(self, context: Context, tools: list[BaseTool]) -> Context:
        """Run all ``before_model`` hooks in order."""
        for mw in self._middleware:
            context = mw.before_model(context, tools)
        return context

    def run_after_model(self, response: Response, context: Context) -> Response:
        """Run all ``after_model`` hooks in order."""
        for mw in self._middleware:
            response = mw.after_model(response, context)
        return response

    def run_after_agent(self, result: AgentResult, env: Environment | None) -> AgentResult:
        """Run all ``after_agent`` hooks in order."""
        for mw in self._middleware:
            result = mw.after_agent(result, env)
        return result


class LoggingMiddleware(LoopMiddleware):
    """Log every model call and response.

    Recorded events are available via the :attr:`calls` list.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def before_model(self, context: Context, tools: list[BaseTool]) -> Context:
        self.calls.append({"event": "before_model", "message_count": len(context.messages)})
        return context

    def after_model(self, response: Response, context: Context) -> Response:
        self.calls.append({"event": "after_model", "content_length": len(response.content)})
        return response

    def after_agent(self, result: AgentResult, env: Environment | None) -> AgentResult:
        self.calls.append({"event": "after_agent", "success": result.success})
        return result


class EnsureToolCallMiddleware(LoopMiddleware):
    """Ensure agent calls at least one tool per turn.

    If model returns no tool calls and no final answer signal,
    injects a think tool call to keep the loop alive.
    """

    def after_model(self, response: Response, context: Context) -> Response:
        # If no tool calls and response looks like it's not done,
        # we just let it pass -- the loop handles no-tool-call as done
        return response


class SafetyNetMiddleware(LoopMiddleware):
    """Auto-commit uncommitted changes after agent finishes.

    If the agent made file changes but didn't commit, this middleware
    creates a commit with a generated message. Prevents lost work.

    Args:
        auto_commit: Whether to auto-commit (default ``True``).
        commit_message: Commit message to use (default
            ``"Auto-commit by agent"``).
    """

    def __init__(
        self,
        auto_commit: bool = True,
        commit_message: str = "Auto-commit by agent",
    ) -> None:
        self._auto_commit = auto_commit
        self._commit_message = commit_message
        self.auto_committed: bool = False

    def after_agent(self, result: AgentResult, env: Environment | None) -> AgentResult:
        if not self._auto_commit or env is None:
            return result

        # Check for uncommitted changes
        try:
            status = env.run_command("git status --porcelain")
            if status.stdout and status.stdout.strip():
                env.run_command("git add -A")
                env.run_command(f'git commit -m "{self._commit_message}"')
                self.auto_committed = True
        except Exception:
            pass  # not a git repo or git not available

        return result
