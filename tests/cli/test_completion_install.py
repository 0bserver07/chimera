"""Tests for ``chimera completion install`` — auto-wire the completion script.

Coverage:
- bash install with explicit ``--rc-path`` writes script + marker block.
- Re-running ``install`` is idempotent (single marker block).
- ``--undo`` removes both the script file and the marker block.
- ``--dry-run`` prints planned paths without touching disk.
- fish install writes to ``~/.config/fish/completions/`` and skips rc edits.
- ``--shell auto`` detects ``zsh`` from ``$SHELL=/bin/zsh``.
- ``--shell auto`` with an unrecognized ``$SHELL`` exits with rc=1.
- The CLI dispatch path (``chimera completion install``) reaches the same code.

All tests use ``tmp_path`` as a sandboxed ``$HOME`` so we never write to the
real user dotfiles.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from chimera.cli.completion import (
    MARKER_BEGIN,
    MARKER_END,
    detect_shell,
    install,
)
from chimera.cli.main import main


# --------------------------------------------------------------------------- #
# detect_shell — shell detection from $SHELL
# --------------------------------------------------------------------------- #
class TestDetectShell:
    """``detect_shell`` maps a $SHELL path to a canonical shell name."""

    def test_bash_path(self) -> None:
        assert detect_shell("/bin/bash") == "bash"

    def test_zsh_path(self) -> None:
        assert detect_shell("/bin/zsh") == "zsh"
        assert detect_shell("/usr/local/bin/zsh") == "zsh"

    def test_fish_path(self) -> None:
        assert detect_shell("/usr/local/bin/fish") == "fish"

    def test_unknown_returns_none(self) -> None:
        assert detect_shell("/bin/tcsh") is None
        assert detect_shell("/bin/sh") is None

    def test_empty_or_none(self) -> None:
        assert detect_shell("") is None
        assert detect_shell(None) is None


# --------------------------------------------------------------------------- #
# install — bash
# --------------------------------------------------------------------------- #
class TestInstallBash:
    """Installing for bash writes a script + marker block in the rc file."""

    def test_writes_script_and_marker(self, tmp_path: Path) -> None:
        rc = tmp_path / ".bashrc"
        rc.write_text("# user's existing rc\n")
        buf = io.StringIO()
        rc_code = install(
            shell="bash",
            rc_path=str(rc),
            home=tmp_path,
            out=buf,
        )
        assert rc_code == 0
        script = tmp_path / ".chimera" / "completion" / "bash.sh"
        assert script.exists()
        assert script.read_text().startswith("# chimera bash completion")
        rc_text = rc.read_text()
        assert MARKER_BEGIN in rc_text
        assert MARKER_END in rc_text
        # Original rc content is preserved.
        assert "# user's existing rc" in rc_text
        # The source line points at the actual script path.
        assert str(script) in rc_text

    def test_idempotent_second_run(self, tmp_path: Path) -> None:
        rc = tmp_path / ".bashrc"
        for _ in range(2):
            install(shell="bash", rc_path=str(rc), home=tmp_path, out=io.StringIO())
        rc_text = rc.read_text()
        # Exactly one marker block, even after running twice.
        assert rc_text.count(MARKER_BEGIN) == 1
        assert rc_text.count(MARKER_END) == 1

    def test_undo_removes_script_and_marker(self, tmp_path: Path) -> None:
        rc = tmp_path / ".bashrc"
        rc.write_text("# pre-existing\n")
        install(shell="bash", rc_path=str(rc), home=tmp_path, out=io.StringIO())
        script = tmp_path / ".chimera" / "completion" / "bash.sh"
        assert script.exists()
        assert MARKER_BEGIN in rc.read_text()

        # Now uninstall.
        rc_code = install(
            shell="bash",
            rc_path=str(rc),
            undo=True,
            home=tmp_path,
            out=io.StringIO(),
        )
        assert rc_code == 0
        assert not script.exists()
        rc_text = rc.read_text()
        assert MARKER_BEGIN not in rc_text
        assert MARKER_END not in rc_text
        # Pre-existing user content survives the unwire.
        assert "# pre-existing" in rc_text

    def test_undo_when_nothing_installed(self, tmp_path: Path) -> None:
        rc = tmp_path / ".bashrc"
        rc.write_text("untouched\n")
        # Idempotent: undo when nothing is wired returns 0 cleanly.
        rc_code = install(
            shell="bash",
            rc_path=str(rc),
            undo=True,
            home=tmp_path,
            out=io.StringIO(),
        )
        assert rc_code == 0
        assert rc.read_text() == "untouched\n"

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        rc = tmp_path / ".bashrc"
        buf = io.StringIO()
        rc_code = install(
            shell="bash",
            rc_path=str(rc),
            dry_run=True,
            home=tmp_path,
            out=buf,
        )
        assert rc_code == 0
        # No files should have appeared on disk.
        assert not rc.exists()
        assert not (tmp_path / ".chimera").exists()
        # The output should mention both planned paths.
        out = buf.getvalue()
        assert "[dry-run]" in out
        assert "bash.sh" in out
        assert ".bashrc" in out


# --------------------------------------------------------------------------- #
# install — fish
# --------------------------------------------------------------------------- #
class TestInstallFish:
    """Fish autoloads from ~/.config/fish/completions/ — no rc edits."""

    def test_fish_writes_to_completions_dir(self, tmp_path: Path) -> None:
        buf = io.StringIO()
        rc_code = install(shell="fish", home=tmp_path, out=buf)
        assert rc_code == 0
        script = tmp_path / ".config" / "fish" / "completions" / "chimera.fish"
        assert script.exists()
        # Fish completions use ``complete -c chimera``.
        assert "complete -c chimera" in script.read_text()
        # No rc file should have been created/modified.
        assert not (tmp_path / ".bashrc").exists()
        assert not (tmp_path / ".zshrc").exists()
        # Status output mentions the autoload behavior.
        assert "autoload" in buf.getvalue().lower()

    def test_fish_undo_removes_script(self, tmp_path: Path) -> None:
        install(shell="fish", home=tmp_path, out=io.StringIO())
        script = tmp_path / ".config" / "fish" / "completions" / "chimera.fish"
        assert script.exists()
        rc_code = install(
            shell="fish", undo=True, home=tmp_path, out=io.StringIO()
        )
        assert rc_code == 0
        assert not script.exists()


# --------------------------------------------------------------------------- #
# install — auto-detect
# --------------------------------------------------------------------------- #
class TestAutoDetect:
    """``--shell auto`` reads $SHELL to pick the target shell."""

    def test_auto_zsh(self, tmp_path: Path) -> None:
        rc = tmp_path / ".zshrc"
        buf = io.StringIO()
        rc_code = install(
            shell="auto",
            rc_path=str(rc),
            home=tmp_path,
            env_shell="/bin/zsh",
            out=buf,
        )
        assert rc_code == 0
        # Auto-detection should have picked zsh, so the zsh script appears.
        script = tmp_path / ".chimera" / "completion" / "zsh.sh"
        assert script.exists()
        assert "#compdef chimera" in script.read_text()
        assert MARKER_BEGIN in rc.read_text()

    def test_auto_bash(self, tmp_path: Path) -> None:
        rc = tmp_path / ".bashrc"
        rc_code = install(
            shell="auto",
            rc_path=str(rc),
            home=tmp_path,
            env_shell="/usr/local/bin/bash",
            out=io.StringIO(),
        )
        assert rc_code == 0
        assert (tmp_path / ".chimera" / "completion" / "bash.sh").exists()

    def test_auto_unknown_shell_returns_1(self, tmp_path: Path) -> None:
        buf = io.StringIO()
        rc_code = install(
            shell="auto",
            home=tmp_path,
            env_shell="/bin/tcsh",
            out=buf,
        )
        assert rc_code == 1
        # Friendly message includes a hint.
        msg = buf.getvalue()
        assert "auto-detect" in msg or "Could not" in msg
        assert "--shell" in msg
        # No files were written despite the failure.
        assert not (tmp_path / ".chimera").exists()

    def test_auto_with_no_env_shell_returns_1(self, tmp_path: Path) -> None:
        # $SHELL unset (None) is also a friendly error.
        buf = io.StringIO()
        rc_code = install(
            shell="auto",
            home=tmp_path,
            env_shell=None,
            out=buf,
        )
        assert rc_code == 1
        assert "--shell" in buf.getvalue()


# --------------------------------------------------------------------------- #
# CLI dispatch — exercise the argparse → run() → install() path
# --------------------------------------------------------------------------- #
def _run_cli_capture(*argv: str) -> tuple[int, str]:
    """Invoke ``chimera`` in-process and return ``(rc, stdout)``."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(list(argv))
    return rc, buf.getvalue()


class TestCliDispatch:
    """``chimera completion install ...`` reaches the install handler."""

    def test_dry_run_via_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Steer Path.home() at the tmp dir so the dry-run output prints
        # paths under tmp_path. We don't write anything in dry-run mode,
        # but we still want the printed paths to be the sandboxed ones.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        rc, out = _run_cli_capture(
            "completion", "install",
            "--shell", "bash",
            "--rc-path", str(tmp_path / ".bashrc"),
            "--dry-run",
        )
        assert rc == 0
        assert "[dry-run]" in out
        assert "bash.sh" in out

    def test_install_via_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        rc, _ = _run_cli_capture(
            "completion", "install",
            "--shell", "bash",
            "--rc-path", str(tmp_path / ".bashrc"),
        )
        assert rc == 0
        assert (tmp_path / ".chimera" / "completion" / "bash.sh").exists()
        assert MARKER_BEGIN in (tmp_path / ".bashrc").read_text()

    def test_legacy_print_path_unchanged(self) -> None:
        # Wave-9 behavior: ``chimera completion bash`` prints to stdout.
        rc, out = _run_cli_capture("completion", "bash")
        assert rc == 0
        assert "_chimera_completions" in out
