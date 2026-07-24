"""Abstract base class for execution environments.

An :class:`Environment` is the sandboxed workspace where generated code lives,
gets written, and is tested.  Concrete implementations (e.g. local filesystem,
Docker container, remote VM) must implement every abstract method defined here.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from functools import lru_cache

from chimera.types import CommandResult, TestResult


def _translate(pattern: str) -> str:
    """Translate a pathlib-style glob into a regular expression.

    Args:
        pattern: A glob pattern using ``*``, ``**``, ``?`` and ``[seq]``.

    Returns:
        A regex source string (unanchored) equivalent to *pattern*.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            if j - i >= 2:  # ``**`` spans path segments
                if j < n and pattern[j] == "/":
                    # Optional so ``**/x`` also matches a bare ``x``, matching
                    # pathlib.
                    out.append("(?:.*/)?")
                    j += 1
                else:
                    out.append(".*")
            else:  # a single ``*`` stops at the separator
                out.append("[^/]*")
            i = j
            continue
        if char == "?":
            out.append("[^/]")
            i += 1
            continue
        if char == "[":
            j = i + 1
            if j < n and pattern[j] == "!":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:  # unterminated class — treat as a literal bracket
                out.append(re.escape(char))
                i += 1
                continue
            body = pattern[i + 1 : j].replace("\\", r"\\")
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = j + 1
            continue
        out.append(re.escape(char))
        i += 1
    return "".join(out)


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(f"(?s:{_translate(pattern)})\\Z")


def glob_match(path: str, pattern: str) -> bool:
    """Match a workspace-relative POSIX path against a glob pattern.

    Implements the semantics of :meth:`pathlib.Path.glob`, which
    :class:`~chimera.env.local.LocalEnvironment` uses and which therefore
    defines what ``list_files(pattern)`` means for *every* backend:

    * ``*`` matches any run of characters **except** the path separator.
    * ``**`` matches any number of path segments, including none.
    * ``?`` matches a single non-separator character.
    * ``[seq]`` / ``[!seq]`` character classes behave as in :mod:`fnmatch`.

    Backends that enumerate remote paths themselves (E2B, Daytona, …) must
    filter through this rather than raw :func:`fnmatch.fnmatch`, whose ``*``
    happily crosses ``/`` and so wrongly reports nested files for ``"*.py"``.

    Args:
        path: A workspace-relative POSIX path, e.g. ``"sub/mod.py"``.
        pattern: The glob to test it against.

    Returns:
        ``True`` when *path* matches *pattern*.
    """
    return _compiled(pattern).match(path) is not None


class Environment(ABC):
    """Abstract execution environment where generated code lives and gets tested.

    Concrete implementations (local filesystem, Docker container, remote VM,
    etc.) must implement every abstract method.  Environments support the
    context-manager protocol: ``setup()`` is called on entry and
    ``cleanup()`` on exit.
    """

    @abstractmethod
    def setup(self) -> None:
        """Initialize the workspace."""

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up resources."""

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read a file from the workspace.

        Args:
            path: Workspace-relative file path.

        Returns:
            The full text content of the file.
        """

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write a file to the workspace.

        Args:
            path: Workspace-relative file path.  Parent directories are
                created as needed by most implementations.
            content: The text content to write.
        """

    @abstractmethod
    def list_files(self, pattern: str = "**/*") -> list[str]:
        """List files matching a glob pattern.

        Implementations must honour :func:`glob_match` semantics (those of
        :meth:`pathlib.Path.glob`): a single ``*`` stops at the path
        separator, ``**`` spans segments.  Backends that enumerate paths
        remotely should filter through :func:`glob_match` so a given pattern
        selects the same files no matter which backend is mounted.

        Args:
            pattern: Glob pattern relative to the workspace root.

        Returns:
            A list of workspace-relative file paths that match *pattern*.
        """

    @abstractmethod
    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult:
        """Run a shell command in the workspace.

        Args:
            cmd: The command to execute.
            timeout: Max seconds to wait.
            shell_name: Target shell when a persistent session is active.
                        Ignored when no session is running.

        Returns:
            A :class:`~chimera.types.CommandResult` with stdout, stderr,
            and the exit code.
        """

    @abstractmethod
    def run_tests(self) -> TestResult:
        """Execute the test suite and return results.

        Returns:
            A :class:`~chimera.types.TestResult` summarising pass/fail
            counts and any captured output.
        """

    @abstractmethod
    def checkpoint(self) -> str:
        """Save current state and return a checkpoint identifier.

        Returns:
            A string checkpoint ID that can be passed to :meth:`restore`.
        """

    @abstractmethod
    def restore(self, checkpoint_id: str) -> None:
        """Restore the workspace to a previous checkpoint.

        Args:
            checkpoint_id: ID returned by a prior :meth:`checkpoint` call.
        """

    def clone(self) -> Environment:
        """Create an independent copy for parallel execution.

        Returns:
            A new Environment instance with the same workspace contents.

        Raises:
            NotImplementedError: If the environment does not support cloning.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support clone()")

    def __enter__(self) -> Environment:
        self.setup()
        return self

    def __exit__(self, *args: object) -> None:
        self.cleanup()
