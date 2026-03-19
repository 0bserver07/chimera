"""LSP feedback middleware: inject language server diagnostics after file edits.

After each file-modifying tool call, queries the LSP server for diagnostics
and feeds errors/warnings back into the agent's context. Gives real-time
feedback without waiting for a test run.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.core.middleware import LoopMiddleware

if TYPE_CHECKING:
    from chimera.core.context import Context
    from chimera.lsp.manager import LSPManager
    from chimera.providers.base import Response

# Tools that modify files
_WRITE_TOOLS = {"write_file", "edit_file", "replace_in_file"}


class LSPFeedbackMiddleware(LoopMiddleware):
    """Inject LSP diagnostics after file modifications.

    Args:
        lsp: LSPManager instance.
        severity: Minimum severity to report ("error", "warning", "info").
        max_diagnostics: Maximum number of diagnostics to inject per turn.

    Example::

        from chimera.lsp import LSPManager
        lsp = LSPManager()
        mw = LSPFeedbackMiddleware(lsp, severity="error")
        config = LoopConfig(middleware=[mw])
    """

    def __init__(
        self,
        lsp: LSPManager,
        severity: str = "error",
        max_diagnostics: int = 10,
    ) -> None:
        self._lsp = lsp
        self._severity = severity
        self._max_diagnostics = max_diagnostics
        self._modified_files: list[str] = []

    def after_model(self, response: Response, context: Context) -> Response:
        """Track which files were modified by tool calls."""
        if not response.has_tool_calls:
            return response

        for tc in response.tool_calls:
            if tc.name in _WRITE_TOOLS:
                path = tc.arguments.get("path", "")
                if path and path not in self._modified_files:
                    self._modified_files.append(path)

        return response

    def before_model(self, context: Context, tools: object) -> Context:
        """Query LSP for diagnostics on recently modified files."""
        if not self._modified_files:
            return context

        diagnostics = self._collect_diagnostics()
        self._modified_files.clear()

        if diagnostics:
            from chimera.types import Message
            context.messages.append(Message.system(
                f"[LSP Diagnostics]\n{diagnostics}"
            ))

        return context

    def _collect_diagnostics(self) -> str:
        """Query LSP and format diagnostics."""
        severity_order = {"error": 0, "warning": 1, "info": 2, "hint": 3}
        min_severity = severity_order.get(self._severity, 1)

        lines: list[str] = []
        count = 0

        for path in self._modified_files:
            try:
                diags = self._lsp.get_diagnostics(path)
            except Exception:
                continue

            for d in diags:
                d_severity = getattr(d, "severity", "warning")
                if isinstance(d_severity, str):
                    d_level = severity_order.get(d_severity.lower(), 2)
                else:
                    d_level = int(d_severity) if d_severity else 2

                if d_level > min_severity:
                    continue

                line_num = getattr(d, "line", "?")
                msg = getattr(d, "message", str(d))
                sev = d_severity if isinstance(d_severity, str) else "error"
                lines.append(f"  {path}:{line_num} [{sev}] {msg}")
                count += 1

                if count >= self._max_diagnostics:
                    lines.append(f"  ... ({count}+ diagnostics, showing first {self._max_diagnostics})")
                    return "\n".join(lines)

        return "\n".join(lines)
