"""Per-tool call rendering for the Chimera TUIs (R-REN-5).

One generic ``⚙ name(k=v, k=v)`` printer treats a shell command, a file read,
and a delegated sub-agent as the same event. They are not. This module is the
dispatch table that gives each *tool class* a glyph, a one-line argument
summary, and a display shape:

===========  ====  ==========================================================
class        icon  shape
===========  ====  ==========================================================
shell        ``$``  block card (rich output: command output is the payload)
read         ``→``  inline row
write/edit   ``←``  inline row
search/list  ``✱``  inline row
delegate     ``↳``  block card (a sub-agent's report is prose)
web          ``⇢``  block card
think/todo   ``∴``  inline row
unknown      ``⚙``  inline row — exactly today's glyph and argument preview
===========  ====  ==========================================================

Two rules keep this honest:

- **Identity is never lost.** The verb rendered is the tool's own name, so
  ``test`` and ``bash`` stay distinguishable under the same ``$``. Only names
  that are pure plumbing are rewritten — an MCP tool's
  ``mcp__server__do_thing`` renders as ``do_thing`` with the server as its
  summary prefix.
- **Unknown tools look exactly like they did before.** Their renderer is the
  default ``⚙`` with the historical ``k=v, k=v`` preview, so adding a tool to
  Chimera never needs a change here.

Stdlib-only and widget-free (rich lives in :mod:`chimera.tui.render`, which
consumes this), so the whole table is unit-testable in CI's no-``tui`` posture.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_RENDERER",
    "TOOL_RENDERERS",
    "ToolRenderer",
    "call_row",
    "is_block_tool",
    "renderer_for",
    "short_value",
    "summarize_args",
    "tool_verb",
]


def short_value(value: Any, limit: int = 40) -> str:
    """Collapse a value to a single truncated line for an argument preview.

    Args:
        value: Any argument value.
        limit: Maximum characters (an ellipsis replaces the last one).

    Returns:
        A single-line string of at most *limit* characters.
    """
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


@dataclass(frozen=True)
class ToolRenderer:
    """How one tool class renders (R-REN-5).

    Args:
        icon: One-column glyph identifying the class.
        summary: Which argument summarizer to use — ``shell``, ``path``,
            ``search``, ``delegate``, ``web``, ``text`` or ``default``.
        block: True when the tool's output is rich enough to earn a block
            card (gutter-prefixed); False renders a plain inline row.
        verb: Display verb; empty means "use the tool's own name" (the
            default — identity is never lost). Registered tools and plugins
            may set it when the raw name is plumbing.
    """

    icon: str
    summary: str = "default"
    block: bool = False
    verb: str = ""


#: The unknown-tool renderer: exactly the glyph and preview shipped before
#: per-tool dispatch existed.
DEFAULT_RENDERER = ToolRenderer("⚙", summary="default", block=False)

_SHELL = ToolRenderer("$", summary="shell", block=True)
_READ = ToolRenderer("→", summary="path")
_WRITE = ToolRenderer("←", summary="path")
_SEARCH = ToolRenderer("✱", summary="search")
_DELEGATE = ToolRenderer("↳", summary="delegate", block=True)
_WEB = ToolRenderer("⇢", summary="web", block=True)
_THINK = ToolRenderer("∴", summary="text")

#: tool name → renderer. Covers Chimera's built-in tool set plus the common
#: aliases other agents use, so external-agent lanes get the grammar too.
TOOL_RENDERERS: dict[str, ToolRenderer] = {
    # shell class
    "bash": _SHELL,
    "shell": _SHELL,
    "powershell": _SHELL,
    "ipython": _SHELL,
    "run": _SHELL,
    "test": _SHELL,
    "git": _SHELL,
    "verify": _SHELL,
    # read class
    "read": _READ,
    "read_file": _READ,
    "view": _READ,
    "image_read": _READ,
    "notebook_read": _READ,
    # write / edit class
    "write": _WRITE,
    "write_file": _WRITE,
    "edit": _WRITE,
    "edit_file": _WRITE,
    "replace_in_file": _WRITE,
    "apply_patch": _WRITE,
    "notebook_edit": _WRITE,
    # search / list class
    "search": _SEARCH,
    "grep": _SEARCH,
    "glob": _SEARCH,
    "list_files": _SEARCH,
    "ls": _SEARCH,
    "repo_map": _SEARCH,
    "import_graph": _SEARCH,
    # delegation
    "delegate": _DELEGATE,
    "task": _DELEGATE,
    "agent": _DELEGATE,
    # web
    "web_fetch": _WEB,
    "browser": _WEB,
    # cheap bookkeeping tools
    "think": _THINK,
    "todo": _THINK,
    "ask_user": _THINK,
    "dmail": _THINK,
}

#: Prefix marking a tool that arrived over MCP: ``mcp__<server>__<tool>``.
_MCP_PREFIX = "mcp__"


def _split_mcp(name: str) -> tuple[str, str]:
    """Split ``mcp__server__tool`` into ``(server, tool)``; ``("", name)`` otherwise."""
    if not name.startswith(_MCP_PREFIX):
        return "", name
    rest = name[len(_MCP_PREFIX):]
    server, _, tool = rest.partition("__")
    return (server, tool) if tool else ("", rest)


def renderer_for(name: str) -> ToolRenderer:
    """The renderer for a tool name (unknown names get :data:`DEFAULT_RENDERER`).

    Args:
        name: The tool's registered name. ``mcp__server__tool`` resolves on
            the tool half, so an MCP-served ``read`` still reads as one.

    Returns:
        The matching :class:`ToolRenderer`.
    """
    if name in TOOL_RENDERERS:
        return TOOL_RENDERERS[name]
    _, tool = _split_mcp(name)
    return TOOL_RENDERERS.get(tool, DEFAULT_RENDERER)


def is_block_tool(name: str) -> bool:
    """True when this tool's output earns a block card rather than a plain row."""
    return renderer_for(name).block


def tool_verb(name: str) -> str:
    """The verb rendered for a tool: its own name, or the renderer's override.

    Args:
        name: The tool's registered name.

    Returns:
        The display verb — the bare tool name for everything except MCP tools
        (whose ``mcp__server__`` plumbing moves into the summary) and tools
        whose renderer declares an explicit verb.
    """
    renderer = renderer_for(name)
    if renderer.verb:
        return renderer.verb
    _, tool = _split_mcp(name)
    return tool or name


def _first(args: Mapping[str, Any], *keys: str) -> Any:
    """The first present, non-empty value among *keys*."""
    for key in keys:
        value = args.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _default_summary(args: Mapping[str, Any]) -> str:
    """The historical ``(k=v, k=v)`` preview of the first three arguments."""
    return ", ".join(f"{k}={short_value(v)}" for k, v in list(args.items())[:3])


def _shell_summary(args: Mapping[str, Any]) -> str:
    command = _first(args, "command", "cmd", "script", "code", "args")
    return short_value(command, 72) if command is not None else _default_summary(args)


def _path_summary(args: Mapping[str, Any]) -> str:
    path = _first(args, "path", "file_path", "file", "filename", "target")
    if path is None:
        return _default_summary(args)
    text = short_value(path, 56)
    start = _first(args, "start_line", "line", "offset")
    end = _first(args, "end_line", "limit")
    if start is not None and end is not None:
        return f"{text}:{start}-{end}"
    if start is not None:
        return f"{text}:{start}"
    return text


def _search_summary(args: Mapping[str, Any]) -> str:
    pattern = _first(args, "pattern", "query", "regex", "text")
    where = _first(args, "path", "directory", "dir", "root")
    if pattern is None and where is None:
        return _default_summary(args)
    if pattern is None:
        return short_value(where, 56)
    shown = f'"{short_value(pattern, 48)}"'
    return f"{shown} in {short_value(where, 40)}" if where is not None else shown


def _delegate_summary(args: Mapping[str, Any]) -> str:
    who = _first(args, "agent", "subagent_type", "agent_type", "name")
    what = _first(args, "task", "prompt", "description", "instructions")
    parts = [short_value(who, 24)] if who is not None else []
    if what is not None:
        parts.append(short_value(what, 56))
    return " · ".join(parts) if parts else _default_summary(args)


def _web_summary(args: Mapping[str, Any]) -> str:
    url = _first(args, "url", "uri", "link", "query")
    return short_value(url, 72) if url is not None else _default_summary(args)


def _text_summary(args: Mapping[str, Any]) -> str:
    text = _first(args, "thought", "text", "content", "question", "message", "todos")
    return short_value(text, 72) if text is not None else _default_summary(args)


_SUMMARIZERS = {
    "shell": _shell_summary,
    "path": _path_summary,
    "search": _search_summary,
    "delegate": _delegate_summary,
    "web": _web_summary,
    "text": _text_summary,
    "default": _default_summary,
}


def summarize_args(name: str, args: Mapping[str, Any] | None) -> str:
    """One-line argument summary for a tool call (R-REN-5).

    Each tool class distills what actually matters — the command for a shell
    call, ``path:start-end`` for a read, ``"pattern" in path`` for a search —
    and falls back to the historical ``k=v`` preview whenever the expected
    keys are absent, so a differently-shaped call never renders as nothing.

    Args:
        name: The tool's registered name.
        args: The call's arguments (``None`` reads as empty).

    Returns:
        The summary, possibly empty (a no-argument call).
    """
    mapping: Mapping[str, Any] = args or {}
    summarizer = _SUMMARIZERS.get(renderer_for(name).summary, _default_summary)
    summary = summarizer(mapping)
    server, _ = _split_mcp(name)
    if server:
        return f"[{server}] {summary}" if summary else f"[{server}]"
    return summary


def call_row(name: str, args: Mapping[str, Any] | None) -> tuple[str, str, str]:
    """The three display cells of a tool-call row (icon, verb, summary).

    Args:
        name: The tool's registered name.
        args: The call's arguments.

    Returns:
        ``(icon, verb, summary)`` — the frontend styles them from the
        ``tool.icon`` / ``tool.name`` / ``tool.args`` theme slots.
    """
    return renderer_for(name).icon, tool_verb(name), summarize_args(name, args)
