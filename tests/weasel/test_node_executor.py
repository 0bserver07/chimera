"""Tests for ``chimera.weasel.node_executor`` — Node-subprocess bridge.

The fixture is a self-contained ``index.js`` (CommonJS) and
``index.mjs`` (ESM) — no ``node_modules``, no transpiler, no extra
dependencies. We skip cleanly when ``node`` is not on PATH so the
suite still runs on hosts without Node installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.types import ToolResult
from chimera.weasel.extensions import (
    WeaselExtension,
    load_weasel_extensions,
)
from chimera.weasel.node_executor import (
    NodeExtensionTool,
    _coerce_tool_descriptor,
    _parse_node_response,
    build_node_tools,
    detect_node,
    resolve_node_entry,
)


# ---------------------------------------------------------------------------
# Top-of-file gate: skip the whole module when node is absent.
# Each test still re-checks via the public ``detect_node`` helper so
# unit tests of pure-Python helpers (parse_response, coerce_descriptor,
# resolve_node_entry) can run even on no-node hosts.
# ---------------------------------------------------------------------------


_NODE_PATH = detect_node()
_node_required = pytest.mark.skipif(
    _NODE_PATH is None,
    reason="node executable not available; subprocess-driven tests skipped",
)


# ---------------------------------------------------------------------------
# Self-contained Node fixture (CommonJS).
#
# Reads --tool / --args off argv and prints exactly one JSON object on
# stdout. Recognised tools:
#
#   echo:    {"output": "<args.text>", "metadata": {...}}
#   add:     {"output": "<a + b>"}
#   crash:   throws (exit 1, stderr trace)
#   bad:     prints non-JSON to stdout (exercises parser fallback)
#   err:     prints {"error": "boom"} (explicit extension error)
#   slow:    sleeps 5s (exercises timeout path)
# ---------------------------------------------------------------------------

_INDEX_JS_CJS = r"""
'use strict';
function getArg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : null;
}
const tool = getArg('--tool');
const argsRaw = getArg('--args') || '{}';
let args = {};
try { args = JSON.parse(argsRaw); } catch (e) { args = {}; }

function emit(obj) { process.stdout.write(JSON.stringify(obj)); }

switch (tool) {
  case 'echo':
    emit({ output: String(args.text || ''), metadata: { tool: 'echo' } });
    break;
  case 'add':
    emit({ output: String((args.a || 0) + (args.b || 0)) });
    break;
  case 'crash':
    throw new Error('boom from crash tool');
  case 'bad':
    process.stdout.write('not json at all');
    break;
  case 'err':
    emit({ error: 'explicit extension error' });
    break;
  case 'slow':
    setTimeout(() => emit({ output: 'late' }), 5000);
    break;
  default:
    emit({ error: 'unknown tool: ' + String(tool) });
}
"""


_INDEX_MJS_ESM = r"""
function getArg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : null;
}
const tool = getArg('--tool');
const argsRaw = getArg('--args') || '{}';
let args = {};
try { args = JSON.parse(argsRaw); } catch (e) { args = {}; }

function emit(obj) { process.stdout.write(JSON.stringify(obj)); }

if (tool === 'esm-echo') {
  emit({ output: 'esm:' + String(args.text || ''), metadata: { kind: 'esm' } });
} else {
  emit({ error: 'esm fixture only knows esm-echo' });
}
"""


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write_cjs_extension(root: Path, name: str = "node-ext") -> Path:
    """Materialize a CommonJS extension dir with a manifest + index.js."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "package.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "node fixture",
                "main": "index.js",
                "language": "javascript",
                "tools": [
                    {"name": "echo", "description": "echo text",
                     "parameters": {
                         "type": "object",
                         "properties": {"text": {"type": "string"}},
                     }},
                    "add",
                    "crash",
                    "bad",
                    "err",
                    {"name": "slow", "timeout": 0.5},
                ],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "index.js").write_text(_INDEX_JS_CJS, encoding="utf-8")
    return plugin_dir


def _write_esm_extension(root: Path, name: str = "esm-ext") -> Path:
    """Materialize an ESM extension dir with ``"type": "module"``."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "package.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "type": "module",
                "main": "index.mjs",
                "language": "javascript",
                "tools": ["esm-echo"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "index.mjs").write_text(_INDEX_MJS_ESM, encoding="utf-8")
    return plugin_dir


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Project root for the integration loader path."""
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def user_root(tmp_path: Path) -> Path:
    """Mock ``~/.weasel/extensions/`` root."""
    root = tmp_path / "home" / ".weasel" / "extensions"
    root.mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# Pure-Python helpers (no node required)
# ---------------------------------------------------------------------------


def test_detect_node_returns_path_or_none() -> None:
    """``detect_node`` returns either an absolute path or ``None``."""
    result = detect_node()
    assert result is None or isinstance(result, str)
    if result is not None:
        # If returned, it should look like a real path.
        assert "/" in result or "\\" in result


def test_resolve_node_entry_prefers_main_js(tmp_path: Path) -> None:
    """``main: index.js`` resolves to that file."""
    (tmp_path / "index.js").write_text("// stub\n", encoding="utf-8")
    entry = resolve_node_entry(tmp_path, {"main": "index.js"})
    assert entry is not None
    assert entry.entry_path.name == "index.js"
    assert entry.is_module is False


def test_resolve_node_entry_swaps_ts_to_js(tmp_path: Path) -> None:
    """``main: ext.ts`` falls back to ``ext.js`` when present."""
    (tmp_path / "ext.js").write_text("// stub\n", encoding="utf-8")
    entry = resolve_node_entry(tmp_path, {"main": "ext.ts"})
    assert entry is not None
    assert entry.entry_path.name == "ext.js"


def test_resolve_node_entry_falls_back_to_index_js(tmp_path: Path) -> None:
    """No ``main`` field: ``index.js`` is the default entry."""
    (tmp_path / "index.js").write_text("// stub\n", encoding="utf-8")
    entry = resolve_node_entry(tmp_path, {})
    assert entry is not None
    assert entry.entry_path.name == "index.js"


def test_resolve_node_entry_detects_esm(tmp_path: Path) -> None:
    """``"type": "module"`` flips ``is_module`` to True."""
    (tmp_path / "package.json").write_text(
        json.dumps({"type": "module"}), encoding="utf-8",
    )
    (tmp_path / "index.mjs").write_text("// stub\n", encoding="utf-8")
    entry = resolve_node_entry(tmp_path, {"main": "index.mjs"})
    assert entry is not None
    assert entry.is_module is True


def test_resolve_node_entry_returns_none_when_missing(tmp_path: Path) -> None:
    """No JS file anywhere: ``None``."""
    assert resolve_node_entry(tmp_path, {"main": "nope.ts"}) is None


def test_coerce_tool_descriptor_string() -> None:
    """A bare string becomes a descriptor with default schema/timeout."""
    d = _coerce_tool_descriptor("foo")
    assert d == {
        "name": "foo",
        "description": "",
        "parameters": {"type": "object"},
        "timeout_s": 30.0,
    }


def test_coerce_tool_descriptor_dict_with_overrides() -> None:
    """Dict descriptors carry through description/parameters/timeout."""
    d = _coerce_tool_descriptor(
        {
            "name": "bar",
            "description": "bar tool",
            "parameters": {"type": "object", "properties": {"x": {"type": "number"}}},
            "timeout": 10,
        }
    )
    assert d is not None
    assert d["name"] == "bar"
    assert d["description"] == "bar tool"
    assert d["parameters"]["properties"]["x"] == {"type": "number"}
    assert d["timeout_s"] == 10.0


def test_coerce_tool_descriptor_rejects_garbage() -> None:
    """Non-string/non-dict entries return ``None``."""
    assert _coerce_tool_descriptor(123) is None
    assert _coerce_tool_descriptor(None) is None
    assert _coerce_tool_descriptor({}) is None
    assert _coerce_tool_descriptor({"name": ""}) is None


def test_parse_node_response_success() -> None:
    """Standard success payload becomes a ToolResult with output + metadata."""
    res = _parse_node_response(
        stdout='{"output": "hi", "metadata": {"k": "v"}}',
        stderr="",
        exit_code=0,
        tool_name="t",
    )
    assert res.success is True
    assert res.output == "hi"
    assert res.metadata["k"] == "v"
    # Debug fields always preserved.
    assert "exit_code" in res.metadata


def test_parse_node_response_explicit_error() -> None:
    """``{"error": "..."}`` payload surfaces as a non-success ToolResult."""
    res = _parse_node_response(
        stdout='{"error": "boom"}',
        stderr="",
        exit_code=0,
        tool_name="t",
    )
    assert res.success is False
    assert res.error == "boom"


def test_parse_node_response_non_json() -> None:
    """Non-JSON stdout is surfaced as an error with raw output captured."""
    res = _parse_node_response(
        stdout="garbage",
        stderr="",
        exit_code=0,
        tool_name="t",
    )
    assert res.success is False
    assert res.error is not None
    assert "non-JSON" in res.error
    assert res.metadata["stdout"] == "garbage"


def test_parse_node_response_empty_with_crash() -> None:
    """No stdout + non-zero exit: we trust stderr is the diagnostic."""
    res = _parse_node_response(
        stdout="",
        stderr="Error: kaboom",
        exit_code=1,
        tool_name="crash",
    )
    assert res.success is False
    assert res.error is not None
    assert "exited 1" in res.error
    assert "kaboom" in res.metadata["stderr"]


def test_parse_node_response_non_object() -> None:
    """JSON list at top level is not a valid response shape."""
    res = _parse_node_response(
        stdout="[1, 2, 3]",
        stderr="",
        exit_code=0,
        tool_name="t",
    )
    assert res.success is False
    assert res.error is not None
    assert "expected an object" in res.error


# ---------------------------------------------------------------------------
# Node-required: actual subprocess execution
# ---------------------------------------------------------------------------


@_node_required
def test_node_executor_echo_round_trip(tmp_path: Path) -> None:
    """End-to-end: spawn node, send args, parse JSON stdout."""
    plugin_dir = _write_cjs_extension(tmp_path)
    entry = resolve_node_entry(plugin_dir, {"main": "index.js"})
    assert entry is not None

    tool = NodeExtensionTool(
        tool_name="echo",
        description="echo text",
        entry_point=entry,
        plugin_dir=plugin_dir,
        node_path=_NODE_PATH,
    )
    result = tool.execute({"text": "hello"}, None)
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.output == "hello"
    assert result.metadata.get("tool") == "echo"


@_node_required
def test_node_executor_explicit_error(tmp_path: Path) -> None:
    """Tool that prints ``{"error": "..."}`` becomes a failed ToolResult."""
    plugin_dir = _write_cjs_extension(tmp_path)
    entry = resolve_node_entry(plugin_dir, {"main": "index.js"})
    tool = NodeExtensionTool(
        tool_name="err",
        description="",
        entry_point=entry,
        plugin_dir=plugin_dir,
        node_path=_NODE_PATH,
    )
    result = tool.execute({}, None)
    assert result.success is False
    assert result.error == "explicit extension error"


@_node_required
def test_node_executor_crash(tmp_path: Path) -> None:
    """A thrown error in the JS yields an error ToolResult with stderr."""
    plugin_dir = _write_cjs_extension(tmp_path)
    entry = resolve_node_entry(plugin_dir, {"main": "index.js"})
    tool = NodeExtensionTool(
        tool_name="crash",
        description="",
        entry_point=entry,
        plugin_dir=plugin_dir,
        node_path=_NODE_PATH,
    )
    result = tool.execute({}, None)
    assert result.success is False
    assert result.metadata["exit_code"] != 0
    assert "boom" in result.metadata["stderr"]


@_node_required
def test_node_executor_non_json_stdout(tmp_path: Path) -> None:
    """Stdout that is not JSON surfaces as a parse error, not a crash."""
    plugin_dir = _write_cjs_extension(tmp_path)
    entry = resolve_node_entry(plugin_dir, {"main": "index.js"})
    tool = NodeExtensionTool(
        tool_name="bad",
        description="",
        entry_point=entry,
        plugin_dir=plugin_dir,
        node_path=_NODE_PATH,
    )
    result = tool.execute({}, None)
    assert result.success is False
    assert result.error is not None
    assert "non-JSON" in result.error


@_node_required
def test_node_executor_timeout(tmp_path: Path) -> None:
    """A slow tool with a tight timeout surfaces a clean timeout error."""
    plugin_dir = _write_cjs_extension(tmp_path)
    entry = resolve_node_entry(plugin_dir, {"main": "index.js"})
    tool = NodeExtensionTool(
        tool_name="slow",
        description="",
        entry_point=entry,
        plugin_dir=plugin_dir,
        node_path=_NODE_PATH,
        timeout_s=0.5,
    )
    result = tool.execute({}, None)
    assert result.success is False
    assert result.error is not None
    assert "timed out" in result.error


@_node_required
def test_node_executor_esm_round_trip(tmp_path: Path) -> None:
    """An ESM extension (``type: module``) executes the same way as CJS."""
    plugin_dir = _write_esm_extension(tmp_path)
    entry = resolve_node_entry(plugin_dir, {"main": "index.mjs"})
    assert entry is not None
    assert entry.is_module is True

    tool = NodeExtensionTool(
        tool_name="esm-echo",
        description="",
        entry_point=entry,
        plugin_dir=plugin_dir,
        node_path=_NODE_PATH,
    )
    result = tool.execute({"text": "world"}, None)
    assert result.success is True
    assert result.output == "esm:world"
    assert result.metadata.get("kind") == "esm"


# ---------------------------------------------------------------------------
# build_node_tools + loader integration
# ---------------------------------------------------------------------------


def test_build_node_tools_skips_when_no_tools_field(tmp_path: Path) -> None:
    """Missing/empty manifest ``tools`` field: empty result, no errors."""
    plugin_dir = tmp_path / "ext"
    plugin_dir.mkdir()
    errors: list[str] = []
    tools, eps = build_node_tools(
        plugin_dir=plugin_dir,
        plugin_name="ext",
        manifest={},
        errors=errors,
    )
    assert tools == []
    assert eps == []
    assert errors == []


def test_build_node_tools_records_missing_entry_point(tmp_path: Path) -> None:
    """Manifest declares tools but no JS file exists: error recorded."""
    plugin_dir = tmp_path / "ext"
    plugin_dir.mkdir()
    errors: list[str] = []
    tools, eps = build_node_tools(
        plugin_dir=plugin_dir,
        plugin_name="ext",
        manifest={"main": "index.js", "tools": ["foo"]},
        errors=errors,
    )
    # Tools are still built (so the agent knows they exist) but invocation
    # will fail; loader-level error explains the cause.
    assert len(tools) == 1
    assert any("no JS entry point" in e for e in errors)


def test_build_node_tools_skips_malformed_descriptors(tmp_path: Path) -> None:
    """Descriptor entries that fail coercion are skipped with an error."""
    (tmp_path / "index.js").write_text("// stub\n", encoding="utf-8")
    errors: list[str] = []
    tools, _eps = build_node_tools(
        plugin_dir=tmp_path,
        plugin_name="ext",
        manifest={"main": "index.js", "tools": ["good", 123, {"name": ""}]},
        errors=errors,
    )
    assert [t.name for t in tools] == ["good"]
    assert any("malformed" in e for e in errors)


def test_build_node_tools_dedupes_tool_names(tmp_path: Path) -> None:
    """Duplicate tool names in the manifest produce only one wrapper."""
    (tmp_path / "index.js").write_text("// stub\n", encoding="utf-8")
    errors: list[str] = []
    tools, _eps = build_node_tools(
        plugin_dir=tmp_path,
        plugin_name="ext",
        manifest={"main": "index.js", "tools": ["foo", "foo"]},
        errors=errors,
    )
    assert [t.name for t in tools] == ["foo"]


@_node_required
def test_loader_wires_node_tools_end_to_end(
    project_root: Path, user_root: Path
) -> None:
    """Loader produces a callable JS-backed tool from a manifest."""
    _write_cjs_extension(user_root, name="loader-ext")

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert len(extensions) == 1
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert e.language == "javascript"
    names = sorted(t.name for t in e.tools)
    assert "echo" in names
    assert "add" in names

    # The tool we exercise via the loader produces real output.
    echo = next(t for t in e.tools if t.name == "echo")
    assert isinstance(echo, NodeExtensionTool)
    result = echo.execute({"text": "from loader"}, None)
    assert result.success is True
    assert result.output == "from loader"

    # Entry point recorded relative to the plugin dir.
    assert "index.js" in (e.entry_points or [""])[0]
    # No "not yet supported" error: the JS bridge is wired.
    assert not any("not yet supported" in msg for msg in e.load_errors)


def test_loader_records_error_when_node_missing(
    project_root: Path, user_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``node`` is absent, tools still build but loader notes it."""
    monkeypatch.setattr(
        "chimera.weasel.node_executor.shutil.which", lambda _name: None,
    )
    _write_cjs_extension(user_root, name="no-node-ext")

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert any("node executable not found" in msg for msg in e.load_errors)

    # Calling the tool returns a clean failure rather than crashing.
    echo = next(t for t in e.tools if t.name == "echo")
    assert isinstance(echo, NodeExtensionTool)
    result = echo.execute({"text": "x"}, None)
    assert result.success is False
    assert result.error is not None
    assert "Node is not installed" in result.error


def test_node_extension_tool_handles_unserializable_args(tmp_path: Path) -> None:
    """Args that don't JSON-serialize are flagged before spawning Node."""
    (tmp_path / "index.js").write_text("// stub\n", encoding="utf-8")
    entry = resolve_node_entry(tmp_path, {"main": "index.js"})
    tool = NodeExtensionTool(
        tool_name="foo",
        description="",
        entry_point=entry,
        plugin_dir=tmp_path,
        node_path=_NODE_PATH or "/usr/bin/node",
    )

    class NotJSONable:
        pass

    args: dict[str, Any] = {"obj": NotJSONable()}
    result = tool.execute(args, None)
    assert result.success is False
    assert result.error is not None
    assert "serialize" in result.error
