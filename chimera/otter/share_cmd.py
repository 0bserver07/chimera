"""``chimera otter share <session-id>`` — render + dispatch a session share.

This module is the otter analogue of :func:`chimera.mink.cli._run_runs_share`
and :mod:`chimera.sessions.share`. It packages a persisted otter session
(``~/.chimera/eventlog/otter-<id>/``) into a self-contained transcript
and routes it to one of three sinks:

* ``file`` — writes ``~/.chimera/shares/otter-<id>.html`` (or ``.json`` /
  ``.md`` when ``--format`` is ``json`` / ``md``). Returns the absolute
  path on stdout.
* ``http`` — POSTs the rendered transcript to ``$OTTER_SHARE_URL``
  (default: a local placeholder URL). The body is JSON when ``--format``
  is ``json``, otherwise ``text/html`` / ``text/markdown``. The HTTP
  sink uses :mod:`urllib.request` so we stay stdlib-only.
* ``stdout`` — prints the rendered transcript directly. Convenient for
  piping into ``less`` or feeding into another tool.

Trademark hygiene: the default ``$OTTER_SHARE_URL`` is **not** the
upstream open-source coding agent's share endpoint. We deliberately
ship a self-host placeholder (``http://localhost:5174/api/shares``) so
nothing in this file points at a third-party brand.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chimera.otter import sessions as _sessions

__all__ = [
    "DEFAULT_SHARE_URL",
    "VALID_SINKS",
    "VALID_FORMATS",
    "cmd_share",
    "default_shares_dir",
    "dispatch_share",
    "render_html",
    "render_json",
    "render_markdown",
    "send_http",
    "write_file_sink",
]


# WHY: the upstream coding agent posts to its own hosted endpoint. We
# refuse to hardcode that here. The default below is a local-only stub
# so users who don't set ``$OTTER_SHARE_URL`` get a loud connection
# refusal instead of an accidental third-party POST.
DEFAULT_SHARE_URL = "http://localhost:5174/api/shares"

VALID_SINKS = ("file", "http", "stdout")
VALID_FORMATS = ("html", "json", "md")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def default_shares_dir() -> Path:
    """Return ``~/.chimera/shares/`` (created lazily by callers)."""
    return Path.home() / ".chimera" / "shares"


def _resolve_format(fmt: str | None) -> str:
    """Validate and normalize ``--format``.

    Args:
        fmt: Raw value from argparse / caller (``None`` means "default").

    Returns:
        One of :data:`VALID_FORMATS`.

    Raises:
        ValueError: When ``fmt`` is not in :data:`VALID_FORMATS`.
    """
    cleaned = (fmt or "html").strip().lower()
    if cleaned not in VALID_FORMATS:
        raise ValueError(
            f"unknown --format {fmt!r}: expected one of "
            f"{', '.join(VALID_FORMATS)}"
        )
    return cleaned


def _resolve_sink(sink: str | None) -> str:
    """Validate ``--sink``."""
    cleaned = (sink or "file").strip().lower()
    if cleaned not in VALID_SINKS:
        raise ValueError(
            f"unknown --sink {sink!r}: expected one of "
            f"{', '.join(VALID_SINKS)}"
        )
    return cleaned


# ---------------------------------------------------------------------------
# Rendering — turn a SessionDetail into html / json / markdown
# ---------------------------------------------------------------------------


def _short_iso(value: Any) -> str:
    """Best-effort ISO-8601 string for a summary timestamp value."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        try:
            return (
                datetime.fromtimestamp(float(value), tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (OSError, ValueError, OverflowError):
            return str(value)
    return ""


def render_json(detail: _sessions.SessionDetail) -> str:
    """Render ``detail`` as a JSON document.

    The schema mirrors what :func:`chimera.otter.sessions.cmd_sessions_show`
    emits in ``--json`` mode so downstream consumers can use one parser.
    """
    payload = {
        "session_id": detail.session_id,
        "summary": detail.summary,
        "events": detail.events,
    }
    return json.dumps(payload, indent=2, default=str)


def render_markdown(detail: _sessions.SessionDetail) -> str:
    """Render ``detail`` as a GitHub-flavored Markdown transcript."""
    s = detail.summary
    out: list[str] = []
    out.append(f"# Otter session `{detail.session_id}`")
    out.append("")
    out.append(f"- model: `{s.get('model', '')}`")
    out.append(f"- started: {_short_iso(s.get('started_at', ''))}")
    out.append(f"- ended: {_short_iso(s.get('ended_at', ''))}")
    out.append(f"- steps: {s.get('steps', 0)}")
    out.append(f"- tool calls: {s.get('tool_calls_total', 0)}")
    cost = float(s.get("cost_usd", 0.0) or 0.0)
    out.append(f"- cost (USD): {cost:.6f}")
    out.append(f"- success: {bool(s.get('success', False))}")
    if s.get("error"):
        out.append(f"- error: {s['error']}")
    out.append("")
    out.append("## Prompt")
    out.append("")
    out.append("```")
    out.append(str(s.get("prompt", "")))
    out.append("```")
    out.append("")
    out.append(f"## Events ({len(detail.events)})")
    if not detail.events:
        out.append("")
        out.append("_(no events recorded)_")
        return "\n".join(out) + "\n"
    for ev in detail.events:
        ev_type = str(ev.get("type") or "?")
        meta = ev.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {"raw": meta}
        out.append("")
        out.append(f"### `{ev_type}`")
        out.append("")
        out.append("```json")
        out.append(json.dumps(meta, indent=2, default=str))
        out.append("```")
    return "\n".join(out) + "\n"


def render_html(detail: _sessions.SessionDetail) -> str:
    """Render ``detail`` as a self-contained HTML document.

    The HTML carries no external assets so it round-trips through email
    or a static file host without losing fidelity. Styling is minimal
    and inline; viewers that strip ``<style>`` still get readable text.
    """
    s = detail.summary
    rows: list[str] = []
    rows.append("<!doctype html>")
    rows.append('<html lang="en"><head>')
    rows.append('<meta charset="utf-8">')
    rows.append(f"<title>Otter session {html.escape(detail.session_id)}</title>")
    rows.append(
        "<style>"
        "body{font-family:system-ui,sans-serif;max-width:880px;margin:2em auto;"
        "padding:0 1em;color:#222;}"
        "h1{font-size:1.4em;}h2{margin-top:1.4em;}"
        "pre{background:#f6f8fa;padding:.6em .8em;border-radius:6px;"
        "white-space:pre-wrap;word-break:break-word;}"
        "table.meta td{padding:2px 12px 2px 0;vertical-align:top;}"
        ".event{border-left:3px solid #d0d7de;margin:.6em 0;padding:.2em .8em;}"
        ".event-type{font-family:ui-monospace,monospace;color:#0550ae;}"
        "</style>"
    )
    rows.append("</head><body>")
    rows.append(f"<h1>Otter session <code>{html.escape(detail.session_id)}</code></h1>")
    rows.append('<table class="meta"><tbody>')

    def _row(label: str, value: object) -> str:
        return (
            f"<tr><td><b>{html.escape(label)}</b></td>"
            f"<td>{html.escape(str(value))}</td></tr>"
        )

    rows.append(_row("model", s.get("model", "")))
    rows.append(_row("started", _short_iso(s.get("started_at", ""))))
    rows.append(_row("ended", _short_iso(s.get("ended_at", ""))))
    rows.append(_row("steps", s.get("steps", 0)))
    rows.append(_row("tool calls", s.get("tool_calls_total", 0)))
    cost = float(s.get("cost_usd", 0.0) or 0.0)
    rows.append(_row("cost (USD)", f"{cost:.6f}"))
    rows.append(_row("success", bool(s.get("success", False))))
    if s.get("error"):
        rows.append(_row("error", s["error"]))
    rows.append("</tbody></table>")

    rows.append("<h2>Prompt</h2>")
    rows.append(f"<pre>{html.escape(str(s.get('prompt', '')))}</pre>")

    rows.append(f"<h2>Events ({len(detail.events)})</h2>")
    if not detail.events:
        rows.append("<p><em>(no events recorded)</em></p>")
    else:
        for ev in detail.events:
            ev_type = str(ev.get("type") or "?")
            meta = ev.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {"raw": meta}
            rows.append('<div class="event">')
            rows.append(
                f'<div class="event-type">{html.escape(ev_type)}</div>'
            )
            rows.append(
                f"<pre>{html.escape(json.dumps(meta, indent=2, default=str))}</pre>"
            )
            rows.append("</div>")
    rows.append("</body></html>")
    return "\n".join(rows) + "\n"


def _render(detail: _sessions.SessionDetail, fmt: str) -> str:
    """Dispatch to the per-format renderer."""
    if fmt == "html":
        return render_html(detail)
    if fmt == "json":
        return render_json(detail)
    return render_markdown(detail)


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


_FORMAT_CONTENT_TYPES: dict[str, str] = {
    "html": "text/html; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json",
}

_FORMAT_EXTENSIONS: dict[str, str] = {
    "html": ".html",
    "md": ".md",
    "json": ".json",
}


def write_file_sink(
    session_id: str,
    body: str,
    fmt: str,
    *,
    shares_dir: Path | None = None,
) -> Path:
    """Write ``body`` to ``<shares_dir>/otter-<session_id>.<ext>``.

    The ``shares_dir`` is created with parents when missing. We strip the
    canonical ``otter-`` prefix from ``session_id`` only when forming the
    filename so existing shares stay discoverable by id; the content
    itself stays unmodified.
    """
    out_dir = shares_dir or default_shares_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _FORMAT_EXTENSIONS[fmt]
    # WHY: the existing convention prefixes filenames with ``otter-`` so a
    # mixed shares directory (otter + future agents) stays sortable by
    # source.
    name = session_id if session_id.startswith("otter-") else f"otter-{session_id}"
    path = out_dir / f"{name}{ext}"
    path.write_text(body, encoding="utf-8")
    return path.resolve()


def send_http(
    body: str,
    fmt: str,
    *,
    url: str | None = None,
    timeout: float = 30.0,
    opener: Any = None,
) -> tuple[int, str]:
    """POST ``body`` to ``url`` (or ``$OTTER_SHARE_URL``).

    Args:
        body: The rendered transcript bytes (UTF-8).
        fmt: One of :data:`VALID_FORMATS`; selects the ``Content-Type``.
        url: Override the destination. When ``None``, falls back to
            ``$OTTER_SHARE_URL`` and finally :data:`DEFAULT_SHARE_URL`.
        timeout: Socket timeout in seconds.
        opener: Optional :class:`urllib.request.OpenerDirector`-compatible
            object exposing ``.open(req, timeout=...)``. The default
            uses :func:`urllib.request.urlopen`. Tests inject a stub.

    Returns:
        ``(status_code, response_body)``. We do not raise on non-2xx —
        the CLI layer renders the failure message itself.
    """
    target = url or os.environ.get("OTTER_SHARE_URL") or DEFAULT_SHARE_URL
    data = body.encode("utf-8")
    headers = {
        "Content-Type": _FORMAT_CONTENT_TYPES[fmt],
        "User-Agent": "chimera-otter-share/1",
    }
    req = urllib.request.Request(target, data=data, method="POST", headers=headers)
    open_fn = opener.open if opener is not None else urllib.request.urlopen
    try:
        # WHY: we keep the with-block tight; the response object is a file
        # handle that holds a socket open until closed.
        with open_fn(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 0) or getattr(resp, "code", 0) or 0)
            try:
                payload = resp.read()
            except Exception:  # noqa: BLE001
                payload = b""
    except urllib.error.HTTPError as exc:
        # HTTPError is itself a response — preserve the body for callers.
        try:
            payload = exc.read()
        except Exception:  # noqa: BLE001
            payload = b""
        return int(exc.code), payload.decode("utf-8", "replace")
    return status, payload.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------


def cmd_share(args: argparse.Namespace) -> int:
    """Implement ``chimera otter share <session-id>``.

    The handler mirrors :func:`chimera.mink.cli._run_runs_share`: it
    surfaces ``ValueError`` / ``FileNotFoundError`` as exit 2 (usage)
    and any runtime failure (HTTP / disk) as exit 1.

    Args:
        args: An argparse namespace expected to carry:

            * ``share_target`` — the ``otter-...`` session id to share.
            * ``share_sink`` — one of :data:`VALID_SINKS`. Default ``file``.
            * ``share_format`` — one of :data:`VALID_FORMATS`. Default ``html``.
            * ``share_url`` — optional HTTP override. Reads ``$OTTER_SHARE_URL``
              when absent.

    Returns:
        Exit code: ``0`` on success, ``2`` on usage / not-found, ``1``
        on runtime errors (HTTP / disk).
    """
    target = getattr(args, "share_target", None) or getattr(args, "sub_target", None)
    sink_raw = getattr(args, "share_sink", None) or "file"
    fmt_raw = getattr(args, "share_format", None) or "html"
    url_override = getattr(args, "share_url", None)

    if not target:
        print(
            "error: 'otter share' requires a SESSION_ID argument "
            "(see 'otter sessions list' for available ids).",
            file=sys.stderr,
        )
        return 2

    try:
        sink = _resolve_sink(sink_raw)
        fmt = _resolve_format(fmt_raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        detail = _sessions.get_session(target)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    body = _render(detail, fmt)

    if sink == "stdout":
        sys.stdout.write(body)
        if not body.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    if sink == "file":
        try:
            path = write_file_sink(target, body, fmt)
        except OSError as exc:
            print(f"error: failed to write share file: {exc}", file=sys.stderr)
            return 1
        print(str(path))
        return 0

    # sink == "http"
    try:
        status, response = send_http(body, fmt, url=url_override)
    except (urllib.error.URLError, OSError) as exc:
        print(f"error: HTTP share failed: {exc}", file=sys.stderr)
        return 1
    if not 200 <= status < 300:
        snippet = response.strip().splitlines()[0] if response.strip() else ""
        print(
            f"error: share endpoint returned HTTP {status}"
            + (f": {snippet}" if snippet else ""),
            file=sys.stderr,
        )
        return 1
    # On success the endpoint typically returns a JSON document with a
    # share URL; we print whatever it returned so callers can pipe it.
    print(response.rstrip("\n"))
    return 0


def dispatch_share(args: argparse.Namespace) -> int | None:
    """Return an exit code when ``args`` requests the share subcommand.

    Mirrors :func:`chimera.mink.cli._dispatch_runs`. Returns ``None`` when
    the caller should fall through to the next dispatcher.
    """
    sub = getattr(args, "subcommand", None)
    share_cmd = getattr(args, "share_command", None)
    if sub != "share" and share_cmd != "share":
        return None
    return cmd_share(args)
