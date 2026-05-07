"""Tests for ``chimera.sessions.eventlog.cross_cli`` and per-CLI integration.

The cross-CLI walker (B9-W11) lets every per-CLI ``sessions list`` /
``sessions show`` see sessions created by *any* Chimera CLI.  These tests
exercise:

  * the shared walker (``iter_all_sessions`` / ``iter_sessions_for_cli``);
  * the new ``cli_origin`` field on per-CLI ``SessionRecord`` instances;
  * the back-compat default behavior (each CLI sees only its own prefix);
  * the new ``--all-clis`` flag (drops the prefix filter, surfaces an
    ``ORIGIN`` column);
  * cross-CLI ``sessions show`` (a badger CLI can show an otter session
    by id alone).

All tests use a synthetic ``tmp_path/.chimera/eventlog`` and
``monkeypatch`` :func:`pathlib.Path.home` so they run hermetic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from chimera.sessions.eventlog import cross_cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_session(
    root: Path,
    session_id: str,
    *,
    model: str = "glm-5",
    cost: float = 0.01,
    success: bool = True,
    started_at: str = "2026-04-30T05:10:01Z",
    prompt: str = "test prompt",
) -> Path:
    """Write a minimal valid eventlog directory under ``root``.

    Args:
        root: The eventlog root (``tmp_path/.chimera/eventlog``).
        session_id: Directory basename, e.g. ``otter-AAA``.

    Returns:
        Absolute path to the created directory.
    """
    session_dir = root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": "2026-04-30T05:11:01Z",
        "model": model,
        "prompt": prompt,
        "success": success,
        "cost_usd": cost,
        "steps": 3,
        "tool_calls_total": 5,
    }
    (session_dir / "summary.json").write_text(json.dumps(summary))
    (session_dir / "event-000001-user.json").write_text(json.dumps({
        "type": "user_message",
        "metadata": {"content": "hello"},
    }))
    return session_dir


@pytest.fixture()
def populated_eventlog(tmp_path: Path, monkeypatch) -> Path:
    """Build an eventlog with one mink, one otter, and one ferret session."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    _make_session(eventlog, "mink-20260430T050000-AAA")
    _make_session(eventlog, "otter-20260430T060000-BBB")
    _make_session(eventlog, "ferret-20260430T070000-CCC")
    # An unrecognized prefix must NOT be picked up by the walker — this
    # is what guards us from treating ``backup-...`` or ``shares-...``
    # sibling dirs as sessions.
    unrelated = eventlog / "backup-20260430T080000-XXX"
    unrelated.mkdir()
    (unrelated / "summary.json").write_text("{}")
    return eventlog


# ---------------------------------------------------------------------------
# Common walker
# ---------------------------------------------------------------------------


def test_iter_all_sessions_returns_all_three(populated_eventlog: Path) -> None:
    """The cross-CLI walker yields one record per known-prefix dir."""
    records = list(cross_cli.iter_all_sessions())
    ids = sorted(r.session_id for r in records)
    assert ids == [
        "ferret-20260430T070000-CCC",
        "mink-20260430T050000-AAA",
        "otter-20260430T060000-BBB",
    ]


def test_iter_all_sessions_skips_unknown_prefix(populated_eventlog: Path) -> None:
    """Directories outside :data:`KNOWN_CLI_ORIGINS` are silently skipped."""
    records = list(cross_cli.iter_all_sessions())
    assert all(r.cli_origin in cross_cli.KNOWN_CLI_ORIGINS for r in records)
    # The synthetic ``backup-...`` dir from the fixture is gone.
    assert not any("backup-" in r.session_id for r in records)


def test_iter_sessions_for_cli_filters(populated_eventlog: Path) -> None:
    """Filtering by CLI yields only matching prefixes."""
    otters = list(cross_cli.iter_sessions_for_cli("otter"))
    assert len(otters) == 1
    assert otters[0].cli_origin == "otter"
    assert otters[0].session_id == "otter-20260430T060000-BBB"

    mink = list(cross_cli.iter_sessions_for_cli("mink"))
    assert len(mink) == 1
    assert mink[0].cli_origin == "mink"


def test_iter_all_sessions_orders_newest_first(
    populated_eventlog: Path,
) -> None:
    """Records sort by directory name descending (timestamp-sortable)."""
    records = list(cross_cli.iter_all_sessions())
    ids = [r.session_id for r in records]
    assert ids == sorted(ids, reverse=True)


def test_session_record_has_cli_origin(populated_eventlog: Path) -> None:
    """The new ``cli_origin`` field is populated from the directory prefix."""
    records = {r.session_id: r for r in cross_cli.iter_all_sessions()}
    assert records["otter-20260430T060000-BBB"].cli_origin == "otter"
    assert records["mink-20260430T050000-AAA"].cli_origin == "mink"
    assert records["ferret-20260430T070000-CCC"].cli_origin == "ferret"


def test_iter_all_sessions_handles_missing_root(tmp_path: Path, monkeypatch) -> None:
    """An absent eventlog dir yields zero records, not an exception."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert list(cross_cli.iter_all_sessions()) == []


def test_parse_cli_origin() -> None:
    """``parse_cli_origin`` extracts the CLI prefix when known."""
    assert cross_cli.parse_cli_origin("otter-20260430T-AAA") == "otter"
    assert cross_cli.parse_cli_origin("mink-20260430T-BBB") == "mink"
    assert cross_cli.parse_cli_origin("badger-AAA") == "badger"
    # Unknown prefix returns "" — we never silently invent an origin.
    assert cross_cli.parse_cli_origin("backup-20260430T-XYZ") == ""
    assert cross_cli.parse_cli_origin("noprefix") == ""


def test_find_session_dir_locates_any_origin(populated_eventlog: Path) -> None:
    """``find_session_dir`` resolves a session id regardless of origin."""
    p = cross_cli.find_session_dir("otter-20260430T060000-BBB")
    assert p is not None
    assert p.name == "otter-20260430T060000-BBB"

    assert cross_cli.find_session_dir("nope-XXX") is None


# ---------------------------------------------------------------------------
# Per-CLI back-compat: existing iter_sessions still filters to its prefix
# ---------------------------------------------------------------------------


def test_badger_iter_sessions_default_filters_current_cli(
    populated_eventlog: Path,
) -> None:
    """``badger.sessions.iter_sessions()`` with default args sees only badger."""
    from chimera.badger import sessions as badger_sessions

    # Only ``mink``/``otter``/``ferret`` exist in the fixture, so badger
    # sees zero records — the historic behavior.
    records = list(badger_sessions.iter_sessions())
    assert records == []


def test_badger_iter_sessions_all_clis_drops_filter(
    populated_eventlog: Path,
) -> None:
    """``all_clis=True`` makes badger.iter_sessions yield every CLI's records."""
    from chimera.badger import sessions as badger_sessions

    records = list(badger_sessions.iter_sessions(all_clis=True))
    origins = sorted(r.cli_origin for r in records)
    assert origins == ["ferret", "mink", "otter"]


def test_otter_iter_sessions_default_filters_to_otter(
    populated_eventlog: Path,
) -> None:
    """Otter's default iter_sessions still filters to ``otter-`` prefix."""
    from chimera.otter import sessions as otter_sessions

    records = list(otter_sessions.iter_sessions())
    assert len(records) == 1
    assert records[0].cli_origin == "otter"


def test_otter_iter_sessions_all_clis(populated_eventlog: Path) -> None:
    """``all_clis=True`` on otter sees mink + ferret + otter."""
    from chimera.otter import sessions as otter_sessions

    records = list(otter_sessions.iter_sessions(all_clis=True))
    assert sorted(r.cli_origin for r in records) == ["ferret", "mink", "otter"]


def test_ferret_iter_sessions_all_clis(populated_eventlog: Path) -> None:
    """Ferret cross-CLI walk."""
    from chimera.ferret import sessions as ferret_sessions

    records = list(ferret_sessions.iter_sessions(all_clis=True))
    assert sorted(r.cli_origin for r in records) == ["ferret", "mink", "otter"]


def test_weasel_iter_sessions_all_clis(populated_eventlog: Path) -> None:
    """Weasel cross-CLI walk."""
    from chimera.weasel import sessions as weasel_sessions

    records = list(weasel_sessions.iter_sessions(all_clis=True))
    assert sorted(r.cli_origin for r in records) == ["ferret", "mink", "otter"]


def test_shrew_iter_sessions_all_clis(populated_eventlog: Path) -> None:
    """Shrew cross-CLI walk."""
    from chimera.shrew import sessions as shrew_sessions

    records = list(shrew_sessions.iter_sessions(all_clis=True))
    assert sorted(r.cli_origin for r in records) == ["ferret", "mink", "otter"]


def test_stoat_iter_sessions_all_clis(populated_eventlog: Path) -> None:
    """Stoat cross-CLI walk."""
    from chimera.stoat import sessions as stoat_sessions

    records = list(stoat_sessions.iter_sessions(all_clis=True))
    assert sorted(r.cli_origin for r in records) == ["ferret", "mink", "otter"]


# ---------------------------------------------------------------------------
# Cross-CLI ``sessions show``
# ---------------------------------------------------------------------------


def test_show_finds_cross_cli_session(populated_eventlog: Path) -> None:
    """``badger.sessions.get_session`` resolves an ``otter-...`` id by directory.

    Today the badger ``sessions show`` only filtered the listing by
    prefix; the show path keys off ``root / session_id`` directly, so an
    otter session id passed to badger's ``get_session`` should resolve.
    This test pins that behavior so we don't accidentally regress to a
    prefix-required form.
    """
    from chimera.badger import sessions as badger_sessions

    detail = badger_sessions.get_session("otter-20260430T060000-BBB")
    assert detail.session_id == "otter-20260430T060000-BBB"
    assert detail.summary["model"] == "glm-5"


def test_show_missing_id_raises(populated_eventlog: Path) -> None:
    """Unknown ids still raise ``FileNotFoundError`` (cross-CLI or not)."""
    from chimera.badger import sessions as badger_sessions

    with pytest.raises(FileNotFoundError):
        badger_sessions.get_session("otter-does-not-exist")


# ---------------------------------------------------------------------------
# CLI ``sessions list``: default vs ``--all-clis``
# ---------------------------------------------------------------------------


def test_list_default_filters_current_cli(
    populated_eventlog: Path, capsys,
) -> None:
    """Backwards-compat: ``cmd_sessions_list`` without ``--all-clis``
    only shows the current CLI's prefix.

    We use otter here because otter has its own ``otter-...`` fixture,
    so the table is non-empty — exercising both the header and the
    no-origin column path.
    """
    from chimera.otter import sessions as otter_sessions

    args = argparse.Namespace(
        sessions_since=None,
        sessions_model=None,
        sessions_limit=10,
        sessions_json=False,
        sessions_all_clis=False,
        no_color=True,
    )
    rc = otter_sessions.cmd_sessions_list(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "otter-20260430T060000-BBB" in out
    # Other CLIs' sessions must NOT leak in.
    assert "ferret-20260430T070000-CCC" not in out
    assert "mink-20260430T050000-AAA" not in out
    # The ORIGIN column is suppressed in default mode.
    assert "ORIGIN" not in out


def test_list_all_clis_drops_filter(
    populated_eventlog: Path, capsys,
) -> None:
    """``--all-clis`` shows every CLI's sessions and renders ORIGIN."""
    from chimera.otter import sessions as otter_sessions

    args = argparse.Namespace(
        sessions_since=None,
        sessions_model=None,
        sessions_limit=10,
        sessions_json=False,
        sessions_all_clis=True,
        no_color=True,
    )
    rc = otter_sessions.cmd_sessions_list(args)
    assert rc == 0
    out = capsys.readouterr().out
    # All three sessions appear regardless of prefix.
    assert "otter-20260430T060000-BBB" in out
    assert "ferret-20260430T070000-CCC" in out
    assert "mink-20260430T050000-AAA" in out
    # ORIGIN column header is rendered.
    assert "ORIGIN" in out


def test_list_all_clis_json_includes_origin(
    populated_eventlog: Path, capsys,
) -> None:
    """``--all-clis --json`` includes ``cli_origin`` in every JSON row."""
    from chimera.otter import sessions as otter_sessions

    args = argparse.Namespace(
        sessions_since=None,
        sessions_model=None,
        sessions_limit=10,
        sessions_json=True,
        sessions_all_clis=True,
        no_color=True,
    )
    rc = otter_sessions.cmd_sessions_list(args)
    assert rc == 0
    out = capsys.readouterr().out
    rows = json.loads(out)
    assert len(rows) == 3
    origins = sorted(r["cli_origin"] for r in rows)
    assert origins == ["ferret", "mink", "otter"]


def test_weasel_list_all_clis(populated_eventlog: Path, capsys) -> None:
    """Weasel's CLI handler honors ``all_clis=True`` (B9-W11)."""
    from chimera.weasel import sessions as weasel_sessions

    rc = weasel_sessions.cmd_sessions_list(all_clis=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "ORIGIN" in out
    assert "otter-20260430T060000-BBB" in out
    assert "ferret-20260430T070000-CCC" in out


def test_weasel_list_default_filters(populated_eventlog: Path, capsys) -> None:
    """Without ``--all-clis``, weasel sees zero sessions (none in fixture)."""
    from chimera.weasel import sessions as weasel_sessions

    rc = weasel_sessions.cmd_sessions_list()
    assert rc == 0
    out = capsys.readouterr().out
    assert "no weasel sessions found" in out


# ---------------------------------------------------------------------------
# Argparse wiring smoke test — ``--all-clis`` is registered on every CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "chimera.badger.cli",
        "chimera.ferret.cli",
        "chimera.otter.cli",
        "chimera.weasel.cli",
        "chimera.shrew.cli",
        "chimera.stoat.cli",
    ],
)
def test_cli_registers_all_clis_flag(module_name: str) -> None:
    """Every per-CLI ``add_arguments`` registers ``--all-clis`` (B9-W11)."""
    import importlib

    module = importlib.import_module(module_name)
    parser = argparse.ArgumentParser()
    module.add_arguments(parser)
    # ``parse_known_args(['--all-clis'])`` succeeds and sets the dest.
    ns, _ = parser.parse_known_args(["--all-clis"])
    assert getattr(ns, "sessions_all_clis", None) is True
