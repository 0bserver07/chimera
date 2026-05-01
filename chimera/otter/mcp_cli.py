"""Otter ``mcp`` subcommand handlers — list / add / auth.

This module is the *write-side* counterpart to :mod:`chimera.otter.mcp`.
``mcp.py`` only reads ``~/.opencode/config.json`` and the project-level
``.opencode/{config,mcp}.json`` to materialize :class:`MCPServerConfig`
instances for the agent's tool group; it never touches those files.

This module owns the CLI surface for **mutating** those files and for
running an interactive OAuth device flow against an HTTP MCP server:

* :func:`cmd_mcp_list` — print known MCP servers from project + user
  config, marking the source scope and connect transport.
* :func:`cmd_mcp_add` — append a new entry under
  ``<project>/.opencode/config.json`` (or ``~/.opencode/config.json``
  with ``--user``). Always prompts before writing unless the caller
  passes ``yes=True`` (test seam / future ``--yes`` flag).
* :func:`cmd_mcp_auth` — initiate an OAuth device-code flow for an HTTP
  MCP server that ships an ``oauth`` block. Reuses
  :class:`chimera.auth.OAuthDeviceFlow` when the entry's metadata
  carries the required endpoints; otherwise falls back to a manual
  "open this URL, paste the code back" UX so users without an OAuth
  block can still bootstrap a token.

Trademark hygiene mirrors :mod:`chimera.otter.mcp`: we reference
``~/.opencode/config.json`` as a filesystem path, never as a brand
claim. All printed strings stay neutral.

The dispatcher entry point is :func:`dispatch_mcp` — wired into
:mod:`chimera.otter.cli` via ``_SUBCOMMAND_DISPATCH["mcp"]``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from chimera.otter.mcp import MCPServerConfig, load_mcp_servers

__all__ = [
    "cmd_mcp_add",
    "cmd_mcp_auth",
    "cmd_mcp_list",
    "dispatch_mcp",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_home(home: Path | None = None) -> Path:
    """Return the home directory, honoring ``$HOME`` (test seam)."""
    if home is not None:
        return home
    return Path(os.path.expanduser("~"))


def _user_config_path(home: Path | None = None) -> Path:
    """Path to ``~/.opencode/config.json`` for the resolved home."""
    return _resolve_home(home) / ".opencode" / "config.json"


def _project_config_path(project_root: Path) -> Path:
    """Path to ``<project_root>/.opencode/config.json``."""
    return Path(project_root) / ".opencode" / "config.json"


def _read_json_or_empty(path: Path) -> dict[str, Any]:
    """Return the top-level JSON object at *path* or ``{}`` on any failure.

    Symmetric with :func:`chimera.otter.mcp._read_json` but kept local
    so a failure here cannot accidentally re-shape what the loader
    accepts.
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


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write *data* as pretty JSON, ``0o600``, parents created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _confirm(
    prompt: str,
    *,
    yes: bool = False,
    reader: Callable[[str], str] | None = None,
) -> bool:
    """Prompt for a y/N confirmation, returning ``True`` if confirmed.

    ``yes=True`` short-circuits (auto-confirm: tests + future ``--yes``).
    A custom *reader* lets tests inject deterministic input without
    monkeypatching :func:`builtins.input`.
    """
    if yes:
        return True
    read = reader if reader is not None else input
    try:
        answer = read(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


# ---------------------------------------------------------------------------
# `mcp list`
# ---------------------------------------------------------------------------


def cmd_mcp_list(
    project_root: Path,
    *,
    home: Path | None = None,
    out: Any = None,
) -> int:
    """Print known MCP servers across user + project scopes.

    Output is intentionally plain (no rich/colour) so it pipes cleanly
    into ``grep`` / ``awk`` workflows. Each line is::

        <name>  <transport>  <connect>     <source>

    where ``<connect>`` is ``command...`` (stdio) or the URL (http),
    and ``<source>`` is ``user`` / ``project`` / ``user+project`` /
    ``disabled``.

    Args:
        project_root: Project working directory; ``.opencode/`` is
            scanned beneath it.
        home: Optional home override (test seam — symmetric with
            :func:`chimera.otter.mcp.load_mcp_servers`).
        out: File-like sink. Defaults to ``sys.stdout``.

    Returns:
        Exit code (always ``0`` — no servers is a valid state).
    """
    sink = out if out is not None else sys.stdout

    # We need both the merged + per-scope view to label the source.
    user_blob = _read_json_or_empty(_user_config_path(home))
    project_blob = _read_json_or_empty(_project_config_path(project_root))
    project_mcp_blob = _read_json_or_empty(
        Path(project_root) / ".opencode" / "mcp.json"
    )

    user_names = _names_from_blob(user_blob)
    project_names = _names_from_blob(project_blob) | _names_from_blob(
        project_mcp_blob
    )

    cfgs = load_mcp_servers(project_root, home=home)
    if not cfgs:
        sink.write("No MCP servers configured.\n")
        sink.write(
            "Add one with: chimera otter mcp add <name> <command...>\n"
        )
        return 0

    sink.write(f"{'NAME':<20} {'TRANSPORT':<10} {'CONNECT':<40} SOURCE\n")
    for cfg in cfgs:
        connect = (
            " ".join(cfg.command) if cfg.transport == "stdio" else cfg.url
        )
        if len(connect) > 38:
            connect = connect[:35] + "..."
        scope = _scope_label(cfg.name, user_names, project_names, cfg.enabled)
        sink.write(
            f"{cfg.name:<20} {cfg.transport:<10} {connect:<40} {scope}\n"
        )
    return 0


def _names_from_blob(blob: dict[str, Any]) -> set[str]:
    """Extract MCP entry names from a config blob (any recognized shape)."""
    for key in ("mcp", "mcpServers", "servers"):
        block = blob.get(key)
        if isinstance(block, dict):
            return {str(n) for n in block.keys()}
    if blob and all(isinstance(v, dict) for v in blob.values()):
        looks_like_servers = any(
            isinstance(v, dict) and ("type" in v or "command" in v or "url" in v)
            for v in blob.values()
        )
        if looks_like_servers:
            return {str(n) for n in blob.keys()}
    return set()


def _scope_label(
    name: str,
    user_names: set[str],
    project_names: set[str],
    enabled: bool,
) -> str:
    """Compose the source-scope label for the ``list`` table."""
    in_user = name in user_names
    in_proj = name in project_names
    if in_user and in_proj:
        scope = "user+project"
    elif in_proj:
        scope = "project"
    elif in_user:
        scope = "user"
    else:
        scope = "?"
    if not enabled:
        scope = f"{scope} (disabled)"
    return scope


# ---------------------------------------------------------------------------
# `mcp add`
# ---------------------------------------------------------------------------


def cmd_mcp_add(
    name: str,
    command: list[str],
    *,
    project_root: Path,
    user_scope: bool = False,
    home: Path | None = None,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
    yes: bool = False,
    reader: Callable[[str], str] | None = None,
    out: Any = None,
) -> int:
    """Append an MCP server entry to the chosen config file.

    Two transport modes are supported, mirroring :mod:`chimera.otter.mcp`:

    * **stdio** — the default. Pass *command* (the executable + args) and
      optionally *env*. The entry is written with ``"type": "local"``
      so it round-trips cleanly through the loader.
    * **http** — set *url* (and optionally *headers*); *command* must
      be empty. The entry is written with ``"type": "remote"``.

    Args:
        name: Server name. Must be unique within the chosen scope.
        command: Executable + args for stdio. Empty when *url* is set.
        project_root: Project directory (used when *user_scope* is False).
        user_scope: When True, write to ``~/.opencode/config.json``;
            otherwise write to ``<project>/.opencode/config.json``.
        home: Optional home override (test seam).
        url: HTTP MCP endpoint URL. Mutually exclusive with *command*.
        headers: HTTP headers map for the http transport.
        env: Environment overrides for the stdio subprocess.
        yes: Skip the y/N confirmation (tests / future ``--yes`` flag).
        reader: Optional input function for the confirmation prompt.
        out: File-like sink for status messages. Defaults to stdout.

    Returns:
        Exit code: ``0`` on success, ``1`` on user abort, ``2`` on
        invalid arguments (e.g. neither *command* nor *url* set, or
        *name* already present in the chosen file).
    """
    sink = out if out is not None else sys.stdout

    if not name or not name.strip():
        sink.write("error: server name must be non-empty\n")
        return 2
    name = name.strip()

    has_command = bool(command)
    has_url = bool(url and url.strip())
    if has_command and has_url:
        sink.write(
            "error: pass either a command (stdio) or --http URL "
            "(remote), not both\n"
        )
        return 2
    if not has_command and not has_url:
        sink.write(
            "error: missing command. Usage: "
            "chimera otter mcp add <name> <command...>  "
            "(or --http <url> for HTTP)\n"
        )
        return 2

    target = (
        _user_config_path(home) if user_scope else _project_config_path(project_root)
    )
    blob = _read_json_or_empty(target)

    mcp_block = blob.get("mcp")
    if not isinstance(mcp_block, dict):
        mcp_block = {}

    if name in mcp_block:
        sink.write(
            f"error: an MCP server named {name!r} already exists in "
            f"{target}\n"
        )
        sink.write(
            "(remove it manually before re-adding, or pick a new name)\n"
        )
        return 2

    if has_url:
        entry: dict[str, Any] = {
            "type": "remote",
            "url": (url or "").strip(),
        }
        if headers:
            entry["headers"] = dict(headers)
    else:
        entry = {
            "type": "local",
            "command": list(command),
        }
        if env:
            entry["environment"] = dict(env)

    scope_label = "user" if user_scope else "project"
    sink.write(f"About to add MCP server {name!r} ({scope_label} scope):\n")
    sink.write(f"  file:  {target}\n")
    sink.write(f"  entry: {json.dumps(entry)}\n")
    if not _confirm("Write this entry? [y/N]: ", yes=yes, reader=reader):
        sink.write("aborted (no changes written).\n")
        return 1

    mcp_block[name] = entry
    blob["mcp"] = mcp_block
    _write_json(target, blob)
    sink.write(f"wrote {target}\n")
    return 0


# ---------------------------------------------------------------------------
# `mcp auth`
# ---------------------------------------------------------------------------


def cmd_mcp_auth(
    name: str,
    *,
    project_root: Path,
    home: Path | None = None,
    flow_factory: Callable[..., Any] | None = None,
    credential_store: Any | None = None,
    reader: Callable[[str], str] | None = None,
    out: Any = None,
) -> int:
    """Run an OAuth device flow for an HTTP MCP server.

    Resolution order:

    1. Look up *name* via :func:`load_mcp_servers` to get its merged
       config (user + project, project wins).
    2. Reject the call if the entry isn't HTTP — stdio MCP servers
       authenticate via per-process env vars, not OAuth.
    3. If the entry has an ``oauth`` block with ``client_id`` plus
       device + token endpoints, hand it to
       :class:`chimera.auth.OAuthDeviceFlow` and persist the resulting
       :class:`chimera.auth.Credential` into :class:`CredentialStore`
       under ``mcp:<name>``.
    4. Otherwise, fall back to a manual "open this URL, paste the code
       back" UX: prompt for the bearer token / code, store it under
       ``~/.chimera/credentials.json``.

    Args:
        name: Server name.
        project_root: Project directory.
        home: Optional home override.
        flow_factory: Optional factory returning an
            :class:`AuthProvider` instance. Tests inject a fake to skip
            the real device flow.
        credential_store: Optional pre-built :class:`CredentialStore`.
            Defaults to one rooted under ``$HOME/.chimera/`` so the
            test seam survives.
        reader: Optional input function for manual fallback prompts.
        out: File-like sink. Defaults to stdout.

    Returns:
        ``0`` on success, ``1`` on user abort or auth failure,
        ``2`` on invalid request (unknown name, non-http transport).
    """
    sink = out if out is not None else sys.stdout
    read = reader if reader is not None else input

    cfgs = load_mcp_servers(project_root, home=home)
    target: MCPServerConfig | None = next(
        (c for c in cfgs if c.name == name), None,
    )
    if target is None:
        sink.write(f"error: no MCP server named {name!r} is configured.\n")
        sink.write("Run 'chimera otter mcp list' to see what's available.\n")
        return 2
    if target.transport != "http":
        sink.write(
            f"error: MCP server {name!r} uses transport "
            f"{target.transport!r}; OAuth only applies to http transports.\n"
        )
        return 2

    store = credential_store if credential_store is not None else _default_store(home)
    provider_id = f"mcp:{name}"

    oauth_block = target.oauth or {}
    client_id = oauth_block.get("client_id") or ""
    device_url = (
        oauth_block.get("device_authorization_endpoint")
        or oauth_block.get("device_auth_url")
        or ""
    )
    token_url = (
        oauth_block.get("token_endpoint") or oauth_block.get("token_url") or ""
    )

    if client_id and device_url and token_url:
        return _run_device_flow(
            name=name,
            provider_id=provider_id,
            client_id=str(client_id),
            device_url=str(device_url),
            token_url=str(token_url),
            store=store,
            sink=sink,
            flow_factory=flow_factory,
        )

    # Fallback: manual paste-token UX.
    return _run_manual_token_paste(
        name=name,
        provider_id=provider_id,
        url=target.url,
        store=store,
        sink=sink,
        read=read,
    )


def _default_store(home: Path | None) -> Any:
    """Build a :class:`CredentialStore` rooted under the resolved home."""
    from chimera.auth.store import CredentialStore

    store_path = _resolve_home(home) / ".chimera" / "credentials.json"
    return CredentialStore(str(store_path))


def _run_device_flow(
    *,
    name: str,
    provider_id: str,
    client_id: str,
    device_url: str,
    token_url: str,
    store: Any,
    sink: Any,
    flow_factory: Callable[..., Any] | None,
) -> int:
    """Execute :class:`OAuthDeviceFlow`, persist the credential."""
    sink.write(
        f"Starting OAuth device flow for MCP server {name!r}.\n"
        f"  device endpoint: {device_url}\n"
        f"  token endpoint:  {token_url}\n"
    )
    if flow_factory is None:
        from chimera.auth.oauth import OAuthDeviceFlow

        flow = OAuthDeviceFlow(
            provider_name=provider_id,
            client_id=client_id,
            device_auth_url=device_url,
            token_url=token_url,
        )
    else:
        flow = flow_factory(
            provider_name=provider_id,
            client_id=client_id,
            device_auth_url=device_url,
            token_url=token_url,
        )

    try:
        credential = flow.authenticate()
    except Exception as exc:  # noqa: BLE001 — network/oauth surface is broad
        sink.write(f"error: OAuth flow failed: {exc}\n")
        return 1

    try:
        store.save(credential)
    except Exception as exc:  # noqa: BLE001 — IO failures should be surfaced, not raised
        sink.write(f"error: token persisted-write failed: {exc}\n")
        return 1
    sink.write(f"saved credential for {provider_id} to credential store.\n")
    return 0


def _run_manual_token_paste(
    *,
    name: str,
    provider_id: str,
    url: str,
    store: Any,
    sink: Any,
    read: Callable[[str], str],
) -> int:
    """Manual fallback: prompt the user to open the URL + paste a token."""
    from chimera.auth.base import Credential

    sink.write(
        f"No 'oauth' block on MCP server {name!r}.\n"
        "Falling back to manual token paste.\n"
        f"  1. Open this URL in your browser: {url}\n"
        "  2. Authorize the application.\n"
        "  3. Copy the access token / API key.\n"
    )
    try:
        token = read("Paste token (empty to abort): ")
    except (EOFError, KeyboardInterrupt):
        sink.write("aborted (no token entered).\n")
        return 1
    token = (token or "").strip()
    if not token:
        sink.write("aborted (no token entered).\n")
        return 1

    credential = Credential(provider=provider_id, token=token)
    try:
        store.save(credential)
    except Exception as exc:  # noqa: BLE001 — surface IO errors clearly
        sink.write(f"error: token persisted-write failed: {exc}\n")
        return 1
    sink.write(f"saved credential for {provider_id} to credential store.\n")
    # Touch ``time`` so the import stays meaningful even when the device
    # flow path is unused; lets ``mcp auth`` smoke tests run without
    # pulling in the full device-flow stack.
    _ = time.time
    return 0


# ---------------------------------------------------------------------------
# Dispatcher (called from ``chimera.otter.cli``)
# ---------------------------------------------------------------------------


def dispatch_mcp(args: argparse.Namespace) -> int:
    """Route ``chimera otter mcp <action> ...`` to the right handler.

    Reads the same positional layout the otter parser already provides:

    * ``args.sub_action`` — ``"list"`` / ``"add"`` / ``"auth"``.
    * ``args.sub_target`` — server name (for ``add`` / ``auth``).
    * ``args.mcp_extra`` — trailing positionals (the stdio command for
      ``add``); empty list when not provided.
    * ``args.agents_user`` — bool, written by ``--user`` (the flag is
      shared with ``agents create`` so both subcommands respect a
      single source-of-truth dest).
    * ``args.mcp_http`` — optional URL, written by ``--http``.
    * ``args.mcp_header`` — optional list of ``KEY=VALUE`` strings.
    * ``args.mcp_env`` — optional list of ``KEY=VALUE`` strings.
    * ``args.mcp_yes`` — bool, skip confirmation (``--yes``).

    Args:
        args: Parsed namespace from the otter parser.

    Returns:
        Process exit code.
    """
    action = getattr(args, "sub_action", None) or "list"
    cwd = getattr(args, "cwd", None) or os.getcwd()
    project_root = Path(cwd)

    if action == "list":
        return cmd_mcp_list(project_root)

    if action == "add":
        name = getattr(args, "sub_target", None) or ""
        extra = list(getattr(args, "mcp_extra", []) or [])
        headers = _parse_kv_list(getattr(args, "mcp_header", None))
        env = _parse_kv_list(getattr(args, "mcp_env", None))
        return cmd_mcp_add(
            name,
            extra,
            project_root=project_root,
            user_scope=bool(getattr(args, "agents_user", False)),
            url=getattr(args, "mcp_http", None),
            headers=headers,
            env=env,
            yes=bool(getattr(args, "mcp_yes", False)),
        )

    if action == "auth":
        name = getattr(args, "sub_target", None) or ""
        if not name.strip():
            sys.stdout.write(
                "error: missing server name. Usage: "
                "chimera otter mcp auth <name>\n"
            )
            return 2
        return cmd_mcp_auth(name, project_root=project_root)

    sys.stdout.write(
        f"error: unknown 'mcp' action: {action!r} "
        "(supported: list, add, auth)\n"
    )
    return 2


def _parse_kv_list(raw: list[str] | None) -> dict[str, str]:
    """Parse a list of ``KEY=VALUE`` strings into a dict.

    Malformed entries (no ``=``, empty key) are skipped silently —
    the CLI only forwards what argparse already validated.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = value
    return out
