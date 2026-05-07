"""Tests for ``chimera otter worktree {create|list|remove}`` (W14-2)."""
from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from chimera.otter import worktree as wt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Iterator[Path]:
    """Initialize a clean git repo with a single commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    yield repo


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


# ---------------------------------------------------------------------------
# default_worktree_root / manifest_path
# ---------------------------------------------------------------------------


def test_default_worktree_root_uses_home(fake_home: Path) -> None:
    assert wt.default_worktree_root() == fake_home / ".chimera" / "worktrees"


def test_manifest_path_default_under_root(fake_home: Path) -> None:
    assert wt.manifest_path() == fake_home / ".chimera" / "worktrees" / "index.json"


# ---------------------------------------------------------------------------
# load_manifest / save_manifest
# ---------------------------------------------------------------------------


def test_load_manifest_returns_empty_when_missing(fake_home: Path) -> None:
    assert wt.load_manifest() == []


def test_save_load_round_trip(fake_home: Path) -> None:
    rec = wt.WorktreeRecord(
        name="alpha",
        path="/tmp/wt-alpha",
        branch="otter/alpha",
        repo="/tmp/repo",
        created_at="2026-05-07T00:00:00Z",
    )
    wt.save_manifest([rec])
    loaded = wt.load_manifest()
    assert len(loaded) == 1
    assert loaded[0].name == "alpha"
    assert loaded[0].branch == "otter/alpha"


def test_load_manifest_drops_malformed_entries(fake_home: Path) -> None:
    target = wt.manifest_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([
            {"name": "ok", "path": "/p", "branch": "b", "repo": "/r", "created_at": "x"},
            "not a dict",
            {"missing": "fields"},
        ])
    )
    loaded = wt.load_manifest()
    assert [r.name for r in loaded] == ["ok"]


def test_load_manifest_handles_garbage_json(fake_home: Path) -> None:
    target = wt.manifest_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not json{{{")
    assert wt.load_manifest() == []


# ---------------------------------------------------------------------------
# create_worktree
# ---------------------------------------------------------------------------


def test_create_worktree_makes_directory_and_branch(
    tmp_repo: Path, fake_home: Path
) -> None:
    record = wt.create_worktree("alpha", repo=tmp_repo)
    assert record.name == "alpha"
    assert record.branch == "otter/alpha"
    assert Path(record.path).exists()
    # Manifest should have it.
    loaded = wt.load_manifest()
    assert [r.name for r in loaded] == ["alpha"]
    # Git knows about it.
    out = _git(tmp_repo, "worktree", "list")
    assert "alpha" in out.stdout


def test_create_worktree_uses_explicit_branch(
    tmp_repo: Path, fake_home: Path
) -> None:
    record = wt.create_worktree("beta", branch="feature/beta", repo=tmp_repo)
    assert record.branch == "feature/beta"


def test_create_worktree_rejects_path_separator(
    tmp_repo: Path, fake_home: Path
) -> None:
    with pytest.raises(ValueError):
        wt.create_worktree("a/b", repo=tmp_repo)


def test_create_worktree_rejects_dotdot(
    tmp_repo: Path, fake_home: Path
) -> None:
    with pytest.raises(ValueError):
        wt.create_worktree("..wat", repo=tmp_repo)


def test_create_worktree_errors_on_non_repo(
    tmp_path: Path, fake_home: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        wt.create_worktree("x", repo=tmp_path)


def test_create_worktree_idempotent_on_repeat(
    tmp_repo: Path, fake_home: Path
) -> None:
    wt.create_worktree("alpha", repo=tmp_repo)
    # Re-create with same name → manifest still has one entry.
    wt.create_worktree("alpha", repo=tmp_repo)
    loaded = wt.load_manifest()
    assert len(loaded) == 1


# ---------------------------------------------------------------------------
# remove_worktree
# ---------------------------------------------------------------------------


def test_remove_worktree_drops_manifest_and_directory(
    tmp_repo: Path, fake_home: Path
) -> None:
    record = wt.create_worktree("alpha", repo=tmp_repo)
    assert Path(record.path).exists()
    removed = wt.remove_worktree("alpha")
    assert removed is True
    assert wt.load_manifest() == []


def test_remove_worktree_returns_false_for_missing(fake_home: Path) -> None:
    assert wt.remove_worktree("ghost") is False


def test_remove_worktree_cleans_manifest_when_path_already_gone(
    tmp_repo: Path, fake_home: Path
) -> None:
    record = wt.create_worktree("alpha", repo=tmp_repo)
    # Remove the directory out-of-band.
    import shutil

    shutil.rmtree(record.path)
    # Should still drop the manifest entry.
    removed = wt.remove_worktree("alpha")
    assert removed is True
    assert wt.load_manifest() == []


# ---------------------------------------------------------------------------
# dispatch_worktree
# ---------------------------------------------------------------------------


def _ns(**fields: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "sub_action": None,
        "sub_target": None,
        "sessions_json": False,
        "worktree_json": False,
        "worktree_branch": None,
        "worktree_repo": None,
        "worktree_force": False,
        "cwd": None,
    }
    base.update(fields)
    return argparse.Namespace(**base)


def test_dispatch_list_empty_prints_friendly_notice(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = wt.dispatch_worktree(_ns(sub_action="list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "no worktrees" in out


def test_dispatch_create_requires_name(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = wt.dispatch_worktree(_ns(sub_action="create"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "requires <NAME>" in err


def test_dispatch_create_then_list(
    tmp_repo: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = wt.dispatch_worktree(
        _ns(sub_action="create", sub_target="gamma", worktree_repo=str(tmp_repo))
    )
    assert rc == 0
    rc = wt.dispatch_worktree(_ns(sub_action="list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "gamma" in out


def test_dispatch_remove_unknown_returns_1(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = wt.dispatch_worktree(_ns(sub_action="remove", sub_target="ghost"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "no such worktree" in err


def test_dispatch_unknown_action_returns_2(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = wt.dispatch_worktree(_ns(sub_action="frobnicate"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown" in err


def test_dispatch_list_json_emits_array(
    tmp_repo: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wt.dispatch_worktree(
        _ns(sub_action="create", sub_target="delta", worktree_repo=str(tmp_repo))
    )
    capsys.readouterr()  # discard
    rc = wt.dispatch_worktree(_ns(sub_action="list", worktree_json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload[0]["name"] == "delta"


def test_reset_for_tests_wipes_manifest(fake_home: Path) -> None:
    rec = wt.WorktreeRecord(
        name="x", path="/p", branch="b", repo="/r", created_at="z"
    )
    wt.save_manifest([rec])
    assert wt.load_manifest() != []
    wt._reset_for_tests()
    assert wt.load_manifest() == []
