"""CliTemplateRunner — drive an external coding-agent CLI as an AgentRunner.

Generalizes the subprocess command-template pattern from
``chimera/mcp_servers/teammate_runner.py`` (which runs ``codex exec
--prompt-file {prompt_file}`` / ``opencode run "{prompt}"``) off the team
queue and behind the
:class:`~chimera.eval.runners.base.AgentRunner` protocol, so any templated
CLI becomes a matrix row. See ``docs/specs/agent-benchmark-matrix.md`` (A3).

The command template accepts these placeholders, substituted per task:

* ``{prompt}`` — the task prompt, shell-quoted then re-tokenized
* ``{prompt_file}`` — path to a tempfile holding the (multi-line) prompt
* ``{repo}`` — the repository / working directory for the attempt
* ``{patch_out}`` — path to a tempfile the CLI may write its patch to
  (used when ``patch_from="file"``)
* ``{task_id}`` — the task's id, when the task carries one

The subprocess call is injectable (``runner=subprocess.run``) so the runner
is unit-testable without any real CLI, subprocess, network, or LLM. These are
honest scaffolds: live execution requires the external tool installed, and
real end-to-end verification happens later with real infra.

This runner cannot observe the external agent's token cost, so it never
fabricates one — ``cost_usd`` stays ``0.0`` and ``raw["cost"] == "unknown"``.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
import time
from typing import TYPE_CHECKING, Any, Callable

from chimera.eval.runners.base import AgentRunResult

if TYPE_CHECKING:
    from chimera.env.base import Environment

_PROMPT_KEYS = ("prompt", "problem", "question", "instruction", "task")
_TASK_ID_KEYS = ("id", "instance_id", "task_id")

#: Placeholders recognized in a command template. ``prompt_file`` precedes
#: ``prompt`` in the alternation so ``{prompt_file}`` never matches as
#: ``{prompt}`` followed by a literal ``_file}``.
_PLACEHOLDER = re.compile(r"\{(prompt_file|prompt|repo|patch_out|task_id)\}")


def _prompt_of(task: Any) -> str:
    """Extract the prompt string from a benchmark task.

    Args:
        task: A prompt string, a dict with a ``prompt``/``problem`` (etc.)
            key, or an object exposing one of those attributes.

    Returns:
        The prompt text, or ``str(task)`` as a last resort.
    """
    if isinstance(task, str):
        return task
    if isinstance(task, dict):
        for key in _PROMPT_KEYS:
            val = task.get(key)
            if isinstance(val, str) and val:
                return val
        return str(task)
    for attr in _PROMPT_KEYS:
        val = getattr(task, attr, None)
        if isinstance(val, str) and val:
            return val
    return str(task)


def _task_id_of(task: Any) -> str:
    """Best-effort extraction of a task id (empty string when absent)."""
    if isinstance(task, dict):
        for key in _TASK_ID_KEYS:
            val = task.get(key)
            if isinstance(val, str) and val:
                return val
    else:
        for attr in _TASK_ID_KEYS:
            val = getattr(task, attr, None)
            if isinstance(val, str) and val:
                return val
    return ""


def _as_text(value: Any) -> str:
    """Coerce subprocess stdout/stderr (str, bytes, or None) to ``str``."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


class CliTemplateRunner:
    """Run an external CLI per task via a command template.

    Args:
        id: Row label for the matrix.
        cmd: Command template string with ``{prompt}`` / ``{prompt_file}`` /
            ``{repo}`` / ``{patch_out}`` / ``{task_id}`` placeholders. It is
            substituted, then ``shlex.split`` into an argv list.
        patch_from: How to collect the produced patch. ``"git-diff"`` runs
            ``git -C <repo> diff`` after a successful exit; ``"file"`` reads
            the ``{patch_out}`` tempfile the CLI was asked to write.
        timeout: Per-attempt wall-clock cap (seconds) passed to the runner.
        cwd: Working directory for the subprocess. Falls back to the task's
            ``repo`` or the environment's ``workdir`` when unset.
        runner: The ``subprocess.run``-compatible callable. Injectable so
            tests pass a fake — no real process is spawned.

    Raises:
        ValueError: If *patch_from* is not ``"git-diff"`` or ``"file"``.
    """

    def __init__(
        self,
        id: str,
        cmd: str,
        patch_from: str = "git-diff",
        timeout: float = 1800.0,
        cwd: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ) -> None:
        if patch_from not in ("git-diff", "file"):
            raise ValueError(
                f"patch_from must be 'git-diff' or 'file', got {patch_from!r}"
            )
        self.id = id
        self.cmd = cmd
        self.patch_from = patch_from
        self.timeout = timeout
        self.cwd = cwd
        self.runner = runner

    def run(
        self,
        task: Any,
        env: Environment | None = None,
        budget: Any = None,
    ) -> AgentRunResult:
        """Attempt *task* by rendering and running the command template.

        Args:
            task: A benchmark task (prompt string, dict, or object).
            env: Optional environment; its ``workdir`` is a fallback for the
                repo / working directory. The subprocess itself runs on the
                host in this scaffold (sandbox integration is a later phase).
            budget: Optional budget spec. This runner cannot honor a tool-call
                budget (the external agent does not route through Chimera's
                tool executor), so it is recorded in ``raw`` but not enforced.

        Returns:
            An :class:`AgentRunResult`: ``patch`` from git-diff/file on success,
            ``answer`` set to stdout, ``status`` mapped from the exit code
            (``completed`` | ``error`` | ``timeout``), and ``cost_usd`` left at
            ``0.0`` with ``raw["cost"] == "unknown"``.
        """
        prompt = _prompt_of(task)
        task_id = _task_id_of(task)
        repo = self._repo_of(task, env)
        run_cwd = str(self.cwd) if self.cwd else (repo if repo != "." else None)
        env_label = None if env is None else type(env).__name__
        budget_repr = None if budget is None else repr(budget)

        prompt_file = ""
        patch_out = ""
        started = time.monotonic()
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".prompt.txt", delete=False, encoding="utf-8",
            ) as handle:
                handle.write(prompt)
                prompt_file = handle.name
            with tempfile.NamedTemporaryFile(
                "w", suffix=".patch", delete=False, encoding="utf-8",
            ) as handle:
                patch_out = handle.name

            mapping = {
                "prompt": prompt,
                "prompt_file": prompt_file,
                "repo": repo,
                "patch_out": patch_out,
                "task_id": task_id,
            }
            argv = self._render_argv(mapping)

            try:
                proc = self.runner(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=run_cwd,
                )
            except subprocess.TimeoutExpired as exc:
                return AgentRunResult(
                    patch=None,
                    answer=_as_text(getattr(exc, "stdout", "")),
                    status="timeout",
                    cost_usd=0.0,
                    wall_clock_sec=time.monotonic() - started,
                    raw={
                        "cost": "unknown",
                        "timed_out": True,
                        "exit_code": None,
                        "stderr": _as_text(getattr(exc, "stderr", "")),
                        "argv": argv,
                        "patch_from": self.patch_from,
                        "budget": budget_repr,
                        "env": env_label,
                    },
                )

            returncode = int(getattr(proc, "returncode", 0) or 0)
            stdout = _as_text(getattr(proc, "stdout", ""))
            stderr = _as_text(getattr(proc, "stderr", ""))
            status = "completed" if returncode == 0 else "error"

            patch: str | None = None
            patch_error: str | None = None
            if status == "completed":
                try:
                    patch = self._extract_patch(repo, patch_out)
                except Exception as exc:  # noqa: BLE001 - report, never crash
                    patch_error = repr(exc)

            raw: dict[str, Any] = {
                "cost": "unknown",
                "timed_out": False,
                "exit_code": returncode,
                "stderr": stderr,
                "argv": argv,
                "patch_from": self.patch_from,
                "budget": budget_repr,
                "env": env_label,
            }
            if patch_error is not None:
                raw["patch_error"] = patch_error
            return AgentRunResult(
                patch=patch,
                answer=stdout,
                status=status,
                cost_usd=0.0,
                wall_clock_sec=time.monotonic() - started,
                raw=raw,
            )
        finally:
            for path in (prompt_file, patch_out):
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

    def _render_argv(self, mapping: dict[str, str]) -> list[str]:
        """Substitute placeholders into the template and tokenize to argv.

        Placeholder values are ``shlex.quote``-d before substitution so a
        prompt with spaces survives ``shlex.split`` as a single token. The
        substitution is a single regex pass, so a value that itself contains
        a ``{...}`` sequence is not re-expanded.
        """
        rendered = _PLACEHOLDER.sub(
            lambda m: shlex.quote(mapping[m.group(1)]), self.cmd,
        )
        return shlex.split(rendered)

    def _extract_patch(self, repo: str, patch_out: str) -> str | None:
        """Collect the produced patch per ``patch_from``.

        Args:
            repo: Repo/working directory for ``git -C <repo> diff``.
            patch_out: Tempfile the CLI was asked to write (``file`` mode).

        Returns:
            The unified diff text, or ``None`` when empty / unavailable.
        """
        if self.patch_from == "git-diff":
            proc = self.runner(
                ["git", "-C", repo, "diff"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if int(getattr(proc, "returncode", 0) or 0) == 0:
                out = _as_text(getattr(proc, "stdout", ""))
                return out or None
            return None
        # patch_from == "file"
        try:
            with open(patch_out, encoding="utf-8") as handle:
                data = handle.read()
        except OSError:
            return None
        return data or None

    def _repo_of(self, task: Any, env: Environment | None) -> str:
        """Resolve the repo/working directory (defaults to ``"."``)."""
        if isinstance(task, dict):
            repo = task.get("repo")
            if isinstance(repo, str) and repo:
                return repo
        if self.cwd:
            return str(self.cwd)
        workdir = getattr(env, "workdir", None)
        if workdir:
            return str(workdir)
        return "."
