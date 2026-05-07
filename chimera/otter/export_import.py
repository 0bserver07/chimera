"""``chimera otter export`` / ``chimera otter import`` — round-trip a session.

A session ``export`` packs ``summary.json`` + every ``event-*.json`` from
an eventlog directory into one of:

* ``json`` — a single dict ``{schema, summary, events}`` that's a strict
  superset of the on-disk layout (default; lossless).
* ``md`` — Markdown transcript suitable for sharing or pasting into a PR.
* ``html`` — minimal HTML wrapper around the same transcript so a single
  ``open foo.html`` produces a readable view.

``import`` accepts only the JSON form — Markdown / HTML are
human-readable, not round-trip surfaces. The imported session is
written under ``~/.chimera/eventlog/<session-id>/`` (re-using the
existing on-disk layout) so subsequent ``otter sessions show``
inspections work without further conversion.

Trademark hygiene: this module never names the upstream open-source
coding agent in user-visible source.

Schema
------

The export envelope is::

    {
      "schema": "chimera.otter.session/1",
      "summary": <summary.json contents>,
      "events":  [<event-*.json contents>, ...],
      "exported_at": "2026-05-07T12:34:56Z"
    }

Round-trip property: ``import_session(export_session(<id>)) == <id>``
on disk (modulo the ``exported_at`` envelope key).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html as _html
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ExportEnvelope",
    "export_session",
    "import_session",
    "render_markdown",
    "render_html",
    "dispatch_export",
    "dispatch_import",
]


_SCHEMA = "chimera.otter.session/1"


@dataclass
class ExportEnvelope:
    """Container for the JSON-form export shape."""

    summary: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    schema: str = _SCHEMA
    exported_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "summary": self.summary,
            "events": self.events,
            "exported_at": self.exported_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ExportEnvelope:
        if not isinstance(data, dict):
            raise ValueError("export envelope must be a JSON object")
        schema = data.get("schema")
        if schema != _SCHEMA:
            raise ValueError(
                f"unsupported export schema: {schema!r} "
                f"(expected {_SCHEMA!r})"
            )
        summary = data.get("summary")
        if not isinstance(summary, dict):
            raise ValueError("export envelope is missing 'summary' object")
        events = data.get("events") or []
        if not isinstance(events, list):
            raise ValueError("export envelope 'events' must be a list")
        clean_events = [e for e in events if isinstance(e, dict)]
        return cls(
            summary=summary,
            events=clean_events,
            schema=schema,
            exported_at=str(data.get("exported_at", "")),
        )


def _utc_iso8601() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def export_session(
    session_id: str,
    *,
    eventlog_root: Path | None = None,
) -> ExportEnvelope:
    """Pack a session directory into an :class:`ExportEnvelope`.

    Args:
        session_id: Directory name under the eventlog root.
        eventlog_root: Override; defaults to
            :func:`chimera.otter.sessions.default_eventlog_root`.

    Raises:
        FileNotFoundError: When the session does not exist.
    """
    from chimera.otter.sessions import (
        default_eventlog_root as _default_root,
        get_session,
    )

    root = eventlog_root or _default_root()
    detail = get_session(session_id, eventlog_root=root)
    return ExportEnvelope(
        summary=dict(detail.summary),
        events=[dict(e) for e in detail.events],
        exported_at=_utc_iso8601(),
    )


def import_session(
    envelope: ExportEnvelope | dict[str, Any],
    *,
    eventlog_root: Path | None = None,
    overwrite: bool = False,
) -> str:
    """Materialize an envelope under the eventlog root.

    Args:
        envelope: Either an :class:`ExportEnvelope` or a dict matching
            its schema.
        eventlog_root: Override the default root.
        overwrite: When ``True`` replace an existing session directory.

    Returns:
        The session id that was written.

    Raises:
        FileExistsError: When the session already exists and
            ``overwrite`` is ``False``.
        ValueError: When the envelope is missing required fields.
    """
    from chimera.otter.sessions import default_eventlog_root as _default_root

    if isinstance(envelope, dict):
        envelope = ExportEnvelope.from_dict(envelope)

    summary = envelope.summary
    session_id = (
        str(summary.get("session_id"))
        or str(summary.get("run_id"))
    )
    if not session_id or session_id == "None":
        raise ValueError(
            "envelope is missing both 'session_id' and 'run_id' in summary"
        )

    root = eventlog_root or _default_root()
    target = root / session_id
    if target.exists():
        if not overwrite:
            raise FileExistsError(
                f"session already exists: {target} "
                "(pass overwrite=True or rename the export)"
            )
        # Drop any pre-existing event files before re-writing so we
        # don't end up with stale event-*.json from the old session.
        for p in target.glob("event-*.json"):
            try:
                p.unlink()
            except OSError:
                pass

    target.mkdir(parents=True, exist_ok=True)
    (target / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for idx, event in enumerate(envelope.events):
        # Pad the index to 6 digits so lexical sort = chronological.
        # Existing on-disk events already follow this convention.
        path = target / f"event-{idx:06d}.json"
        path.write_text(
            json.dumps(event, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return session_id


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _summary_lines(summary: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in (
        "session_id", "run_id", "started_at", "ended_at",
        "model", "success", "cost_usd", "steps", "tool_calls_total",
        "title", "prompt",
    ):
        if key in summary and summary[key] not in (None, ""):
            out.append(f"- **{key}**: {summary[key]}")
    return out


def render_markdown(envelope: ExportEnvelope) -> str:
    """Render an envelope as a Markdown transcript."""
    summary = envelope.summary
    title = summary.get("title") or summary.get("prompt") or "session"
    lines: list[str] = [f"# Session: {title}", ""]
    lines.extend(_summary_lines(summary))
    lines.append("")
    lines.append("## Events")
    lines.append("")
    for idx, ev in enumerate(envelope.events):
        kind = ev.get("type") or ev.get("kind") or "event"
        lines.append(f"### {idx}. `{kind}`")
        body = ev.get("text") or ev.get("content") or ev.get("data")
        if isinstance(body, str) and body.strip():
            lines.append("")
            lines.append("```")
            lines.append(body.strip())
            lines.append("```")
        else:
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(ev, indent=2, sort_keys=True))
            lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(envelope: ExportEnvelope) -> str:
    """Render an envelope as a single self-contained HTML document."""
    md = render_markdown(envelope)
    body = _html.escape(md)
    title = envelope.summary.get("title") or envelope.summary.get("prompt") or "otter session"
    return (
        "<!doctype html>\n"
        "<html><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(str(title))}</title>"
        "<style>body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
        "margin:2em auto;max-width:50em;line-height:1.5;}"
        "pre{white-space:pre-wrap;}</style></head>"
        f"<body><pre>{body}</pre></body></html>\n"
    )


# ---------------------------------------------------------------------------
# argparse dispatchers
# ---------------------------------------------------------------------------


def dispatch_export(args: argparse.Namespace) -> int:
    """Wire ``chimera otter export <session-id> [--format json|md|html]``.

    Reads ``args.sub_action`` for the session id and
    ``args.export_format`` (with fallbacks to ``--format`` /
    ``--output-format``).
    """
    session_id = getattr(args, "sub_action", None)
    if not session_id:
        print("error: 'export' requires <SESSION_ID>", file=sys.stderr)
        return 2
    fmt = (
        getattr(args, "export_format", None)
        or getattr(args, "sub_target", None)  # ``otter export <id> json`` shorthand
        or "json"
    ).lower()
    out_path = getattr(args, "export_output", None)
    try:
        envelope = export_session(session_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if fmt == "json":
        rendered = json.dumps(envelope.to_dict(), indent=2, sort_keys=True)
    elif fmt in {"md", "markdown"}:
        rendered = render_markdown(envelope)
    elif fmt == "html":
        rendered = render_html(envelope)
    else:
        print(
            f"error: unknown --format {fmt!r} (supported: json, md, html)",
            file=sys.stderr,
        )
        return 2

    if out_path:
        Path(out_path).write_text(rendered, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        print(rendered)
    return 0


def dispatch_import(args: argparse.Namespace) -> int:
    """Wire ``chimera otter import <file> [--overwrite]``.

    Reads ``args.sub_action`` for the input file path; ``args.sub_target``
    is reserved for an optional rename (``otter import file.json
    new-id``) — when set, the imported summary's ``session_id`` is
    rewritten before materialization.
    """
    src = getattr(args, "sub_action", None)
    if not src:
        print("error: 'import' requires <FILE>", file=sys.stderr)
        return 2
    rename_to = getattr(args, "sub_target", None)
    overwrite = bool(getattr(args, "import_overwrite", False))
    path = Path(src)
    if not path.exists():
        print(f"error: file not found: {src}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: invalid JSON in {src}: {exc}", file=sys.stderr)
        return 1
    try:
        envelope = ExportEnvelope.from_dict(data)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if rename_to:
        envelope.summary = dict(envelope.summary)
        envelope.summary["session_id"] = rename_to
        envelope.summary["run_id"] = rename_to
    try:
        new_id = import_session(envelope, overwrite=overwrite)
    except (FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"imported session {new_id}")
    return 0
