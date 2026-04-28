"""Per-process :class:`MCPClient` cache for the otter agent's MCP wiring.

Wave 1 wired :func:`chimera.otter.cli._attach_mcp_tools` to construct a fresh
:class:`chimera.mcp.client.MCPClient` (and re-spawn every stdio MCP server
subprocess) on every agent build. The HTTP/ACP factories build a new agent
per session, so a long-running ``chimera otter serve`` would respawn the
same servers dozens of times per day. This module memoizes one
:class:`MCPClient` per ``(server_name, transport_signature)`` for the life
of the process so repeated builds reuse the already-connected client.

Cache invalidation:
    Keying on the *full* normalised spec dict means any config change
    (transport flip, command tweak, new header) yields a new key and a
    new client; the old entry stays parked but is reaped at process exit
    via :func:`atexit.register`.

Concurrency:
    Single-threaded by construction. The otter CLI builds agents on the
    main thread (one-shot ``-p``) or per HTTP request thread (which only
    *consumes* an already-built agent), so a cooperative
    :class:`threading.Lock` is enough; a busy server lane will serialise
    around the connect path which is also what the wave-1 wiring did.
"""
from __future__ import annotations

import atexit
import json
import sys
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.mcp.client import MCPClient

# WHY: a tuple key (name, transport_signature) keeps lookup O(1) and lets a
# config-only rename (same server, new spec) miss the cache cleanly. We
# normalise the spec via :func:`json.dumps(..., sort_keys=True)` so dict
# ordering can never silently invalidate a key.
_MCP_CLIENT_CACHE: dict[tuple[str, str], MCPClient] = {}
_CACHE_LOCK = threading.Lock()
_ATEXIT_REGISTERED = False


def _spec_signature(spec: dict[str, Any]) -> str:
    """Canonicalise *spec* into a stable key fragment.

    The spec dict is what :meth:`chimera.otter.mcp.MCPServerConfig.to_client_spec`
    emits — JSON-serialisable, no callables, no live transports. We sort
    keys recursively so ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` map
    to the same signature.

    Args:
        spec: The MCP server spec dict (transport, command, url, headers, ...).

    Returns:
        A canonical string. Falls back to ``repr(spec)`` if the spec
        contains anything not JSON-serialisable.
    """
    try:
        return json.dumps(spec, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        # Defensive: callers should only feed JSON-clean specs, but a
        # bad plugin could slip something exotic through.
        return repr(sorted(spec.items())) if isinstance(spec, dict) else repr(spec)


def cache_key(server_name: str, spec: dict[str, Any]) -> tuple[str, str]:
    """Build the cache key for a server name + transport spec.

    Exposed so tests (and the cache lookup site) share one definition.
    """
    return (server_name, _spec_signature(spec))


def _ensure_atexit() -> None:
    """Register the teardown hook on first use.

    Registering at import time would attach the hook to every test that
    even imports :mod:`chimera.otter.cli`; deferring until the first
    actual cache insert keeps the test surface clean.
    """
    global _ATEXIT_REGISTERED
    if _ATEXIT_REGISTERED:
        return
    atexit.register(shutdown_cached_clients)
    _ATEXIT_REGISTERED = True


def get_or_create(
    entries: list[tuple[str, dict[str, Any]]],
    *,
    client_factory: Any | None = None,
) -> MCPClient | None:
    """Return a cached :class:`MCPClient` for *entries*, building on miss.

    *entries* is the ordered ``(server_name, spec)`` list the caller wants
    served. We walk it twice:

        1. Resolve the cache: every entry whose ``(name, signature)`` is
           already memoised maps to the same :class:`MCPClient`. If every
           entry hits and they all map to the same client, return it.
        2. On any miss (or split — same names splayed across two cached
           clients), build a fresh client, register all entries, connect,
           and memoise each ``(name, signature) -> client`` pair.

    The "split" case shouldn't happen in practice (otter discovers servers
    once per process root) but the fallback keeps the cache safe under
    surprise inputs from plugin tests.

    Args:
        entries: ``(server_name, spec)`` pairs derived from
            ``MCPServerConfig.to_client_spec()`` for the enabled servers.
        client_factory: Optional factory returning a new :class:`MCPClient`.
            When ``None``, the real :class:`MCPClient` constructor is used.
            Tests inject a fake to track instantiations.

    Returns:
        A connected :class:`MCPClient`, or ``None`` if no entries were
        registered (every ``add_from_spec`` failed or the caller passed
        an empty list).
    """
    if not entries:
        return None

    # Phase 1 — does every entry resolve to the same cached client?
    keys = [cache_key(name, spec) for name, spec in entries]
    with _CACHE_LOCK:
        cached = [_MCP_CLIENT_CACHE.get(k) for k in keys]
        if all(c is not None for c in cached):
            first = cached[0]
            if all(c is first for c in cached):
                return first  # type: ignore[return-value]

    # Phase 2 — build + connect a fresh client.
    if client_factory is None:
        from chimera.mcp.client import MCPClient as _Client
        client_factory = _Client

    client = client_factory()
    registered: list[int] = []  # indices of entries successfully added
    for idx, (name, spec) in enumerate(entries):
        try:
            client.add_from_spec(name, spec)
            registered.append(idx)
        except Exception as exc:  # noqa: BLE001 — keep going on per-server failure
            sys.stderr.write(
                f"[otter] MCP server '{name}' failed to register: {exc}\n"
            )
            sys.stderr.flush()

    if not registered:
        return None

    try:
        client.connect_all()
    except Exception as exc:  # noqa: BLE001 — connection-time failures are non-fatal
        sys.stderr.write(
            f"[otter] MCP connect_all failed; continuing without MCP tools: {exc}\n"
        )
        sys.stderr.flush()
        return None

    # Memoise only the entries that actually registered. We register every
    # *successful* (name, spec) -> client mapping so a later call that
    # asks for a subset still hits.
    with _CACHE_LOCK:
        for idx in registered:
            _MCP_CLIENT_CACHE[keys[idx]] = client
        _ensure_atexit()
    return client


def shutdown_cached_clients() -> None:
    """Disconnect every cached client and clear the cache.

    Idempotent: safe to call manually (tests do) and registered with
    :func:`atexit` so the process never leaks stdio MCP subprocesses.
    Per-client ``disconnect_all`` failures are swallowed since teardown
    is best-effort.
    """
    with _CACHE_LOCK:
        # Deduplicate clients (multiple keys may share one) so we only
        # call disconnect_all once per live MCPClient instance.
        seen: set[int] = set()
        for client in list(_MCP_CLIENT_CACHE.values()):
            cid = id(client)
            if cid in seen:
                continue
            seen.add(cid)
            try:
                client.disconnect_all()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
        _MCP_CLIENT_CACHE.clear()


def _reset_for_tests() -> None:
    """Drop every cached entry without disconnecting.

    Tests that install fake clients use this to clear state between
    cases; production code should call :func:`shutdown_cached_clients`
    instead.
    """
    with _CACHE_LOCK:
        _MCP_CLIENT_CACHE.clear()


__all__ = [
    "cache_key",
    "get_or_create",
    "shutdown_cached_clients",
]
