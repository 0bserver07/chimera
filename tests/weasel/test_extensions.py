"""Tests for ``chimera.weasel.extensions`` — npm-style auto-discovery.

Exercises the loader against synthetic extension directories materialized
under ``tmp_path``. Mirrors the otter-loader test layout: parametrize the
fixtures so each test names exactly the surfaces it is asserting on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.plugins.base import ComponentRegistry, Hook
from chimera.types import ToolResult
from chimera.weasel import extensions as ext_mod
from chimera.weasel.extensions import (
    WeaselExtension,
    WeaselSlashCommand,
    load_weasel_extensions,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_manifest(
    plugin_dir: Path,
    payload: dict[str, Any],
    *,
    name: str = "manifest.json",
) -> None:
    """Write a manifest file under ``plugin_dir``."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def _write_python(plugin_dir: Path, relpath: str, source: str) -> Path:
    """Write a Python file under ``plugin_dir`` and return its path."""
    target = plugin_dir / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return target


def _write_command_md(
    plugin_dir: Path,
    filename: str,
    *,
    name: str | None = None,
    description: str = "test command",
    body: str = "Command body",
    sub: str = "commands",
) -> None:
    """Write a slash-command markdown file under the requested subdir."""
    cmd_dir = plugin_dir / sub
    cmd_dir.mkdir(parents=True, exist_ok=True)
    if name is None:
        text = body
    else:
        text = f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    (cmd_dir / filename).write_text(text, encoding="utf-8")


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Return a fresh project root under tmp_path."""
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def user_root(tmp_path: Path) -> Path:
    """Return a fresh user-level extension root under tmp_path."""
    root = tmp_path / "home" / ".weasel" / "extensions"
    root.mkdir(parents=True)
    return root


# A trivial BaseTool subclass we can hand back from extensions.
class _GreetTool(BaseTool):
    """Hello-world tool used to verify Python tool wiring."""

    name = "greet"
    description = "Greet someone"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(
        self, args: dict[str, Any], env: Environment | None
    ) -> ToolResult:
        return ToolResult(output="hi")


# Source string for an extension's tools file. Materializes a single
# instance of ``_GreetTool`` and exposes it via the ``TOOLS`` convention.
_TOOLS_SOURCE = """
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class GreetTool(BaseTool):
    name = "greet"
    description = "Greet someone"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args, env):
        return ToolResult(output="hi")


TOOLS = [GreetTool()]
"""


_GET_TOOLS_SOURCE = """
from typing import Any

from chimera.core.tool import BaseTool
from chimera.types import ToolResult


class FarewellTool(BaseTool):
    name = "farewell"
    description = "Say goodbye"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args, env):
        return ToolResult(output="bye")


def get_tools():
    return [FarewellTool()]
"""


_REGISTER_SOURCE = """
from typing import Any

from chimera.core.tool import BaseTool
from chimera.types import ToolResult


class PingTool(BaseTool):
    name = "ping"
    description = "Ping"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args, env):
        return ToolResult(output="pong")


def register(registry):
    registry.register_tool(PingTool())
"""


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------


def test_load_returns_empty_when_no_roots(
    project_root: Path, tmp_path: Path
) -> None:
    """Both roots absent: clean empty list, no exception."""
    missing_user = tmp_path / "no-user"
    extensions = load_weasel_extensions(project_root, user_root=missing_user)
    assert extensions == []


def test_load_user_extension_with_manifest(
    project_root: Path, user_root: Path
) -> None:
    """A user-level extension with a manifest is discovered."""
    plugin_dir = user_root / "demo"
    _write_manifest(
        plugin_dir,
        {"name": "demo", "version": "1.2.3", "description": "d", "author": "Yad"},
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert len(extensions) == 1
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert e.name == "demo"
    assert e.version == "1.2.3"
    assert e.description == "d"
    assert e.author == "Yad"
    assert e.scope == "user"
    assert e.path == plugin_dir
    assert e.language == "python"


def test_load_skips_dirs_without_manifest(
    project_root: Path, user_root: Path
) -> None:
    """A subdir lacking a recognised manifest is skipped, not raised."""
    (user_root / "no-manifest").mkdir()
    _write_manifest(user_root / "good", {"name": "good"})

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert [e.name for e in extensions] == ["good"]


def test_load_skips_hidden_and_pycache(
    project_root: Path, user_root: Path
) -> None:
    """Hidden dirs and __pycache__ are not treated as extensions."""
    _write_manifest(user_root / ".hidden", {"name": "hidden"})
    _write_manifest(user_root / "__pycache__", {"name": "cache"})
    _write_manifest(user_root / "real", {"name": "real"})

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert [e.name for e in extensions] == ["real"]


def test_load_falls_back_to_dirname_when_manifest_lacks_name(
    project_root: Path, user_root: Path
) -> None:
    """Empty/missing manifest ``name`` falls back to the directory name."""
    plugin_dir = user_root / "fallback-name"
    _write_manifest(plugin_dir, {})
    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert [e.name for e in extensions] == ["fallback-name"]


def test_load_accepts_alternate_manifest_filenames(
    project_root: Path, user_root: Path
) -> None:
    """``package.json``, ``weasel.json``, ``extension.json`` all work."""
    _write_manifest(user_root / "a", {"name": "a"}, name="package.json")
    _write_manifest(user_root / "b", {"name": "b"}, name="weasel.json")
    _write_manifest(user_root / "c", {"name": "c"}, name="extension.json")

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    names = sorted(e.name for e in extensions)
    assert names == ["a", "b", "c"]


def test_load_ignores_malformed_manifest(
    project_root: Path, user_root: Path
) -> None:
    """An unparseable manifest is skipped silently."""
    bad = user_root / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{ not valid json", encoding="utf-8")
    _write_manifest(user_root / "good", {"name": "good"})

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert [e.name for e in extensions] == ["good"]


def test_package_json_with_nested_weasel_payload(
    project_root: Path, user_root: Path
) -> None:
    """``package.json`` with a ``"weasel"`` block uses that as the manifest."""
    plugin_dir = user_root / "npm-style"
    payload = {
        "name": "npm-style",
        "version": "9.9.9",
        "author": {"name": "Inner Author"},
        "weasel": {
            "description": "from nested",
        },
    }
    _write_manifest(plugin_dir, payload, name="package.json")

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert len(extensions) == 1
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    # Nested block inherits npm name/version + npm dict-author.
    assert e.name == "npm-style"
    assert e.version == "9.9.9"
    assert e.description == "from nested"
    assert e.author == "Inner Author"


# ---------------------------------------------------------------------------
# Project override
# ---------------------------------------------------------------------------


def test_project_overrides_user_on_name_conflict(
    project_root: Path, user_root: Path
) -> None:
    """Project extension replaces user extension sharing the same ``name``."""
    _write_manifest(user_root / "shared", {"name": "shared", "version": "1.0.0"})
    project_plugin_dir = project_root / ".weasel" / "extensions" / "shared"
    _write_manifest(project_plugin_dir, {"name": "shared", "version": "9.9.9"})

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert len(extensions) == 1
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert e.name == "shared"
    assert e.version == "9.9.9"
    assert e.scope == "project"


def test_user_and_project_coexist_when_distinct(
    project_root: Path, user_root: Path
) -> None:
    """Distinct names from each scope appear together."""
    _write_manifest(user_root / "alpha", {"name": "alpha"})
    _write_manifest(
        project_root / ".weasel" / "extensions" / "beta",
        {"name": "beta"},
    )
    extensions = load_weasel_extensions(project_root, user_root=user_root)
    by_name = {e.name: e for e in extensions}
    assert set(by_name) == {"alpha", "beta"}
    alpha = by_name["alpha"]
    beta = by_name["beta"]
    assert isinstance(alpha, WeaselExtension)
    assert isinstance(beta, WeaselExtension)
    assert alpha.scope == "user"
    assert beta.scope == "project"


# ---------------------------------------------------------------------------
# Python tool loading (TOOLS / get_tools / register conventions)
# ---------------------------------------------------------------------------


def test_extension_loads_tools_via_TOOLS_attr(
    project_root: Path, user_root: Path
) -> None:
    """A module-level ``TOOLS`` list contributes BaseTool instances."""
    plugin_dir = user_root / "with-tools"
    _write_manifest(plugin_dir, {"name": "with-tools", "main": "ext.py"})
    _write_python(plugin_dir, "ext.py", _TOOLS_SOURCE)

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert len(extensions) == 1
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert len(e.tools) == 1
    assert e.tools[0].name == "greet"
    assert e.entry_points == ["ext.py"]
    assert e.load_errors == []


def test_extension_loads_tools_via_get_tools_callable(
    project_root: Path, user_root: Path
) -> None:
    """A module-level ``get_tools()`` function is honoured."""
    plugin_dir = user_root / "with-getter"
    _write_manifest(plugin_dir, {"name": "with-getter", "main": "./ext.py"})
    _write_python(plugin_dir, "ext.py", _GET_TOOLS_SOURCE)

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert [t.name for t in e.tools] == ["farewell"]


def test_extension_loads_tools_via_register_callable(
    project_root: Path, user_root: Path
) -> None:
    """A ``register(registry)`` callable populates a scratch registry."""
    plugin_dir = user_root / "with-register"
    _write_manifest(plugin_dir, {"name": "with-register", "main": "ext.py"})
    _write_python(plugin_dir, "ext.py", _REGISTER_SOURCE)

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert [t.name for t in e.tools] == ["ping"]


def test_extension_loads_extra_tool_files_from_manifest(
    project_root: Path, user_root: Path
) -> None:
    """Manifest ``tools`` array contributes additional Python files."""
    plugin_dir = user_root / "multi-tools"
    _write_manifest(
        plugin_dir,
        {
            "name": "multi-tools",
            "main": "ext.py",
            "tools": ["./extras/extra.py"],
        },
    )
    _write_python(plugin_dir, "ext.py", _TOOLS_SOURCE)
    _write_python(plugin_dir, "extras/extra.py", _GET_TOOLS_SOURCE)

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    names = sorted(t.name for t in e.tools)
    assert names == ["farewell", "greet"]


def test_extension_records_import_failure_without_raising(
    project_root: Path, user_root: Path
) -> None:
    """A bad Python file is recorded onto ``load_errors``, not raised."""
    plugin_dir = user_root / "broken"
    _write_manifest(plugin_dir, {"name": "broken", "main": "ext.py"})
    _write_python(plugin_dir, "ext.py", "this is not valid python !!!")

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert e.tools == []
    assert any("import failed" in msg for msg in e.load_errors)


def test_extension_records_missing_entry_point(
    project_root: Path, user_root: Path
) -> None:
    """A manifest pointing at a missing file records a precise error."""
    plugin_dir = user_root / "missing"
    _write_manifest(plugin_dir, {"name": "missing", "main": "no-such.py"})

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert any("entry not found" in msg for msg in e.load_errors)


def test_extension_resolves_dotted_module_entry_point(
    project_root: Path, user_root: Path
) -> None:
    """``main: "pkg.module"`` resolves to ``pkg/module.py`` under the dir."""
    plugin_dir = user_root / "dotted"
    _write_manifest(plugin_dir, {"name": "dotted", "main": "pkg.module"})
    _write_python(plugin_dir, "pkg/__init__.py", "")
    _write_python(plugin_dir, "pkg/module.py", _TOOLS_SOURCE)

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert [t.name for t in e.tools] == ["greet"]


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def test_extension_loads_hooks_from_manifest_list(
    project_root: Path, user_root: Path
) -> None:
    """A list of hook dicts in the manifest becomes Hook records."""
    plugin_dir = user_root / "hooked"
    _write_manifest(
        plugin_dir,
        {
            "name": "hooked",
            "hooks": [
                {"command": "echo pre", "event_type": "PreToolUse"},
                {"command": "echo post", "event_type": "PostToolUse", "timeout": 5},
            ],
        },
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert [(h.command, h.event_type) for h in e.hooks] == [
        ("echo pre", "PreToolUse"),
        ("echo post", "PostToolUse"),
    ]
    assert e.hooks[1].timeout == 5


def test_extension_loads_hooks_from_manifest_dict(
    project_root: Path, user_root: Path
) -> None:
    """A dict keyed by event name with list values is accepted."""
    plugin_dir = user_root / "hooks-dict"
    _write_manifest(
        plugin_dir,
        {
            "name": "hooks-dict",
            "hooks": {
                "PreToolUse": [{"command": "echo pre"}],
                "PostToolUse": [{"command": "echo post"}],
            },
        },
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    by_event = {h.event_type: h.command for h in e.hooks}
    assert by_event == {"PreToolUse": "echo pre", "PostToolUse": "echo post"}


def test_extension_loads_sidecar_hooks_json(
    project_root: Path, user_root: Path
) -> None:
    """A ``hooks.json`` sidecar is loaded alongside manifest hooks."""
    plugin_dir = user_root / "sidecar"
    _write_manifest(plugin_dir, {"name": "sidecar"})
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "hooks.json").write_text(
        json.dumps([{"command": "echo s", "event_type": "Pre"}]),
        encoding="utf-8",
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert [(h.command, h.event_type) for h in e.hooks] == [("echo s", "Pre")]


def test_extension_skips_invalid_hook_entry(
    project_root: Path, user_root: Path
) -> None:
    """Entries missing ``command`` or ``event_type`` are dropped."""
    plugin_dir = user_root / "hooks-bad"
    _write_manifest(
        plugin_dir,
        {
            "name": "hooks-bad",
            "hooks": [
                {"command": "echo good", "event_type": "Pre"},
                {"event_type": "Pre"},                # missing command
                {"command": "echo no-event"},          # missing event_type
                {"command": "echo bad-timeout", "event_type": "Pre", "timeout": "x"},
            ],
        },
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    cmds = [h.command for h in e.hooks]
    assert "echo good" in cmds
    assert "echo bad-timeout" in cmds
    bad_timeout = next(h for h in e.hooks if h.command == "echo bad-timeout")
    assert bad_timeout.timeout == 30
    assert len(e.hooks) == 2


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def test_extension_loads_slash_commands_from_markdown(
    project_root: Path, user_root: Path
) -> None:
    """``commands/*.md`` files become ``WeaselSlashCommand`` records."""
    plugin_dir = user_root / "with-cmds"
    _write_manifest(plugin_dir, {"name": "with-cmds"})
    _write_command_md(
        plugin_dir,
        "review.md",
        name="review",
        description="review code",
        body="Do a review.",
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert len(e.slash_commands) == 1
    cmd = e.slash_commands[0]
    assert isinstance(cmd, WeaselSlashCommand)
    assert cmd.name == "review"
    assert cmd.description == "review code"
    assert "Do a review." in cmd.body
    assert cmd.plugin == "with-cmds"


def test_extension_loads_inline_slash_commands(
    project_root: Path, user_root: Path
) -> None:
    """Inline manifest ``slash_commands`` entries are recognised."""
    plugin_dir = user_root / "inline-cmds"
    _write_manifest(
        plugin_dir,
        {
            "name": "inline-cmds",
            "slash_commands": [
                {"name": "ping", "description": "ping me", "body": "Run ping."},
            ],
        },
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert [c.name for c in e.slash_commands] == ["ping"]
    assert e.slash_commands[0].source is None
    assert e.slash_commands[0].body == "Run ping."


def test_inline_slash_command_takes_precedence_over_markdown(
    project_root: Path, user_root: Path
) -> None:
    """Inline entries shadow markdown files sharing the same name."""
    plugin_dir = user_root / "dup"
    _write_manifest(
        plugin_dir,
        {
            "name": "dup",
            "slash_commands": [
                {"name": "ping", "body": "inline body"},
            ],
        },
    )
    _write_command_md(plugin_dir, "ping.md", name="ping", body="md body")

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert len(e.slash_commands) == 1
    assert e.slash_commands[0].body == "inline body"


# ---------------------------------------------------------------------------
# Non-Python language detection
# ---------------------------------------------------------------------------


def test_typescript_extension_without_tools_field_indexes_cleanly(
    project_root: Path, user_root: Path
) -> None:
    """TS extension lacking a manifest ``tools`` list contributes nothing.

    Wave 9 wires JS/TS extensions through a Node subprocess (see
    ``test_node_executor.py``), but a manifest that doesn't *declare*
    any tools still indexes successfully — language is recognised and
    no spurious load error is recorded.
    """
    plugin_dir = user_root / "ts-ext"
    _write_manifest(plugin_dir, {"name": "ts-ext", "main": "index.ts"})
    (plugin_dir / "index.ts").write_text("// stub\n", encoding="utf-8")

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    e = extensions[0]
    assert isinstance(e, WeaselExtension)
    assert e.language == "typescript"
    assert e.tools == []
    # No "not yet supported" error: JS/TS is now a first-class runtime.
    assert not any("not yet supported" in msg for msg in e.load_errors)


# ---------------------------------------------------------------------------
# BasePlugin.activate integration
# ---------------------------------------------------------------------------


def test_activate_registers_tools_and_hooks(
    project_root: Path, user_root: Path
) -> None:
    """Calling ``ext.activate(reg)`` propagates tools + hooks."""
    plugin_dir = user_root / "rich"
    _write_manifest(
        plugin_dir,
        {
            "name": "rich",
            "main": "ext.py",
            "hooks": [{"command": "echo a", "event_type": "PreToolUse"}],
        },
    )
    _write_python(plugin_dir, "ext.py", _TOOLS_SOURCE)

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    assert extensions, "loader returned no extensions"

    registry = ComponentRegistry()
    extensions[0].activate(registry)
    assert [t.name for t in registry.tools] == ["greet"]
    pre = registry.hooks.get("PreToolUse", [])
    assert len(pre) == 1
    assert isinstance(pre[0], Hook)
    assert pre[0].command == "echo a"


def test_activate_stages_slash_commands_into_command_slot(
    project_root: Path, user_root: Path
) -> None:
    """Activate exposes slash commands via the generic command slot."""
    plugin_dir = user_root / "slashy"
    _write_manifest(
        plugin_dir,
        {
            "name": "slashy",
            "slash_commands": [{"name": "hello", "body": "hi"}],
        },
    )

    extensions = load_weasel_extensions(project_root, user_root=user_root)
    registry = ComponentRegistry()
    extensions[0].activate(registry)
    kinds = [item.get("kind") for item in registry.commands]
    assert kinds == ["slash_command"]
    cmd = registry.commands[0]["command"]
    assert isinstance(cmd, WeaselSlashCommand)
    assert cmd.name == "hello"


# ---------------------------------------------------------------------------
# Default user_root via Path.home()
# ---------------------------------------------------------------------------


def test_default_user_root_uses_path_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``user_root=`` the loader resolves ``~/.weasel/extensions``."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    plugin_dir = fake_home / ".weasel" / "extensions" / "by-home"
    _write_manifest(plugin_dir, {"name": "by-home"})

    project = tmp_path / "proj"
    project.mkdir()

    extensions = load_weasel_extensions(project)
    assert [e.name for e in extensions] == ["by-home"]


def test_module_exports_public_api() -> None:
    """The public surface stays intentional."""
    assert "load_weasel_extensions" in ext_mod.__all__
    assert "WeaselExtension" in ext_mod.__all__
    assert "WeaselSlashCommand" in ext_mod.__all__


# ---------------------------------------------------------------------------
# Sanity: BaseTool subclass holds (used to build _GreetTool above)
# ---------------------------------------------------------------------------


def test_internal_greet_tool_executes() -> None:
    """Sanity: the test's own BaseTool subclass returns a ToolResult."""
    tool = _GreetTool()
    result = tool.execute({}, None)
    assert isinstance(result, ToolResult)
    assert result.success is True
