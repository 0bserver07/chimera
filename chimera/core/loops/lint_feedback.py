"""Lint feedback loop: run linter after edits, feed errors back.

After the inner loop completes a turn, runs the configured linter on the
workspace.  If lint errors are found, adds them as context and runs another
turn to fix them.  Inspired by Aider's lint-and-fix workflow where
``lint_edited`` feeds errors back as a ``reflected_message``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Generator
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message, StepResult

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig


class LintFeedbackLoop:
    """Run a linter after each agent turn.  If errors found, feed them back.

    After the inner loop completes a turn, runs the configured linter
    on the workspace.  If lint errors are found, adds them as context
    and runs another turn to fix them.

    Args:
        inner: The loop to wrap.  Defaults to a :class:`ReAct` instance.
        linter: Linter command name (e.g. ``"ruff"``, ``"flake8"``).
        lint_args: Arguments passed to the linter.  Defaults to
            ``["check", "--no-fix"]`` for ruff.
        max_lint_rounds: Maximum number of lint-fix iterations before
            giving up.
        config: Optional :class:`LoopConfig` forwarded to the inner loop
            when it is constructed by default.

    Attributes:
        lint_history: List of raw lint outputs from each round.
    """

    def __init__(
        self,
        inner: ReAct | None = None,
        linter: str = "ruff",
        lint_args: list[str] | None = None,
        max_lint_rounds: int = 3,
        config: LoopConfig | None = None,
    ) -> None:
        self._inner = inner or ReAct()
        self._linter = linter
        self._lint_args = lint_args or ["check", "--no-fix"]
        self._max_rounds = max_lint_rounds
        self.config = config
        self.lint_history: list[str] = []

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run inner loop, then lint, then fix, repeat.

        Args:
            provider: LLM provider.
            tools: Available tools.
            context: Initial conversation context.
            env: Execution environment (optional).

        Returns:
            The final :class:`AgentResult` with accumulated cost and steps.
        """
        # First run: normal execution
        result = self._inner.run(provider, tools, context, env)
        total_cost = result.cost
        total_steps = result.steps

        # Lint-fix rounds
        for _round_num in range(self._max_rounds):
            lint_output = self._run_linter(env)
            self.lint_history.append(lint_output)

            if not lint_output.strip():
                # No lint errors -- done
                break

            # Lint errors found -- ask agent to fix
            fix_context = Context(system=context.system)
            fix_context.add(Message.user(
                f"The linter ({self._linter}) found these issues in the "
                f"code you wrote:\n\n"
                f"```\n{lint_output[:2000]}\n```\n\n"
                f"Fix these lint errors. Do not change functionality."
            ))

            fix_result = self._inner.run(provider, tools, fix_context, env)
            total_cost += fix_result.cost
            total_steps += fix_result.steps
            result = fix_result

        return AgentResult(
            output=result.output,
            steps=total_steps,
            tool_calls_total=result.tool_calls_total,
            cost=total_cost,
            success=result.success,
            error=result.error,
        )

    def _run_linter(self, env: Environment | None) -> str:
        """Run the linter on the workspace.

        Args:
            env: Execution environment used to determine the working
                directory.  Falls back to the current directory.

        Returns:
            Combined stdout and stderr from the linter, or an empty string
            if the linter is not available or times out.
        """
        workdir = getattr(env, "workdir", ".") if env else "."

        try:
            cmd = [self._linter] + self._lint_args
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(workdir),
                timeout=30,
            )
            # Exit code is the authoritative success signal, NOT output
            # emptiness. Ruff exits 0 when there are no violations even though
            # it may still print "All checks passed!" and, on an empty
            # workspace, a "No Python files found" warning — treating that
            # non-empty-but-successful output as lint errors would feed the
            # warning back to the model as a bogus fix task and derail it into
            # lint commentary instead of writing the solution.
            if proc.returncode == 0:
                return ""
            return proc.stdout + proc.stderr
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""  # linter not available or timed out, skip

    def iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> Generator[StepResult, None, AgentResult]:
        """Delegate to inner loop (no lint feedback in iter mode).

        Lint feedback semantics apply only to :meth:`run`.  When
        streaming steps via ``iter_steps`` the caller gets the raw
        inner-loop behaviour.
        """
        return self._inner.iter_steps(provider, tools, context, env)
