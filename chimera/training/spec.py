"""Synthesis specification -- the 'loss function' for code generation.

A :class:`Spec` describes *what* should be synthesized.  It can be built from
a plain-text description, a file on disk, or a test directory, and rendered
into a prompt string that an agent can act on via :meth:`Spec.to_prompt`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Spec:
    """Specification for what to synthesize -- the 'loss function'.

    A Spec can be constructed from a text description, a file path, or
    a tests directory.  The to_prompt() method renders it as a prompt string
    that an agent can act on.
    """

    text: str = ""
    files: list[str] = field(default_factory=list)
    tests_dir: str | None = None
    source_file: str | None = None

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_string(cls, text: str) -> Spec:
        """Create a Spec from a plain-text description.

        Args:
            text: Free-form description of what should be synthesized.

        Returns:
            A new :class:`Spec` instance with the given text.
        """
        return cls(text=text)

    @classmethod
    def from_file(cls, path: str) -> Spec:
        content = Path(path).read_text()
        return cls(text=content, files=[path], source_file=path)

    @classmethod
    def from_tests(cls, tests_dir: str, description: str | None = None) -> Spec:
        """Create a Spec whose goal is to make a test suite pass.

        Args:
            tests_dir: Path to the directory containing the test files.
            description: Optional human-readable description.  When omitted,
                a generic "make all tests pass" message is generated.

        Returns:
            A new :class:`Spec` pointing at the given tests directory.
        """
        if description:
            return cls(text=description, tests_dir=tests_dir)
        return cls(
            text=f"Make all tests in {tests_dir} pass.",
            tests_dir=tests_dir,
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def to_prompt(self) -> str:
        """Render the specification as a prompt string for the agent.

        Returns:
            A multi-line string combining the text description, tests
            directory, and any referenced spec files.
        """
        parts: list[str] = []
        if self.text:
            parts.append(self.text)
        if self.tests_dir:
            parts.append(f"Tests directory: {self.tests_dir}")
        if self.files:
            parts.append(f"Spec files: {', '.join(self.files)}")
        return "\n\n".join(parts) if parts else "No specification provided."
