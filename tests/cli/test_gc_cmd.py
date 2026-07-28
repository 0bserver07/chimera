"""``chimera gc`` — the reclaim surface (M2 of the storage spec).

The command is only worth having if it is hard to make dangerous, so the tests
that matter are the refusals:

* dry run is the default and changes nothing;
* ``--apply`` on a machine with no retention configured is a no-op;
* ``--apply`` cannot touch, or even name, a directory the registry does not
  declare — proven by planting one next to a store that *is* being pruned;
* ``datasets`` stays put no matter what a config file says.
"""
import argparse
import io
import json
import os
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chimera.cli import gc_cmd
from chimera.config.paths import STATE_DIRNAME

NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """Isolate from the developer's real ``~/.chimera`` and any stray config."""
    for var in (
        "CHIMERA_HOME",
        "CHIMERA_CONFIG_HOME",
        "CHIMERA_DATASETS_DIR",
        "CHIMERA_FS_HOME",
        "CHIMERA_CRON_DIR",
        "CHIMERA_TEAMS_HOME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    workdir = tmp_path / "cwd"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)
    return tmp_path


@pytest.fixture()
def home(_hermetic) -> Path:
    root = _hermetic / "home" / STATE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def project(_hermetic) -> Path:
    return _hermetic / "cwd"


def _write_config(project: Path, text: str) -> None:
    scope = project / STATE_DIRNAME
    scope.mkdir(parents=True, exist_ok=True)
    (scope / "config.toml").write_text(text, encoding="utf-8")


def _aged(path: Path, days: float, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    ts = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))
    return path


def _args(**over) -> argparse.Namespace:
    base = {
        "store": None,
        "apply": False,
        "archive": None,
        "project": None,
        "json": False,
    }
    base.update(over)
    return argparse.Namespace(**base)


def _run(**over) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = gc_cmd.run(_args(**over))
    return rc, buf.getvalue()


def _seed_sessions(home: Path, count: int = 4) -> Path:
    sessions = home / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        _aged(sessions / f"s{i}.jsonl", days=float(count * 10 - i * 10))
    return sessions


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_gc_subcommand_is_registered():
    from chimera.cli.main import build_parser

    parser = build_parser()
    subs = [
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    ]
    assert "gc" in subs[0].choices


def test_gc_reaches_run_through_main(home, project):
    from chimera.cli.main import main

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["gc", "--json"])
    assert rc == 0
    assert json.loads(buf.getvalue())["applied"] is False


# ---------------------------------------------------------------------------
# Dry run is the default
# ---------------------------------------------------------------------------


def test_bare_gc_is_a_dry_run_and_changes_nothing(home, project):
    _write_config(project, "[storage.sessions]\nretain = 1\n")
    sessions = _seed_sessions(home)
    before = sorted(p.name for p in sessions.iterdir())

    rc, out = _run()
    assert rc == 0
    assert "dry run" in out
    assert "--apply" in out
    assert sorted(p.name for p in sessions.iterdir()) == before


def test_the_dry_run_names_the_rule_that_selected_each_candidate(home, project):
    _write_config(project, "[storage.sessions]\nretain = 1\n")
    _seed_sessions(home)
    _, out = _run()
    assert "retain=1 (position 2)" in out


def test_the_report_accounts_for_stores_it_skipped(home, project):
    """Silence is what let the original problem grow — skips are printed."""
    _write_config(project, "[storage.sessions]\nretain = 1\n")
    _seed_sessions(home)
    _, out = _run()
    assert "no retention configured" in out
    assert "never prunable (structural)" in out
    assert "datasets" in out  # named as never-prunable, not as a candidate


# ---------------------------------------------------------------------------
# --apply
# ---------------------------------------------------------------------------


def test_apply_removes_the_selected_entries(home, project):
    _write_config(project, "[storage.sessions]\nretain = 2\n")
    sessions = _seed_sessions(home, count=5)

    rc, out = _run(apply=True)
    assert rc == 0
    assert "removed" in out
    survivors = sorted(p.name for p in sessions.iterdir())
    assert survivors == ["s3.jsonl", "s4.jsonl"]  # the two newest


def test_apply_with_no_retention_configured_is_a_no_op(home, project):
    """Retention is opt-in: an unconfigured machine loses nothing to ``--apply``."""
    sessions = _seed_sessions(home, count=6)
    cohorts = home / "cohorts"
    for i in range(4):
        _aged(cohorts / f"c{i}" / "manifest.json", days=900, content="{}")
    before_sessions = sorted(p.name for p in sessions.iterdir())
    before_cohorts = sorted(p.name for p in cohorts.iterdir())

    rc, out = _run(apply=True)
    assert rc == 0
    assert "0 candidate(s)" in out
    assert sorted(p.name for p in sessions.iterdir()) == before_sessions
    assert sorted(p.name for p in cohorts.iterdir()) == before_cohorts


def test_apply_cannot_touch_or_name_an_unregistered_directory(home, project):
    """The milestone's acceptance test.

    An unregistered directory is planted right beside a store that *is* being
    pruned, holding entries far older than the retention limit. gc removes the
    registered entries and leaves the undeclared tree byte-for-byte intact —
    and never prints its name, because the registry is the whole vocabulary the
    command has.
    """
    _write_config(project, "[storage.sessions]\nretain = 1\n")
    sessions = _seed_sessions(home, count=4)

    rogue = home / "definitely-not-a-store"
    rogue.mkdir(parents=True)
    for i in range(3):
        _aged(rogue / f"junk{i}.bin", days=999, content="precious")
    rogue_before = {p.name: p.read_text(encoding="utf-8") for p in rogue.iterdir()}

    # ...and one in the position that would have been the 2 GB tree.
    checkpoints = project / f"{STATE_DIRNAME}_checkpoints"
    _aged(checkpoints / "0" / "blob.bin", days=999, content="two gigabytes")

    rc, out = _run(apply=True)
    assert rc == 0
    assert len(list(sessions.iterdir())) == 1  # gc really did act
    assert "definitely-not-a-store" not in out
    assert f"{STATE_DIRNAME}_checkpoints" not in out
    assert {p.name: p.read_text(encoding="utf-8") for p in rogue.iterdir()} == (
        rogue_before
    )
    assert (checkpoints / "0" / "blob.bin").read_text(encoding="utf-8") == (
        "two gigabytes"
    )


def test_apply_cannot_reach_datasets_however_it_is_configured(home, project):
    _write_config(
        project, "[storage.datasets]\nretain = 1\nmax-age-days = 1\n"
    )
    datasets = home / "datasets"
    for i in range(4):
        _aged(datasets / f"bench-{i}.jsonl", days=999)

    rc, out = _run(apply=True)
    assert rc == 0
    assert len(list(datasets.iterdir())) == 4
    assert "0 candidate(s)" in out


def test_apply_leaves_project_state_config_files_alone(home, project):
    """One retention line on the parent store must not delete live config."""
    _write_config(project, "[storage.project-state]\nretain = 1\n")
    _aged(project / STATE_DIRNAME / "todo.json", days=999, content="{}")
    _aged(project / STATE_DIRNAME / "rules.md", days=999, content="# rules")

    rc, out = _run(apply=True)
    assert rc == 0
    assert (project / STATE_DIRNAME / "todo.json").exists()
    assert (project / STATE_DIRNAME / "rules.md").exists()
    assert "root contains" in out


# ---------------------------------------------------------------------------
# --archive
# ---------------------------------------------------------------------------


def test_archive_relocates_instead_of_deleting(home, project, tmp_path):
    """The owner's standing rule, reachable from the CLI."""
    _write_config(project, "[storage.sessions]\nretain = 1\n")
    _seed_sessions(home, count=3)
    archive = tmp_path / "attic"

    rc, out = _run(apply=True, archive=str(archive))
    assert rc == 0
    assert f"archived to {archive}" in out
    moved = sorted(p.name for p in (archive / "sessions").iterdir())
    assert moved == ["s0.jsonl", "s1.jsonl"]


def test_archive_with_nothing_selected_creates_no_directory(
    home, project, tmp_path
):
    archive = tmp_path / "attic"
    rc, _ = _run(apply=True, archive=str(archive))
    assert rc == 0
    assert not archive.exists()


# ---------------------------------------------------------------------------
# Filters, JSON, errors
# ---------------------------------------------------------------------------


def test_store_filter_restricts_the_plan(home, project):
    _write_config(
        project,
        "[storage.sessions]\nretain = 1\n\n[storage.eventlog]\nretain = 1\n",
    )
    _seed_sessions(home, count=3)
    eventlog = home / "eventlog"
    for i in range(3):
        _aged(eventlog / f"e{i}.jsonl", days=float(30 - i))

    _, out = _run(store=["eventlog"])
    assert "eventlog" in out
    assert "sessions" not in out


def test_the_legacy_tui_cohorts_alias_still_drives_gc(home, project):
    """A config written before `[storage]` existed keeps working, end to end.

    `[tui.cohorts]` is read by the shared retention resolver, so it reaches
    `chimera gc` as well as the TUI's own auto-prune — the two cannot disagree
    about what a user's existing config means.
    """
    _write_config(project, "[tui.cohorts]\nretain = 1\n")
    cohorts = home / "cohorts"
    for i in range(3):
        _aged(
            cohorts / f"2026010{i}-000000-aaaa" / "manifest.json",
            days=float(30 - i),
            content="{}",
        )

    rc, out = _run()
    assert rc == 0
    assert "cohorts" in out
    assert "retain=1" in out

    rc, _ = _run(apply=True)
    assert rc == 0
    assert sorted(p.name for p in cohorts.iterdir()) == ["20260102-000000-aaaa"]


def test_unknown_store_name_exits_two(home, project):
    rc, out = _run(store=["not-a-real-store"])
    assert rc == 2
    assert "unknown store" in out


def test_json_output_is_machine_readable(home, project):
    _write_config(project, "[storage.sessions]\nretain = 1\n")
    _seed_sessions(home, count=3)
    _, out = _run(json=True)
    payload = json.loads(out)
    assert payload["applied"] is False
    assert payload["stores"] == ["sessions"]
    assert len(payload["candidates"]) == 2
    assert payload["candidates"][0]["rule"].startswith("retain=1")
    assert {"store", "reason"} <= set(payload["skipped"][0])


def test_json_output_after_apply_reports_what_was_done(home, project):
    _write_config(project, "[storage.sessions]\nretain = 1\n")
    _seed_sessions(home, count=3)
    _, out = _run(json=True, apply=True)
    payload = json.loads(out)
    assert payload["applied"] is True
    assert len(payload["candidates"]) == 2
