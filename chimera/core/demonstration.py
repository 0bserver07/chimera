"""Demonstration-based few-shot prompting.

Inspired by SWE-Agent's demonstration system: solved examples are included
in the prompt so the agent learns the expected format and approach before
tackling the actual task.

SWE-Agent loads trajectory files (JSON/YAML with a ``history`` key) and
injects them into the conversation either step-by-step or as a single
rendered message.  DemonstrationPrompt takes a simpler, more portable
approach -- examples are plain ``(task, solution)`` pairs stored in
lightweight markdown files or added programmatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.core.prompt import Prompt


@dataclass
class Example:
    """A solved example for few-shot prompting.

    Attributes:
        task: The task description.
        solution: The solution or trajectory.
        source: Where this example came from (e.g. file path).
    """

    task: str
    solution: str
    source: str = ""


class DemonstrationPrompt:
    """Prompt builder that includes solved examples for few-shot learning.

    Inspired by SWE-Agent's demonstration-based prompting.  The agent
    sees examples of solved tasks before tackling the actual task,
    learning the expected format and approach.

    Args:
        system: System prompt text prepended before examples.
        examples: Initial list of solved examples.
        max_examples: Maximum number of examples to include when rendering.
        example_prefix: Markdown heading prefix for each example section.
    """

    def __init__(
        self,
        system: str = "",
        examples: list[Example] | None = None,
        max_examples: int = 3,
        example_prefix: str = "## Example",
    ) -> None:
        self._system = system
        self._examples: list[Example] = list(examples) if examples else []
        self._max_examples = max_examples
        self._prefix = example_prefix

    # ------------------------------------------------------------------
    # Adding examples
    # ------------------------------------------------------------------

    def add_example(self, task: str, solution: str, source: str = "") -> None:
        """Add a solved example.

        Args:
            task: The task description.
            solution: The solution or trajectory.
            source: Optional provenance label.
        """
        self._examples.append(Example(task=task, solution=solution, source=source))

    def add_from_file(self, path: str) -> None:
        """Load an example from a markdown file.

        Expected format::

            # Task
            <task description>

            # Solution
            <solution/trajectory>

        Args:
            path: Path to the markdown file.
        """
        with open(path) as f:
            content = f.read()

        task = ""
        solution = ""
        current_section: str | None = None
        lines: list[str] = []

        for line in content.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("# task"):
                if current_section == "solution":
                    solution = "\n".join(lines).strip()
                current_section = "task"
                lines = []
            elif stripped.startswith("# solution"):
                if current_section == "task":
                    task = "\n".join(lines).strip()
                current_section = "solution"
                lines = []
            else:
                lines.append(line)

        # Capture the last section
        if current_section == "task":
            task = "\n".join(lines).strip()
        elif current_section == "solution":
            solution = "\n".join(lines).strip()

        if task or solution:
            self._examples.append(Example(task=task, solution=solution, source=path))

    def add_from_directory(self, path: str, pattern: str = "*.md") -> None:
        """Load all example files from a directory.

        Args:
            path: Directory containing example files.
            pattern: Glob pattern for matching example files.
        """
        import glob

        for fpath in sorted(glob.glob(os.path.join(path, pattern))):
            self.add_from_file(fpath)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def examples(self) -> list[Example]:
        """Return a copy of the current example list."""
        return list(self._examples)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, task: str = "", **kwargs: object) -> str:
        """Render the full prompt with system, examples, and task.

        Args:
            task: The actual task to solve (appended at the end).
            **kwargs: Additional variables (currently unused, reserved
                for future template substitution).

        Returns:
            The rendered prompt string.
        """
        parts: list[str] = []

        if self._system:
            parts.append(self._system)

        # Add examples (up to max)
        selected = self._examples[: self._max_examples]
        if selected:
            parts.append("\n---\n")
            for i, ex in enumerate(selected, 1):
                parts.append(f"{self._prefix} {i}")
                if ex.task:
                    parts.append(f"\n**Task:** {ex.task}")
                if ex.solution:
                    parts.append(f"\n**Solution:**\n{ex.solution}")
                parts.append("")
            parts.append("---\n")

        if task:
            parts.append(f"Now solve this task:\n\n{task}")

        return "\n".join(parts)

    def to_prompt(self) -> Prompt:
        """Convert to a Chimera :class:`~chimera.core.prompt.Prompt` object.

        Returns:
            A ``Prompt`` whose template is the fully rendered demonstration
            text (with no further ``{{variable}}`` substitution).
        """
        from chimera.core.prompt import Prompt

        return Prompt.from_string(self.render())
