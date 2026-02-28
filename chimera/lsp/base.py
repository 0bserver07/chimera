"""LSP client abstraction for diagnostic feedback."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    """LSP diagnostic severity levels."""

    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4


@dataclass
class Diagnostic:
    """A single diagnostic from an LSP server."""

    file: str
    line: int
    column: int
    severity: Severity
    message: str
    source: str | None = None
    code: str | int | None = None

    def to_feedback_str(self) -> str:
        """Format as a human-readable feedback string."""
        sev = self.severity.name.lower()
        loc = f"{self.file}:{self.line}:{self.column}"
        parts = [f"[{sev}] {loc}: {self.message}"]
        if self.source:
            parts.append(f"({self.source})")
        if self.code is not None:
            parts.append(f"[{self.code}]")
        return " ".join(parts)


class LSPClient(ABC):
    """Abstract base class for Language Server Protocol clients."""

    @abstractmethod
    def initialize(self, root_path: str) -> None:
        """Initialize the LSP server for the given project root."""

    @abstractmethod
    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        """Get diagnostics for a specific file."""

    @abstractmethod
    def shutdown(self) -> None:
        """Shut down the LSP server."""

    def __enter__(self) -> LSPClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.shutdown()
