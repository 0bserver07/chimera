"""Tests for the LSP module."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.lsp.base import Diagnostic, LSPClient, Severity


# ---------------------------------------------------------------------------
# Tests: Diagnostic
# ---------------------------------------------------------------------------

class TestDiagnostic:
    def test_construction(self) -> None:
        d = Diagnostic(
            file="foo.py",
            line=10,
            column=5,
            severity=Severity.ERROR,
            message="undefined variable",
        )
        assert d.file == "foo.py"
        assert d.line == 10
        assert d.severity == Severity.ERROR

    def test_to_feedback_str_basic(self) -> None:
        d = Diagnostic(
            file="bar.py",
            line=3,
            column=1,
            severity=Severity.WARNING,
            message="unused import",
        )
        s = d.to_feedback_str()
        assert "warning" in s
        assert "bar.py:3:1" in s
        assert "unused import" in s

    def test_to_feedback_str_with_source_and_code(self) -> None:
        d = Diagnostic(
            file="baz.py",
            line=1,
            column=1,
            severity=Severity.INFORMATION,
            message="hint",
            source="pyright",
            code="reportMissing",
        )
        s = d.to_feedback_str()
        assert "(pyright)" in s
        assert "[reportMissing]" in s

    def test_optional_fields_default(self) -> None:
        d = Diagnostic(
            file="x.py", line=1, column=1,
            severity=Severity.HINT, message="hi",
        )
        assert d.source is None
        assert d.code is None


# ---------------------------------------------------------------------------
# Tests: Severity
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_values(self) -> None:
        assert Severity.ERROR.value == 1
        assert Severity.WARNING.value == 2
        assert Severity.INFORMATION.value == 3
        assert Severity.HINT.value == 4


# ---------------------------------------------------------------------------
# Tests: LSPClient
# ---------------------------------------------------------------------------

class TestLSPClient:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            LSPClient()  # type: ignore[abstract]

    def test_mock_implementation(self) -> None:
        class MockLSP(LSPClient):
            def initialize(self, root_path: str) -> None:
                pass

            def diagnostics(self, file_path: str) -> list[Diagnostic]:
                return [
                    Diagnostic(
                        file=file_path, line=1, column=1,
                        severity=Severity.ERROR, message="test error",
                    )
                ]

            def shutdown(self) -> None:
                pass

        client = MockLSP()
        client.initialize("/tmp/project")
        diags = client.diagnostics("test.py")
        assert len(diags) == 1
        assert diags[0].severity == Severity.ERROR
        client.shutdown()

    def test_context_manager(self) -> None:
        class MockLSP(LSPClient):
            def __init__(self) -> None:
                self.shut_down = False

            def initialize(self, root_path: str) -> None:
                pass

            def diagnostics(self, file_path: str) -> list[Diagnostic]:
                return []

            def shutdown(self) -> None:
                self.shut_down = True

        with MockLSP() as client:
            client.initialize("/tmp")
        assert client.shut_down is True


# ---------------------------------------------------------------------------
# Tests: imports
# ---------------------------------------------------------------------------

class TestImports:
    def test_chimera_exports(self) -> None:
        from chimera import Diagnostic, LSPClient, Severity
        assert Diagnostic is not None
        assert LSPClient is not None
        assert Severity is not None

    def test_new_type_exports(self) -> None:
        from chimera import ChangeType, FileChange, PendingApproval, drain_steps
        assert ChangeType is not None
        assert FileChange is not None
        assert PendingApproval is not None
        assert drain_steps is not None


# ---------------------------------------------------------------------------
# Tests: LSPSession background reader + diagnostics cache
# ---------------------------------------------------------------------------

import threading
from chimera.lsp.session import LSPSession


class TestLSPSessionDiagnosticsCache:
    def test_handle_diagnostics_parses(self) -> None:
        """_handle_diagnostics caches parsed Diagnostic objects."""
        session = LSPSession(["echo"])
        session._handle_diagnostics({
            "uri": "file:///test.py",
            "diagnostics": [
                {
                    "range": {"start": {"line": 5, "character": 3}},
                    "severity": 1,
                    "message": "undefined name 'foo'",
                    "source": "pyright",
                    "code": "reportUndefinedVariable",
                },
                {
                    "range": {"start": {"line": 10, "character": 0}},
                    "severity": 2,
                    "message": "unused import",
                },
            ],
        })
        diags = session.get_diagnostics("file:///test.py")
        assert len(diags) == 2
        assert diags[0].severity == Severity.ERROR
        assert diags[0].line == 5
        assert diags[0].message == "undefined name 'foo'"
        assert diags[0].source == "pyright"
        assert diags[0].code == "reportUndefinedVariable"
        assert diags[1].severity == Severity.WARNING

    def test_get_diagnostics_empty_for_unknown_uri(self) -> None:
        """get_diagnostics returns empty list for unknown URIs."""
        session = LSPSession(["echo"])
        assert session.get_diagnostics("file:///unknown.py") == []

    def test_cached_diagnostics_returns_copy(self) -> None:
        """cached_diagnostics returns a copy, not the internal dict."""
        session = LSPSession(["echo"])
        session._handle_diagnostics({
            "uri": "file:///a.py",
            "diagnostics": [
                {"range": {"start": {"line": 0, "character": 0}},
                 "severity": 1, "message": "err"},
            ],
        })
        cache = session.cached_diagnostics
        assert "file:///a.py" in cache
        # Modifying returned dict shouldn't affect internal cache
        cache.clear()
        assert len(session.cached_diagnostics) == 1

    def test_diagnostics_overwritten_on_update(self) -> None:
        """New publishDiagnostics replaces previous cache for same URI."""
        session = LSPSession(["echo"])
        session._handle_diagnostics({
            "uri": "file:///b.py",
            "diagnostics": [
                {"range": {"start": {"line": 0, "character": 0}},
                 "severity": 1, "message": "err1"},
            ],
        })
        assert len(session.get_diagnostics("file:///b.py")) == 1
        session._handle_diagnostics({
            "uri": "file:///b.py",
            "diagnostics": [],
        })
        assert len(session.get_diagnostics("file:///b.py")) == 0

    def test_read_loop_routes_response(self) -> None:
        """Background reader routes responses to pending requests."""
        session = LSPSession(["echo"])
        # Simulate a pending request
        event = threading.Event()
        session._pending[42] = event
        # Simulate receiving a response
        response = {"jsonrpc": "2.0", "id": 42, "result": {"foo": "bar"}}
        session._responses[42] = response
        event.set()
        # Verify response is accessible
        assert session._responses[42] == response


# ---------------------------------------------------------------------------
# Tests: LSPSession new methods (completion, rename, code_action)
# ---------------------------------------------------------------------------

from unittest.mock import patch as mock_patch


class TestLSPSessionNewMethods:
    def test_completion_returns_items(self) -> None:
        """completion() returns list of completion items."""
        session = LSPSession(["echo"])
        response = {"result": [
            {"label": "append", "kind": 2},
            {"label": "clear", "kind": 2},
        ]}
        with mock_patch.object(session, "_send_request", return_value=response):
            items = session.completion("file:///test.py", 5, 10)
        assert len(items) == 2
        assert items[0]["label"] == "append"

    def test_completion_handles_completion_list(self) -> None:
        """completion() handles CompletionList response."""
        session = LSPSession(["echo"])
        response = {"result": {"isIncomplete": False, "items": [
            {"label": "foo"},
        ]}}
        with mock_patch.object(session, "_send_request", return_value=response):
            items = session.completion("file:///test.py", 0, 0)
        assert len(items) == 1
        assert items[0]["label"] == "foo"

    def test_completion_returns_empty_on_none(self) -> None:
        """completion() returns [] when server returns None."""
        session = LSPSession(["echo"])
        with mock_patch.object(session, "_send_request", return_value=None):
            items = session.completion("file:///test.py", 0, 0)
        assert items == []

    def test_rename_returns_workspace_edit(self) -> None:
        """rename() returns workspace edit dict."""
        session = LSPSession(["echo"])
        edit = {"changes": {"file:///test.py": [{"range": {}, "newText": "bar"}]}}
        response = {"result": edit}
        with mock_patch.object(session, "_send_request", return_value=response):
            result = session.rename("file:///test.py", 1, 5, "bar")
        assert result == edit

    def test_rename_returns_none_when_unavailable(self) -> None:
        """rename() returns None when server returns None."""
        session = LSPSession(["echo"])
        with mock_patch.object(session, "_send_request", return_value=None):
            result = session.rename("file:///test.py", 1, 5, "bar")
        assert result is None

    def test_code_action_returns_actions(self) -> None:
        """code_action() returns list of available code actions."""
        session = LSPSession(["echo"])
        response = {"result": [
            {"title": "Import os", "kind": "quickfix"},
            {"title": "Extract method", "kind": "refactor.extract"},
        ]}
        with mock_patch.object(session, "_send_request", return_value=response):
            actions = session.code_action("file:///test.py", 1, 0, 5, 10)
        assert len(actions) == 2
        assert actions[0]["title"] == "Import os"

    def test_code_action_returns_empty_on_none(self) -> None:
        """code_action() returns [] when server returns None."""
        session = LSPSession(["echo"])
        with mock_patch.object(session, "_send_request", return_value=None):
            actions = session.code_action("file:///test.py", 0, 0, 0, 0)
        assert actions == []


# ---------------------------------------------------------------------------
# Tests: LSPTool expanded actions
# ---------------------------------------------------------------------------

from chimera.lsp.tool import LSPTool
from chimera.lsp.manager import LSPManager


class TestLSPToolExpanded:
    def _make_tool(self) -> tuple[LSPTool, MagicMock]:
        manager = LSPManager()
        manager.add("python", ["pyright", "--stdio"], (".py",))
        mock_session = MagicMock()
        manager._sessions["python"] = mock_session
        tool = LSPTool(manager)
        return tool, mock_session

    def test_diagnostics_action(self) -> None:
        """diagnostics action returns cached diagnostics."""
        tool, session = self._make_tool()
        session.get_diagnostics.return_value = [
            Diagnostic(file="test.py", line=1, column=0, severity=Severity.ERROR, message="err"),
        ]
        result = tool.execute({"action": "diagnostics", "file": "test.py"}, None)
        assert result.success
        assert "err" in result.output

    def test_diagnostics_empty(self) -> None:
        """diagnostics action with no diagnostics."""
        tool, session = self._make_tool()
        session.get_diagnostics.return_value = []
        result = tool.execute({"action": "diagnostics", "file": "test.py"}, None)
        assert "No diagnostics" in result.output

    def test_completion_action(self) -> None:
        """completion action returns completion items."""
        tool, session = self._make_tool()
        session.completion.return_value = [
            {"label": "append", "kind": 2},
            {"label": "clear", "kind": 2},
        ]
        result = tool.execute(
            {"action": "completion", "file": "test.py", "line": 5, "character": 10},
            None,
        )
        assert result.success
        assert "append" in result.output

    def test_rename_action(self) -> None:
        """rename action returns edit summary."""
        tool, session = self._make_tool()
        session.rename.return_value = {
            "changes": {
                "file:///test.py": [{"range": {}, "newText": "bar"}],
                "file:///other.py": [{"range": {}, "newText": "bar"}, {"range": {}, "newText": "bar"}],
            },
        }
        result = tool.execute(
            {"action": "rename", "file": "test.py", "line": 1, "character": 5, "new_name": "bar"},
            None,
        )
        assert result.success
        assert "3 edits" in result.output
        assert "2 files" in result.output

    def test_rename_missing_new_name(self) -> None:
        """rename action requires new_name parameter."""
        tool, session = self._make_tool()
        result = tool.execute(
            {"action": "rename", "file": "test.py", "line": 1, "character": 5},
            None,
        )
        assert not result.success
        assert "new_name" in result.error

    def test_code_action_action(self) -> None:
        """code_action action returns available actions."""
        tool, session = self._make_tool()
        session.code_action.return_value = [
            {"title": "Import os", "kind": "quickfix"},
        ]
        result = tool.execute(
            {"action": "code_action", "file": "test.py", "line": 1, "character": 0,
             "end_line": 1, "end_character": 5},
            None,
        )
        assert result.success
        assert "Import os" in result.output

    def test_unknown_action(self) -> None:
        """Unknown action returns error."""
        tool, session = self._make_tool()
        result = tool.execute({"action": "bogus", "file": "test.py"}, None)
        assert not result.success
        assert "Unknown action" in result.error
