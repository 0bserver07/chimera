"""Tests for ``chimera otter export`` / ``import`` (W14-2)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from chimera.otter import export_import as ei


# ---------------------------------------------------------------------------
# Fixture: synthetic eventlog with one session
# ---------------------------------------------------------------------------


@pytest.fixture()
def eventlog_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    home = tmp_path / "home"
    root = home / ".chimera" / "eventlog"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return root


def _write_session(
    root: Path,
    session_id: str = "otter-20260507T000000-aaaa",
    events: list[dict[str, object]] | None = None,
) -> Path:
    d = root / session_id
    d.mkdir(parents=True, exist_ok=True)
    summary = {
        "session_id": session_id,
        "run_id": session_id,
        "started_at": "2026-05-07T00:00:00Z",
        "ended_at": "2026-05-07T00:01:00Z",
        "model": "claude-sonnet-4-6",
        "prompt": "hello",
        "title": "demo session",
        "success": True,
        "cost_usd": 0.05,
        "steps": 2,
        "tool_calls_total": 4,
    }
    (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    for idx, ev in enumerate(events or [{"type": "user", "text": "hi"}]):
        (d / f"event-{idx:06d}.json").write_text(
            json.dumps(ev), encoding="utf-8"
        )
    return d


# ---------------------------------------------------------------------------
# ExportEnvelope
# ---------------------------------------------------------------------------


def test_export_envelope_to_dict_round_trip() -> None:
    env = ei.ExportEnvelope(summary={"session_id": "x"}, events=[{"a": 1}])
    parsed = ei.ExportEnvelope.from_dict(env.to_dict())
    assert parsed.summary == {"session_id": "x"}
    assert parsed.events == [{"a": 1}]


def test_envelope_from_dict_rejects_non_dict() -> None:
    with pytest.raises(ValueError):
        ei.ExportEnvelope.from_dict("nope")


def test_envelope_from_dict_rejects_unknown_schema() -> None:
    with pytest.raises(ValueError):
        ei.ExportEnvelope.from_dict({"schema": "v999", "summary": {}})


def test_envelope_from_dict_requires_summary_object() -> None:
    with pytest.raises(ValueError):
        ei.ExportEnvelope.from_dict(
            {"schema": "chimera.otter.session/1", "summary": "no"}
        )


def test_envelope_from_dict_drops_non_dict_events() -> None:
    env = ei.ExportEnvelope.from_dict(
        {
            "schema": "chimera.otter.session/1",
            "summary": {"session_id": "y"},
            "events": [{"a": 1}, "not a dict", 42],
        }
    )
    assert env.events == [{"a": 1}]


# ---------------------------------------------------------------------------
# export_session
# ---------------------------------------------------------------------------


def test_export_session_packs_summary_and_events(eventlog_root: Path) -> None:
    _write_session(
        eventlog_root,
        events=[{"type": "user", "text": "hi"}, {"type": "assistant", "text": "yo"}],
    )
    env = ei.export_session("otter-20260507T000000-aaaa")
    assert env.summary["session_id"] == "otter-20260507T000000-aaaa"
    assert len(env.events) == 2


def test_export_session_missing_raises(eventlog_root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ei.export_session("does-not-exist")


# ---------------------------------------------------------------------------
# import_session — round-trip property
# ---------------------------------------------------------------------------


def test_import_round_trip_recreates_session(eventlog_root: Path) -> None:
    _write_session(
        eventlog_root,
        events=[{"type": "user", "text": "round"}],
    )
    env = ei.export_session("otter-20260507T000000-aaaa")
    # Wipe the original.
    import shutil

    shutil.rmtree(eventlog_root / "otter-20260507T000000-aaaa")
    # Re-import.
    new_id = ei.import_session(env)
    assert new_id == "otter-20260507T000000-aaaa"
    assert (eventlog_root / new_id / "summary.json").exists()
    assert (eventlog_root / new_id / "event-000000.json").exists()


def test_import_existing_without_overwrite_raises(eventlog_root: Path) -> None:
    _write_session(eventlog_root)
    env = ei.export_session("otter-20260507T000000-aaaa")
    with pytest.raises(FileExistsError):
        ei.import_session(env)


def test_import_with_overwrite_replaces_events(eventlog_root: Path) -> None:
    _write_session(
        eventlog_root,
        events=[{"type": "user", "text": "old"}],
    )
    env = ei.export_session("otter-20260507T000000-aaaa")
    # Mutate envelope events and re-import with overwrite.
    env.events = [{"type": "user", "text": "new"}]
    new_id = ei.import_session(env, overwrite=True)
    body = json.loads(
        (eventlog_root / new_id / "event-000000.json").read_text()
    )
    assert body["text"] == "new"


def test_import_dict_passthrough_works(eventlog_root: Path) -> None:
    _write_session(eventlog_root)
    env = ei.export_session("otter-20260507T000000-aaaa")
    raw = env.to_dict()
    # Wipe original.
    import shutil

    shutil.rmtree(eventlog_root / "otter-20260507T000000-aaaa")
    new_id = ei.import_session(raw)
    assert new_id == "otter-20260507T000000-aaaa"


def test_import_session_id_required_in_summary(eventlog_root: Path) -> None:
    env = ei.ExportEnvelope(summary={})
    with pytest.raises(ValueError):
        ei.import_session(env)


# ---------------------------------------------------------------------------
# render_markdown / render_html
# ---------------------------------------------------------------------------


def test_render_markdown_includes_summary_and_events(
    eventlog_root: Path,
) -> None:
    _write_session(
        eventlog_root,
        events=[{"type": "user", "text": "MarkdownProbe"}],
    )
    env = ei.export_session("otter-20260507T000000-aaaa")
    md = ei.render_markdown(env)
    assert "# Session" in md
    assert "MarkdownProbe" in md


def test_render_html_wraps_markdown(eventlog_root: Path) -> None:
    _write_session(eventlog_root)
    env = ei.export_session("otter-20260507T000000-aaaa")
    html = ei.render_html(env)
    assert html.startswith("<!doctype html>")
    assert "</html>" in html


# ---------------------------------------------------------------------------
# dispatch_export / dispatch_import
# ---------------------------------------------------------------------------


def _ns(**fields: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "sub_action": None,
        "sub_target": None,
        "export_format": None,
        "export_output": None,
        "import_overwrite": False,
    }
    base.update(fields)
    return argparse.Namespace(**base)


def test_dispatch_export_requires_session_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = ei.dispatch_export(_ns())
    assert rc == 2
    assert "requires <SESSION_ID>" in capsys.readouterr().err


def test_dispatch_export_to_stdout_json(
    eventlog_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_session(eventlog_root)
    rc = ei.dispatch_export(
        _ns(sub_action="otter-20260507T000000-aaaa", export_format="json")
    )
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["schema"] == "chimera.otter.session/1"


def test_dispatch_export_to_file_md(
    eventlog_root: Path, tmp_path: Path
) -> None:
    _write_session(eventlog_root)
    out = tmp_path / "report.md"
    rc = ei.dispatch_export(
        _ns(
            sub_action="otter-20260507T000000-aaaa",
            export_format="md",
            export_output=str(out),
        )
    )
    assert rc == 0
    assert out.exists()
    assert "# Session" in out.read_text()


def test_dispatch_export_unknown_format(
    eventlog_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_session(eventlog_root)
    rc = ei.dispatch_export(
        _ns(sub_action="otter-20260507T000000-aaaa", export_format="pdf")
    )
    assert rc == 2
    assert "unknown --format" in capsys.readouterr().err


def test_dispatch_import_requires_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = ei.dispatch_import(_ns())
    assert rc == 2
    assert "requires <FILE>" in capsys.readouterr().err


def test_dispatch_import_round_trip(
    eventlog_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_session(eventlog_root)
    # Export to file.
    out = tmp_path / "session.json"
    ei.dispatch_export(
        _ns(
            sub_action="otter-20260507T000000-aaaa",
            export_format="json",
            export_output=str(out),
        )
    )
    capsys.readouterr()
    # Wipe original.
    import shutil

    shutil.rmtree(eventlog_root / "otter-20260507T000000-aaaa")
    # Import.
    rc = ei.dispatch_import(_ns(sub_action=str(out)))
    assert rc == 0
    assert "imported session" in capsys.readouterr().out


def test_dispatch_import_invalid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json{{{")
    rc = ei.dispatch_import(_ns(sub_action=str(bad)))
    assert rc == 1
    assert "invalid JSON" in capsys.readouterr().err


def test_dispatch_import_rename_via_sub_target(
    eventlog_root: Path, tmp_path: Path
) -> None:
    _write_session(eventlog_root)
    out = tmp_path / "session.json"
    ei.dispatch_export(
        _ns(
            sub_action="otter-20260507T000000-aaaa",
            export_format="json",
            export_output=str(out),
        )
    )
    rc = ei.dispatch_import(
        _ns(sub_action=str(out), sub_target="otter-renamed-id")
    )
    assert rc == 0
    assert (eventlog_root / "otter-renamed-id").exists()
