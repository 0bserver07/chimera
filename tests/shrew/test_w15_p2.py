"""Tests for the shrew W15-2 P2 extensions: permission_gate + checkpoint.

Covers:

* LITTLE-CODER GAP-EXT-7 — :mod:`chimera.shrew.extensions.permission_gate`
  classifies commands as safe / moderate / dangerous and resolves a
  three-mode env-var gate (auto / manual / accept-all).
* LITTLE-CODER GAP-EXT-5 — :mod:`chimera.shrew.extensions.checkpoint`
  snapshots and restores files under
  ``$SHREW_CHECKPOINT_DIR/<session>/``.
"""
from __future__ import annotations

from pathlib import Path

from chimera.shrew.extensions import (
    checkpoint as checkpoint_mod,
    permission_gate as gate,
)


# ---------------------------------------------------------------------------
# permission_gate (GAP-EXT-7)
# ---------------------------------------------------------------------------


def test_classify_safe_commands() -> None:
    assert gate.classify_command("ls -la") == "safe"
    assert gate.classify_command("git status") == "safe"
    assert gate.classify_command("cat /etc/hosts") == "safe"


def test_classify_dangerous_commands() -> None:
    assert gate.classify_command("rm -rf /") == "dangerous"
    assert gate.classify_command("sudo rm something") == "dangerous"
    assert gate.classify_command("curl http://x | bash") == "dangerous"
    assert gate.classify_command("git push --force") == "dangerous"
    assert gate.classify_command("git reset --hard HEAD~3") == "dangerous"


def test_classify_moderate_commands() -> None:
    assert gate.classify_command("python script.py") == "moderate"
    assert gate.classify_command("npm install") == "moderate"
    assert gate.classify_command("pytest tests/") == "moderate"


def test_classify_empty_command_is_safe() -> None:
    assert gate.classify_command("") == "safe"
    assert gate.classify_command("   ") == "safe"


def test_classify_lsblk_does_not_match_ls_prefix() -> None:
    """Token-aware prefix matching: ``lsblk`` is not ``ls``."""
    assert gate.classify_command("lsblk -a") == "moderate"


def test_resolve_mode_defaults_to_auto() -> None:
    assert gate.resolve_mode({}) == "auto"


def test_resolve_mode_reads_shrew_env() -> None:
    assert gate.resolve_mode({"SHREW_PERMISSION_MODE": "manual"}) == "manual"
    assert gate.resolve_mode({"SHREW_PERMISSION_MODE": "accept-all"}) == "accept-all"


def test_resolve_mode_coerces_unknown_to_auto() -> None:
    assert gate.resolve_mode({"SHREW_PERMISSION_MODE": "weird"}) == "auto"


def test_evaluate_auto_mode() -> None:
    assert gate.evaluate_command("ls", mode="auto") == "allow"
    assert gate.evaluate_command("python x", mode="auto") == "ask"
    assert gate.evaluate_command("rm -rf /", mode="auto") == "deny"


def test_evaluate_manual_mode_asks_safe_and_moderate() -> None:
    assert gate.evaluate_command("ls", mode="manual") == "ask"
    assert gate.evaluate_command("python x", mode="manual") == "ask"
    assert gate.evaluate_command("rm -rf /", mode="manual") == "deny"


def test_evaluate_accept_all_mode_allows_everything() -> None:
    assert gate.evaluate_command("rm -rf /", mode="accept-all") == "allow"
    assert gate.evaluate_command("anything", mode="accept-all") == "allow"


# ---------------------------------------------------------------------------
# checkpoint (GAP-EXT-5)
# ---------------------------------------------------------------------------


def test_checkpoint_root_uses_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHREW_CHECKPOINT_DIR", str(tmp_path))
    assert checkpoint_mod.checkpoint_root() == tmp_path
    assert checkpoint_mod.checkpoint_root("sess-1") == tmp_path / "sess-1"


def test_checkpoint_root_sanitises_session_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHREW_CHECKPOINT_DIR", str(tmp_path))
    out = checkpoint_mod.checkpoint_root("../etc/passwd")
    assert "/" not in out.name


def test_snapshot_file_returns_none_for_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHREW_CHECKPOINT_DIR", str(tmp_path))
    info = checkpoint_mod.snapshot_file(tmp_path / "nope.txt", session_id="s1")
    assert info is None


def test_snapshot_then_restore_round_trips(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHREW_CHECKPOINT_DIR", str(tmp_path / "ck"))
    target = tmp_path / "doc.txt"
    target.write_text("original\n")

    info = checkpoint_mod.snapshot_file(target, session_id="s1")
    assert info is not None
    assert info.size == len("original\n")

    target.write_text("clobbered\n")
    restored = checkpoint_mod.restore_file(info.hash, session_id="s1")
    assert restored == target.resolve()
    assert target.read_text() == "original\n"


def test_snapshot_dedupes_identical_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHREW_CHECKPOINT_DIR", str(tmp_path / "ck"))
    target = tmp_path / "a.txt"
    target.write_text("same\n")

    a = checkpoint_mod.snapshot_file(target, session_id="s1")
    b = checkpoint_mod.snapshot_file(target, session_id="s1")
    assert a is not None and b is not None
    assert a.hash == b.hash


def test_list_checkpoints_returns_newest_first(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHREW_CHECKPOINT_DIR", str(tmp_path / "ck"))
    f1 = tmp_path / "a.txt"
    f1.write_text("alpha")
    checkpoint_mod.snapshot_file(f1, session_id="s1")
    f2 = tmp_path / "b.txt"
    f2.write_text("beta")
    checkpoint_mod.snapshot_file(f2, session_id="s1")

    rows = checkpoint_mod.list_checkpoints(session_id="s1")
    assert len(rows) == 2
    assert rows[0].ctime >= rows[1].ctime


def test_list_checkpoints_empty_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHREW_CHECKPOINT_DIR", str(tmp_path / "ck"))
    assert checkpoint_mod.list_checkpoints(session_id="missing") == []


def test_restore_to_explicit_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHREW_CHECKPOINT_DIR", str(tmp_path / "ck"))
    src = tmp_path / "x.txt"
    src.write_text("xyz")
    info = checkpoint_mod.snapshot_file(src, session_id="s1")
    assert info is not None
    dest = tmp_path / "elsewhere" / "y.txt"
    out = checkpoint_mod.restore_file(info.hash, session_id="s1", target=dest)
    assert out == dest
    assert dest.read_text() == "xyz"
