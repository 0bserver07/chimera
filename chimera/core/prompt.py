from __future__ import annotations

import re
from pathlib import Path


class Prompt:
    """System prompt with simple {{variable}} template substitution.

    No Jinja2 dependency -- uses basic regex replacement.
    """

    def __init__(self, template: str) -> None:
        self._template = template

    @classmethod
    def from_string(cls, template: str) -> Prompt:
        """Create a Prompt from a template string."""
        return cls(template)

    @classmethod
    def from_file(cls, path: str) -> Prompt:
        """Create a Prompt by reading a template file."""
        content = Path(path).read_text()
        return cls(content)

    def render(self, **kwargs: object) -> str:
        """Render template, substituting {{variable}} placeholders.

        If 'tools' kwarg is provided (a list of tool names), append
        a section listing available tools.
        """
        tools = kwargs.pop("tools", None)

        # Substitute {{variable}} placeholders
        def replacer(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            if key in kwargs:
                return str(kwargs[key])
            return match.group(0)  # Leave unmatched placeholders as-is

        result = re.sub(r"\{\{(\s*\w+\s*)\}\}", replacer, self._template)

        # Append tool names if provided
        if tools:
            # tools is typed as object via **kwargs; it's expected to be iterable
            assert hasattr(tools, "__iter__")
            tool_list = ", ".join(str(t) for t in tools)  # type: ignore[attr-defined]
            result += f"\n\nAvailable tools: {tool_list}"

        return result
