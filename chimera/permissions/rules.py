"""Permission rule primitives — sources, behaviors, and rule values.

Rule grammar (BNF):

    rule          ::= tool_pattern [ "(" body ")" ]
    body          ::= arg_key ":" arg_pattern    (* arg-key match *)
                    | content_pattern             (* legacy content match *)
    tool_pattern  ::= GLOB                        (* fnmatch on tool name *)
    arg_key       ::= IDENT                       (* Python identifier *)
    arg_pattern   ::= GLOB                        (* fnmatch on tool_input[arg_key] *)
    content_pattern ::= GLOB

Examples:
    ``Bash``                              — bare tool, matches any invocation
    ``Bash(git push *)``                  — legacy content match
    ``Bash(command:git push *)``          — arg-key match against ``command``
    ``Read(path:/Users/yadkonrad/**)``    — path glob on the ``path`` arg
    ``WebFetch(url:https://docs.*)``      — URL glob on the ``url`` arg
    ``mcp__*``                            — tool-name glob (matches all MCP tools)
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "RuleSource",
    "PermissionBehavior",
    "PermissionRuleValue",
    "PermissionRule",
]


# Identifier regex used to decide whether a body of the form "lhs:rhs" is an
# arg-key selector vs a legacy content pattern that happens to contain a
# colon.  An arg key must look like a normal Python identifier with no glob
# meta-characters or whitespace.
_ARG_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RuleSource(Enum):
    """Where a permission rule originated, ordered by precedence (lowest first).

    When multiple rules match, higher-precedence sources win.
    """

    POLICY = "policy"
    FLAG = "flag"
    LOCAL = "local"
    PROJECT = "project"
    USER = "user"
    CLI_ARG = "cli_arg"
    COMMAND = "command"
    SESSION = "session"


class PermissionBehavior(Enum):
    """What should happen when a rule matches."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionRuleValue:
    """A parsed tool-name + optional body (arg-key match or content match).

    The string format is ``ToolName(body)`` where the parenthesised
    part is optional.  ``body`` may either be ``arg_key:arg_pattern``
    or a legacy ``content_pattern``.

    Attributes:
        tool_name: Glob pattern for the tool name (e.g. ``Bash``, ``mcp__*``).
        content: Legacy content pattern (set when ``body`` lacks an arg key).
        arg_key: Argument name to inspect on the tool input dict.  When set,
            ``arg_pattern`` is matched against ``tool_input[arg_key]``.
        arg_pattern: Glob pattern for the value of ``tool_input[arg_key]``.
    """

    tool_name: str
    content: str | None = None
    arg_key: str | None = None
    arg_pattern: str | None = None

    # ----- parsing ----------------------------------------------------------

    @classmethod
    def from_string(cls, rule: str) -> PermissionRuleValue:
        """Parse ``"ToolName(body)"`` into a :class:`PermissionRuleValue`.

        Examples:
            * ``"Bash"``                          -> tool_name="Bash"
            * ``"Bash(ls -la)"``                  -> content="ls -la"
            * ``"Bash()"``                        -> content=""
            * ``"Bash(command:git push *)"``      -> arg_key="command",
              arg_pattern="git push *"
            * ``"Read(path:/etc/**)"``            -> arg_key="path",
              arg_pattern="/etc/**"

        Disambiguation: a body of the form ``lhs:rhs`` is treated as an
        arg-key match only when ``lhs`` is a valid Python identifier (no
        spaces, no glob meta-characters).  Otherwise the entire body is
        treated as a legacy content pattern, preserving back-compat with
        rules whose content happens to contain a colon.
        """
        paren_idx = rule.find("(")
        if paren_idx == -1:
            return cls(tool_name=rule, content=None)

        tool_name = rule[:paren_idx]
        # Everything between the first '(' and the last ')'
        if rule.endswith(")"):
            body = rule[paren_idx + 1 : -1]
        else:
            body = rule[paren_idx + 1 :]
        body = body.replace('\\\\', '\x00').replace('\\(', '(').replace('\\)', ')').replace('\x00', '\\')

        # Detect "arg_key:arg_pattern" form.
        colon = body.find(":")
        if colon > 0:
            lhs = body[:colon]
            rhs = body[colon + 1 :]
            if _ARG_KEY_RE.match(lhs):
                return cls(
                    tool_name=tool_name,
                    content=None,
                    arg_key=lhs,
                    arg_pattern=rhs,
                )

        return cls(tool_name=tool_name, content=body)

    # ----- serialisation ----------------------------------------------------

    def to_string(self) -> str:
        """Reverse of :meth:`from_string`."""
        if self.arg_key is not None and self.arg_pattern is not None:
            return f"{self.tool_name}({self.arg_key}:{self.arg_pattern})"
        if self.content is None:
            return self.tool_name
        escaped = self.content.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        return f"{self.tool_name}({escaped})"

    # ----- matching ---------------------------------------------------------

    def matches(
        self,
        tool_name: str,
        input_content: str | None = None,
        tool_input: dict[str, Any] | None = None,
    ) -> bool:
        """Return ``True`` if this rule matches the supplied invocation.

        Match algorithm:
            1. ``tool_name`` must satisfy :meth:`_tool_matches` (fnmatch with
               MCP-server-prefix shorthand).
            2. If ``arg_key``/``arg_pattern`` are set, fnmatch
               ``str(tool_input.get(arg_key, ""))`` against ``arg_pattern``.
               When ``tool_input`` is missing the rule does not match.
            3. Else if ``content`` is set, fall back to legacy content match
               against ``input_content``.
            4. Else (bare tool name) match unconditionally.

        Args:
            tool_name: Name of the tool being invoked.
            input_content: Single extracted string (legacy content match).
            tool_input: Full tool input dict (used for arg-key match).

        Returns:
            True when every applicable check passes; False otherwise.
        """
        # Tool name matching
        if not self._tool_matches(tool_name):
            return False

        # Arg-key matching takes precedence.
        if self.arg_key is not None and self.arg_pattern is not None:
            if tool_input is None:
                return False
            value = tool_input.get(self.arg_key, "")
            return fnmatch.fnmatch(str(value), self.arg_pattern)

        # Legacy content matching (if the rule specifies a content pattern)
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
