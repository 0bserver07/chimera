"""Checkpoints are bounded, registry-scoped, and never prune uninvited (M3).

The incident this file encodes: ``LocalEnvironment.setup`` created
``<workdir>/.chimera_checkpoints`` on every setup and ``env.checkpoint()``
filled it with a *full* tree copy. On the owner's machine that produced a
2.0 GB checkpoint containing ``.venv``, ``site/node_modules`` (759 MB) and a
944 MB duplicate of an unrelated run-output directory — an active unbounded
writer, not dead residue, so it regenerated after every cleanup.

:func:`test_canary_a_checkpoint_never_contains_a_venv_or_node_modules` is that
incident as a permanent regression test. It fails against the pre-M3 writer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.config.paths import store_path
from chimera.env.local import (
    LEGACY_CHECKPOINT_DIRNAME,
    LargeCheckpointWarning,
    LocalEnvironment,
)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """Isolate retention lookups from the developer's real config chain.

    ``store_retention`` reads ``~/.chimera/config.toml`` and ``./.chimera/``.
    Without this, a machine that has configured checkpoint retention would see
    the "nothing is deleted by default" tests delete things.
    """
    monkeypatch.delenv("CHIMERA_HOME", raising=False)
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    cwd = tmp_path / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(cwd)


def _workspace(root: Path) -> LocalEnvironment:
    """A workspace shaped like the one that produced the 2.0 GB checkpoint."""
    env = LocalEnvironment(workdir=str(root), test_cmd="true")
    env.setup()
    # Real source the checkpoint must capture.
    env.write_file("app.py", "print('hello')\n")
    env.write_file("src/core.py", "VALUE = 1\n")
    env.write_file("README.md", "# project\n")
    # Bulk the checkpoint must refuse.
    env.write_file(".venv/lib/python3.12/site-packages/dep/__init__.py", "x" * 4096)
    env.write_file(".venv/bin/python", "#!/bin/sh\n")
    env.write_file("node_modules/left-pad/index.js", "y" * 4096)
    env.write_file("src/__pycache__/core.cpython-312.pyc", "z" * 4096)
    # Nested, not top-level: the incident's largest single component was
    # `site/node_modules` at 759 MB, one level down.
    env.write_file("site/index.astro", "---\n---\n")
    env.write_file("site/node_modules/astro/dist/index.js", "w" * 4096)
    return env


def _relative_files(root: Path) -> set[str]:
    return {
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
    }


# -- the canary --------------------------------------------------------------


def test_canary_a_checkpoint_never_contains_a_venv_or_node_modules(tmp_path):
    """The 2.0 GB incident, as a regression test.

    A checkpoint of a tree holding ``.venv``, ``node_modules`` and
    ``__pycache__`` alongside real source must contain **none** of the three and
    **all** of the source, and ``restore`` must round-trip the source exactly.

    Deliberately reads the checkpoint through ``env._checkpoint_dir`` rather
    than the registry, so this asserts the *exclusion* property alone and fails
    for that reason and no other. Where the store lives is
    :func:`test_checkpoints_land_in_the_registry_store`'s job.
    """
    env = _workspace(tmp_path / "ws")
    cp_id = env.checkpoint()

    assert env._checkpoint_dir is not None
    cp_dir = env._checkpoint_dir / cp_id
    captured = _relative_files(cp_dir)

    assert captured == {"app.py", "src/core.py", "README.md", "site/index.astro"}
    # Stated separately from the equality above so a failure names the culprit.
    for excluded in (".venv", "node_modules", "__pycache__"):
        assert not any(excluded in path for path in captured), excluded
    assert not (cp_dir / ".venv").exists()
    assert not (cp_dir / "node_modules").exists()
    # The nested case — a top-level-only exclusion would have copied this one.
    assert not (cp_dir / "site" / "node_modules").exists()

    # Round-trip: source is restored byte-for-byte.
    env.write_file("app.py", "print('broken')\n")
    env.write_file("src/core.py", "VALUE = 999\n")
    Path(env.workdir / "README.md").unlink()

    env.restore(cp_id)

    assert env.read_file("app.py") == "print('hello')\n"
    assert env.read_file("src/core.py") == "VALUE = 1\n"
    assert env.read_file("README.md") == "# project\n"


def test_restore_leaves_the_excluded_directories_alone(tmp_path):
    """Excluding is symmetric: not captured, and therefore not destroyed.

    ``restore`` clears the workspace before copying back. If it cleared what the
    checkpoint refused to capture, the first restore in a project would delete
    the user's virtualenv and never bring it back — strictly worse than the bug
    being fixed.
    """
    env = _workspace(tmp_path / "ws")
    cp_id = env.checkpoint()

    env.restore(cp_id)

    assert (env.workdir / ".venv" / "bin" / "python").exists()
    assert (env.workdir / "node_modules" / "left-pad" / "index.js").exists()
    assert env.read_file(".venv/bin/python") == "#!/bin/sh\n"


def test_the_checkpoint_is_smaller_than_the_workspace_it_snapshots(tmp_path):
    """The size claim, measured rather than asserted in prose."""
    from chimera.env.local import _tree_size

    env = _workspace(tmp_path / "ws")
    cp_id = env.checkpoint()
    cp_dir = store_path("project-checkpoints", tmp_path / "ws") / cp_id

    bulk = _tree_size(env.workdir / ".venv") + _tree_size(env.workdir / "node_modules")
    assert bulk > 0
    assert _tree_size(cp_dir) < bulk


# -- where checkpoints live --------------------------------------------------


def test_checkpoints_land_in_the_registry_store(tmp_path):
    env = _workspace(tmp_path / "ws")
    cp_id = env.checkpoint()

    expected = tmp_path / "ws" / ".chimera" / "checkpoints" / cp_id
    assert expected.is_dir()
    assert expected == store_path("project-checkpoints", tmp_path / "ws") / cp_id
    assert not (tmp_path / "ws" / LEGACY_CHECKPOINT_DIRNAME).exists()


def test_setup_creates_no_directory_until_something_checkpoints(tmp_path):
    """The pre-M3 writer created a directory on *every* setup, checkpoint or not."""
    env = LocalEnvironment(workdir=str(tmp_path / "ws"), test_cmd="true")
    env.setup()

    assert not (tmp_path / "ws" / ".chimera").exists()
    assert not (tmp_path / "ws" / LEGACY_CHECKPOINT_DIRNAME).exists()

    env.write_file("a.py", "x")
    env.checkpoint()
    assert (tmp_path / "ws" / ".chimera" / "checkpoints" / "0").is_dir()


def test_a_checkpoint_never_nests_the_checkpoint_store(tmp_path):
    """The store lives under ``.chimera`` now — copying it would nest forever."""
    env = _workspace(tmp_path / "ws")
    env.checkpoint()
    second = env.checkpoint()

    cp_dir = store_path("project-checkpoints", tmp_path / "ws") / second
    assert not (cp_dir / ".chimera").exists()


def test_project_state_survives_a_restore(tmp_path):
    """``.chimera`` holds todo.json and settings — a restore must not wipe it."""
    env = _workspace(tmp_path / "ws")
    cp_id = env.checkpoint()
    (tmp_path / "ws" / ".chimera" / "todo.json").write_text("[]", encoding="utf-8")

    env.restore(cp_id)

    assert (tmp_path / "ws" / ".chimera" / "todo.json").read_text() == "[]"


# -- pre-M3 checkpoints stay readable ----------------------------------------


def test_a_legacy_checkpoint_is_still_restorable(tmp_path):
    """Old trees are read, never stranded and never deleted.

    Moving the store would otherwise orphan every checkpoint taken before this
    change. The standing rule is archive or relocate, never delete.
    """
    ws = tmp_path / "ws"
    env = LocalEnvironment(workdir=str(ws), test_cmd="true")
    env.setup()

    legacy = ws / LEGACY_CHECKPOINT_DIRNAME / "0"
    legacy.mkdir(parents=True)
    (legacy / "app.py").write_text("the old content\n", encoding="utf-8")

    env.write_file("app.py", "the new content\n")
    env.restore("0")

    assert env.read_file("app.py") == "the old content\n"
    assert legacy.exists(), "a legacy checkpoint must never be deleted"


def test_new_ids_never_collide_with_legacy_ids(tmp_path):
    """ID allocation spans both locations, or ``0`` would mean two snapshots."""
    ws = tmp_path / "ws"
    env = LocalEnvironment(workdir=str(ws), test_cmd="true")
    env.setup()
    for old in ("0", "1", "2"):
        (ws / LEGACY_CHECKPOINT_DIRNAME / old).mkdir(parents=True)

    env.write_file("app.py", "x")
    assert env.checkpoint() == "3"


def test_an_unknown_id_still_fails_loudly(tmp_path):
    env = _workspace(tmp_path / "ws")
    with pytest.raises(ValueError, match="not found"):
        env.restore("41")


# -- retention is opt-in -----------------------------------------------------


def test_nothing_is_ever_deleted_without_configured_retention(tmp_path):
    """No ``[storage]`` config: every checkpoint is kept, forever.

    The project's standing rule — retention is opt-in, nobody loses work they
    did not ask to discard.
    """
    env = _workspace(tmp_path / "ws")
    ids = [env.checkpoint() for _ in range(6)]

    store = store_path("project-checkpoints", tmp_path / "ws")
    assert sorted(d.name for d in store.iterdir()) == sorted(ids)
    assert len(ids) == 6


def test_retain_keeps_the_newest_n_when_configured(tmp_path):
    """``[storage.checkpoints] retain`` is honoured through the M1 config chain."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / ".chimera").mkdir()
    (ws / ".chimera" / "config.toml").write_text(
        "[storage.checkpoints]\nretain = 2\n", encoding="utf-8"
    )

    env = _workspace(ws)
    for _ in range(5):
        env.checkpoint()

    store = store_path("project-checkpoints", ws)
    assert sorted(d.name for d in store.iterdir()) == ["3", "4"]


def test_retention_never_reaches_the_legacy_directory(tmp_path):
    """Only the registry store is prunable — an unregistered path never is."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / ".chimera").mkdir()
    (ws / ".chimera" / "config.toml").write_text(
        "[storage.checkpoints]\nretain = 1\n", encoding="utf-8"
    )
    for old in ("0", "1"):
        (ws / LEGACY_CHECKPOINT_DIRNAME / old).mkdir(parents=True)

    env = _workspace(ws)
    env.checkpoint()
    env.checkpoint()

    assert (ws / LEGACY_CHECKPOINT_DIRNAME / "0").exists()
    assert (ws / LEGACY_CHECKPOINT_DIRNAME / "1").exists()


def test_the_checkpoint_just_written_is_never_pruned(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / ".chimera").mkdir()
    (ws / ".chimera" / "config.toml").write_text(
        "[storage.checkpoints]\nretain = 1\n", encoding="utf-8"
    )

    env = _workspace(ws)
    latest = env.checkpoint()
    assert (store_path("project-checkpoints", ws) / latest).is_dir()
    assert env.read_file("app.py") == "print('hello')\n"
    env.restore(latest)
    assert env.read_file("app.py") == "print('hello')\n"


# -- a large checkpoint announces itself -------------------------------------


def test_a_large_checkpoint_warns(tmp_path, monkeypatch):
    """Past the threshold the checkpoint is still written — and says so."""
    monkeypatch.setattr("chimera.env.local.CHECKPOINT_SIZE_WARN_BYTES", 1024)

    env = LocalEnvironment(workdir=str(tmp_path / "ws"), test_cmd="true")
    env.setup()
    env.write_file("big.txt", "x" * 4096)

    with pytest.warns(LargeCheckpointWarning, match="retain"):
        cp_id = env.checkpoint()

    assert (store_path("project-checkpoints", tmp_path / "ws") / cp_id).is_dir()


def test_a_small_checkpoint_is_silent(tmp_path, recwarn):
    env = _workspace(tmp_path / "ws")
    env.checkpoint()
    assert [w for w in recwarn if issubclass(w.category, LargeCheckpointWarning)] == []


def test_excluded_bulk_does_not_count_toward_the_warning(tmp_path, monkeypatch, recwarn):
    """The threshold measures the checkpoint, not the workspace.

    A 64-byte source tree beside a 64 KB ``.venv`` must not warn even at an
    8 KB threshold: the whole point is that the bulk was never copied.
    """
    monkeypatch.setattr("chimera.env.local.CHECKPOINT_SIZE_WARN_BYTES", 8192)

    env = LocalEnvironment(workdir=str(tmp_path / "ws"), test_cmd="true")
    env.setup()
    env.write_file("app.py", "x" * 64)
    env.write_file(".venv/lib/big.bin", "y" * 65536)

    env.checkpoint()

    assert [w for w in recwarn if issubclass(w.category, LargeCheckpointWarning)] == []
