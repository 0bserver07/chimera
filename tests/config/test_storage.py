"""Storage inspection + the one retention engine (M2 of the storage spec).

Three properties carry the milestone, and each has a test that fails loudly if
it regresses:

1. **The orphan scan sees project-root ``.chimera*`` siblings.** The spec as
   first written scanned the two scope roots only, which would have walked
   straight past ``<workdir>/.chimera_checkpoints`` — the 2.0 GB tree the whole
   subsystem exists to surface. ``test_orphan_scan_catches_a_checkpoints_
   position_sibling`` plants a directory in exactly that position.
2. **Nothing outside the registry can be deleted.** Not by config, not by a
   hand-built candidate, not by a store whose root happens to contain it.
3. **Retention is opt-in and singly implemented.** No config, no candidates;
   and ``chimera/tui/cohort.py`` routes through the same selector rather than
   keeping a second copy of the rules.
"""
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chimera.config.paths import STATE_DIRNAME, StoreRetention, UnknownStore, get_store
from chimera.config.storage import (
    Orphan,
    PruneCandidate,
    StoreEntry,
    apply_prune,
    collect_entries,
    find_orphans,
    format_age,
    format_size,
    plan_gc,
    report_stores,
    select_for_prune,
    tree_size,
)

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
    """The resolved user-scope storage root."""
    root = _hermetic / "home" / STATE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def project(_hermetic) -> Path:
    """The project root (cwd)."""
    return _hermetic / "cwd"


def _write_config(scope: Path, text: str) -> None:
    scope.mkdir(parents=True, exist_ok=True)
    (scope / "config.toml").write_text(text, encoding="utf-8")


def _aged(path: Path, days: float, *, content: str = "x") -> Path:
    """Create a file (or stamp a directory) with an mtime *days* old."""
    import os

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    ts = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))
    return path


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size,expected",
    [
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1.0 kB"),
        (150_700_000, "150.7 MB"),
        (2_000_000_000, "2.0 GB"),
    ],
)
def test_format_size_uses_decimal_units(size, expected):
    """Decimal units, because "the 2 GB checkpoint" is how the incident is named.

    Reporting GiB under a GB label would invite a reader to compare the number
    against one that means something else.
    """
    assert format_size(size) == expected


def test_format_age_switches_to_hours_under_a_day():
    assert format_age(None) == "-"
    assert format_age(0.5) == "12.0h"
    assert format_age(3.25) == "3.2d"


# ---------------------------------------------------------------------------
# tree_size
# ---------------------------------------------------------------------------


def test_tree_size_walks_a_directory(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.bin").write_bytes(b"x" * 100)
    (tmp_path / "two.bin").write_bytes(b"y" * 50)
    assert tree_size(tmp_path) == (150, 2)


def test_tree_size_of_a_missing_path_is_zero(tmp_path):
    assert tree_size(tmp_path / "nope") == (0, 0)


def test_tree_size_does_not_follow_symlinks(tmp_path):
    """A link into a large tree must not inflate a report or unbound the walk."""
    big = tmp_path / "big"
    big.mkdir()
    (big / "blob").write_bytes(b"z" * 10_000)
    scanned = tmp_path / "scanned"
    scanned.mkdir()
    (scanned / "link").symlink_to(big, target_is_directory=True)
    size, files = tree_size(scanned)
    assert size < 10_000
    assert files <= 1


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_report_stores_covers_every_registry_row_and_both_scopes(project):
    """Absent stores are reported as absent, never omitted.

    "Declared and empty" and "not declared at all" are different facts; only a
    report that states both can be trusted when it says a directory is orphaned.
    """
    from chimera.config.paths import all_stores

    reports = report_stores(project=project)
    assert len(reports) == len(all_stores())
    scopes = {r.store.scope for r in reports}
    assert scopes == {"user", "project"}
    assert all(not r.exists for r in reports)  # nothing written yet


def test_report_stores_measures_a_populated_store(home, project):
    sessions = home / "sessions"
    _aged(sessions / "a.jsonl", days=3, content="x" * 10)
    _aged(sessions / "b.jsonl", days=30, content="y" * 20)

    by_name = {r.store.name: r for r in report_stores(project=project)}
    report = by_name["sessions"]
    assert report.exists and not report.is_file
    assert report.size_bytes == 30
    assert report.entries == 2
    assert report.file_count == 2
    # Ages are measured against the real clock, so assert ordering not values.
    assert report.newest_age_days is not None
    assert report.oldest_age_days is not None
    assert report.oldest_age_days > report.newest_age_days
    assert report.retention_label == "keep forever"


def test_a_file_store_reports_as_a_file(home, project):
    """``history`` is a single file — the M1 sweep's correction to the spec."""
    (home / "history").write_text("cmd\n", encoding="utf-8")
    by_name = {r.store.name: r for r in report_stores(project=project)}
    assert by_name["history"].is_file is True
    assert by_name["history"].entries == 1


def test_never_prunable_stores_say_so_even_with_retention_configured(home, project):
    """A retention typo cannot relabel datasets as reclaimable."""
    _write_config(project / STATE_DIRNAME, "[storage.datasets]\nretain = 1\n")
    (home / "datasets").mkdir(parents=True, exist_ok=True)
    by_name = {r.store.name: r for r in report_stores(project=project)}
    assert by_name["datasets"].retention_label == "never prunable"
    assert by_name["datasets"].retention.active is False


def test_configured_retention_shows_in_the_report(home, project):
    _write_config(
        project / STATE_DIRNAME,
        "[storage.sessions]\nretain = 5\nmax-age-days = 90\n",
    )
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    by_name = {r.store.name: r for r in report_stores(project=project)}
    assert by_name["sessions"].retention_label == "retain=5 max-age-days=90"


# ---------------------------------------------------------------------------
# Orphans — the milestone's point
# ---------------------------------------------------------------------------


def test_orphan_scan_catches_a_checkpoints_position_sibling(home, project):
    """The spec's original blind spot, pinned.

    ``<workdir>/.chimera_checkpoints`` sits *beside* ``<proj>/.chimera``, not
    inside it. A scan of ``chimera_home()`` and the project state dir — which
    is what the spec first asked for — reports nothing here. This is the exact
    position of the 2.0 GB tree that motivated the whole subsystem, so the
    fixture plants a directory there and the assertion is that it is named.
    """
    orphan_dir = project / f"{STATE_DIRNAME}_checkpoints" / "0" / ".venv"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "blob.bin").write_bytes(b"x" * 4096)
    (project / STATE_DIRNAME).mkdir(exist_ok=True)

    found = find_orphans(project=project)
    paths = {o.path for o in found}
    assert project / f"{STATE_DIRNAME}_checkpoints" in paths

    entry = next(o for o in found if o.path.name == f"{STATE_DIRNAME}_checkpoints")
    assert entry.scope == "project-root"
    assert entry.size_bytes >= 4096
    assert "beside" in entry.reason


def test_the_state_dir_itself_is_never_its_own_sibling_orphan(project):
    """``<proj>/.chimera`` matches the ``.chimera*`` glob; it must be excluded."""
    (project / STATE_DIRNAME).mkdir()
    assert all(o.path != project / STATE_DIRNAME for o in find_orphans(project=project))


def test_unclaimed_directories_under_each_scope_root_are_reported(home, project):
    (home / "not-a-store").mkdir()
    (project / STATE_DIRNAME / "stray").mkdir(parents=True)

    by_name = {o.path.name: o for o in find_orphans(project=project)}
    assert by_name["not-a-store"].scope == "user"
    assert by_name["stray"].scope == "project"


def test_declared_stores_are_never_reported_as_orphans(home, project):
    for name in ("sessions", "cohorts", "datasets", "eventlog"):
        (home / name).mkdir(parents=True, exist_ok=True)
    (project / STATE_DIRNAME / "sessions").mkdir(parents=True)
    assert find_orphans(project=project) == []


def test_loose_files_at_a_scope_root_are_not_orphans(home, project):
    """``config.toml``/``todo.json``/``settings.json`` legitimately live there.

    Flagging them would train the reader to skim past the section — which is
    exactly the failure mode a 2 GB tree exploited.
    """
    (home / "config.toml").write_text("", encoding="utf-8")
    (project / STATE_DIRNAME).mkdir()
    (project / STATE_DIRNAME / "todo.json").write_text("{}", encoding="utf-8")
    assert find_orphans(project=project) == []


def test_an_env_relocated_store_does_not_become_a_false_orphan(
    home, project, monkeypatch, tmp_path
):
    """``$CHIMERA_DATASETS_DIR`` moves the live copy; the slot stays claimed."""
    monkeypatch.setenv("CHIMERA_DATASETS_DIR", str(tmp_path / "elsewhere"))
    (home / "datasets").mkdir(parents=True, exist_ok=True)
    assert all(o.path.name != "datasets" for o in find_orphans(project=project))


def test_running_from_home_does_not_manufacture_orphans(_hermetic, monkeypatch):
    """When both scope roots are the same directory, neither half lies.

    Run ``chimera doctor`` from ``$HOME`` and ``<proj>/.chimera`` *is*
    ``~/.chimera``. Scanning it once per scope with each scope's half of the
    vocabulary would report every user store as a project orphan (and the
    project stores as user orphans) — a report full of invented findings is
    worse than no report, because it trains the reader to ignore the section.
    """
    home_dir = _hermetic / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(home_dir)
    state = home_dir / STATE_DIRNAME
    for name in ("datasets", "cohorts", "sessions", "eventlog"):
        (state / name).mkdir(parents=True, exist_ok=True)
    (state / "agents").mkdir(parents=True, exist_ok=True)  # both scopes claim it

    from chimera.config.paths import chimera_home, project_state_dir

    assert chimera_home() == project_state_dir(home_dir)  # the precondition
    assert find_orphans(project=home_dir) == []

    (state / "genuinely-unclaimed").mkdir()
    assert [o.path.name for o in find_orphans(project=home_dir)] == [
        "genuinely-unclaimed"
    ]


def test_orphans_sort_largest_first(home, project):
    small = home / "small"
    small.mkdir()
    (small / "f").write_bytes(b"x" * 10)
    large = home / "large"
    large.mkdir()
    (large / "f").write_bytes(b"x" * 10_000)
    found = find_orphans(project=project)
    assert [o.path.name for o in found] == ["large", "small"]


def test_orphan_to_dict_is_json_safe(home, project):
    (home / "junk").mkdir()
    payload = find_orphans(project=project)[0].to_dict()
    import json

    assert json.loads(json.dumps(payload))["scope"] == "user"


# ---------------------------------------------------------------------------
# collect_entries
# ---------------------------------------------------------------------------


def test_collect_entries_orders_newest_first(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    _aged(root / "old", days=40)
    _aged(root / "mid", days=10)
    _aged(root / "new", days=1)
    entries = collect_entries(root, now=NOW)
    assert [e.id for e in entries] == ["new", "mid", "old"]
    assert entries[0].age_days < entries[-1].age_days


def test_collect_entries_skips_dot_entries(tmp_path):
    """Atomic writers stage ``.<name>.tmp`` siblings; a reclaim pass must not race one."""
    root = tmp_path / "store"
    root.mkdir()
    _aged(root / "real", days=5)
    _aged(root / ".manifest.json.tmp", days=5)
    assert [e.id for e in collect_entries(root, now=NOW)] == ["real"]


def test_collect_entries_id_order_is_available_for_timestamped_ids(tmp_path):
    """Cohort ids are ``<UTC-stamp>-<rand>``, so lexical order is chronological."""
    root = tmp_path / "store"
    root.mkdir()
    for name in ("20260101-a", "20260701-b", "20260401-c"):
        _aged(root / name, days=1)  # identical mtimes: only id order disambiguates
    entries = collect_entries(root, order="id", now=NOW)
    assert [e.id for e in entries] == ["20260701-b", "20260401-c", "20260101-a"]


def test_collect_entries_on_a_missing_root_is_empty(tmp_path):
    assert collect_entries(tmp_path / "nope", now=NOW) == []


# ---------------------------------------------------------------------------
# select_for_prune — the one set of rules
# ---------------------------------------------------------------------------


def _entries(*specs: tuple[str, float], root: Path) -> list[StoreEntry]:
    return [
        StoreEntry(id=name, path=root / name, age_days=age) for name, age in specs
    ]


def test_no_retention_selects_nothing(tmp_path):
    entries = _entries(("a", 900.0), ("b", 800.0), root=tmp_path)
    assert select_for_prune(entries, None, store="sessions", root=tmp_path) == []
    assert (
        select_for_prune(
            entries, StoreRetention(), store="sessions", root=tmp_path
        )
        == []
    )


def test_retain_keeps_the_newest_n_as_a_hard_floor(tmp_path):
    entries = _entries(("n1", 1.0), ("n2", 2.0), ("n3", 3.0), ("n4", 4.0), root=tmp_path)
    picked = select_for_prune(
        entries, StoreRetention(retain=2), store="sessions", root=tmp_path,
        measure=False,
    )
    assert [c.entry.id for c in picked] == ["n3", "n4"]
    assert picked[0].rule == "retain=2 (position 3)"


def test_max_age_alone_drops_everything_older_regardless_of_count(tmp_path):
    entries = _entries(("n1", 1.0), ("n2", 40.0), ("n3", 90.0), root=tmp_path)
    picked = select_for_prune(
        entries, StoreRetention(max_age_days=30), store="sessions", root=tmp_path,
        measure=False,
    )
    assert [c.entry.id for c in picked] == ["n2", "n3"]
    assert "max-age-days=30" in picked[0].rule


def test_the_two_knobs_compose_as_keep_n_then_drop_old(tmp_path):
    entries = _entries(
        ("n1", 100.0), ("n2", 200.0), ("n3", 5.0), ("n4", 300.0), root=tmp_path
    )
    picked = select_for_prune(
        entries,
        StoreRetention(retain=2, max_age_days=50),
        store="sessions",
        root=tmp_path,
        measure=False,
    )
    # n1/n2 are inside the retain floor despite being old; n3 is young.
    assert [c.entry.id for c in picked] == ["n4"]


def test_excluded_ids_survive_and_still_occupy_their_position(tmp_path):
    """The live cohort is untouchable without silently promoting an older entry."""
    entries = _entries(("n1", 1.0), ("n2", 2.0), ("n3", 3.0), root=tmp_path)
    picked = select_for_prune(
        entries,
        StoreRetention(retain=1),
        store="cohorts",
        root=tmp_path,
        exclude={"n2"},
        measure=False,
    )
    assert [c.entry.id for c in picked] == ["n3"]


# ---------------------------------------------------------------------------
# apply_prune — the structural guarantee
# ---------------------------------------------------------------------------


def _candidate(store: str, root: Path, name: str) -> PruneCandidate:
    return PruneCandidate(
        store=store,
        root=root,
        entry=StoreEntry(id=name, path=root / name, age_days=99.0),
        rule="test",
    )


def test_apply_prune_removes_files_and_directories(tmp_path):
    root = tmp_path / "sessions"
    (root / "adir").mkdir(parents=True)
    (root / "adir" / "inner").write_text("x", encoding="utf-8")
    (root / "afile").write_text("x", encoding="utf-8")
    done = apply_prune(
        [_candidate("sessions", root, "adir"), _candidate("sessions", root, "afile")]
    )
    assert len(done) == 2
    assert list(root.iterdir()) == []


def test_apply_prune_refuses_an_unregistered_store_and_deletes_nothing(tmp_path):
    """The guarantee: an arbitrary directory cannot be laundered into a deletion.

    A caller that fabricates a candidate for a store the registry never
    declared gets ``UnknownStore`` — and, because validation runs over the whole
    batch before the first unlink, the *valid* sibling candidate survives too.
    """
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "keep").write_text("x", encoding="utf-8")
    rogue = tmp_path / "definitely-not-a-store"
    rogue.mkdir()
    (rogue / "precious.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(UnknownStore):
        apply_prune(
            [
                _candidate("sessions", root, "keep"),
                _candidate("definitely-not-a-store", rogue, "precious.txt"),
            ]
        )
    assert (rogue / "precious.txt").exists()
    assert (root / "keep").exists()


def test_apply_prune_refuses_a_never_prunable_store(tmp_path):
    root = tmp_path / "datasets"
    root.mkdir()
    (root / "swebench.jsonl").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="prunable=False"):
        apply_prune([_candidate("datasets", root, "swebench.jsonl")])
    assert (root / "swebench.jsonl").exists()


def test_apply_prune_refuses_a_path_outside_the_declared_root(tmp_path):
    """A candidate must be a *direct child*: no `../` escape, no grandchildren."""
    root = tmp_path / "sessions"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "deep").write_text("x", encoding="utf-8")
    escapee = PruneCandidate(
        store="sessions",
        root=root,
        entry=StoreEntry(
            id="deep", path=root / "nested" / "deep", age_days=99.0
        ),
        rule="test",
    )
    with pytest.raises(ValueError, match="direct child"):
        apply_prune([escapee])
    assert (root / "nested" / "deep").exists()


def test_apply_prune_can_archive_instead_of_delete(tmp_path):
    """The owner's standing rule — archive/relocate — is a reachable mode."""
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "old.jsonl").write_text("payload", encoding="utf-8")
    archive = tmp_path / "archive"
    done = apply_prune([_candidate("sessions", root, "old.jsonl")], archive_to=archive)
    assert len(done) == 1
    assert not (root / "old.jsonl").exists()
    assert (archive / "sessions" / "old.jsonl").read_text(encoding="utf-8") == "payload"


def test_apply_prune_survives_a_vanished_path(tmp_path):
    """A racer that removed the entry first is skipped, never fatal."""
    root = tmp_path / "sessions"
    root.mkdir()
    done = apply_prune([_candidate("sessions", root, "never-existed")])
    assert done == []


# ---------------------------------------------------------------------------
# plan_gc
# ---------------------------------------------------------------------------


def test_plan_gc_with_no_config_selects_nothing(home, project):
    for name in ("sessions", "cohorts", "eventlog"):
        store_dir = home / name
        store_dir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            _aged(store_dir / f"e{i}", days=400)
    plan = plan_gc(project=project, now=NOW)
    assert plan.candidates == []
    assert "no retention configured" in {s.reason for s in plan.skips}


def test_plan_gc_selects_only_configured_prunable_stores(home, project):
    _write_config(project / STATE_DIRNAME, "[storage.sessions]\nretain = 2\n")
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    for i in range(4):
        _aged(sessions / f"s{i}", days=float(40 - i * 10))
    cohorts = home / "cohorts"
    cohorts.mkdir(parents=True)
    for i in range(4):
        _aged(cohorts / f"c{i}", days=400)

    plan = plan_gc(project=project, now=NOW)
    assert plan.stores == ["sessions"]
    assert len(plan.candidates) == 2
    assert all(c.store == "sessions" for c in plan.candidates)


def test_plan_gc_cannot_reach_a_never_prunable_store_even_with_retention(
    home, project
):
    """``datasets`` + ``function_synthesis`` are structurally unreachable."""
    _write_config(
        project / STATE_DIRNAME,
        "[storage.datasets]\nretain = 1\n\n[storage.function_synthesis]\nretain = 1\n",
    )
    for name in ("datasets", "function_synthesis"):
        store_dir = home / name
        store_dir.mkdir(parents=True)
        for i in range(5):
            _aged(store_dir / f"f{i}", days=500)

    plan = plan_gc(project=project, now=NOW)
    assert plan.candidates == []
    structural = {s.store for s in plan.skips if s.reason.startswith("never prunable")}
    assert {"datasets", "function_synthesis"} <= structural


def test_plan_gc_never_names_an_unregistered_directory(home, project):
    """The registry is the whole vocabulary: undeclared paths cannot appear.

    Deliberately run with an *active* policy that does produce candidates —
    a plan that selected nothing at all would pass this vacuously.
    """
    _write_config(project / STATE_DIRNAME, "[storage.sessions]\nretain = 1\n")
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    for i in range(3):
        _aged(sessions / f"s{i}", days=float(30 - i))
    rogue = home / "definitely-not-a-store"
    rogue.mkdir(parents=True)
    for i in range(5):
        _aged(rogue / f"junk{i}", days=900)

    plan = plan_gc(project=project, now=NOW)
    assert plan.candidates, "fixture must produce candidates or the test is vacuous"
    named = {str(c.entry.path) for c in plan.candidates}
    assert not any("definitely-not-a-store" in p for p in named)

    apply_prune(plan.candidates)
    assert sorted(p.name for p in rogue.iterdir()) == [f"junk{i}" for i in range(5)]


def test_plan_gc_skips_a_store_whose_root_contains_another_store(home, project):
    """``project-state`` is ``<proj>/.chimera`` — its children are other stores.

    Pruning them by age would delete ``sessions/``, ``settings.json`` and
    ``todo.json`` from one retention line, so a parent store is never
    child-pruned.
    """
    _write_config(project / STATE_DIRNAME, "[storage.project-state]\nretain = 1\n")
    (project / STATE_DIRNAME / "sessions").mkdir(parents=True, exist_ok=True)
    _aged(project / STATE_DIRNAME / "todo.json", days=900, content="{}")

    plan = plan_gc(project=project, now=NOW)
    assert plan.candidates == []
    nested = {s.store: s.reason for s in plan.skips}
    assert "project-state" in nested
    assert "root contains" in nested["project-state"]
    assert (project / STATE_DIRNAME / "todo.json").exists()


def test_plan_gc_honours_the_store_filter(home, project):
    _write_config(
        project / STATE_DIRNAME,
        "[storage.sessions]\nretain = 1\n\n[storage.eventlog]\nretain = 1\n",
    )
    for name in ("sessions", "eventlog"):
        store_dir = home / name
        store_dir.mkdir(parents=True)
        for i in range(3):
            _aged(store_dir / f"e{i}", days=float(30 - i))
    plan = plan_gc(project=project, stores=["eventlog"], now=NOW)
    assert plan.stores == ["eventlog"]


def test_plan_gc_totals_are_measured(home, project):
    _write_config(project / STATE_DIRNAME, "[storage.sessions]\nretain = 1\n")
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    _aged(sessions / "new", days=1, content="a")
    _aged(sessions / "old", days=90, content="b" * 500)
    plan = plan_gc(project=project, now=NOW)
    assert plan.total_bytes == 500


def test_registry_agrees_that_datasets_and_function_synthesis_are_locked():
    """A guard on the guard: if these ever flip, the tests above go quiet."""
    assert get_store("datasets").prunable is False
    assert get_store("function_synthesis").prunable is False


def test_orphan_dataclass_is_frozen():
    """A report row is evidence; a caller must not be able to edit it in place."""
    orphan = Orphan(
        path=Path("/tmp/x"), scope="user", size_bytes=1, file_count=1, reason="r"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        orphan.path = Path("/tmp/y")  # type: ignore[misc]
