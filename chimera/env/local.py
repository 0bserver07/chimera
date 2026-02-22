from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult


class LocalEnvironment(Environment):
    """Local filesystem environment with git-based checkpointing."""

    def __init__(
        self,
        workdir: str,
        test_cmd: str = "python -m pytest",
        timeout: int = 300,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.test_cmd = test_cmd
        self.timeout = timeout
        self._checkpoint_dir: Path | None = None

    def setup(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._checkpoint_dir = self.workdir / ".chimera_checkpoints"
        self._checkpoint_dir.mkdir(exist_ok=True)

    def cleanup(self) -> None:
        pass  # Don't delete workdir -- user may want to inspect

    def read_file(self, path: str) -> str:
        full = self.workdir / path
        if not full.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return full.read_text()

    def write_file(self, path: str, content: str) -> None:
        full = self.workdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def list_files(self, pattern: str = "**/*") -> list[str]:
        checkpoint_dir = self._checkpoint_dir
        results = []
        for p in self.workdir.glob(pattern):
            if p.is_file():
                if checkpoint_dir and str(p).startswith(str(checkpoint_dir)):
                    continue
                results.append(str(p.relative_to(self.workdir)))
        return sorted(results)

    def run_command(self, cmd: str, timeout: int | None = None, shell_name: str = "main") -> CommandResult:
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.workdir),
                timeout=timeout or self.timeout,
            )
            return CommandResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Command timed out", exit_code=124)

    def run_tests(self) -> TestResult:
        result = self.run_command(self.test_cmd)
        return self._parse_test_output(result)

    def checkpoint(self) -> str:
        assert self._checkpoint_dir is not None
        # Find next checkpoint ID
        existing = [
            int(d.name) for d in self._checkpoint_dir.iterdir()
            if d.is_dir() and d.name.isdigit()
        ]
        cp_id = str(max(existing, default=-1) + 1)
        cp_dir = self._checkpoint_dir / cp_id

        # Copy all non-checkpoint files
        self._copy_workspace(self.workdir, cp_dir)
        return cp_id

    def restore(self, checkpoint_id: str) -> None:
        assert self._checkpoint_dir is not None
        cp_dir = self._checkpoint_dir / checkpoint_id
        if not cp_dir.exists():
            raise ValueError(f"Checkpoint {checkpoint_id} not found")

        # Remove current files (except checkpoints)
        for item in self.workdir.iterdir():
            if item != self._checkpoint_dir:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        # Restore from checkpoint
        self._copy_workspace(cp_dir, self.workdir)

    def _copy_workspace(self, src: Path, dst: Path) -> None:
        """Copy workspace files, excluding checkpoint directory."""
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name == ".chimera_checkpoints":
                continue
            dest_item = dst / item.name
            if item.is_dir():
                shutil.copytree(item, dest_item, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest_item)

    def _parse_test_output(self, result: CommandResult) -> TestResult:
        """Parse pytest output to extract pass/fail counts."""
        output = result.stdout + result.stderr
        passed = failed = errors = 0

        # Match pytest summary line: "X passed, Y failed, Z errors"
        match = re.search(r"(\d+) passed", output)
        if match:
            passed = int(match.group(1))
        match = re.search(r"(\d+) failed", output)
        if match:
            failed = int(match.group(1))
        match = re.search(r"(\d+) error", output)
        if match:
            errors = int(match.group(1))

        return TestResult(passed=passed, failed=failed, errors=errors, output=output)
