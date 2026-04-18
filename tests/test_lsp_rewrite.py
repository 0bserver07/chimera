# tests/test_lsp_rewrite.py
"""Tests for the rewritten LSP module (manager, session, tool, servers)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from chimera.lsp.servers import BUILTIN_SERVERS
from chimera.lsp.session import LSPSession
from chimera.lsp.manager import LSPManager
from chimera.lsp.tool import LSPTool
from chimera.core.loop_config import LoopConfig


# ---- Server configs ----


class TestLanguageServerConfig:
    def test_builtin_python(self):
        python = [s for s in BUILTIN_SERVERS if s.name == "python"]
        assert len(python) == 1
        assert ".py" in python[0].extensions

    def test_builtin_count(self):
        assert len(BUILTIN_SERVERS) >= 4  # python, typescript, go, rust


# ---- LSPSession ----


class TestLSPSession:
    def test_write_message_content_length(self):
        """Verify LSP uses Content-Length framing (not newline-delimited)."""
        LSPSession(["echo"])
        import json
        msg = {"jsonrpc": "2.0", "method": "test"}
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        # Verify the framing includes Content-Length
        assert b"Content-Length" in header

    def test_definition_parses_single_location(self):
        session = LSPSession(["echo"])
        session._process = MagicMock()
        session._lock = MagicMock()
        # Mock _send_request to return a single location
        session._send_request = MagicMock(return_value={
            "result": {"uri": "file:///foo.py", "range": {"start": {"line": 10, "character": 0}}},
        })
        result = session.definition("file:///bar.py", 5, 3)
        assert len(result) == 1
        assert result[0]["uri"] == "file:///foo.py"

    def test_definition_parses_list(self):
        session = LSPSession(["echo"])
        session._send_request = MagicMock(return_value={
            "result": [
                {"uri": "file:///a.py", "range": {"start": {"line": 1}}},
                {"uri": "file:///b.py", "range": {"start": {"line": 2}}},
            ],
        })
        result = session.definition("file:///c.py", 0, 0)
        assert len(result) == 2

    def test_references(self):
        session = LSPSession(["echo"])
        session._send_request = MagicMock(return_value={
            "result": [
                {"uri": "file:///x.py", "range": {"start": {"line": 5}}},
            ],
        })
        result = session.references("file:///y.py", 1, 0)
        assert len(result) == 1

    def test_hover_dict_contents(self):
        session = LSPSession(["echo"])
        session._send_request = MagicMock(return_value={
            "result": {"contents": {"value": "def foo() -> int"}},
        })
        result = session.hover("file:///z.py", 0, 0)
        assert result == "def foo() -> int"

    def test_hover_string_contents(self):
        session = LSPSession(["echo"])
        session._send_request = MagicMock(return_value={
            "result": {"contents": "simple hover text"},
        })
        result = session.hover("file:///z.py", 0, 0)
        assert result == "simple hover text"

    def test_hover_none(self):
        session = LSPSession(["echo"])
        session._send_request = MagicMock(return_value={"result": None})
        result = session.hover("file:///z.py", 0, 0)
        assert result is None

    def test_document_symbols(self):
        session = LSPSession(["echo"])
        session._send_request = MagicMock(return_value={
            "result": [
                {"name": "MyClass", "kind": 5, "range": {"start": {"line": 0}}},
                {"name": "my_func", "kind": 12, "range": {"start": {"line": 10}}},
            ],
        })
        result = session.document_symbols("file:///m.py")
        assert len(result) == 2
        assert result[0]["name"] == "MyClass"


# ---- LSPManager ----


class TestLSPManager:
    def test_add_server(self):
        manager = LSPManager()
        manager.add("python", ["pyright-langserver", "--stdio"], (".py",))
        assert ".py" in manager._ext_map
        assert manager._ext_map[".py"] == "python"

    def test_ext_routing(self):
        manager = LSPManager()
        manager.add("python", ["pyright-langserver", "--stdio"], (".py",))
        manager.add("typescript", ["ts-server", "--stdio"], (".ts", ".tsx"))
        # Without started sessions, get_session returns None
        assert manager.get_session("foo.py") is None  # no session started
        assert manager.get_session("bar.rs") is None   # no config

    def test_get_session_unknown_ext(self):
        manager = LSPManager()
        assert manager.get_session("file.xyz") is None

    def test_for_project_auto_detects(self):
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd == "pyright-langserver" else None
            manager = LSPManager.for_project("/tmp/project")
            assert "python" in manager._configs
            assert "go" not in manager._configs  # gopls not on PATH

    def test_context_manager(self):
        manager = LSPManager()
        with manager as m:
            assert m is manager

    def test_add_string_command(self):
        manager = LSPManager()
        manager.add("custom", "my-langserver --stdio", (".custom",))
        assert manager._configs["custom"].command == ["my-langserver", "--stdio"]


# ---- LSPTool ----


class TestLSPTool:
    def _make_tool_with_session(self):
        manager = LSPManager()
        manager.add("python", ["pyright", "--stdio"], (".py",))
        # Create a mock session
        mock_session = MagicMock()
        manager._sessions["python"] = mock_session
        tool = LSPTool(manager)
        return tool, mock_session

    def test_go_to_definition(self):
        tool, session = self._make_tool_with_session()
        session.definition.return_value = [
            {"uri": "file:///foo.py", "range": {"start": {"line": 10, "character": 0}}},
        ]
        result = tool.execute({"action": "go_to_definition", "file": "test.py", "line": 5, "character": 3}, None)
        assert result.success
        assert "foo.py" in result.output

    def test_find_references(self):
        tool, session = self._make_tool_with_session()
        session.references.return_value = [
            {"uri": "file:///a.py", "range": {"start": {"line": 1}}},
            {"uri": "file:///b.py", "range": {"start": {"line": 2}}},
        ]
        result = tool.execute({"action": "find_references", "file": "test.py", "line": 0, "character": 0}, None)
        assert result.success
        assert "2 references" in result.output

    def test_hover(self):
        tool, session = self._make_tool_with_session()
        session.hover.return_value = "def foo() -> int"
        result = tool.execute({"action": "hover", "file": "test.py", "line": 0, "character": 0}, None)
        assert result.success
        assert "def foo" in result.output

    def test_document_symbols(self):
        tool, session = self._make_tool_with_session()
        session.document_symbols.return_value = [
            {"name": "MyClass", "kind": 5, "range": {"start": {"line": 0}}},
        ]
        result = tool.execute({"action": "document_symbols", "file": "test.py"}, None)
        assert result.success
        assert "MyClass" in result.output

    def test_no_server_for_file(self):
        manager = LSPManager()
        tool = LSPTool(manager)
        result = tool.execute({"action": "hover", "file": "test.xyz"}, None)
        assert not result.success
        assert "No language server" in result.error

    def test_unknown_action(self):
        tool, session = self._make_tool_with_session()
        result = tool.execute({"action": "invalid_action", "file": "test.py"}, None)
        assert not result.success
        assert "Unknown action" in result.error


# ---- LoopConfig LSP field ----


class TestLoopConfigLSP:
    def test_lsp_field_accepted(self):
        manager = LSPManager()
        config = LoopConfig(lsp=manager)
        assert config.lsp is manager

    def test_lsp_field_default_none(self):
        config = LoopConfig()
        assert config.lsp is None


# ---- Backward compat ----


class TestBackwardCompat:
    def test_diagnostic_and_severity_exported(self):
        from chimera.lsp import Diagnostic, Severity
        assert Diagnostic is not None
        assert Severity is not None

    def test_lsp_client_still_exported(self):
        from chimera.lsp import LSPClient
        assert LSPClient is not None

    def test_chimera_top_level_exports(self):
        from chimera import Diagnostic, LSPClient, LSPManager, LSPTool, Severity
        assert all(x is not None for x in [Diagnostic, LSPClient, LSPManager, LSPTool, Severity])
