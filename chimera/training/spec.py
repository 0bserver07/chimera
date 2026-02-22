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

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_string(cls, text: str) -> Spec:
        return cls(text=text)

    @classmethod
    def from_file(cls, path: str) -> Spec:
        content = Path(path).read_text()
        return cls(text=content, files=[path])

    @classmethod
    def from_tests(cls, tests_dir: str) -> Spec:
        return cls(tests_dir=tests_dir)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def to_prompt(self) -> str:
        parts: list[str] = []
        if self.text:
            parts.append(self.text)
        if self.tests_dir:
            parts.append(f"Tests directory: {self.tests_dir}")
        if self.files:
            parts.append(f"Spec files: {', '.join(self.files)}")
        return "\n\n".join(parts) if parts else "No specification provided."
