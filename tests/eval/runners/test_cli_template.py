"""Plumbing tests for CliTemplateRunner (no real CLI / subprocess / LLM).

The subprocess callable is injected as a fake, so these exercise placeholder
substitution, result mapping, patch extraction, and the timeout/error paths
without spawning any process.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from chimera.eval.runners import AgentRunner, AgentRunResult
from chimera.eval.runners.cli_template import CliTemplateRunner


class _FakeRunner:
    """Records argv/kwargs per call and returns a canned CompletedProcess.

    Args:
        returncode: Exit code to report.
        stdout: Constant stdout, unless *route* is given.
        stderr: Constant stderr.
        raises: If set, raise this exception instead of returning.
        route: ``callable(argv) -> stdout`` to vary stdout per call (e.g. to
            distinguish the agent command from the ``git diff`` command).
        side_effect: ``callable(argv)`` run for effects (e.g. writing a patch
            file to a ``{patch_out}`` path).
    """

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "done",
        stderr: str = "",
        raises: BaseException | None = None,
        route: Callable[[list[str]], str] | None = None,
        side_effect: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.returncode = returncode
        self._stdout = stdout
        self.stderr = stderr
        self.raises = raises
        self.route = route
        self.side_effect = side_effect

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        if self.side_effect is not None:
            self.side_effect(argv)
        stdout = self.route(argv) if self.route is not None else self._stdout
        return subprocess.CompletedProcess(argv, self.returncode, stdout=stdout, stderr=self.stderr)


def test_substitutes_placeholders_and_maps_result() -> None:
    def route(argv: list[str]) -> str:
        return "diff --git a/x b/x\n+patched\n" if argv[:1] == ["git"] else "agent said hi"

    fake = _FakeRunner(route=route)
    runner = CliTemplateRunner(
        "codex",
        cmd="codex exec --file {prompt_file} --task {task_id} --repo {repo} -- {prompt}",
        runner=fake,
    )
    assert isinstance(runner, AgentRunner)  # runtime_checkable

    out = runner.run({"id": "t-1", "prompt": "fix the bug", "repo": "/work/repo"})

    assert isinstance(out, AgentRunResult)
    # First call is the agent command.
    agent_argv, agent_kwargs = fake.calls[0]
    assert agent_argv[0] == "codex"
    assert "fix the bug" in agent_argv  # prompt survived shlex.split as one token
    assert "t-1" in agent_argv  # {task_id} substituted
    assert "/work/repo" in agent_argv  # {repo} substituted
    assert any(a.endswith(".prompt.txt") for a in agent_argv)  # {prompt_file} path
    assert agent_kwargs["capture_output"] is True
    assert agent_kwargs["text"] is True
    assert agent_kwargs["timeout"] == 1800.0
    assert agent_kwargs["cwd"] == "/work/repo"  # repo used as subprocess cwd

    # Second call is the git diff for patch extraction (patch_from defaults to git-diff).
    diff_argv, _ = fake.calls[1]
    assert diff_argv == ["git", "-C", "/work/repo", "diff"]

    assert out.status == "completed"
    assert out.answer == "agent said hi"  # stdout of the agent command
    assert out.patch == "diff --git a/x b/x\n+patched\n"  # stdout of git diff
    assert out.cost_usd == 0.0
    assert out.raw["cost"] == "unknown"  # never fabricated
    assert out.wall_clock_sec >= 0.0


def test_prompt_file_is_written_and_cleaned_up() -> None:
    captured: dict[str, str] = {}

    def side_effect(argv: list[str]) -> None:
        prompt_file = next(a for a in argv if a.endswith(".prompt.txt"))
        captured["prompt_file"] = prompt_file
        captured["contents"] = Path(prompt_file).read_text(encoding="utf-8")

    fake = _FakeRunner(side_effect=side_effect)
    runner = CliTemplateRunner("x", cmd="x {prompt_file}", runner=fake)

    runner.run({"prompt": "multi\nline\nprompt"})

    assert captured["contents"] == "multi\nline\nprompt"  # prompt written to tempfile
    assert not Path(captured["prompt_file"]).exists()  # cleaned up after run


def test_patch_from_file_reads_patch_out() -> None:
    captured: dict[str, str] = {}

    def side_effect(argv: list[str]) -> None:
        patch_path = next(a for a in argv if a.endswith(".patch"))
        captured["patch_path"] = patch_path
        Path(patch_path).write_text("PATCH-CONTENT\n", encoding="utf-8")

    fake = _FakeRunner(returncode=0, stdout="ok", side_effect=side_effect)
    runner = CliTemplateRunner(
        "aider",
        cmd="aider --apply {patch_out} --file {prompt_file}",
        patch_from="file",
        runner=fake,
    )

    out = runner.run({"prompt": "do it"})

    assert out.status == "completed"
    assert out.patch == "PATCH-CONTENT\n"
    assert out.answer == "ok"
    assert len(fake.calls) == 1  # no git diff in file mode
    assert not Path(captured["patch_path"]).exists()  # patch_out tempfile cleaned up


def test_nonzero_exit_maps_to_error() -> None:
    fake = _FakeRunner(returncode=1, stdout="", stderr="boom")
    runner = CliTemplateRunner("x", cmd="x {prompt_file}", runner=fake)

    out = runner.run("just a raw prompt")

    assert out.status == "error"
    assert out.patch is None
    assert out.raw["exit_code"] == 1
    assert out.raw["stderr"] == "boom"
    assert out.raw["cost"] == "unknown"
    assert len(fake.calls) == 1  # no patch extraction attempted on failure


def test_timeout_maps_to_timeout_status() -> None:
    fake = _FakeRunner(raises=subprocess.TimeoutExpired(cmd="x", timeout=1.0))
    runner = CliTemplateRunner("x", cmd="x {prompt_file}", timeout=1.0, runner=fake)

    out = runner.run({"prompt": "p"})

    assert out.status == "timeout"
    assert out.patch is None
    assert out.raw["timed_out"] is True
    assert out.raw["cost"] == "unknown"


def test_invalid_patch_from_raises() -> None:
    with pytest.raises(ValueError, match="patch_from"):
        CliTemplateRunner("x", cmd="x {prompt}", patch_from="magic")
