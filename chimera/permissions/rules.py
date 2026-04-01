"""Permission rule primitives — sources, behaviors, and rule values."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "RuleSource",
    "PermissionBehavior",
    "PermissionRuleValue",
    "PermissionRule",
]


class RuleSource(Enum):
    """Where a permission rule originated, ordered by precedence (lowest first).

    When multiple rules match, higher-precedence sources win.
    """

    POLICY = 0
    FLAG = 1
    LOCAL = 2
    PROJECT = 3
    USER = 4
    CLI_ARG = 5
    COMMAND = 6
    SESSION = 7


class PermissionBehavior(Enum):
    """What should happen when a rule matches."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionRuleValue:
    """A parsed tool-name + optional content pattern.

    The string format is ``ToolName(content)`` where the parenthesised
    part is optional.  Content may contain nested parentheses — only the
    *last* ``)`` closes the group.
    """

    tool_name: str
    content: str | None = None

    # ----- parsing ----------------------------------------------------------

    @classmethod
    def from_string(cls, rule: str) -> PermissionRuleValue:
        """Parse ``"ToolName(content)"`` into a :class:`PermissionRuleValue`.

        * ``"Bash"``          -> tool_name="Bash", content=None
        * ``"Bash(ls -la)"``  -> tool_name="Bash", content="ls -la"
        * ``"Bash()"``        -> tool_name="Bash", content=""
        """
        paren_idx = rule.find("(")
        if paren_idx == -1:
            return cls(tool_name=rule, content=None)

        tool_name = rule[:paren_idx]
        # Everything between the first '(' and the last ')'
        if rule.endswith(")"):
            content = rule[paren_idx + 1 : -1]
        else:
            content = rule[paren_idx + 1 :]
        return cls(tool_name=tool_name, content=content)

    # ----- serialisation ----------------------------------------------------

    def to_string(self) -> str:
        """Reverse of :meth:`from_string`."""
        if self.content is None:
            return self.tool_name
        return f"{self.tool_name}({self.content})"

    # ----- matching ---------------------------------------------------------

    def matches(
        self,
        tool_name: str,
        input_content: str | None = None,
    ) -> bool:
        """Return ``True`` if *tool_name* (and optionally *input_content*)
        match this rule value.

        Supports:
        * ``fnmatch`` glob matching on tool name.
        * MCP server-level matching: ``mcp__server`` matches
          ``mcp__server__tool`` (implicit ``*`` suffix when the pattern
          looks like an MCP server prefix).
        * Optional content pattern matching via ``fnmatch``.
        """
        # Tool name matching
        if not self._tool_matches(tool_name):
            return False

        # Content matching (if the rule specifies a content pattern)
        if self.content is not None:
            if input_content is None:
                return False
            return fnmatch.fnmatch(input_content, self.content)

        return True

    def _tool_matches(self, tool_name: str) -> bool:
        """Check if *tool_name* matches :attr:`tool_name` pattern."""
        if fnmatch.fnmatch(tool_name, self.tool_name):
            return True
        # MCP server-level matching: "mcp__server" should match
        # "mcp__server__tool" even without an explicit glob wildcard.
        if (
            self.tool_name.startswith("mcp__")
            and "__" in self.tool_name
            and not any(c in self.tool_name for c in ("*", "?", "["))
        ):
            return tool_name.startswith(self.tool_name + "__")
        return False


@dataclass
class PermissionRule:
    """A fully-resolved permission rule: source + behavior + value."""

    source: RuleSource
    behavior: PermissionBehavior
    value: PermissionRuleValue
