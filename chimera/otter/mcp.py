"""Otter MCP server discovery.

Mirrors the mink ``settings.json`` ingest pattern, but for the open-source
coding agent ecosystem: reads ``~/.opencode/config.json`` (user scope) and
the project-level ``.opencode/mcp.json`` (or the ``mcp`` block of
``.opencode/config.json``), then emits a normalized list of
:class:`MCPServerConfig` entries that downstream wiring can hand to
:class:`chimera.mcp.client.MCPClient`.

Supported transports
--------------------
* **stdio** -- spawn a subprocess and speak JSON-RPC over its stdin/stdout.
  Upstream calls this ``"local"`` and carries a ``command`` array.
* **http** -- talk JSON-RPC over an HTTP endpoint. Upstream calls this
  ``"remote"`` and carries a ``url`` plus optional ``headers``.

The schema follows the upstream config (see
``packages/opencode/src/config/config.ts`` -> ``McpLocal`` / ``McpRemote``):

.. code-block:: json

    {
      "mcp": {
        "fs":       {"type": "local",  "command": ["fs-server"], "environment": {"X": "1"}},
        "weather":  {"type": "remote", "url": "https://example/mcp", "headers": {"Authorization": "Bearer ..."}}
      }
    }

A standalone ``.opencode/mcp.json`` file is also accepted; it may use either
the same top-level ``{"mcp": {...}}`` envelope or a bare ``{<name>: {...}}``
map. Project entries override user entries on name conflict.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "MCPServerConfig",
    "load_mcp_servers",
]


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """One MCP server entry, normalized across stdio + http transports.

    Attributes:
        name: Unique server name (the merge key in upstream configs).
        transport: ``"stdio"`` or ``"http"``.
        command: Executable + args for stdio transport (empty for http).
        env: Subprocess environment overrides for stdio (empty for http).
        url: Endpoint URL for http transport (empty string for stdio).
        headers: HTTP headers for http transport (empty for stdio).
        enabled: ``False`` if the upstream config disabled this entry.
        timeout_ms: Per-request timeout hint, if the source set one.
        oauth: Optional OAuth block for http transport. The dict is passed
            through verbatim to :func:`chimera.mcp.oauth.oauth_config_from_dict`
            via ``MCPClient.add_from_spec``; typical keys are ``client_id``,
            ``auth_server_metadata_url`` / ``authorization_endpoint`` /
            ``token_endpoint``, ``redirect_uri``, and ``scopes``.
    """

    name: str
    transport: str
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout_ms: int | None = None
    oauth: dict[str, Any] | None = None

    def to_client_spec(self) -> dict[str, Any]:
        """Convert this config to the dict shape ``MCPClient.add_from_spec`` accepts."""
        if self.transport == "stdio":
            cmd, *args = self.command if self.command else [""]
            spec: dict[str, Any] = {
                "transport": "stdio",
                "command": cmd,
                "args": args,
            }
            if self.env:
                spec["env"] = dict(self.env)
            return spec
        # http transport
        spec_http: dict[str, Any] = {
            "transport": "http",
            "url": self.url,
        }
        if self.headers:
            spec_http["headers"] = dict(self.headers)
        if self.oauth:
            spec_http["oauth"] = dict(self.oauth)
        return spec_http


# ---------------------------------------------------------------------------
# JSON loaders (tolerant: missing/bad files yield empty maps, never raise)
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    """Return the top-level JSON object at *path* or ``{}`` on any failure.

    Failures we swallow:
        * file does not exist
        * file is unreadable (permissions, IO error)
        * file is malformed JSON
        * file's top-level value is not an object
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_mcp_block(blob: dict[str, Any]) -> dict[str, Any]:
    """Pull the per-server map out of a config blob.

    Recognized shapes (in priority order):

    1. ``{"mcp": {<name>: {...}}}``       -- upstream user-config style
    2. ``{"mcpServers": {<name>: {...}}}`` -- common ecosystem alias
    3. ``{"servers": {<name>: {...}}}``    -- legacy chimera shape
    4. ``{<name>: {...}}``                 -- bare per-server map (mcp.json)
    """
    for key in ("mcp", "mcpServers", "servers"):
        block = blob.get(key)
        if isinstance(block, dict):
            return dict(block)
    # Bare map: every value must look like a server (has type/command/url/enabled)
    if blob and all(isinstance(v, dict) for v in blob.values()):
        looks_like_servers = any(
            isinstance(v, dict) and ("type" in v or "command" in v or "url" in v)
            for v in blob.values()
        )
        if looks_like_servers:
            return dict(blob)
    return {}


# ---------------------------------------------------------------------------
# Single-entry normalization
# ---------------------------------------------------------------------------


def _coerce_command(raw: Any) -> list[str]:
    """Accept ``["bin", "arg"]`` (upstream) or ``"bin arg"`` (legacy shell-style)."""
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str) and raw.strip():
        # Best-effort split; whitespace-only edges trimmed. We do NOT shlex
        # because upstream's canonical form is the array; the string form is
        # only here for pre-existing chimera mcp.json files.
        return raw.split()
    return []


def _coerce_str_map(raw: Any) -> dict[str, str]:
    """Coerce a JSON object into ``dict[str, str]``; non-dict input -> ``{}``."""
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _entry_to_config(name: str, entry: dict[str, Any]) -> MCPServerConfig | None:
    """Normalize one server entry into :class:`MCPServerConfig`.

    Returns ``None`` if the entry is unrecognizable (e.g. enabled-only
    pseudo-entry, or no command/url at all).
    """
    if not isinstance(entry, dict):
        return None

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True

    timeout_raw = entry.get("timeout")
    timeout_ms = int(timeout_raw) if isinstance(timeout_raw, (int, float)) else None

    # Decide transport. Priority:
    #   1. explicit "type" -- upstream uses "local"/"remote"
    #   2. explicit "transport" -- chimera/.mcp.json idiom
    #   3. inferred from presence of command vs url
    transport_raw = entry.get("type") or entry.get("transport") or ""
    transport_raw = str(transport_raw).strip().lower()
    if transport_raw in ("local", "stdio"):
        transport = "stdio"
    elif transport_raw in ("remote", "http", "https"):
        transport = "http"
    elif "command" in entry:
        transport = "stdio"
    elif "url" in entry:
        transport = "http"
    else:
        # No transport hint and no command/url. This may be a bare
        # ``{"enabled": false}`` toggle; upstream allows it. Skip it -- we
        # have nothing actionable to register.
        return None

    if transport == "stdio":
        command = _coerce_command(entry.get("command"))
        if not command:
            return None
        # Upstream key is "environment"; chimera/.mcp.json uses "env".
        env_raw = entry.get("environment")
        if env_raw is None:
            env_raw = entry.get("env")
        return MCPServerConfig(
            name=name,
            transport="stdio",
            command=command,
            env=_coerce_str_map(env_raw),
            enabled=enabled,
            timeout_ms=timeout_ms,
        )

    # http
    url = entry.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    oauth_raw = entry.get("oauth")
    oauth = dict(oauth_raw) if isinstance(oauth_raw, dict) else None
    return MCPServerConfig(
        name=name,
        transport="http",
        url=url.strip(),
        headers=_coerce_str_map(entry.get("headers")),
        enabled=enabled,
        timeout_ms=timeout_ms,
        oauth=oauth,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_mcp_servers(
    project_root: Path,
    *,
    home: Path | None = None,
) -> list[MCPServerConfig]:
    """Load MCP server configs from user + project scopes and merge them.

    Args:
        project_root: Project working directory. The loader looks at
            ``<project_root>/.opencode/mcp.json`` and, as a fallback, the
            ``mcp`` block of ``<project_root>/.opencode/config.json``.
        home: Override for the user-home directory (test seam). Defaults to
            ``Path(os.path.expanduser("~"))`` so a monkeypatched ``HOME``
            env var transparently re-roots the user-scope lookup.

    Returns:
        A name-sorted list of :class:`MCPServerConfig` instances. Entries
        with ``enabled=False`` are still returned (callers decide whether
        to skip them); malformed or unrecognizable entries are dropped.
        If a server name appears in both scopes, the project entry wins.
    """
    home_path = home if home is not None else Path(os.path.expanduser("~"))
    project_path = Path(project_root)

    # ----- user scope: ~/.opencode/config.json -----
    user_blob = _read_json(home_path / ".opencode" / "config.json")
    user_block = _extract_mcp_block(user_blob)

    # ----- project scope: .opencode/mcp.json (preferred), then config.json's mcp -----
    project_block: dict[str, Any] = {}
    project_mcp_file = project_path / ".opencode" / "mcp.json"
    if project_mcp_file.exists():
        project_block = _extract_mcp_block(_read_json(project_mcp_file))
    if not project_block:
        project_cfg_file = project_path / ".opencode" / "config.json"
        if project_cfg_file.exists():
            project_block = _extract_mcp_block(_read_json(project_cfg_file))

    # ----- merge: project overrides user on name conflict -----
    merged: dict[str, dict[str, Any]] = {}
    for name, entry in user_block.items():
        if isinstance(entry, dict):
            merged[name] = entry
    for name, entry in project_block.items():
        if isinstance(entry, dict):
            merged[name] = entry

    out: list[MCPServerConfig] = []
    for name in sorted(merged):
        cfg = _entry_to_config(name, merged[name])
        if cfg is not None:
            out.append(cfg)
    return out
