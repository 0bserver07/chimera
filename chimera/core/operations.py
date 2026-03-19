"""Protocol interfaces for pluggable tool backends."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

from chimera.types import CommandResult


@runtime_checkable
class ReadOps(Protocol):
    """Backend for file reading."""
    def read_file(self, path: str) -> str: ...
    def file_exists(self, path: str) -> bool: ...


@runtime_checkable
class WriteOps(Protocol):
    """Backend for file writing."""
    def write_file(self, path: str, content: str) -> None: ...


@runtime_checkable
class BashOps(Protocol):
    """Backend for command execution."""
    def run_command(self, command: str, timeout: int = 120,
                    cwd: str | None = None) -> CommandResult: ...


@runtime_checkable
class SearchOps(Protocol):
    """Backend for file search."""
    def search_files(self, pattern: str, path: str = ".") -> list[str]: ...
    def list_files(self, pattern: str = "**/*") -> list[str]: ...


class LocalReadOps:
    """ReadOps using local filesystem."""
    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def read_file(self, path: str) -> str:
        full = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        with open(full) as f:
            return f.read()

    def file_exists(self, path: str) -> bool:
        full = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        return os.path.exists(full)


class LocalWriteOps:
    """WriteOps using local filesystem."""
    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def write_file(self, path: str, content: str) -> None:
        full = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        with open(full, "w") as f:
            f.write(content)


class LocalBashOps:
    """BashOps using subprocess."""
    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def run_command(self, command: str, timeout: int = 120,
                    cwd: str | None = None) -> CommandResult:
        work_dir = cwd or self.cwd
        try:
            result = subprocess.run(
                command, shell=True, cwd=work_dir,
                capture_output=True, text=True, timeout=timeout,
            )
            return CommandResult(
                stdout=result.stdout, stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Timeout", exit_code=-1)


class LocalSearchOps:
    """SearchOps using local filesystem."""
    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def list_files(self, pattern: str = "**/*") -> list[str]:
        base = Path(self.cwd)
        return [
            str(p.relative_to(base))
            for p in base.glob(pattern)
            if p.is_file()
        ]

    def search_files(self, pattern: str, path: str = ".") -> list[str]:
        import re
        regex = re.compile(pattern)
        results: list[str] = []
        search_dir = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        for filepath in Path(search_dir).rglob("*"):
            if not filepath.is_file():
                continue
            try:
                content = filepath.read_text()
            except (UnicodeDecodeError, PermissionError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    rel = str(filepath.relative_to(self.cwd)) if not os.path.isabs(path) else str(filepath)
                    results.append(f"{rel}:{i}: {line}")
        return results
