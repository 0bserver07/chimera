from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.types import CommandResult, TestResult


class Environment(ABC):
    """Where generated code lives and gets tested."""

    @abstractmethod
    def setup(self) -> None:
        """Initialize the workspace."""

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up resources."""

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read a file from the workspace."""

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write a file to the workspace."""

    @abstractmethod
    def list_files(self, pattern: str = "**/*") -> list[str]:
        """List files matching a glob pattern."""

    @abstractmethod
    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult:
        """Run a shell command in the workspace.

        Args:
            cmd: The command to execute.
            timeout: Max seconds to wait.
            shell_name: Target shell when a persistent session is active.
                        Ignored when no session is running.
        """

    @abstractmethod
    def run_tests(self) -> TestResult:
        """Execute the test suite and return results."""

    @abstractmethod
    def checkpoint(self) -> str:
        """Save current state. Returns checkpoint ID."""

    @abstractmethod
    def restore(self, checkpoint_id: str) -> None:
        """Restore to a previous checkpoint."""

    def __enter__(self) -> Environment:
        self.setup()
        return self

    def __exit__(self, *args: object) -> None:
        self.cleanup()
