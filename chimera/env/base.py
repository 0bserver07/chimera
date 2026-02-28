"""Abstract base class for execution environments.

An :class:`Environment` is the sandboxed workspace where generated code lives,
gets written, and is tested.  Concrete implementations (e.g. local filesystem,
Docker container, remote VM) must implement every abstract method defined here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.types import CommandResult, TestResult


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
