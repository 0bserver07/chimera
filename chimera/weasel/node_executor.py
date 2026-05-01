"""Node-subprocess bridge for TS/JS weasel extensions.

Wave 9 closes the loop opened by W3: extensions written in TypeScript or
JavaScript are now *executable*, not just indexed. Each tool declared in
a JS/TS extension's manifest is wrapped in a :class:`NodeExtensionTool`
that, on invocation, spawns ``node <ext-dir>/<entry> --tool <name>
--args <json>`` and parses a single JSON object off stdout.

Design constraints:

* Stdlib only — :mod:`subprocess`, :mod:`shutil`, :mod:`json`. No deps.
* Fail-open: if ``node`` is not on the user's ``PATH`` we still build
  the tool wrappers, but each invocation returns a ``ToolResult`` whose
  ``error`` explains that Node is unavailable. This mirrors the
  loader's broader "one bad extension can't poison the run" stance.
* CommonJS *and* ESM entry points are supported. We never install or
  shell out to a bundler — the ``main`` field in the manifest is taken
  literally and resolved against the extension directory.

Wire protocol (the Node side must implement):

```text
$ node <entry> --tool <tool_name> --args '<json-args>'
{"output": "string", "metadata": {"k": "v"}}      # success
{"error":  "message"}                              # failure
```

Anything that is not a JSON object on stdout is surfaced as an error
with the raw stdout/stderr captured in the result metadata, so authors
can debug from the agent transcript without rerunning by hand.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


# Default per-call timeout (seconds). Tunable per-tool at construction.
_DEFAULT_TIMEOUT_S: float = 30.0


@dataclass
class NodeEntryPoint:
    """Resolved Node entry point for an extension.

    Args:
        entry_path: Absolute path to the JS file the subprocess runs.
        is_module: ``True`` when the package declares ``"type": "module"``
            (ESM). Currently informational — Node 14+ runs both shapes
            via the same ``node <file>`` invocation, so we do not branch
            on this. Recorded so callers can introspect.
    """

    entry_path: Path
    is_module: bool


def detect_node() -> str | None:
    """Return the absolute path to the ``node`` executable, or ``None``.

    Wraps :func:`shutil.which` so callers can branch without importing
    ``shutil`` themselves. Used by the loader to decide whether to wire
    JS/TS tools at all.
    """
    return shutil.which("node")


def _read_package_json(plugin_dir: Path) -> dict[str, Any]:
    """Best-effort read of ``package.json`` from ``plugin_dir``.

    Returns an empty dict on any failure. We never raise: a missing or
    malformed ``package.json`` simply means we fall back to CommonJS
    semantics, which is the Node default.
    """
    candidate = plugin_dir / "package.json"
    if not candidate.is_file():
        return {}
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_esm_package(plugin_dir: Path) -> bool:
    """Heuristic: ``"type": "module"`` in the plugin's ``package.json``."""
    return _read_package_json(plugin_dir).get("type") == "module"


def resolve_node_entry(plugin_dir: Path, manifest: dict[str, Any]) -> NodeEntryPoint | None:
    """Resolve the JS file Node should execute for this extension.

    Resolution priority:

    1. ``manifest["main"]`` if it points at an existing ``.js`` / ``.mjs``
       / ``.cjs`` file.
    2. ``manifest["main"]`` interpreted with extension swapped for
       ``.js`` (covers the common TS-author case where ``main`` points
       at a ``.ts`` source they expect to be transpiled).
    3. ``index.js`` next to the manifest.
    4. ``index.mjs`` next to the manifest.
    5. ``index.cjs`` next to the manifest.

    Returns ``None`` when no JS file is found. We do **not** transpile
    ``.ts`` here — that is the extension author's responsibility (a
    bundler step they ship with the plugin).
    """
    main = manifest.get("main")
    candidates: list[Path] = []
    if isinstance(main, str) and main.strip():
        cleaned = main.strip()
        cleaned = cleaned[2:] if cleaned.startswith("./") else cleaned
        primary = (plugin_dir / cleaned)
        if primary.suffix.lower() in {".js", ".mjs", ".cjs"}:
            candidates.append(primary)
        else:
            # TS author shipped a build artifact next to the source.
            stem = primary.with_suffix("")
            for suffix in (".js", ".mjs", ".cjs"):
                candidates.append(stem.with_suffix(suffix))

    for fallback in ("index.js", "index.mjs", "index.cjs"):
        candidates.append(plugin_dir / fallback)

    seen: set[Path] = set()
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return NodeEntryPoint(
                entry_path=resolved,
                is_module=_is_esm_package(plugin_dir),
            )
    return None


class NodeExtensionTool(BaseTool):
    """Wrap a JS/TS function as a :class:`BaseTool`.

    Each invocation spawns ``node <entry> --tool <name> --args <json>``
    and parses a single JSON object off stdout. Failures are surfaced
    via :class:`~chimera.types.ToolResult`'s ``error`` field; the raw
    stdout/stderr/exit-code are recorded in ``metadata`` so authors can
    debug from the agent transcript.

    Args:
        tool_name: Tool name as the agent sees it.
        description: Human description for the tool schema.
        entry_point: Resolved JS entry point (built by
            :func:`resolve_node_entry`).
        plugin_dir: Extension root, used as ``cwd`` so relative
            ``require``/``import`` paths in the JS resolve correctly.
        node_path: Absolute path to ``node``. ``None`` means Node was
            not found at construction time; the wrapper still builds
            but every call short-circuits to a clear error.
        parameters: Optional JSON Schema for the tool's args. Defaults
            to the permissive ``{"type": "object"}`` so the agent can
            still call the tool when authors omit a schema.
        timeout_s: Per-call timeout in seconds. Authors can override
            via the manifest's ``timeout`` field for slow tools.
    """

    def __init__(
        self,
        *,
        tool_name: str,
        description: str,
        entry_point: NodeEntryPoint | None,
        plugin_dir: Path,
        node_path: str | None,
        parameters: dict[str, Any] | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.name = tool_name
        self.description = description or f"Node-backed tool '{tool_name}'."
        self.parameters = parameters if parameters is not None else {"type": "object"}
        self._entry_point = entry_point
        self._plugin_dir = plugin_dir
        self._node_path = node_path
        self._timeout_s = float(timeout_s)

    # ------------------------------------------------------------------ exec

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Spawn ``node`` and parse a JSON response off stdout."""
        if self._node_path is None:
            return ToolResult(
                output="",
                error=(
                    "Node is not installed or not on PATH; "
                    f"cannot execute extension tool '{self.name}'."
                ),
                metadata={"node_available": False},
            )
        if self._entry_point is None:
            return ToolResult(
                output="",
                error=(
                    f"No JS entry point found for tool '{self.name}' "
                    f"under {self._plugin_dir}."
                ),
                metadata={"plugin_dir": str(self._plugin_dir)},
            )

        # Serialize args defensively. A non-JSON-serializable value here
        # is a programmer error in the calling agent, not the extension.
        try:
            args_json = json.dumps(args)
        except (TypeError, ValueError) as exc:
            return ToolResult(
                output="",
                error=f"Could not serialize args to JSON: {exc}",
                metadata={"args_repr": repr(args)},
            )

        cmd = [
            self._node_path,
            str(self._entry_point.entry_path),
            "--tool",
            self.name,
            "--args",
            args_json,
        ]

        try:
            completed = subprocess.run(  # noqa: S603 — args list, no shell
                cmd,
                cwd=str(self._plugin_dir),
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(
                output="",
                error=(
                    f"Node extension tool '{self.name}' timed out after "
                    f"{self._timeout_s:.1f}s."
                ),
                metadata={
                    "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                    "timeout_s": self._timeout_s,
                },
            )
        except OSError as exc:
            return ToolResult(
                output="",
                error=f"Could not spawn node: {exc}",
                metadata={"cmd": cmd},
            )

        return _parse_node_response(
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            tool_name=self.name,
        )


def _parse_node_response(
    *,
    stdout: str,
    stderr: str,
    exit_code: int,
    tool_name: str,
) -> ToolResult:
    """Parse the single JSON object the Node side is expected to emit.

    Recognised shapes:

    * ``{"output": "...", "metadata": {...}}`` — success.
    * ``{"error": "..."}`` — explicit error from the extension.

    Anything else (non-JSON, list, scalar, missing keys) is surfaced as
    an error. The raw stdout/stderr/exit-code always land in
    ``metadata`` so authors can debug without rerunning by hand.
    """
    debug_meta: dict[str, Any] = {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
    }

    # A non-zero exit with no parseable stdout is just a crash. Surface
    # stderr as the error message — that's where Node prints stack traces.
    stripped = (stdout or "").strip()
    if not stripped:
        if exit_code != 0:
            return ToolResult(
                output="",
                error=(
                    f"Node extension tool '{tool_name}' exited "
                    f"{exit_code} with no stdout."
                ),
                metadata=debug_meta,
            )
        return ToolResult(
            output="",
            error=(
                f"Node extension tool '{tool_name}' produced no output."
            ),
            metadata=debug_meta,
        )

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return ToolResult(
            output="",
            error=(
                f"Node extension tool '{tool_name}' returned non-JSON "
                f"stdout: {exc}"
            ),
            metadata=debug_meta,
        )

    if not isinstance(payload, dict):
        return ToolResult(
            output="",
            error=(
                f"Node extension tool '{tool_name}' returned a "
                f"{type(payload).__name__}, expected an object."
            ),
            metadata=debug_meta,
        )

    if "error" in payload and payload["error"]:
        err_msg = payload["error"]
        return ToolResult(
            output=str(payload.get("output", "")),
            error=str(err_msg),
            metadata={
                **debug_meta,
                **(
                    payload.get("metadata", {})
                    if isinstance(payload.get("metadata"), dict)
                    else {}
                ),
            },
        )

    output_raw = payload.get("output", "")
    output_str = output_raw if isinstance(output_raw, str) else json.dumps(output_raw)
    extra_meta = payload.get("metadata", {})
    metadata: dict[str, Any] = dict(debug_meta)
    if isinstance(extra_meta, dict):
        metadata.update(extra_meta)
    return ToolResult(output=output_str, error=None, metadata=metadata)


def build_node_tools(
    *,
    plugin_dir: Path,
    plugin_name: str,
    manifest: dict[str, Any],
    errors: list[str],
) -> tuple[list[BaseTool], list[str]]:
    """Build :class:`NodeExtensionTool` wrappers for a JS/TS extension.

    Reads the ``tools`` field on the manifest and produces one
    :class:`NodeExtensionTool` per entry. Two manifest shapes are
    accepted:

    1. ``["tool_one", "tool_two"]`` — list of tool names. Description
       and parameters fall back to defaults.
    2. ``[{"name": "tool_one", "description": "...", "parameters": {...},
       "timeout": 60}, ...]`` — full descriptors. Lets authors avoid a
       round-trip to fetch a schema from Node before the tool is usable.

    Returns ``(tools, entry_points)``. Errors that prevent a tool from
    being built are appended onto ``errors`` rather than raised; the
    rest of the manifest still loads.
    """
    raw_tools = manifest.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        return [], []

    node_path = detect_node()
    if node_path is None:
        errors.append(
            "node executable not found on PATH; "
            f"JS/TS tools for '{plugin_name}' will report runtime errors."
        )

    entry_point = resolve_node_entry(plugin_dir, manifest)
    if entry_point is None:
        errors.append(
            f"no JS entry point resolved for '{plugin_name}'; "
            "expected manifest.main to point at a .js/.mjs/.cjs file or "
            "an index.js next to the manifest."
        )

    tools: list[BaseTool] = []
    entry_points: list[str] = []
    seen_names: set[str] = set()

    for raw in raw_tools:
        descriptor = _coerce_tool_descriptor(raw)
        if descriptor is None:
            errors.append(f"skipping malformed JS tool descriptor: {raw!r}")
            continue
        if descriptor["name"] in seen_names:
            continue
        seen_names.add(descriptor["name"])
        tools.append(
            NodeExtensionTool(
                tool_name=descriptor["name"],
                description=descriptor["description"],
                entry_point=entry_point,
                plugin_dir=plugin_dir,
                node_path=node_path,
                parameters=descriptor["parameters"],
                timeout_s=descriptor["timeout_s"],
            )
        )

    if entry_point is not None:
        entry_points.append(str(entry_point.entry_path.relative_to(plugin_dir)))

    return tools, entry_points


def _coerce_tool_descriptor(raw: Any) -> dict[str, Any] | None:
    """Normalize a manifest ``tools`` entry to a uniform descriptor dict.

    Returns ``None`` when the entry cannot be coerced (e.g. an int).
    Default ``parameters`` permissive so unschematized tools still load.
    """
    if isinstance(raw, str) and raw.strip():
        return {
            "name": raw.strip(),
            "description": "",
            "parameters": {"type": "object"},
            "timeout_s": _DEFAULT_TIMEOUT_S,
        }
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    description = raw.get("description")
    parameters = raw.get("parameters")
    timeout_raw = raw.get("timeout", _DEFAULT_TIMEOUT_S)
    try:
        timeout_s = float(timeout_raw)
    except (TypeError, ValueError):
        timeout_s = _DEFAULT_TIMEOUT_S
    return {
        "name": name.strip(),
        "description": str(description) if isinstance(description, str) else "",
        "parameters": parameters if isinstance(parameters, dict) else {"type": "object"},
        "timeout_s": timeout_s,
    }


__all__ = [
    "NodeEntryPoint",
    "NodeExtensionTool",
    "build_node_tools",
    "detect_node",
    "resolve_node_entry",
]
