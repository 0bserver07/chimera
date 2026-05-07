"""End-to-end tests for JS/TS extension execution wiring (W13-G10).

Coverage scope (orthogonal to ``test_node_executor.py``, which exercises
the wrapper unit-by-unit):

* ``detect_node_version`` — parses ``vX.Y.Z`` correctly.
* ``ensure_npm_install`` — short-circuits, runs, and remembers.
* Loader → executor pipeline — a fixture extension with a TS-ish handler
  (compiled to plain ``.js`` so we don't ship a transpiler) loads via
  :func:`load_weasel_extensions` and produces a *callable* tool that
  returns the expected output when invoked.
* Subprocess sandbox via timeout — slow tools surface a clean error
  rather than hanging the loader.

The whole module is gated on ``node`` being on PATH; pure-Python
helpers also have their own no-node assertions further down.
"""
from __future__ import annotations

import json
import os
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
    _NPM_INSTALL_MARKER,
    detect_node,
    detect_node_version,
    detect_npm,
    ensure_npm_install,
)


_NODE_PATH = detect_node()
_node_required = pytest.mark.skipif(
    _NODE_PATH is None,
    reason="node executable not on PATH",
)


# ---------------------------------------------------------------------------
# Self-contained Node fixture: a single index.js that recognises a few
# tool names. Authors may write the source in TypeScript — we don't ship
# a transpiler here, so the fixture pretends it was already compiled.
# ---------------------------------------------------------------------------


_INDEX_JS = r"""
'use strict';
function getArg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : null;
}
const tool = getArg('--tool');
const argsRaw = getArg('--args') || '{}';
let args = {};
try { args = JSON.parse(argsRaw); } catch (e) { args = {}; }
function emit(o) { process.stdout.write(JSON.stringify(o)); }

switch (tool) {
  case 'greet':
    emit({ output: 'hello, ' + String(args.who || 'world'),
           metadata: { source: 'index.js' } });
    break;
  case 'add':
    emit({ output: String(Number(args.a || 0) + Number(args.b || 0)) });
    break;
  case 'sleep-too-long':
    setTimeout(() => emit({ output: 'late' }), 5000);
    break;
  default:
    emit({ error: 'unknown tool: ' + String(tool) });
}
"""


def _write_extension(
    root: Path,
    *,
    name: str = "weasel-js-fixture",
    with_deps: bool = False,
) -> Path:
    """Materialize a JS extension dir under ``root`` and return its path."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    package: dict[str, object] = {
        "name": name,
        "version": "0.1.0",
        "description": "fixture",
        "main": "index.js",
        "language": "javascript",
        "tools": [
            {
                "name": "greet",
                "description": "say hi to args.who",
                "parameters": {
                    "type": "object",
                    "properties": {"who": {"type": "string"}},
                },
            },
            "add",
            {"name": "sleep-too-long", "timeout": 0.5},
        ],
    }
    if with_deps:
        # Any non-empty ``dependencies`` block triggers ``ensure_npm_install``
        # in the loader. Tests that exercise the failure path patch
        # ``subprocess.run`` rather than relying on npm's network state.
        package["dependencies"] = {"nonexistent-pkg-xyz": "0.0.1"}
    (plugin_dir / "package.json").write_text(
        json.dumps(package), encoding="utf-8"
    )
    (plugin_dir / "index.js").write_text(_INDEX_JS, encoding="utf-8")
    return plugin_dir


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def user_root(tmp_path: Path) -> Path:
    root = tmp_path / "home" / ".weasel" / "extensions"
    root.mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# Pure-Python helpers (no node required)
# ---------------------------------------------------------------------------


def test_detect_node_version_returns_tuple_or_none() -> None:
    """Returns either a ``(major, minor, patch)`` tuple or ``None``."""
    result = detect_node_version()
    assert result is None or (
        isinstance(result, tuple)
        and len(result) == 3
        and all(isinstance(n, int) for n in result)
    )


@_node_required
def test_detect_node_version_major_at_least_14() -> None:
    """Any host that has ``node`` should report a sane major version.

    Node 12 reached EOL in 2022 and Node 14 in 2023; the floor for
    this project is whatever ships in current LTS, which is well
    above 14. Asserting >= 14 catches a parsing regression without
    pinning to a specific runtime.
    """
    version = detect_node_version()
    assert version is not None
    major, _, _ = version
    assert major >= 14


def test_detect_npm_returns_path_or_none() -> None:
    """``detect_npm`` mirrors :func:`detect_node`'s return contract."""
    result = detect_npm()
    assert result is None or isinstance(result, str)


def test_ensure_npm_install_skips_when_no_deps(tmp_path: Path) -> None:
    """No ``dependencies`` key in package.json: nothing runs."""
    plugin_dir = _write_extension(tmp_path, with_deps=False)
    ran, err = ensure_npm_install(plugin_dir)
    assert ran is False
    assert err is None


def test_ensure_npm_install_skips_when_marker_present(tmp_path: Path) -> None:
    """Marker file under ``node_modules`` short-circuits the spawn."""
    plugin_dir = _write_extension(tmp_path, with_deps=True)
    node_modules = plugin_dir / "node_modules"
    node_modules.mkdir(parents=True)
    (node_modules / _NPM_INSTALL_MARKER).write_text("ok\n", encoding="utf-8")

    ran, err = ensure_npm_install(plugin_dir)
    assert ran is False
    assert err is None


def test_ensure_npm_install_reports_missing_npm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``npm`` is absent from PATH, error is reported but not raised."""
    plugin_dir = _write_extension(tmp_path, with_deps=True)
    # Force npm-detection to fail by hijacking shutil.which inside the
    # node_executor module.
    import chimera.weasel.node_executor as ne

    monkeypatch.setattr(
        ne, "shutil", _ShutilStub(node_path=_NODE_PATH, npm_path=None)
    )
    ran, err = ensure_npm_install(plugin_dir)
    assert ran is False
    assert err is not None
    assert "npm executable not found" in err


class _ShutilStub:
    """Stub mirroring the small ``shutil`` surface we use."""

    def __init__(self, *, node_path: str | None, npm_path: str | None) -> None:
        self._node = node_path
        self._npm = npm_path

    def which(self, name: str) -> str | None:
        if name == "node":
            return self._node
        if name == "npm":
            return self._npm
        return None


# ---------------------------------------------------------------------------
# End-to-end loader → tool execution (node required)
# ---------------------------------------------------------------------------


@_node_required
def test_loader_to_tool_round_trip(
    project_root: Path, user_root: Path
) -> None:
    """User-scoped JS extension loads and its tool returns expected stdout."""
    _write_extension(user_root, name="hello-js")

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert len(extensions) == 1
    ext = extensions[0]
    assert isinstance(ext, WeaselExtension)
    assert ext.language == "javascript"
    # No "not yet supported" error: the JS bridge is wired.
    assert not any(
        "not yet supported" in msg for msg in ext.load_errors
    )

    tools_by_name = {t.name: t for t in ext.tools}
    assert {"greet", "add", "sleep-too-long"} <= set(tools_by_name)

    greet = tools_by_name["greet"]
    assert isinstance(greet, NodeExtensionTool)
    out = greet.execute({"who": "weasel"}, None)
    assert isinstance(out, ToolResult)
    assert out.success is True
    assert out.output == "hello, weasel"
    # Index file recorded so debug surfaces work.
    assert "index.js" in (ext.entry_points or [""])[0]

    add = tools_by_name["add"]
    out = add.execute({"a": 2, "b": 5}, None)
    assert out.success is True
    assert out.output == "7"


@_node_required
def test_loader_project_overrides_user(
    project_root: Path, user_root: Path
) -> None:
    """Project-scoped extension wins on plugin-name conflict."""
    _write_extension(user_root, name="dup")
    proj_dir = project_root / ".weasel" / "extensions"
    proj_dir.mkdir(parents=True)
    plugin_dir = _write_extension(proj_dir, name="dup")
    # Tweak the project copy so we can tell them apart.
    (plugin_dir / "index.js").write_text(
        _INDEX_JS.replace("'hello, '", "'project-hello, '"),
        encoding="utf-8",
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert len(extensions) == 1
    [ext] = extensions
    assert isinstance(ext, WeaselExtension)
    assert ext.scope == "project"
    greet = next(t for t in ext.tools if t.name == "greet")
    out = greet.execute({"who": "x"}, None)
    assert out.success is True
    assert out.output == "project-hello, x"


@_node_required
def test_loader_sandboxes_via_timeout(
    project_root: Path, user_root: Path
) -> None:
    """Slow tools fail with a timeout error, never block the loader."""
    _write_extension(user_root, name="slow-ext")
    extensions = load_weasel_extensions(project_root, user_root=user_root)
    [ext] = extensions
    assert isinstance(ext, WeaselExtension)
    slow = next(t for t in ext.tools if t.name == "sleep-too-long")
    out = slow.execute({}, None)
    assert out.success is False
    assert out.error is not None
    assert "timed out" in out.error


@_node_required
def test_loader_tolerates_failed_npm_install(
    project_root: Path, user_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``npm install`` records an error but tools still build.

    We patch ``subprocess.run`` to simulate npm exiting non-zero so the
    test stays hermetic (no network, no real npm registry). We assert:

    * The loader does not raise.
    * The error gets recorded onto ``ext.load_errors`` so the operator
      sees the cause without reading shell output.

    Tools themselves still build — they will fail per-call when their
    missing module is required, but the loader stays usable.
    """
    if detect_npm() is None:
        pytest.skip("npm not on PATH")
    _write_extension(user_root, name="bad-deps", with_deps=True)

    import subprocess as _sub
    real_run = _sub.run

    def fake_run(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        cmd = args[0] if args else kwargs.get("args") or []
        # Only intercept the npm invocation; let real subprocess handle
        # version checks and node tool spawns elsewhere.
        if isinstance(cmd, list) and cmd and Path(cmd[0]).name == "npm":
            class _R:
                returncode = 1
                stdout = ""
                stderr = "npm ERR! synthetic failure for test\n"
            return _R()
        return real_run(*args, **kwargs)

    monkeypatch.setattr(_sub, "run", fake_run)

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert len(extensions) == 1
    [ext] = extensions
    assert isinstance(ext, WeaselExtension)
    assert any(
        "npm install" in msg for msg in ext.load_errors
    ), f"expected npm install error, got {ext.load_errors!r}"
    # Tools are still wired so the agent sees them.
    assert {t.name for t in ext.tools} >= {"greet", "add"}


@_node_required
def test_subprocess_isolation_uses_plugin_cwd(
    tmp_path: Path,
) -> None:
    """Spawned node sees ``plugin_dir`` as cwd so ``require()`` resolves.

    We verify this by writing a sibling ``hello.js`` that the entry
    requires; if the cwd were the test runner's, the require would
    fail.
    """
    plugin_dir = tmp_path / "ext-cwd"
    plugin_dir.mkdir()
    (plugin_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "ext-cwd",
                "version": "0.1.0",
                "main": "index.js",
                "language": "javascript",
                "tools": ["from-sibling"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "hello.js").write_text(
        "module.exports = function () { return 'sibling-loaded'; };\n",
        encoding="utf-8",
    )
    (plugin_dir / "index.js").write_text(
        r"""
'use strict';
const sibling = require('./hello.js');
process.stdout.write(JSON.stringify({ output: sibling() }));
""",
        encoding="utf-8",
    )

    user_root = tmp_path / "home" / ".weasel" / "extensions"
    user_root.mkdir(parents=True)
    # Move our extension under the user root for a clean loader test.
    target = user_root / "ext-cwd"
    target.mkdir()
    for src in plugin_dir.iterdir():
        (target / src.name).write_bytes(src.read_bytes())

    project = tmp_path / "project"
    project.mkdir()
    extensions = load_weasel_extensions(project, user_root=user_root)
    [ext] = extensions
    assert isinstance(ext, WeaselExtension)
    tool = next(t for t in ext.tools if t.name == "from-sibling")
    # Run with a non-extension cwd to prove the executor sets cwd itself.
    cwd = os.getcwd()
    try:
        os.chdir(project)
        result = tool.execute({}, None)
    finally:
        os.chdir(cwd)
    assert result.success is True
    assert result.output == "sibling-loaded"
