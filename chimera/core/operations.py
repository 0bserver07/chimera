"""Protocol interfaces for pluggable tool backends."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

from chimera.types import CommandResult


def _contain(cwd: str, path: str) -> Path:
    """Resolve ``path`` against ``cwd`` and refuse to escape the sandbox.

    Guards against the same three escape vectors as
    :py:meth:`chimera.env.local.LocalEnvironment._contain`:

    * absolute paths outside ``cwd`` (``/etc/passwd``),
    * ``..`` traversal (``../../id_rsa``),
    * symlink redirection outside ``cwd`` (``resolve()`` follows links so
      ``relative_to`` detects the escape).

    Args:
        cwd: Sandbox root. Resolved with :py:meth:`Path.resolve`.
        path: Caller-supplied path (absolute or relative).

    Returns:
        Absolute :class:`Path` guaranteed to live under ``cwd``.

    Raises:
        PermissionError: When the resolved path escapes ``cwd``.
    """
    cwd_resolved = Path(cwd).resolve()
    candidate = (cwd_resolved / path).resolve()
    try:
        candidate.relative_to(cwd_resolved)
    except ValueError as exc:
        raise PermissionError(f"Path escapes sandbox cwd: {path}") from exc
    return candidate


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
        full = _contain(self.cwd, path)
        with open(full) as f:
            return f.read()

    def file_exists(self, path: str) -> bool:
        try:
            full = _contain(self.cwd, path)
        except PermissionError:
            return False
        return os.path.exists(full)


class LocalWriteOps:
    """WriteOps using local filesystem."""
    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def write_file(self, path: str, content: str) -> None:
        full = _contain(self.cwd, path)
        full.parent.mkdir(parents=True, exist_ok=True)
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
        base = Path(self.cwd).resolve()
        return [
            str(p.relative_to(base))
            for p in base.glob(pattern)
            if p.is_file()
        ]

    def search_files(self, pattern: str, path: str = ".") -> list[str]:
        import re
        regex = re.compile(pattern)
        results: list[str] = []
        search_dir = _contain(self.cwd, path)
        base = Path(self.cwd).resolve()
        for filepath in search_dir.rglob("*"):
            if not filepath.is_file():
                continue
            try:
                content = filepath.read_text()
            except (UnicodeDecodeError, PermissionError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    try:
                        rel = str(filepath.relative_to(base))
                    except ValueError:
                        # Should not happen — ``_contain`` confirms search_dir
                        # sits under ``base``. Skip defensively instead of
                        # leaking absolute paths.
                        continue
                    results.append(f"{rel}:{i}: {line}")
        return results
