"""Otter — first-class LSP tool exposure.

The upstream open-source coding agent treats LSP as a primary capability
rather than a peripheral integration. This module mirrors that posture for
otter by exposing each LSP capability (diagnostics, completion, rename,
go-to-definition, find references) as its own dedicated tool, instead of
funneling everything through a single ``lsp`` action enum the way the
generic :class:`chimera.lsp.LSPTool` does.

Design notes:

* All tools share an :class:`LSPProvider` callable that lazily resolves an
  :class:`~chimera.lsp.manager.LSPManager`. The provider can be swapped out
  in tests via ``provider=...``; in production :func:`build_lsp_tool_group`
  hands in :func:`auto_detect_provider` which creates an
  ``LSPManager.for_project(...)`` and starts it on first use.
* Every tool **gracefully degrades**. If no language server is reachable
  for a file (no manager, no session, server failed to spawn) the tools
  return a :class:`~chimera.types.ToolResult` whose ``error`` is the
  human-readable string ``"LSP not configured"`` (or a more specific
  variant). They never crash the loop.
* Stdlib-only — no third-party imports beyond what ``chimera.lsp`` already
  uses.

Trademark hygiene: this module follows the otter convention. The upstream
brand is referenced only as "the upstream open-source coding agent".
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from chimera.core.tool import BaseTool
from chimera.core.tool_group import ToolGroup
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.lsp.manager import LSPManager
    from chimera.lsp.session import LSPSession


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


LSPProvider = Callable[[], "LSPManager | None"]
"""Callable that returns an :class:`LSPManager` or ``None`` if unavailable."""


_NOT_CONFIGURED = "LSP not configured"


def _file_uri(path: str) -> str:
    """Return a file:// URI for ``path`` (resolved)."""
    return Path(path).resolve().as_uri()


def _resolve_session(
    provider: LSPProvider, file_path: str,
) -> "tuple[LSPSession | None, str | None]":
    """Resolve the LSP session for a file.

    Returns:
        A ``(session, error)`` pair. Exactly one of the two is non-None.
        ``session`` is the active session for the file's language; ``error``
        is a friendly message suitable for ``ToolResult.error``.
    """
    try:
        manager = provider()
    except Exception as exc:  # pragma: no cover — defensive
        return None, f"{_NOT_CONFIGURED}: provider error: {exc}"
    if manager is None:
        return None, _NOT_CONFIGURED
    session = manager.get_session(file_path)
    if session is None:
        return None, f"{_NOT_CONFIGURED}: no language server for {file_path}"
    return session, None


def _ensure_open(session: "LSPSession", manager: "LSPManager", file_path: str) -> str | None:
    """Open ``file_path`` in the session and return its URI.

    Returns ``None`` if the file cannot be read.
    """
    path = Path(file_path)
    try:
        text = path.read_text()
    except (FileNotFoundError, OSError):
        return None
    uri = _file_uri(file_path)
    lang_id = manager._detect_language(path.suffix)  # noqa: SLF001 — internal helper
    session.did_open(uri, lang_id, text)
    return uri


# ---------------------------------------------------------------------------
# Base class for otter LSP tools
# ---------------------------------------------------------------------------


class _OtterLSPToolBase(BaseTool):
    """Common plumbing for the otter LSP tool family.

    Subclasses set ``name`` / ``description`` / ``parameters`` and implement
    :meth:`_run`. They get session resolution, file-open bookkeeping, and
    graceful-degradation error handling for free.
    """

    is_concurrency_safe = True
    is_read_only = True

    def __init__(self, provider: LSPProvider) -> None:
        self._provider = provider

    # -- helpers -----------------------------------------------------------

    def _get(self, file_path: str) -> "tuple[LSPSession | None, LSPManager | None, str | None]":
        manager: LSPManager | None
        try:
            manager = self._provider()
        except Exception as exc:  # pragma: no cover — defensive
            return None, None, f"{_NOT_CONFIGURED}: provider error: {exc}"
        if manager is None:
            return None, None, _NOT_CONFIGURED
        session = manager.get_session(file_path)
        if session is None:
            return None, manager, f"{_NOT_CONFIGURED}: no language server for {file_path}"
        return session, manager, None

    # -- abstract ----------------------------------------------------------

    def _run(
        self,
        args: dict[str, Any],
        session: "LSPSession",
        manager: "LSPManager",
    ) -> ToolResult:  # pragma: no cover — overridden
        raise NotImplementedError

    # -- BaseTool API ------------------------------------------------------

    def execute(self, args: dict[str, Any], env: "Environment | None") -> ToolResult:
        file_path = args.get("path") or args.get("file")
        if not file_path:
            return ToolResult(output="", error="path is required")
        session, manager, err = self._get(file_path)
        if err is not None or session is None or manager is None:
            return ToolResult(output="", error=err or _NOT_CONFIGURED)
        try:
            return self._run(args, session, manager)
        except Exception as exc:
            return ToolResult(output="", error=f"LSP error: {exc}")


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


class OtterLSPDiagnosticsTool(_OtterLSPToolBase):
    """Return errors/warnings for a file from the language server."""

    name = "lsp_diagnostics"
    description = (
        "Get LSP diagnostics (errors, warnings, hints) for a file. "
        "Returns structured results from the underlying language server."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to diagnose."},
            "wait": {
                "type": "number",
                "description": "Seconds to wait for diagnostics (default 0.5).",
            },
        },
        "required": ["path"],
    }

    def _run(
        self, args: dict[str, Any], session: "LSPSession", manager: "LSPManager",
    ) -> ToolResult:
        file_path: str = args["path"]
        wait = float(args.get("wait", 0.5))
        diags = manager.get_diagnostics(file_path, wait=wait)
        if not diags:
            return ToolResult(
                output="No diagnostics",
                metadata={"count": 0, "diagnostics": []},
            )
        items = [
            {
                "file": d.file,
                "line": d.line,
                "column": d.column,
                "severity": d.severity.name.lower(),
                "message": d.message,
                "source": d.source,
                "code": d.code,
            }
            for d in diags
        ]
        body = "\n".join(d.to_feedback_str() for d in diags)
        return ToolResult(
            output=f"{len(diags)} diagnostics:\n{body}",
            metadata={"count": len(diags), "diagnostics": items},
        )


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


class OtterLSPCompletionTool(_OtterLSPToolBase):
    """Return completion candidates at a cursor position."""

    name = "lsp_completion"
    description = (
        "Get completion candidates at a cursor position from the language "
        "server. Use 0-indexed line and character offsets."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path."},
            "line": {"type": "integer", "description": "0-indexed line."},
            "character": {"type": "integer", "description": "0-indexed character offset."},
            "limit": {
                "type": "integer",
                "description": "Maximum number of items to return (default 20).",
            },
        },
        "required": ["path", "line", "character"],
    }

    def _run(
        self, args: dict[str, Any], session: "LSPSession", manager: "LSPManager",
    ) -> ToolResult:
        file_path: str = args["path"]
        line = int(args["line"])
        character = int(args["character"])
        limit = int(args.get("limit", 20))
        uri = _ensure_open(session, manager, file_path)
        if uri is None:
            return ToolResult(output="", error=f"Could not read file: {file_path}")
        items = session.completion(uri, line, character)
        if not items:
            return ToolResult(output="No completions", metadata={"count": 0, "items": []})
        truncated = items[:limit]
        labels = [item.get("label", "?") for item in truncated]
        body = "\n".join(labels)
        return ToolResult(
            output=f"{len(items)} completions (showing {len(truncated)}):\n{body}",
            metadata={
                "count": len(items),
                "items": [
                    {
                        "label": it.get("label"),
                        "kind": it.get("kind"),
                        "detail": it.get("detail"),
                    }
                    for it in truncated
                ],
            },
        )


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


class OtterLSPRenameTool(_OtterLSPToolBase):
    """Rename a symbol across the workspace via LSP."""

    name = "lsp_rename"
    description = (
        "Rename a symbol at a position via the language server. Returns the "
        "workspace edit (file -> list of edits) without applying it."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File containing the symbol."},
            "line": {"type": "integer", "description": "0-indexed line of the symbol."},
            "character": {"type": "integer", "description": "0-indexed character offset."},
            "new_name": {"type": "string", "description": "Replacement name."},
        },
        "required": ["path", "line", "character", "new_name"],
    }

    is_read_only = False

    def _run(
        self, args: dict[str, Any], session: "LSPSession", manager: "LSPManager",
    ) -> ToolResult:
        file_path: str = args["path"]
        line = int(args["line"])
        character = int(args["character"])
        new_name: str = args["new_name"]
        uri = _ensure_open(session, manager, file_path)
        if uri is None:
            return ToolResult(output="", error=f"Could not read file: {file_path}")
        edit = session.rename(uri, line, character, new_name)
        if edit is None:
            return ToolResult(output="Rename not available", metadata={"changes": {}})
        changes = edit.get("changes", {}) or {}
        total = sum(len(edits) for edits in changes.values())
        return ToolResult(
            output=f"Rename: {total} edits across {len(changes)} files",
            metadata={"changes": changes, "total": total, "files": len(changes)},
        )


# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------


class OtterLSPDefinitionTool(_OtterLSPToolBase):
    """Go to definition of a symbol at a position."""

    name = "lsp_definition"
    description = (
        "Resolve the definition site for a symbol at the given position via "
        "the language server. Returns one or more file:line locations."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File containing the symbol."},
            "line": {"type": "integer", "description": "0-indexed line."},
            "character": {"type": "integer", "description": "0-indexed character offset."},
        },
        "required": ["path", "line", "character"],
    }

    def _run(
        self, args: dict[str, Any], session: "LSPSession", manager: "LSPManager",
    ) -> ToolResult:
        file_path: str = args["path"]
        line = int(args["line"])
        character = int(args["character"])
        uri = _ensure_open(session, manager, file_path)
        if uri is None:
            return ToolResult(output="", error=f"Could not read file: {file_path}")
        locations = session.definition(uri, line, character)
        if not locations:
            return ToolResult(output="No definition found", metadata={"locations": []})
        formatted = []
        struct: list[dict[str, Any]] = []
        for loc in locations:
            loc_uri = loc.get("uri", "?")
            start = loc.get("range", {}).get("start", {})
            ln = start.get("line", 0)
            ch = start.get("character", 0)
            formatted.append(f"{loc_uri}:{ln}:{ch}")
            struct.append({"uri": loc_uri, "line": ln, "character": ch})
        return ToolResult(
            output="\n".join(formatted),
            metadata={"locations": struct, "count": len(struct)},
        )


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


class OtterLSPReferencesTool(_OtterLSPToolBase):
    """Find all references to a symbol at a position."""

    name = "lsp_references"
    description = (
        "Find all references (including the declaration) for a symbol at "
        "the given position via the language server."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File containing the symbol."},
            "line": {"type": "integer", "description": "0-indexed line."},
            "character": {"type": "integer", "description": "0-indexed character offset."},
        },
        "required": ["path", "line", "character"],
    }

    def _run(
        self, args: dict[str, Any], session: "LSPSession", manager: "LSPManager",
    ) -> ToolResult:
        file_path: str = args["path"]
        line = int(args["line"])
        character = int(args["character"])
        uri = _ensure_open(session, manager, file_path)
        if uri is None:
            return ToolResult(output="", error=f"Could not read file: {file_path}")
        refs = session.references(uri, line, character)
        if not refs:
            return ToolResult(output="No references found", metadata={"references": []})
        formatted = []
        struct: list[dict[str, Any]] = []
        for ref in refs:
            ref_uri = ref.get("uri", "?")
            start = ref.get("range", {}).get("start", {})
            ln = start.get("line", 0)
            ch = start.get("character", 0)
            formatted.append(f"{ref_uri}:{ln}:{ch}")
            struct.append({"uri": ref_uri, "line": ln, "character": ch})
        body = "\n".join(formatted)
        return ToolResult(
            output=f"{len(refs)} references:\n{body}",
            metadata={"references": struct, "count": len(struct)},
        )


# ---------------------------------------------------------------------------
# Provider factories + tool group builder
# ---------------------------------------------------------------------------


def auto_detect_provider(workdir: str | None = None) -> LSPProvider:
    """Build a provider that lazily creates and starts an :class:`LSPManager`.

    The manager is only created on first call, and only started once. If
    no language servers are available on PATH, the manager is still
    returned (with no sessions); subsequent ``get_session`` lookups
    correctly return ``None``, which the tools surface as
    ``"LSP not configured"``.

    Args:
        workdir: Project root. Defaults to the current working directory.

    Returns:
        A zero-arg callable returning the (possibly empty) LSPManager.
    """
    from chimera.lsp.manager import LSPManager

    state: dict[str, LSPManager | None] = {"manager": None, "started": None}
    root = workdir or str(Path.cwd())

    def _provider() -> LSPManager | None:
        if state["manager"] is None:
            try:
                manager = LSPManager.for_project(root)
            except Exception:
                state["manager"] = None
                return None
            state["manager"] = manager
        mgr = state["manager"]
        if mgr is None:
            return None
        if state["started"] is None:
            try:
                mgr.start(root)
            except Exception:
                # Server start failed; we still return the manager so
                # ``get_session`` can degrade to ``None``.
                pass
            state["started"] = mgr  # type: ignore[assignment]
        return mgr

    return _provider


def build_lsp_tool_group(
    provider: LSPProvider | None = None,
    *,
    workdir: str | None = None,
    name: str = "otter-lsp",
) -> ToolGroup:
    """Return a :class:`ToolGroup` containing the otter LSP tool family.

    Args:
        provider: Optional custom :data:`LSPProvider`. If omitted, an
            auto-detect provider rooted at ``workdir`` is used.
        workdir: Project root passed to :func:`auto_detect_provider` when
            ``provider`` is not supplied.
        name: Tool group name (default ``"otter-lsp"``).

    Returns:
        A :class:`ToolGroup` exposing diagnostics, completion, rename,
        definition, and references as first-class tools.
    """
    if provider is None:
        provider = auto_detect_provider(workdir)
    tools: list[BaseTool] = [
        OtterLSPDiagnosticsTool(provider),
        OtterLSPCompletionTool(provider),
        OtterLSPRenameTool(provider),
        OtterLSPDefinitionTool(provider),
        OtterLSPReferencesTool(provider),
    ]
    return ToolGroup(name, tools)


__all__ = [
    "LSPProvider",
    "OtterLSPCompletionTool",
    "OtterLSPDefinitionTool",
    "OtterLSPDiagnosticsTool",
    "OtterLSPReferencesTool",
    "OtterLSPRenameTool",
    "auto_detect_provider",
    "build_lsp_tool_group",
]
