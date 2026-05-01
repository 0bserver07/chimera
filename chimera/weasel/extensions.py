"""Weasel extension auto-discovery (npm-style, Python-only in this wave).

Weasel mirrors the upstream minimal-harness convention of auto-discovering
extensions from two scopes:

1. **User-level**: ``~/.weasel/extensions/<name>/`` — applies host-wide.
2. **Project-level**: ``<project_root>/.weasel/extensions/<name>/`` —
   overrides user-level on plugin-name conflict.

Each extension directory contributes a manifest plus, by convention, a set
of Python entry points that register tools, hooks, and slash commands. The
upstream brand uses TypeScript modules; this wave ships **Python-only**
support — TS/JS subprocess execution is a follow-up. Extensions written in
TS/JS are recognised (their manifest is parsed, their assets indexed) but
their runtime contributions are deferred until the JS shim lands.

Manifest shape (intentionally permissive — unknown keys are preserved):

```json
{
  "name": "my-ext",                // falls back to dir name when missing
  "version": "0.1.0",
  "description": "...",
  "author": "...",
  "main": "ext.py",                // optional Python entry point (module
                                   //   path or file path relative to dir)
  "tools": ["./tools/foo.py"],     // optional Python files contributing
                                   //   ``BaseTool`` subclasses
  "hooks": [                       // shell-command hooks (Hook records)
    {"command": "echo pre", "event_type": "PreToolUse"}
  ],
  "slash_commands": [              // markdown-backed slash commands
    {"name": "review", "description": "...", "body": "..."}
  ]
}
```

The loader is **stdlib-only** and never raises on a malformed extension:
the offending entry is skipped so a single bad manifest cannot break the
whole weasel invocation.

The companion ``WeaselExtension`` dataclass is a :class:`BasePlugin`
subclass: callers can ``activate`` it against a
:class:`~chimera.plugins.base.ComponentRegistry` to materialize hooks and
slash commands without rewalking the directory.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.plugins.base import BasePlugin, ComponentRegistry, Hook

if TYPE_CHECKING:
    from chimera.core.tool import BaseTool


# Manifest filenames searched in priority order. We accept both
# ``manifest.json`` and ``package.json`` so npm-style extensions and
# bespoke chimera-plugin manifests both load without modification.
_MANIFEST_NAMES: tuple[str, ...] = (
    "manifest.json",
    "package.json",
    "weasel.json",
    "extension.json",
)


# Slash-command directory names searched in priority order. Mirrors the
# otter loader so plugins authored for either CLI work in both.
_COMMAND_DIRS: tuple[str, ...] = ("commands", "command", "slash_commands")


@dataclass
class WeaselSlashCommand:
    """One slash command contributed by a weasel extension.

    Args:
        name: Command name (without leading slash).
        description: Short summary surfaced in ``/help`` listings.
        body: Markdown body served as the command's expanded prompt.
        source: Absolute path to the source ``.md`` file, or ``None``
            when the entry was declared inline in the manifest.
        plugin: The contributing extension's name.
    """

    name: str
    description: str
    body: str
    source: Path | None
    plugin: str


@dataclass
class WeaselExtension(BasePlugin):
    """An extension materialized from a ``.weasel/extensions/<name>/`` dir.

    Holds the manifest metadata plus pre-parsed extension records so
    callers can introspect contributions without re-walking the dir. The
    class is a :class:`BasePlugin` subclass: ``activate(registry)``
    propagates tools / hooks / slash commands onto a
    :class:`ComponentRegistry`.
    """

    _name: str = ""
    _version: str = "0.0.0"
    _description: str = ""
    _author: str = ""
    path: Path = field(default_factory=Path)
    scope: str = "user"
    language: str = "python"
    manifest: dict[str, Any] = field(default_factory=dict)
    tools: list[BaseTool] = field(default_factory=list)
    hooks: list[Hook] = field(default_factory=list)
    slash_commands: list[WeaselSlashCommand] = field(default_factory=list)
    # Python entry-point module paths that produced ``tools``. Useful for
    # introspection and reload semantics; not load-bearing for activation.
    entry_points: list[str] = field(default_factory=list)
    # Errors encountered while loading. Recorded rather than raised so a
    # single bad extension cannot break the whole weasel invocation.
    load_errors: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:  # noqa: D401  (BasePlugin.name docstring suffices)
        return self._name

    def __post_init__(self) -> None:
        """Mirror BasePlugin's class-attribute slots from manifest values."""
        self.version = self._version
        self.description = self._description
        self.author = self._author

    # ---- BasePlugin extension hooks ---------------------------------------

    def register_tools(self, registry: ComponentRegistry) -> None:
        """Stage ``BaseTool`` instances onto the registry."""
        for tool in self.tools:
            registry.register_tool(tool)

    def register_hooks(self, registry: ComponentRegistry) -> None:
        """Stage hooks, keyed by event type."""
        for hook in self.hooks:
            registry.register_hook(hook.event_type, hook)

    def register_skills(self, registry: ComponentRegistry) -> None:
        """Stage slash commands via the generic command slot.

        ``ComponentRegistry`` does not expose a first-class slash-command
        slot, so we land on the same convention as the otter loader and
        record each as ``{"kind": "slash_command", ...}`` so downstream
        aggregators can pluck them out.
        """
        for cmd in self.slash_commands:
            registry.register_command({"kind": "slash_command", "command": cmd})


# ---------------------------------------------------------------------------
# Manifest + asset parsing helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    """Best-effort JSON read; returns ``None`` on any failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _load_manifest(plugin_dir: Path) -> dict[str, Any] | None:
    """Locate and parse the first manifest file present.

    Returns ``None`` when no recognised manifest exists or all parses
    fail. ``package.json`` files are accepted both at the top level and
    when they declare a chimera-style payload under a ``"weasel"`` or
    ``"chimera"`` key — npm-style manifests carry unrelated metadata
    (``"dependencies"``, ``"scripts"``) that we must not confuse with
    extension fields.
    """
    for name in _MANIFEST_NAMES:
        candidate = plugin_dir / name
        if not candidate.is_file():
            continue
        data = _read_json(candidate)
        if not isinstance(data, dict):
            continue
        if name == "package.json":
            # Prefer a nested chimera/weasel payload when present, but
            # fall back to the top-level dict so manifest-style
            # ``package.json`` files still work.
            for key in ("weasel", "chimera"):
                nested = data.get(key)
                if isinstance(nested, dict):
                    # Carry over the npm name/version when missing.
                    nested.setdefault("name", data.get("name", plugin_dir.name))
                    nested.setdefault("version", data.get("version", "0.0.0"))
                    nested.setdefault("description", data.get("description", ""))
                    nested.setdefault("author", _coerce_author(data.get("author")))
                    return nested
            # Otherwise treat the top-level dict as the manifest.
            return data
        return data
    return None


def _coerce_author(raw: Any) -> str:
    """Normalize npm's ``author`` field, which may be a string or dict."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        name = raw.get("name", "")
        return str(name) if isinstance(name, str) else ""
    return ""


def _detect_language(plugin_dir: Path, manifest: dict[str, Any]) -> str:
    """Determine the extension's runtime language.

    Honors a manifest ``"language"`` hint when present, otherwise infers
    from the entry-point filename. Defaults to ``"python"``.
    """
    raw = manifest.get("language")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    main = manifest.get("main")
    if isinstance(main, str):
        lower = main.lower()
        if lower.endswith((".ts", ".tsx", ".mts")):
            return "typescript"
        if lower.endswith((".js", ".mjs", ".cjs")):
            return "javascript"
        if lower.endswith(".py"):
            return "python"
    # Index files in the dir as a last-resort hint.
    if (plugin_dir / "index.ts").is_file() or (plugin_dir / "index.js").is_file():
        return "typescript" if (plugin_dir / "index.ts").is_file() else "javascript"
    return "python"


# ---------------------------------------------------------------------------
# Python entry-point loading
# ---------------------------------------------------------------------------


def _resolve_python_path(plugin_dir: Path, entry: str) -> Path | None:
    """Resolve a manifest entry-point string to an on-disk Python file.

    ``entry`` may be either:

    * A path relative to ``plugin_dir`` (``./tools/foo.py`` or
      ``tools/foo.py``); or
    * A dotted module name (``my_ext.tools``) that resolves to
      ``my_ext/tools.py`` or ``my_ext/tools/__init__.py`` under the
      plugin directory.

    Returns ``None`` when the entry cannot be located.
    """
    if not entry:
        return None
    # Strip leading ``./`` for cleaner Path semantics.
    cleaned = entry[2:] if entry.startswith("./") else entry
    if cleaned.endswith(".py") or "/" in cleaned or "\\" in cleaned:
        candidate = (plugin_dir / cleaned).resolve()
        if candidate.is_file():
            return candidate
        return None
    # Treat as a dotted module name.
    parts = cleaned.split(".")
    module_file = (plugin_dir / Path(*parts).with_suffix(".py"))
    if module_file.is_file():
        return module_file
    package_init = plugin_dir / Path(*parts) / "__init__.py"
    if package_init.is_file():
        return package_init
    return None


def _import_module_from_file(path: Path, plugin_name: str, suffix: str) -> Any:
    """Import a Python file under a synthetic module name.

    The synthetic name is namespaced by the plugin so two extensions can
    each ship a ``tools.py`` without colliding in ``sys.modules``.
    """
    safe_plugin = "".join(c if c.isalnum() else "_" for c in plugin_name)
    safe_suffix = "".join(c if c.isalnum() else "_" for c in suffix)
    module_name = f"_chimera_weasel_ext_{safe_plugin}_{safe_suffix}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_tools_from_module(module: Any) -> list[BaseTool]:
    """Pull ``BaseTool`` instances out of an imported extension module.

    Recognised conventions, in priority order:

    1. Module-level ``TOOLS`` attribute — list/tuple of instances.
    2. Module-level ``get_tools()`` callable returning an iterable of
       instances.
    3. ``register(registry)`` callable that mutates a
       :class:`ComponentRegistry` we hand in.
    """
    # Local import keeps the top of this module zero-dependency at import
    # time; the tool ABC pulls in core/tool.py which has its own imports.
    from chimera.core.tool import BaseTool as _BaseTool

    out: list[BaseTool] = []

    tools_attr = getattr(module, "TOOLS", None)
    if isinstance(tools_attr, (list, tuple)):
        out.extend(t for t in tools_attr if isinstance(t, _BaseTool))

    getter = getattr(module, "get_tools", None)
    if callable(getter):
        try:
            produced = getter()
        except Exception:  # noqa: BLE001 — quarantine extension errors
            produced = []
        if isinstance(produced, (list, tuple)):
            out.extend(t for t in produced if isinstance(t, _BaseTool))

    register = getattr(module, "register", None)
    if callable(register):
        scratch = ComponentRegistry()
        try:
            register(scratch)
        except Exception:  # noqa: BLE001
            pass
        else:
            out.extend(scratch.tools)

    return out


def _load_python_tools(
    plugin_dir: Path,
    plugin_name: str,
    manifest: dict[str, Any],
    errors: list[str],
) -> tuple[list[BaseTool], list[str]]:
    """Materialize ``BaseTool`` instances from manifest entry points.

    The manifest may declare:

    * ``main`` — a single Python entry point (``"ext.py"`` or
      ``"my_ext"``).
    * ``tools`` — a list of additional Python files contributing tools.

    Each path is imported in isolation; failures are recorded onto
    ``errors`` rather than raised so one bad file does not poison the
    whole extension.
    """
    tools: list[BaseTool] = []
    entry_points: list[str] = []

    candidates: list[str] = []
    main = manifest.get("main")
    if isinstance(main, str) and main.strip():
        candidates.append(main.strip())
    tools_raw = manifest.get("tools")
    if isinstance(tools_raw, list):
        candidates.extend(t for t in tools_raw if isinstance(t, str) and t.strip())

    seen: set[Path] = set()
    for entry in candidates:
        resolved = _resolve_python_path(plugin_dir, entry)
        if resolved is None:
            errors.append(f"entry not found: {entry}")
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            module = _import_module_from_file(resolved, plugin_name, entry)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"import failed for {entry}: {exc}")
            continue
        try:
            produced = _collect_tools_from_module(module)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tool collection failed for {entry}: {exc}")
            continue
        tools.extend(produced)
        entry_points.append(entry)

    return tools, entry_points


# ---------------------------------------------------------------------------
# Hook + slash-command parsing
# ---------------------------------------------------------------------------


def _coerce_hook(entry: Any) -> Hook | None:
    """Normalize one hook descriptor into a :class:`Hook`.

    Returns ``None`` when the entry is missing required fields. We are
    deliberately liberal: an integer ``timeout`` that fails to coerce
    falls back to the dataclass default of 30 seconds.
    """
    if not isinstance(entry, dict):
        return None
    cmd = entry.get("command")
    event = entry.get("event_type") or entry.get("event")
    if not isinstance(cmd, str) or not cmd:
        return None
    if not isinstance(event, str) or not event:
        return None
    timeout_raw = entry.get("timeout", 30)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = 30
    env_raw = entry.get("env", {})
    env = (
        {str(k): str(v) for k, v in env_raw.items()}
        if isinstance(env_raw, dict)
        else {}
    )
    wd = entry.get("working_dir")
    return Hook(
        command=cmd,
        event_type=event,
        working_dir=str(wd) if isinstance(wd, str) else None,
        timeout=timeout,
        env=env,
    )


def _load_hooks(
    plugin_dir: Path,
    manifest: dict[str, Any],
) -> list[Hook]:
    """Aggregate hooks from the manifest plus optional ``hooks.json``.

    Both shapes are accepted, mirroring otter's loader:

    * A list of hook dicts.
    * A dict keyed by event type whose values are lists of hook dicts.
    """
    out: list[Hook] = []

    raw = manifest.get("hooks")
    if isinstance(raw, list):
        for entry in raw:
            hook = _coerce_hook(entry)
            if hook is not None:
                out.append(hook)
    elif isinstance(raw, dict):
        for ev, entries in raw.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    merged = {**entry, "event_type": entry.get("event_type") or ev}
                    hook = _coerce_hook(merged)
                    if hook is not None:
                        out.append(hook)

    # Sidecar hooks file (hooks.json or hooks/hooks.json).
    sidecars = (
        plugin_dir / "hooks.json",
        plugin_dir / "hooks" / "hooks.json",
    )
    for sidecar in sidecars:
        if not sidecar.is_file():
            continue
        data = _read_json(sidecar)
        if isinstance(data, list):
            for entry in data:
                hook = _coerce_hook(entry)
                if hook is not None:
                    out.append(hook)
        elif isinstance(data, dict):
            for ev, entries in data.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        merged = {**entry, "event_type": entry.get("event_type") or ev}
                        hook = _coerce_hook(merged)
                        if hook is not None:
                            out.append(hook)
        # First sidecar wins; don't double-load.
        break

    return out


def _parse_command_md(path: Path, plugin_name: str) -> WeaselSlashCommand | None:
    """Parse one slash-command markdown file.

    Mirrors otter's parser: a YAML frontmatter block (``---`` delimited)
    carrying ``name`` and ``description``, followed by the prompt body.
    Files without frontmatter fall back to using the filename stem as
    the command name.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    name = path.stem
    description = ""
    body = text.strip()
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2].strip()
            for line in frontmatter.splitlines():
                if ":" not in line:
                    continue
                key, _, raw = line.partition(":")
                key = key.strip()
                value = raw.strip().strip("\"'")
                if key == "name" and value:
                    name = value
                elif key == "description":
                    description = value
    return WeaselSlashCommand(
        name=name,
        description=description,
        body=body,
        source=path,
        plugin=plugin_name,
    )


def _coerce_inline_slash_command(
    entry: Any, plugin_name: str
) -> WeaselSlashCommand | None:
    """Normalize a manifest-declared inline slash command."""
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    body = entry.get("body") or entry.get("prompt") or ""
    description = entry.get("description", "")
    return WeaselSlashCommand(
        name=name.strip(),
        description=str(description) if isinstance(description, str) else "",
        body=str(body) if isinstance(body, str) else "",
        source=None,
        plugin=plugin_name,
    )


def _load_slash_commands(
    plugin_dir: Path,
    plugin_name: str,
    manifest: dict[str, Any],
) -> list[WeaselSlashCommand]:
    """Aggregate slash commands from inline manifest + on-disk markdown."""
    out: list[WeaselSlashCommand] = []

    inline = manifest.get("slash_commands") or manifest.get("commands")
    if isinstance(inline, list):
        for entry in inline:
            cmd = _coerce_inline_slash_command(entry, plugin_name)
            if cmd is not None:
                out.append(cmd)

    seen_names = {c.name for c in out}
    for sub in _COMMAND_DIRS:
        d = plugin_dir / sub
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            cmd = _parse_command_md(md, plugin_name)
            if cmd is None or cmd.name in seen_names:
                continue
            out.append(cmd)
            seen_names.add(cmd.name)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _user_root() -> Path:
    """Return the user-level weasel extension root."""
    return Path.home() / ".weasel" / "extensions"


def _project_root_dir(project_root: Path) -> Path:
    """Return the project-level weasel extension root."""
    return project_root / ".weasel" / "extensions"


def _extension_dirs(root: Path) -> list[Path]:
    """Return immediate subdirectories of ``root`` that look like extensions.

    An extension directory is any subdir of ``root`` that contains at
    least one of the recognised manifest files. Hidden directories
    (``.git``, ``__pycache__``, dotfiles) are skipped.
    """
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        if any((child / m).is_file() for m in _MANIFEST_NAMES):
            out.append(child)
    return out


def _build_extension(plugin_dir: Path, scope: str) -> WeaselExtension | None:
    """Materialize one :class:`WeaselExtension` from a directory.

    Returns ``None`` when the manifest is missing or unparseable.
    """
    manifest = _load_manifest(plugin_dir)
    if manifest is None:
        return None
    raw_name = manifest.get("name")
    name = (
        str(raw_name).strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else plugin_dir.name
    )
    language = _detect_language(plugin_dir, manifest)

    errors: list[str] = []
    tools: list[BaseTool] = []
    entry_points: list[str] = []
    if language == "python":
        tools, entry_points = _load_python_tools(plugin_dir, name, manifest, errors)
    elif language in {"javascript", "typescript"}:
        # Wave 9 (W1): wire JS/TS extensions through a Node subprocess.
        # Local import keeps node_executor's optional-by-design subprocess
        # plumbing out of the loader's import graph for pure-Python users.
        from chimera.weasel.node_executor import build_node_tools

        tools, entry_points = build_node_tools(
            plugin_dir=plugin_dir,
            plugin_name=name,
            manifest=manifest,
            errors=errors,
        )
    else:
        # Truly unknown languages still surface a clear error so callers
        # can render a "needs runtime" message instead of failing silently.
        errors.append(
            f"language '{language}' not yet supported; "
            "Python and Node (JS/TS) extensions execute in this wave",
        )

    hooks = _load_hooks(plugin_dir, manifest)
    slash_commands = _load_slash_commands(plugin_dir, name, manifest)

    return WeaselExtension(
        _name=name,
        _version=str(manifest.get("version", "0.0.0")),
        _description=str(manifest.get("description", "")),
        _author=_coerce_author(manifest.get("author", "")),
        path=plugin_dir,
        scope=scope,
        language=language,
        manifest=manifest,
        tools=tools,
        hooks=hooks,
        slash_commands=slash_commands,
        entry_points=entry_points,
        load_errors=errors,
    )


def load_weasel_extensions(
    project_root: Path,
    *,
    user_root: Path | None = None,
) -> list[BasePlugin]:
    """Load all weasel extensions from user and project roots.

    Args:
        project_root: Project directory; ``<project_root>/.weasel/extensions/``
            is scanned for project-scoped extensions.
        user_root: Override for the user-level extension root. Defaults
            to ``~/.weasel/extensions/``. Primarily used by tests.

    Returns:
        A list of :class:`BasePlugin` instances. Project-level extensions
        replace user-level ones on plugin-name conflict; the returned
        order is *user-first then project*, with conflicts resolved by
        emitting only the project-level instance in the project's slot.
    """
    user_dir = user_root if user_root is not None else _user_root()
    project_dir = _project_root_dir(project_root)

    user_exts: dict[str, WeaselExtension] = {}
    for d in _extension_dirs(user_dir):
        ext = _build_extension(d, scope="user")
        if ext is not None:
            user_exts[ext.name] = ext

    project_exts: dict[str, WeaselExtension] = {}
    for d in _extension_dirs(project_dir):
        ext = _build_extension(d, scope="project")
        if ext is not None:
            project_exts[ext.name] = ext

    # Project overrides user on name conflict.
    merged: dict[str, WeaselExtension] = {}
    for name, ext in user_exts.items():
        if name not in project_exts:
            merged[name] = ext
    for name, ext in project_exts.items():
        merged[name] = ext

    return list(merged.values())


__all__ = [
    "WeaselExtension",
    "WeaselSlashCommand",
    "load_weasel_extensions",
]
