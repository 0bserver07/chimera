"""Spec — the specification for what to synthesize."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Spec:
    """The specification -- what to synthesize. Acts as the 'loss function'."""

    text: str
    tests_dir: str | None = None
    source_file: str | None = None

    @classmethod
    def from_string(cls, text: str) -> Spec:
        return cls(text=text)

    @classmethod
    def from_file(cls, path: str) -> Spec:
        content = Path(path).read_text()
        return cls(text=content, source_file=path)

    @classmethod
    def from_tests(cls, tests_dir: str, description: str = "") -> Spec:
        """Tests ARE the spec."""
        return cls(
            text=description or f"Pass all tests in {tests_dir}",
            tests_dir=tests_dir,
        )

    def to_prompt(self) -> str:
        """Convert spec to a prompt string for the agent."""
        parts = [self.text]
        if self.tests_dir:
            parts.append(f"\nTest directory: {self.tests_dir}")
        return "\n".join(parts)
