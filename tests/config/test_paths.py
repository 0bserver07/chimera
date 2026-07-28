"""The path registry and ``[storage]`` config (M1 of the storage spec).

The load-bearing property, pinned first and hardest: **with no environment
override and no config file, every accessor resolves exactly where the data
already lives.** Adopting the registry moved ninety-odd hand-built paths; if
any one of them landed somewhere new, a user's sessions, cohorts, or staged
datasets would silently vanish from the CLI's view. Everything else here —
precedence, per-store env overrides, legacy retention aliases — exists to make
that property survive configuration.
"""
from pathlib import Path

import pytest

from chimera.config import paths
from chimera.config.paths import (
    STATE_DIRNAME,
    Store,
    StoreRetention,
    UnknownStore,
    all_stores,
    chimera_home,
    get_store,
    project_state_dir,
    store_path,
    store_retention,
    user_scope_dir,
)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """Isolate every test from the developer's real ``~/.chimera`` config.

    ``chimera_home`` consults the config chain, whose project scope is the cwd:
    without this, a stray ``./.chimera/config.toml`` would leak into results.
    """
    monkeypatch.delenv("CHIMERA_HOME", raising=False)
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    monkeypatch.delenv("CHIMERA_DATASETS_DIR", raising=False)
    monkeypatch.delenv("CHIMERA_FS_HOME", raising=False)
    monkeypatch.delenv("CHIMERA_CRON_DIR", raising=False)
    monkeypatch.delenv("CHIMERA_TEAMS_HOME", raising=False)
    # Both seams: ``Path.home()`` for direct resolution, ``$HOME`` because
    # ``Path.expanduser`` reads the environment rather than ``Path.home``.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    workdir = tmp_path / "cwd"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)
    return tmp_path


def _write_config(scope: Path, text: str) -> None:
    scope.mkdir(parents=True, exist_ok=True)
    (scope / "config.toml").write_text(text, encoding="utf-8")


# -- the no-config guarantee -------------------------------------------------


def test_defaults_resolve_exactly_where_data_lives_today(_hermetic):
    """No env, no config: every user store is ``~/.chimera/<rel>``, verbatim.

    This is the migration's acceptance property. The expected strings are
    written out longhand rather than derived from the registry, so a typo in a
    ``rel`` field fails here instead of being silently agreed with.
    """
    home = _hermetic / "home"
    assert chimera_home() == home / ".chimera"
    assert store_path("datasets") == home / ".chimera" / "datasets"
    assert store_path("cohorts") == home / ".chimera" / "cohorts"
    assert store_path("sessions") == home / ".chimera" / "sessions"
    assert store_path("eventlog") == home / ".chimera" / "eventlog"
    assert store_path("history") == home / ".chimera" / "history"
    assert store_path("projects") == home / ".chimera" / "projects"
    assert store_path("function_synthesis") == home / ".chimera" / "function_synthesis"
    assert store_path("tasks") == home / ".chimera" / "tasks"
    assert store_path("experiment-runs") == home / ".chimera" / "experiment-runs"


def test_defaults_match_every_registry_row(_hermetic):
    """Sweep the whole registry, not just the rows spelled out above."""
    home = _hermetic / "home"
    for store in all_stores():
        if store.env:
            continue  # covered by the per-store override tests
        root = home / ".chimera" if store.scope == "user" else Path.cwd() / ".chimera"
        assert store_path(store.name) == (root / store.rel if store.rel else root)


def test_home_resolution_is_call_time_not_import_time(_hermetic, monkeypatch):
    """A patched home is honored by code that imported the module earlier."""
    elsewhere = _hermetic / "elsewhere"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: elsewhere))
    assert chimera_home() == elsewhere / ".chimera"


# -- root precedence ---------------------------------------------------------


def test_env_overrides_everything(_hermetic, monkeypatch):
    relocated = _hermetic / "relocated"
    _write_config(_hermetic / "home" / ".chimera", f'[storage]\nroot = "{_hermetic / "cfg"}"\n')
    monkeypatch.setenv("CHIMERA_HOME", str(relocated))
    assert chimera_home() == relocated
    assert store_path("sessions") == relocated / "sessions"


def test_env_expands_tilde(_hermetic, monkeypatch):
    monkeypatch.setenv("CHIMERA_HOME", "~/somewhere")
    assert chimera_home() == _hermetic / "home" / "somewhere"


def test_storage_root_from_user_scope_config(_hermetic):
    configured = _hermetic / "configured"
    _write_config(_hermetic / "home" / ".chimera", f'[storage]\nroot = "{configured}"\n')
    assert chimera_home() == configured
    assert store_path("eventlog") == configured / "eventlog"


def test_project_scope_config_outranks_user_scope(_hermetic):
    """XDG < user < project — the documented chain, for the root too."""
    _write_config(_hermetic / "home" / ".chimera", f'[storage]\nroot = "{_hermetic / "u"}"\n')
    _write_config(Path.cwd() / ".chimera", f'[storage]\nroot = "{_hermetic / "p"}"\n')
    assert chimera_home() == _hermetic / "p"


def test_xdg_scope_is_read(_hermetic):
    configured = _hermetic / "xdg-root"
    _write_config(
        _hermetic / "home" / ".config" / "chimera", f'[storage]\nroot = "{configured}"\n'
    )
    assert chimera_home() == configured


def test_storage_root_tilde_is_expanded(_hermetic):
    _write_config(_hermetic / "home" / ".chimera", '[storage]\nroot = "~/relocated"\n')
    assert chimera_home() == _hermetic / "home" / "relocated"


def test_blank_or_non_string_root_falls_back(_hermetic):
    _write_config(_hermetic / "home" / ".chimera", '[storage]\nroot = "   "\n')
    assert chimera_home() == _hermetic / "home" / ".chimera"
    _write_config(_hermetic / "home" / ".chimera", "[storage]\nroot = 7\n")
    assert chimera_home() == _hermetic / "home" / ".chimera"


def test_broken_config_never_breaks_path_resolution(_hermetic):
    """A corrupt config degrades to the default; it must never raise."""
    _write_config(_hermetic / "home" / ".chimera", "[storage\nroot = nope")
    assert chimera_home() == _hermetic / "home" / ".chimera"


# -- backwards-compatible per-store env overrides ----------------------------


def test_datasets_dir_env_override_still_works(_hermetic, monkeypatch):
    """``CHIMERA_DATASETS_DIR`` predates the registry and must keep working."""
    staged = _hermetic / "staged"
    monkeypatch.setenv("CHIMERA_DATASETS_DIR", str(staged))
    assert store_path("datasets") == staged
    from chimera.eval.datasets import staging_dir

    assert staging_dir() == staged


def test_datasets_env_override_beats_storage_root(_hermetic, monkeypatch):
    monkeypatch.setenv("CHIMERA_HOME", str(_hermetic / "root"))
    monkeypatch.setenv("CHIMERA_DATASETS_DIR", str(_hermetic / "staged"))
    assert store_path("datasets") == _hermetic / "staged"
    assert store_path("sessions") == _hermetic / "root" / "sessions"


def test_function_synthesis_env_override_still_works(_hermetic, monkeypatch):
    fs_home = _hermetic / "fs"
    monkeypatch.setenv("CHIMERA_FS_HOME", str(fs_home))
    assert store_path("function_synthesis") == fs_home
    from chimera.function_synthesis.cache import CacheDirs

    assert CacheDirs.default().root == fs_home


def test_pb_runs_env_override_still_targets_the_pb_subtree(_hermetic, monkeypatch):
    """``CHIMERA_PB_RUNS`` overrides a *subtree*, not the store — as before."""
    monkeypatch.setenv("CHIMERA_HOME", str(_hermetic / "root"))
    assert store_path("experiment-runs") / "pb-runs" == (
        _hermetic / "root" / "experiment-runs" / "pb-runs"
    )
    # The variable is documented on the row rather than wired as a store env,
    # because it does not relocate the store root.
    assert get_store("experiment-runs").env is None
    assert "CHIMERA_PB_RUNS" in get_store("experiment-runs").note


# -- project scope -----------------------------------------------------------


def test_project_state_dir_is_unaffected_by_the_storage_root(_hermetic, monkeypatch):
    monkeypatch.setenv("CHIMERA_HOME", str(_hermetic / "elsewhere"))
    project = _hermetic / "proj"
    assert project_state_dir(project) == project / ".chimera"
    assert store_path("project-state", project) == project / ".chimera"
    assert store_path("project-snapshots", project) == project / ".chimera" / "snapshots"


def test_project_scope_defaults_to_cwd(_hermetic):
    assert store_path("project-memory") == Path.cwd() / ".chimera" / "memory"


# -- config discovery stays anchored on the real home ------------------------


def test_config_discovery_is_not_relocated_by_the_storage_root(_hermetic, monkeypatch):
    """``config.toml`` cannot live inside the root it declares.

    Otherwise ``[storage] root`` would be unreadable after the first read —
    the writer and the reader would disagree the moment it was set.
    """
    from chimera.cli.config_loader import config_path
    from chimera.config.user_config import config_home_dir

    monkeypatch.setenv("CHIMERA_HOME", str(_hermetic / "relocated"))
    anchor = _hermetic / "home" / ".chimera"
    assert user_scope_dir() == anchor
    assert config_home_dir() == anchor
    assert config_path() == anchor / "config.toml"


# -- retention ---------------------------------------------------------------


def test_no_config_means_no_retention(_hermetic):
    for store in all_stores():
        assert store_retention(store.name) == StoreRetention()
        assert not store_retention(store.name).active


def test_storage_retention_is_read(_hermetic):
    _write_config(
        _hermetic / "home" / ".chimera",
        "[storage.sessions]\nretain = 200\nmax-age-days = 90\n",
    )
    policy = store_retention("sessions")
    assert policy == StoreRetention(retain=200, max_age_days=90.0)
    assert policy.active


def test_underscore_key_spelling_is_accepted(_hermetic):
    _write_config(
        _hermetic / "home" / ".chimera", "[storage.sessions]\nmax_age_days = 30\n"
    )
    assert store_retention("sessions").max_age_days == 30.0


def test_dashed_store_name_also_answers_to_underscores(_hermetic):
    _write_config(_hermetic / "home" / ".chimera", "[storage.experiment_runs]\nretain = 3\n")
    assert store_retention("experiment-runs").retain == 3


def test_non_positive_and_malformed_values_disable_the_knob(_hermetic):
    _write_config(
        _hermetic / "home" / ".chimera",
        '[storage.sessions]\nretain = 0\nmax-age-days = "nope"\n',
    )
    assert store_retention("sessions") == StoreRetention()


def test_legacy_tui_cohorts_alias_still_works(_hermetic):
    """Configs written before ``[storage]`` existed must not silently stop."""
    _write_config(
        _hermetic / "home" / ".chimera", "[tui.cohorts]\nretain = 20\nmax-age-days = 30\n"
    )
    assert store_retention("cohorts") == StoreRetention(retain=20, max_age_days=30.0)


def test_cohort_pruner_rides_the_shared_reader(_hermetic):
    """The TUI's own entry point resolves through the same one reader."""
    pytest.importorskip("rich")  # chimera.tui pulls the optional [tui] extra
    pytest.importorskip("textual")
    _write_config(
        _hermetic / "home" / ".chimera", "[tui.cohorts]\nretain = 20\nmax-age-days = 30\n"
    )
    from chimera.tui.cohort import CohortRetention, load_cohort_retention

    assert load_cohort_retention() == CohortRetention(retain=20, max_age_days=30.0)


def test_storage_cohorts_outranks_the_legacy_alias(_hermetic):
    _write_config(
        _hermetic / "home" / ".chimera",
        "[tui.cohorts]\nretain = 20\n\n[storage.cohorts]\nretain = 5\n",
    )
    assert store_retention("cohorts").retain == 5


def test_never_prunable_stores_ignore_retention_config(_hermetic):
    """Structural, not a default: a typo cannot make datasets reclaimable."""
    _write_config(
        _hermetic / "home" / ".chimera",
        "[storage.datasets]\nretain = 1\n\n[storage.function_synthesis]\nretain = 1\n",
    )
    assert store_retention("datasets") == StoreRetention()
    assert store_retention("function_synthesis") == StoreRetention()


def test_broken_config_yields_an_inactive_policy(_hermetic):
    _write_config(_hermetic / "home" / ".chimera", "[storage.sessions\nretain = ")
    assert store_retention("sessions") == StoreRetention()


# -- registry integrity ------------------------------------------------------


def test_unknown_store_fails_loudly(_hermetic):
    with pytest.raises(UnknownStore):
        store_path("not-a-store")
    with pytest.raises(UnknownStore):
        store_retention("not-a-store")


def test_registry_rows_are_well_formed():
    names = [s.name for s in all_stores()]
    assert len(names) == len(set(names)), "store names must be unique across scopes"
    for store in all_stores():
        assert isinstance(store, Store)
        assert store.scope in paths.SCOPES
        assert store.writer, f"{store.name} must declare a writer (or say it has none)"
        assert not store.rel.startswith("/"), f"{store.name} rel must be relative"
        if store.scope == "project":
            assert store.name.startswith("project-")


def test_registry_is_immutable():
    """The registry is the truth; callers get a frozen view of it."""
    assert isinstance(all_stores(), tuple)
    with pytest.raises(Exception):
        all_stores()[0].name = "mutated"  # type: ignore[misc]


def test_the_spec_migration_table_is_fully_declared():
    """Every store the spec's table names is present, with its prunability."""
    declared = {s.name: s for s in all_stores()}
    for name in (
        "datasets",
        "cohorts",
        "sessions",
        "eventlog",
        "history",
        "projects",
        "function_synthesis",
        "tasks",
        "experiment-runs",
    ):
        assert name in declared, name
        assert declared[name].scope == "user"
    assert declared["project-state"].scope == "project"
    assert declared["project-checkpoints"].rel == "checkpoints"
    # The three the spec marks never-prunable.
    assert declared["datasets"].prunable is False
    assert declared["function_synthesis"].prunable is False
    # ...and `history`, which the M1 sweep found is a file, not a directory.
    assert declared["history"].prunable is False


def test_the_live_checkpoint_writer_now_resolves_through_the_registry(tmp_path):
    """The spec said the checkpoint writer was deleted. It was not — M1 pinned
    that; M3 moved it here.

    ``LocalEnvironment.setup`` used to create ``<workdir>/.chimera_checkpoints``
    unconditionally — *beside* ``<project>/.chimera``, so outside both scope
    roots. It now resolves the store through :func:`store_path` and creates
    nothing until something checkpoints. The legacy name stays in the store note
    because pre-M3 trees are still on disk and M2's orphan scan has to look for
    it by name.
    """
    from chimera.env.local import LEGACY_CHECKPOINT_DIRNAME, LocalEnvironment

    env = LocalEnvironment(workdir=str(tmp_path / "ws"))
    env.setup()
    assert env._checkpoint_dir == store_path("project-checkpoints", tmp_path / "ws")
    assert env._checkpoint_dir == tmp_path / "ws" / ".chimera" / "checkpoints"
    # setup() no longer leaves a directory behind at either location.
    assert not env._checkpoint_dir.exists()
    assert not (tmp_path / "ws" / LEGACY_CHECKPOINT_DIRNAME).exists()

    note = get_store("project-checkpoints").note
    assert ".chimera_checkpoints" in note
    assert get_store("project-checkpoints").rel == "checkpoints"


def test_state_dirname_is_the_one_definition():
    assert STATE_DIRNAME == ".chimera"
    assert user_scope_dir(Path("/tmp/x")) == Path("/tmp/x/.chimera")
    assert project_state_dir("/tmp/p") == Path("/tmp/p/.chimera")


# -- the migrated call sites still land on the registry ----------------------


def test_migrated_writers_agree_with_the_registry(_hermetic, monkeypatch):
    """Spot-check writers across the stack against a relocated root.

    Each of these composed its own ``Path.home() / ".chimera" / …`` before M1.
    Pointing the root elsewhere proves they now go through the accessors rather
    than having merely been re-spelled.
    """
    root = _hermetic / "relocated"
    monkeypatch.setenv("CHIMERA_HOME", str(root))

    from chimera.cli.agent_teams import teams_root
    from chimera.eval.datasets import staging_dir
    from chimera.mcp.oauth import TokenStore
    from chimera.mink.runs import default_eventlog_root
    from chimera.otter.server_pidfile import default_pidfile_dir
    from chimera.otter.snapshot import default_snapshot_root
    from chimera.otter.worktree import default_worktree_root
    from chimera.sessions.share import _default_export_dir
    from chimera.stoat.hooks import default_hooks_path
    from chimera.stoat.plan_mode import default_plans_dir
    from chimera.tools.cron_tools import _jobs_dir

    assert default_eventlog_root() == root / "eventlog"
    assert _default_export_dir() == root / "exports"
    assert default_plans_dir() == root / "plans"
    assert default_hooks_path() == root / "stoat" / "hooks.json"
    assert teams_root() == root / "teams"
    assert _jobs_dir() == root / "cron"
    assert staging_dir() == root / "datasets"
    assert default_pidfile_dir() == root / "run"
    assert default_snapshot_root() == root / "snapshots"
    assert default_worktree_root() == root / "worktrees"
    assert TokenStore().base_dir == root / "tokens"


def test_migrated_tui_writer_agrees_with_the_registry(_hermetic, monkeypatch):
    """Same check for the cohort store, which lives behind the [tui] extra."""
    pytest.importorskip("rich")
    pytest.importorskip("textual")
    root = _hermetic / "relocated"
    monkeypatch.setenv("CHIMERA_HOME", str(root))
    from chimera.tui.cohort import default_cohort_root

    assert default_cohort_root() == root / "cohorts"


def test_todo_tool_paths_split_project_and_user_scope(_hermetic, monkeypatch):
    root = _hermetic / "relocated"
    monkeypatch.setenv("CHIMERA_HOME", str(root))
    from chimera.tools.todo import _project_hash, _project_todo_path, _user_todo_path

    project = str(_hermetic / "proj")
    assert _project_todo_path(project) == Path(project) / ".chimera" / "todo.json"
    assert _user_todo_path(project) == (
        root / "projects" / _project_hash(project) / "todo.json"
    )
