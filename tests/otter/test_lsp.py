"""Tests for the otter first-class LSP tool exposure (agent O5).

Covers:

* Tool registration via :func:`chimera.otter.lsp.build_lsp_tool_group` —
  every promised tool is present with a stable name.
* Argument parsing — ``path`` is required; missing returns a friendly
  error rather than raising.
* Graceful degradation — when the provider yields ``None`` the tools
  return a ``ToolResult`` whose ``error`` is ``"LSP not configured"``
  (or a more specific variant); they never crash.
* Happy-path execution against a mocked LSP session for each capability
  (diagnostics, completion, rename, definition, references).
* ``auto_detect_provider`` lazy-creates and starts the manager once.

No real language server is spawned. We mock at the session/manager
level so the tests stay hermetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.lsp.base import Diagnostic, Severity
from chimera.otter.lsp import (
    LSPProvider,
    OtterLSPCompletionTool,
    OtterLSPDefinitionTool,
    OtterLSPDiagnosticsTool,
    OtterLSPReferencesTool,
    OtterLSPRenameTool,
    build_lsp_tool_group,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(
    session: Any | None,
    *,
    diagnostics: list[Diagnostic] | None = None,
) -> Any:
    """Build a mock LSPManager with a single session for any file."""
    mgr = MagicMock(name="LSPManager")
    mgr.get_session.return_value = session
    mgr._detect_language.return_value = "python"
    mgr.get_diagnostics.return_value = diagnostics or []
    return mgr


def _provider_returning(manager: Any | None) -> LSPProvider:
    return lambda: manager


def _make_session() -> Any:
    return MagicMock(name="LSPSession")


def _write_tmp_file(tmp_path: Path, name: str = "snippet.py") -> str:
    p = tmp_path / name
    p.write_text("x = 1\n")
    return str(p)


# ---------------------------------------------------------------------------
# Tool group registration
# ---------------------------------------------------------------------------


def test_build_lsp_tool_group_registers_every_tool() -> None:
    group = build_lsp_tool_group(provider=_provider_returning(None))
    expected = {
        "lsp_diagnostics",
        "lsp_completion",
        "lsp_rename",
        "lsp_definition",
        "lsp_references",
    }
    actual = {t.name for t in group}
    assert expected == actual
    assert len(group) == 5
    assert group.name == "otter-lsp"


def test_build_lsp_tool_group_custom_name() -> None:
    group = build_lsp_tool_group(provider=_provider_returning(None), name="custom")
    assert group.name == "custom"


def test_build_lsp_tool_group_default_provider_resolves(tmp_path: Path) -> None:
    """Without an explicit provider the builder uses auto_detect_provider."""
    group = build_lsp_tool_group(workdir=str(tmp_path))
    # Just check we got the right tools and they don't blow up on init.
    assert {t.name for t in group} == {
        "lsp_diagnostics", "lsp_completion", "lsp_rename",
        "lsp_definition", "lsp_references",
    }


# ---------------------------------------------------------------------------
# Argument parsing — missing path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [
    OtterLSPDiagnosticsTool, OtterLSPCompletionTool,
    OtterLSPRenameTool, OtterLSPDefinitionTool, OtterLSPReferencesTool,
])
def test_missing_path_returns_error(cls: type) -> None:
    tool = cls(_provider_returning(None))
    result = tool.execute({}, env=None)
    assert result.error == "path is required"
    assert result.success is False


# ---------------------------------------------------------------------------
# Graceful degradation — no provider / no session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [
    OtterLSPDiagnosticsTool, OtterLSPCompletionTool,
    OtterLSPRenameTool, OtterLSPDefinitionTool, OtterLSPReferencesTool,
])
def test_provider_returns_none_friendly_error(cls: type) -> None:
    tool = cls(_provider_returning(None))
    result = tool.execute({"path": "x.py", "line": 0, "character": 0,
                           "new_name": "y"}, env=None)
    assert result.error == "LSP not configured"


@pytest.mark.parametrize("cls", [
    OtterLSPDiagnosticsTool, OtterLSPCompletionTool,
    OtterLSPRenameTool, OtterLSPDefinitionTool, OtterLSPReferencesTool,
])
def test_no_session_for_file_friendly_error(cls: type) -> None:
    mgr = _make_manager(session=None)
    tool = cls(_provider_returning(mgr))
    result = tool.execute({"path": "x.unknown", "line": 0, "character": 0,
                           "new_name": "y"}, env=None)
    assert result.error is not None
    assert result.error.startswith("LSP not configured: no language server")


def test_provider_raises_friendly_error() -> None:
    def bad() -> Any:
        raise RuntimeError("boom")

    tool = OtterLSPDiagnosticsTool(bad)
    result = tool.execute({"path": "x.py"}, env=None)
    assert result.error is not None
    assert result.error.startswith("LSP not configured")


def test_session_call_raising_is_caught() -> None:
    sess = _make_session()
    sess.completion.side_effect = RuntimeError("kaboom")
    mgr = _make_manager(session=sess)
    tool = OtterLSPCompletionTool(_provider_returning(mgr))
    # Need a real file so _ensure_open succeeds.
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fp:
        fp.write("pass\n")
        path = fp.name
    result = tool.execute({"path": path, "line": 0, "character": 0}, env=None)
    assert result.error is not None
    assert "LSP error" in result.error


# ---------------------------------------------------------------------------
# Diagnostics — happy path + empty result
# ---------------------------------------------------------------------------


def test_diagnostics_happy_path(tmp_path: Path) -> None:
    sess = _make_session()
    diags = [
        Diagnostic(file="x.py", line=1, column=2, severity=Severity.ERROR,
                   message="oops", source="pyright", code="E001"),
    ]
    mgr = _make_manager(session=sess, diagnostics=diags)
    tool = OtterLSPDiagnosticsTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute({"path": path}, env=None)
    assert result.success
    assert "1 diagnostics" in result.output
    assert result.metadata["count"] == 1
    assert result.metadata["diagnostics"][0]["severity"] == "error"
    assert result.metadata["diagnostics"][0]["message"] == "oops"
    mgr.get_diagnostics.assert_called_once()


def test_diagnostics_empty_result(tmp_path: Path) -> None:
    sess = _make_session()
    mgr = _make_manager(session=sess, diagnostics=[])
    tool = OtterLSPDiagnosticsTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute({"path": path}, env=None)
    assert result.success
    assert result.output == "No diagnostics"
    assert result.metadata["count"] == 0


# ---------------------------------------------------------------------------
# Completion — happy path + empty + truncation
# ---------------------------------------------------------------------------


def test_completion_happy_path(tmp_path: Path) -> None:
    sess = _make_session()
    sess.completion.return_value = [
        {"label": "alpha", "kind": 5, "detail": "method"},
        {"label": "beta", "kind": 6, "detail": "field"},
    ]
    mgr = _make_manager(session=sess)
    tool = OtterLSPCompletionTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute({"path": path, "line": 0, "character": 0}, env=None)
    assert result.success
    assert "2 completions" in result.output
    assert "alpha" in result.output and "beta" in result.output
    assert result.metadata["count"] == 2
    sess.did_open.assert_called_once()


def test_completion_empty(tmp_path: Path) -> None:
    sess = _make_session()
    sess.completion.return_value = []
    mgr = _make_manager(session=sess)
    tool = OtterLSPCompletionTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute({"path": path, "line": 0, "character": 0}, env=None)
    assert result.success
    assert result.output == "No completions"


def test_completion_limit(tmp_path: Path) -> None:
    sess = _make_session()
    sess.completion.return_value = [{"label": f"x{i}"} for i in range(50)]
    mgr = _make_manager(session=sess)
    tool = OtterLSPCompletionTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute(
        {"path": path, "line": 0, "character": 0, "limit": 3}, env=None,
    )
    assert result.success
    assert "50 completions (showing 3)" in result.output
    assert len(result.metadata["items"]) == 3


def test_completion_unreadable_file() -> None:
    sess = _make_session()
    mgr = _make_manager(session=sess)
    tool = OtterLSPCompletionTool(_provider_returning(mgr))
    result = tool.execute(
        {"path": "/no/such/file/exists.py", "line": 0, "character": 0}, env=None,
    )
    assert result.error is not None
    assert "Could not read file" in result.error


# ---------------------------------------------------------------------------
# Rename — happy path + nil + edits aggregation
# ---------------------------------------------------------------------------


def test_rename_happy_path(tmp_path: Path) -> None:
    sess = _make_session()
    sess.rename.return_value = {
        "changes": {
            "file:///a.py": [{"newText": "y"}, {"newText": "y"}],
            "file:///b.py": [{"newText": "y"}],
        },
    }
    mgr = _make_manager(session=sess)
    tool = OtterLSPRenameTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute(
        {"path": path, "line": 0, "character": 0, "new_name": "y"}, env=None,
    )
    assert result.success
    assert "3 edits across 2 files" in result.output
    assert result.metadata["total"] == 3
    assert result.metadata["files"] == 2


def test_rename_unavailable(tmp_path: Path) -> None:
    sess = _make_session()
    sess.rename.return_value = None
    mgr = _make_manager(session=sess)
    tool = OtterLSPRenameTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute(
        {"path": path, "line": 0, "character": 0, "new_name": "y"}, env=None,
    )
    assert result.success
    assert result.output == "Rename not available"
    assert result.metadata["changes"] == {}


def test_rename_is_not_read_only() -> None:
    """Rename mutates the workspace — surface that to permission policies."""
    tool = OtterLSPRenameTool(_provider_returning(None))
    assert tool.is_read_only is False


# ---------------------------------------------------------------------------
# Definition — happy path + nil
# ---------------------------------------------------------------------------


def test_definition_happy_path(tmp_path: Path) -> None:
    sess = _make_session()
    sess.definition.return_value = [
        {"uri": "file:///foo.py",
         "range": {"start": {"line": 12, "character": 4}}},
    ]
    mgr = _make_manager(session=sess)
    tool = OtterLSPDefinitionTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute({"path": path, "line": 1, "character": 1}, env=None)
    assert result.success
    assert "file:///foo.py:12:4" in result.output
    assert result.metadata["count"] == 1
    assert result.metadata["locations"][0]["line"] == 12


def test_definition_none_found(tmp_path: Path) -> None:
    sess = _make_session()
    sess.definition.return_value = []
    mgr = _make_manager(session=sess)
    tool = OtterLSPDefinitionTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute({"path": path, "line": 0, "character": 0}, env=None)
    assert result.success
    assert result.output == "No definition found"
    assert result.metadata["locations"] == []


# ---------------------------------------------------------------------------
# References — happy path + nil
# ---------------------------------------------------------------------------


def test_references_happy_path(tmp_path: Path) -> None:
    sess = _make_session()
    sess.references.return_value = [
        {"uri": "file:///a.py", "range": {"start": {"line": 1, "character": 0}}},
        {"uri": "file:///b.py", "range": {"start": {"line": 9, "character": 7}}},
    ]
    mgr = _make_manager(session=sess)
    tool = OtterLSPReferencesTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute({"path": path, "line": 0, "character": 0}, env=None)
    assert result.success
    assert "2 references" in result.output
    assert result.metadata["count"] == 2


def test_references_none_found(tmp_path: Path) -> None:
    sess = _make_session()
    sess.references.return_value = []
    mgr = _make_manager(session=sess)
    tool = OtterLSPReferencesTool(_provider_returning(mgr))
    path = _write_tmp_file(tmp_path)
    result = tool.execute({"path": path, "line": 0, "character": 0}, env=None)
    assert result.success
    assert result.output == "No references found"
    assert result.metadata["references"] == []


# ---------------------------------------------------------------------------
# auto_detect_provider — lazy + idempotent
# ---------------------------------------------------------------------------


def test_auto_detect_provider_lazy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Provider should not create the manager until first call."""
    created: list[Any] = []
    started: list[str] = []

    class FakeManager:
        @classmethod
        def for_project(cls, path: str) -> "FakeManager":
            inst = cls()
            created.append(inst)
            return inst

        def start(self, workdir: str) -> None:
            started.append(workdir)

        def get_session(self, file_path: str) -> None:
            return None

    import chimera.otter.lsp as mod

    monkeypatch.setattr("chimera.lsp.manager.LSPManager", FakeManager)
    # Re-resolve from the module since auto_detect_provider imports lazily.
    monkeypatch.setattr(mod, "auto_detect_provider", mod.auto_detect_provider)

    provider = mod.auto_detect_provider(workdir=str(tmp_path))
    assert created == []  # not yet
    mgr1 = provider()
    assert mgr1 is not None
    assert len(created) == 1
    assert started == [str(tmp_path)]
    # Calling again is idempotent.
    mgr2 = provider()
    assert mgr2 is mgr1
    assert len(created) == 1
    assert started == [str(tmp_path)]


def test_auto_detect_provider_handles_for_project_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    class BoomManager:
        @classmethod
        def for_project(cls, path: str) -> "BoomManager":
            raise RuntimeError("no PATH")

    import chimera.otter.lsp as mod

    monkeypatch.setattr("chimera.lsp.manager.LSPManager", BoomManager)
    provider = mod.auto_detect_provider(workdir=str(tmp_path))
    assert provider() is None


def test_auto_detect_provider_swallows_start_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    class GrumpyManager:
        @classmethod
        def for_project(cls, path: str) -> "GrumpyManager":
            return cls()

        def start(self, workdir: str) -> None:
            raise RuntimeError("can't start")

        def get_session(self, file_path: str) -> None:
            return None

    import chimera.otter.lsp as mod

    monkeypatch.setattr("chimera.lsp.manager.LSPManager", GrumpyManager)
    provider = mod.auto_detect_provider(workdir=str(tmp_path))
    mgr = provider()
    assert mgr is not None
    # Tool now degrades correctly.
    tool = OtterLSPDiagnosticsTool(provider)
    result = tool.execute({"path": "x.py"}, env=None)
    assert result.error is not None
    assert result.error.startswith("LSP not configured")


# ---------------------------------------------------------------------------
# Tool schema basics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls,name", [
    (OtterLSPDiagnosticsTool, "lsp_diagnostics"),
    (OtterLSPCompletionTool, "lsp_completion"),
    (OtterLSPRenameTool, "lsp_rename"),
    (OtterLSPDefinitionTool, "lsp_definition"),
    (OtterLSPReferencesTool, "lsp_references"),
])
def test_tool_anthropic_schema_has_required_fields(cls: type, name: str) -> None:
    tool = cls(_provider_returning(None))
    schema = tool.to_anthropic_schema()
    assert schema["name"] == name
    assert "input_schema" in schema
    assert schema["input_schema"]["type"] == "object"
    assert "path" in schema["input_schema"]["required"]
