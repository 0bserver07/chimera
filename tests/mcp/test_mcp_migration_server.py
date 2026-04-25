"""Tests for chimera.mcp_servers.migration_server — migration planning MCP server."""
from __future__ import annotations

from chimera.mcp_servers.migration_server import MigrationMCPServer


PY2_FILES = {
    "app.py": 'print "hello"\nx = raw_input("name: ")\n',
    "utils.py": "for i in xrange(10):\n    pass\n",
}

JS_CJS_FILES = {
    "index.js": 'const fs = require("fs");\nmodule.exports = main;\n',
}


def _make_request(msg_id: int, method: str, params: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params or {},
    }


class TestMigrationMCPServerMetadata:
    """Test server metadata and protocol."""

    def test_initialize_returns_server_info(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "initialize"))

        assert resp is not None
        info = resp["result"]["serverInfo"]
        assert info["name"] == "chimera-migration"
        assert info["version"] == "0.1.0"

    def test_initialize_returns_protocol_version(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "initialize"))

        assert resp["result"]["protocolVersion"] == "2024-11-05"

    def test_tools_list(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/list"))

        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "chimera_migration_scan" in tool_names
        assert "chimera_migration_apply" in tool_names
        assert "chimera_migration_presets" in tool_names
        assert len(tools) == 3

    def test_ping(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "ping"))
        assert resp is not None
        assert resp["result"] == {}

    def test_unknown_method_returns_error(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "nonexistent/method"))

        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_notification_returns_none(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message({"jsonrpc": "2.0", "method": "initialize", "params": {}})
        assert resp is None


class TestMigrationScan:
    """Test chimera_migration_scan tool."""

    def test_scan_finds_python2_matches(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_scan",
            "arguments": {"files": PY2_FILES, "preset": "python2-to-3"},
        }))

        text = resp["result"]["content"][0]["text"]
        assert "app.py" in text
        assert "print" in text.lower()

    def test_scan_finds_cjs_matches(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_scan",
            "arguments": {"files": JS_CJS_FILES, "preset": "commonjs-to-esm"},
        }))

        text = resp["result"]["content"][0]["text"]
        assert "index.js" in text
        assert "require" in text.lower() or "import" in text.lower()

    def test_scan_no_matches(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_scan",
            "arguments": {
                "files": {"modern.py": "print('hello')\n"},
                "preset": "python2-to-3",
            },
        }))

        text = resp["result"]["content"][0]["text"]
        assert "No migration opportunities" in text

    def test_scan_missing_files_returns_error(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_scan",
            "arguments": {"preset": "python2-to-3"},
        }))

        assert resp["result"]["isError"] is True
        assert "files" in resp["result"]["content"][0]["text"].lower()

    def test_scan_missing_preset_returns_error(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_scan",
            "arguments": {"files": PY2_FILES},
        }))

        assert resp["result"]["isError"] is True
        assert "preset" in resp["result"]["content"][0]["text"].lower()

    def test_scan_unknown_preset_returns_error(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_scan",
            "arguments": {"files": PY2_FILES, "preset": "nonexistent"},
        }))

        assert resp["result"]["isError"] is True
        assert "nonexistent" in resp["result"]["content"][0]["text"].lower()


class TestMigrationApply:
    """Test chimera_migration_apply tool."""

    def test_apply_python2_to_3(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_apply",
            "arguments": {"files": PY2_FILES, "preset": "python2-to-3"},
        }))

        content = resp["result"]["content"]
        # Second content item is JSON of transformed files
        import json
        transformed = json.loads(content[1]["text"])

        assert 'print("hello")' in transformed["app.py"]
        assert "raw_input" not in transformed["app.py"]
        assert "range(" in transformed["utils.py"]
        assert "xrange" not in transformed["utils.py"]

    def test_apply_commonjs_to_esm(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_apply",
            "arguments": {"files": JS_CJS_FILES, "preset": "commonjs-to-esm"},
        }))

        import json
        content = resp["result"]["content"]
        transformed = json.loads(content[1]["text"])

        assert "import fs" in transformed["index.js"]
        assert "export default" in transformed["index.js"]
        assert "require" not in transformed["index.js"]
        assert "module.exports" not in transformed["index.js"]

    def test_apply_missing_files_returns_error(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_apply",
            "arguments": {"preset": "python2-to-3"},
        }))

        assert resp["result"]["isError"] is True

    def test_apply_unknown_preset_returns_error(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_apply",
            "arguments": {"files": PY2_FILES, "preset": "nonexistent"},
        }))

        assert resp["result"]["isError"] is True
        assert "nonexistent" in resp["result"]["content"][0]["text"].lower()


class TestMigrationPresets:
    """Test chimera_migration_presets tool."""

    def test_presets_lists_available(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_presets",
            "arguments": {},
        }))

        text = resp["result"]["content"][0]["text"]
        assert "python2-to-3" in text
        assert "commonjs-to-esm" in text

    def test_presets_includes_descriptions(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_presets",
            "arguments": {},
        }))

        text = resp["result"]["content"][0]["text"]
        assert "print" in text.lower()
        assert "require" in text.lower() or "import" in text.lower()

    def test_presets_includes_rule_counts(self) -> None:
        from chimera.migration.planner import MigrationPlanner

        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "chimera_migration_presets",
            "arguments": {},
        }))

        text = resp["result"]["content"][0]["text"]
        py_count = len(MigrationPlanner.from_preset("python2-to-3")._rules)
        cjs_count = len(MigrationPlanner.from_preset("commonjs-to-esm")._rules)
        assert f"{py_count} rules" in text
        assert f"{cjs_count} rules" in text


class TestUnknownTool:
    """Test unknown tool handling."""

    def test_unknown_tool_returns_error(self) -> None:
        server = MigrationMCPServer()
        resp = server.handle_message(_make_request(1, "tools/call", {
            "name": "nonexistent_tool",
            "arguments": {},
        }))

        assert resp["result"]["isError"] is True
        assert "Unknown tool" in resp["result"]["content"][0]["text"]
