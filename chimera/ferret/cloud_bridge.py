"""Ferret cloud bridge — drive a local ferret session from a remote UI.

This module mirrors the cloud-native posture of the upstream IDE-first
OpenAI-flagship coding agent: a local agent process, a remote web UI, and
a long-running HTTPS connection that round-trips user prompts and agent
events between the two.

Trademark hygiene: this module never names the upstream brand and never
hardcodes its real cloud endpoint. The default :data:`DEFAULT_REMOTE_URL`
points at a placeholder ``.invalid`` domain — operators must opt in to a
real remote via ``--remote-url`` or :class:`BridgeConfig.remote_url`.

API surface
-----------

================================================== ====== ===========================
Path (relative to ``remote_url``)                  Method Purpose
================================================== ====== ===========================
``/bridge/handshake``                              POST   Register the local agent.
                                                          Body: ``{"client": "...",
                                                          "version": "..."}``.
                                                          Returns
                                                          ``{"bridge_id": "..."}``.
``/bridge/<bridge_id>/poll``                       GET    Long-poll for the next
                                                          inbound message from the
                                                          remote UI. Returns
                                                          ``{"messages": [...]}``.
``/bridge/<bridge_id>/event``                      POST   Forward an agent event
                                                          back to the remote UI.
                                                          Body: ``{"type": "...",
                                                          "data": {...}}``.
================================================== ====== ===========================

Auth
----

Every request carries ``Authorization: Bearer <token>`` where ``<token>``
comes from ``--bridge-token`` or ``$FERRET_BRIDGE_TOKEN``. A 401 surfaces
as :class:`BridgeAuthError`.

Implementation notes
--------------------

* **Stdlib only.** ``urllib.request`` for outbound HTTP, ``threading``
  for the inbound poll loop. The test suite uses
  :class:`http.server.ThreadingHTTPServer` to stand up a minimal remote.
* **Late-binding agent.** :class:`CloudBridge` accepts an
  ``inbound_handler`` callable so callers can wire the bridge to any
  local target — ferret's REPL, a raw :class:`Agent`, or a test mock —
  without taking a hard dependency on FF1's CLI module shape.
* **Reconnect.** The poll loop tolerates transient network errors with
  exponential backoff; it only abandons the bridge on auth failure or
  on an explicit :meth:`CloudBridge.stop`.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "BridgeAuthError",
    "BridgeError",
    "BridgeConfig",
    "CloudBridge",
    "DEFAULT_REMOTE_URL",
    "DEFAULT_USER_AGENT",
    "ENV_TOKEN",
    "build_bridge_from_args",
    "run_bridge",
]


#: Default placeholder remote URL. ``.invalid`` is reserved by RFC 2606,
#: so this value can never resolve — a deliberate trip-wire that forces
#: every operator to opt in to a real remote via ``--remote-url`` or the
#: :class:`BridgeConfig` constructor.
DEFAULT_REMOTE_URL = "https://bridge.example.invalid"

#: Environment variable consulted for the bearer token when
#: ``--bridge-token`` is not supplied on the CLI.
ENV_TOKEN = "FERRET_BRIDGE_TOKEN"

#: ``User-Agent`` header attached to every outbound request. Avoids
#: naming the upstream brand on the wire.
DEFAULT_USER_AGENT = "chimera-ferret-bridge/0.1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BridgeError(RuntimeError):
    """Base class for cloud-bridge runtime errors."""


class BridgeAuthError(BridgeError):
    """Raised when the remote rejects our bearer token (HTTP 401/403)."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class BridgeConfig:
    """User-facing configuration for :class:`CloudBridge`.

    Attributes:
        remote_url: HTTPS base URL of the remote bridge service. Trailing
            slashes are stripped so callers can pass either form.
        token: Shared-secret bearer token. ``None`` falls back to
            ``$FERRET_BRIDGE_TOKEN`` at :meth:`resolve_token` time.
        client_id: Free-form identifier surfaced to the remote during
            handshake. Defaults to ``"ferret"``.
        version: Optional client version string echoed in the handshake.
        poll_interval: Floor on how often the inbound poll loop wakes
            even when the remote returns immediately. Stops a chatty
            remote from spinning the local CPU.
        request_timeout: Per-request HTTP timeout in seconds. Long-poll
            calls inherit this; the test suite injects a smaller value.
        max_backoff: Cap on exponential backoff after transient errors.
        user_agent: ``User-Agent`` header. Defaults to
            :data:`DEFAULT_USER_AGENT`.
    """

    remote_url: str = DEFAULT_REMOTE_URL
    token: str | None = None
    client_id: str = "ferret"
    version: str = "0.1"
    poll_interval: float = 1.0
    request_timeout: float = 30.0
    max_backoff: float = 30.0
    user_agent: str = DEFAULT_USER_AGENT

    def resolve_token(self, env: dict[str, str] | None = None) -> str:
        """Return the bearer token, consulting ``$FERRET_BRIDGE_TOKEN``.

        Args:
            env: Optional override mapping (mostly for tests). Defaults
                to ``os.environ``.

        Returns:
            The resolved bearer token.

        Raises:
            BridgeAuthError: When neither :attr:`token` nor the env var
                supplies a value.
        """
        if self.token is not None and self.token != "":
            return self.token
        source = env if env is not None else os.environ
        envtok = source.get(ENV_TOKEN)
        if envtok:
            return envtok
        raise BridgeAuthError(
            f"no bridge token provided (set --bridge-token or ${ENV_TOKEN})"
        )

    def normalised_remote(self) -> str:
        """Return :attr:`remote_url` with trailing slashes stripped.

        Centralised so :meth:`CloudBridge._url` can join paths cleanly.
        """
        return self.remote_url.rstrip("/")


# ---------------------------------------------------------------------------
# Inbound message + handler protocol
# ---------------------------------------------------------------------------


@dataclass
class InboundMessage:
    """A single message pulled from the remote UI.

    Attributes:
        message_id: Opaque id minted by the remote. Echoed back in
            outbound events so the UI can correlate.
        text: User prompt text. Empty string means "remote sent a no-op
            (typically a heartbeat)" — the handler may skip these.
        kind: Free-form type tag (``"prompt"`` / ``"cancel"`` / ...).
        raw: Original JSON dict so callers needing fields the dataclass
            doesn't model can reach through.
    """

    message_id: str
    text: str = ""
    kind: str = "prompt"
    raw: dict[str, Any] = field(default_factory=dict)


#: Signature for the local-side message handler. Receives the parsed
#: inbound message and returns ``None`` (the handler is expected to push
#: agent events back via :meth:`CloudBridge.send_event`). Synchronous on
#: purpose: the bridge already runs on a dedicated thread.
InboundHandler = Callable[[InboundMessage], None]


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------


class CloudBridge:
    """Long-poll bridge between a remote UI and a local ferret session.

    The bridge owns three responsibilities:

    1. **Handshake.** :meth:`connect` posts to ``/bridge/handshake`` and
       caches the returned ``bridge_id``.
    2. **Inbound poll loop.** A daemon thread repeatedly GETs
       ``/bridge/<id>/poll``; each returned message is dispatched to
       :attr:`inbound_handler`.
    3. **Outbound event push.** :meth:`send_event` POSTs to
       ``/bridge/<id>/event`` so agent progress reaches the UI.

    Args:
        config: Connection settings.
        inbound_handler: Callable invoked once per remote-side message.
        opener: Optional :class:`urllib.request.OpenerDirector`. The
            constructor builds a default opener; tests inject a custom
            one to swap in a fake transport.

    Attributes:
        bridge_id: Server-assigned id, populated after :meth:`connect`.
        running: Whether the poll loop is active.

    Raises:
        BridgeAuthError: Surfaced from :meth:`connect` and the poll loop
            when the remote returns 401/403.
        BridgeError: Surfaced for any other transport-level failure.
    """

    def __init__(
        self,
        config: BridgeConfig,
        inbound_handler: InboundHandler,
        *,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self._config = config
        self._handler = inbound_handler
        self._opener = opener or urllib.request.build_opener()
        self._token: str | None = None
        self.bridge_id: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Records every transient error so callers / tests can introspect.
        self._error_log: list[str] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> str:
        """Run the handshake against the remote and cache the bridge id.

        Returns:
            The server-assigned bridge id.

        Raises:
            BridgeAuthError: 401/403 from the remote.
            BridgeError: Any other transport / parse failure.
        """
        # Resolve the token eagerly so a missing env var fails fast
        # before we touch the network.
        self._token = self._config.resolve_token()
        body = {
            "client": self._config.client_id,
            "version": self._config.version,
        }
        status, payload = self._do_request(
            "POST", "/bridge/handshake", body=body
        )
        if status != 200:
            raise BridgeError(
                f"handshake failed: status={status} body={payload!r}"
            )
        bridge_id = payload.get("bridge_id") if isinstance(payload, dict) else None
        if not isinstance(bridge_id, str) or not bridge_id:
            raise BridgeError(
                f"handshake response missing bridge_id: {payload!r}"
            )
        self.bridge_id = bridge_id
        return bridge_id

    def start(self) -> None:
        """Launch the inbound poll thread.

        Idempotent: a running bridge is left alone.

        Raises:
            BridgeError: When :meth:`connect` has not run yet.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        if self.bridge_id is None:
            raise BridgeError("connect() must run before start()")
        self._stop.clear()
        thread = threading.Thread(
            target=self._poll_loop, name="ferret-bridge-poll", daemon=True
        )
        thread.start()
        self._thread = thread

    def stop(self, *, timeout: float = 2.0) -> None:
        """Signal the poll thread to exit and join it.

        Idempotent: stopping an already-stopped bridge is a no-op.

        Args:
            timeout: Seconds to wait for the thread to drain.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        """Whether the poll thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def errors(self) -> list[str]:
        """Snapshot of transient errors seen by the poll loop."""
        return list(self._error_log)

    # ------------------------------------------------------------------
    # Outbound event push
    # ------------------------------------------------------------------

    def send_event(
        self,
        event_type: str,
        data: Any,
        *,
        message_id: str | None = None,
    ) -> None:
        """Forward an agent event to the remote UI.

        Args:
            event_type: Free-form tag mirroring the otter SSE ``event``
                field (e.g. ``"loop_event"``, ``"result"``).
            data: JSON-serializable payload.
            message_id: Optional correlation id from the originating
                inbound message.

        Raises:
            BridgeError: When the bridge has not connected yet, or when
                the remote returns a non-2xx status.
            BridgeAuthError: 401/403 from the remote.
        """
        if self.bridge_id is None:
            raise BridgeError("send_event() called before connect()")
        body: dict[str, Any] = {"type": event_type, "data": data}
        if message_id is not None:
            body["message_id"] = message_id
        path = f"/bridge/{self.bridge_id}/event"
        status, payload = self._do_request("POST", path, body=body)
        if status >= 300:
            raise BridgeError(
                f"send_event failed: status={status} body={payload!r}"
            )

    # ------------------------------------------------------------------
    # Inbound poll loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Run until :meth:`stop` is called or auth fails fatally.

        Uses exponential backoff capped at :attr:`BridgeConfig.max_backoff`
        so a flapping remote does not melt the local CPU. Auth failures
        are fatal — we record the error and exit so the caller can
        surface a helpful message.
        """
        backoff = 0.0
        while not self._stop.is_set():
            try:
                messages = self._poll_once()
            except BridgeAuthError as exc:
                self._error_log.append(f"auth: {exc}")
                return
            except BridgeError as exc:
                # Transient — record, back off, retry.
                self._error_log.append(str(exc))
                backoff = min(
                    self._config.max_backoff,
                    max(self._config.poll_interval, backoff * 2 or 1.0),
                )
                if self._stop.wait(backoff):
                    return
                continue
            backoff = 0.0
            for msg in messages:
                if self._stop.is_set():
                    return
                try:
                    self._handler(msg)
                except Exception as exc:  # noqa: BLE001 - never crash the loop
                    self._error_log.append(f"handler: {exc}")
            # Floor the loop so a remote that returns an empty list
            # immediately doesn't spin us at 100% CPU.
            if self._stop.wait(self._config.poll_interval):
                return

    def _poll_once(self) -> list[InboundMessage]:
        """Issue a single GET against the remote and parse the response.

        Returns:
            A list of :class:`InboundMessage`. Empty on a quiet poll.

        Raises:
            BridgeAuthError: 401/403 from the remote.
            BridgeError: Any other transport / parse failure.
        """
        if self.bridge_id is None:
            raise BridgeError("_poll_once() called before connect()")
        path = f"/bridge/{self.bridge_id}/poll"
        status, payload = self._do_request("GET", path)
        if status != 200:
            raise BridgeError(
                f"poll failed: status={status} body={payload!r}"
            )
        if not isinstance(payload, dict):
            raise BridgeError(f"poll response not an object: {payload!r}")
        raw_messages = payload.get("messages", [])
        if not isinstance(raw_messages, list):
            raise BridgeError(
                f"poll response.messages not a list: {raw_messages!r}"
            )
        result: list[InboundMessage] = []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            message_id = str(raw.get("message_id") or raw.get("id") or "")
            if not message_id:
                continue
            result.append(
                InboundMessage(
                    message_id=message_id,
                    text=str(raw.get("text") or ""),
                    kind=str(raw.get("kind") or raw.get("type") or "prompt"),
                    raw=raw,
                )
            )
        return result

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Join *path* onto the configured remote URL.

        ``path`` must start with ``/``.
        """
        if not path.startswith("/"):
            path = "/" + path
        return self._config.normalised_remote() + path

    def _do_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Issue a single HTTP request and return ``(status, parsed_body)``.

        Centralises auth-header injection, JSON encoding, and 401/403
        translation. Non-JSON responses surface their raw text in a
        ``{"_raw": "..."}`` wrapper so the caller can still log them.

        Args:
            method: HTTP verb.
            path: Path relative to :attr:`BridgeConfig.remote_url`.
            body: Optional JSON-serializable request body.

        Returns:
            ``(status_code, parsed_body)``. ``parsed_body`` is the JSON
            payload when the response is parseable, otherwise a
            ``{"_raw": ...}`` wrapper.

        Raises:
            BridgeAuthError: On HTTP 401 / 403.
            BridgeError: On any URLError / OSError.
        """
        if self._token is None:
            # Lazily resolve so callers can flip env vars between
            # connect() and the first poll without surprises.
            self._token = self._config.resolve_token()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self._url(path), data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("User-Agent", self._config.user_agent)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            resp = self._opener.open(req, timeout=self._config.request_timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.code in (401, 403):
                raise BridgeAuthError(
                    f"remote rejected token: status={exc.code}"
                ) from exc
            try:
                return exc.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return exc.code, {"_raw": raw.decode("utf-8", "replace")}
        except (urllib.error.URLError, OSError) as exc:
            raise BridgeError(f"transport error: {exc}") from exc
        raw = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
        if not raw:
            return status, {}
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, {"_raw": raw.decode("utf-8", "replace")}


# ---------------------------------------------------------------------------
# CLI helpers (late-bound — FF1 will wire these up in ferret/cli.py)
# ---------------------------------------------------------------------------


def build_bridge_from_args(
    args: Any,
    inbound_handler: InboundHandler,
    *,
    opener: urllib.request.OpenerDirector | None = None,
) -> CloudBridge:
    """Construct a :class:`CloudBridge` from an argparse-style namespace.

    FF1's ``ferret/cli.py`` registers the ``bridge`` subcommand and
    forwards its parsed args here. Doing the wiring through this helper
    means FF5 doesn't take a hard dependency on FF1's parser shape.

    The function reads the following attributes (all optional):

    * ``remote_url`` — falls back to :data:`DEFAULT_REMOTE_URL`.
    * ``bridge_token`` — falls back to ``$FERRET_BRIDGE_TOKEN``.
    * ``client_id`` — defaults to ``"ferret"``.
    * ``poll_interval`` — defaults to 1.0 second.
    * ``request_timeout`` — defaults to 30 seconds.

    Args:
        args: Parsed argparse namespace (or any object with the above
            attributes).
        inbound_handler: Local callable invoked per remote message.
        opener: Optional urllib opener for tests.

    Returns:
        A configured :class:`CloudBridge` (not yet connected).
    """
    config = BridgeConfig(
        remote_url=getattr(args, "remote_url", None) or DEFAULT_REMOTE_URL,
        token=getattr(args, "bridge_token", None),
        client_id=getattr(args, "client_id", None) or "ferret",
        poll_interval=float(getattr(args, "poll_interval", 1.0) or 1.0),
        request_timeout=float(getattr(args, "request_timeout", 30.0) or 30.0),
    )
    return CloudBridge(config, inbound_handler, opener=opener)


def run_bridge(
    args: Any,
    inbound_handler: InboundHandler,
    *,
    opener: urllib.request.OpenerDirector | None = None,
) -> int:
    """Connect, start the poll loop, and block until interrupted.

    Convenience entry point for ``chimera ferret bridge``. FF1's CLI
    dispatcher calls into this function once it routes a ``bridge``
    subcommand.

    Args:
        args: Parsed argparse namespace.
        inbound_handler: Local callable invoked per remote message.
        opener: Optional urllib opener for tests.

    Returns:
        ``0`` on graceful shutdown (Ctrl-C), ``2`` on auth failure,
        ``1`` on any other bridge-level error.
    """
    bridge = build_bridge_from_args(args, inbound_handler, opener=opener)
    try:
        bridge.connect()
    except BridgeAuthError as exc:
        # Surface the message without a traceback so the CLI is friendly.
        print(f"ferret bridge: auth error: {exc}")
        return 2
    except BridgeError as exc:
        print(f"ferret bridge: connect failed: {exc}")
        return 1
    bridge.start()
    try:
        # Sleep in short slices so a Ctrl-C lands quickly.
        while bridge.running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
    # The poll loop logs auth failures into ``errors`` rather than
    # raising; surface the most recent one as a non-zero exit so a
    # supervisor can distinguish a clean shutdown from an auth blow-up.
    if bridge.errors:
        last = bridge.errors[-1]
        if last.startswith("auth:"):
            print(f"ferret bridge: {last}")
            return 2
    return 0
