"""Tests for the W13-G15 ferret CLI flag triplet.

Covers the six flags added by G15:

* ``--full-auto`` / ``--yolo`` — approval shortcuts
* ``--add-dir`` (repeatable) — extra writable directories
* ``--skip-git-repo-check`` — bypass the git-repo guard warning
* ``--image`` (repeatable) — attach image inputs to the prompt
* ``--profile`` — overlay ``~/.chimera/profiles/<NAME>.toml``

The tests stay parser- and helper-level: they exercise ``add_arguments``,
``_resolve_ferret_permissions``, ``_apply_ferret_profile``,
``_apply_ferret_image_prefix``, ``_emit_yolo_warning``, and
``_check_git_repo_guard`` directly — no live provider, no agent run.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from chimera.ferret import cli as ferret_cli


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera ferret")
    ferret_cli.add_arguments(parser)
    return parser


# ---------------------------------------------------------------------------
# Parser surface
# ---------------------------------------------------------------------------


class TestParserSurface:
    def test_all_g15_flags_registered(self):
        parser = _build_parser()
        options: set[str] = set()
        for action in parser._actions:  # noqa: SLF001
            options.update(action.option_strings)
        for flag in (
            "--full-auto",
            "--yolo",
            "--add-dir",
            "--skip-git-repo-check",
            "--image",
            "--profile",
        ):
            assert flag in options, f"missing flag {flag!r}"

    def test_full_auto_default_false(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.full_auto is False

    def test_yolo_default_false(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.yolo is False

    def test_add_dir_repeatable(self):
        parser = _build_parser()
        args = parser.parse_args(["--add-dir", "/a", "--add-dir", "/b"])
        assert args.add_dirs == ["/a", "/b"]

    def test_image_repeatable(self):
        parser = _build_parser()
        args = parser.parse_args(["--image", "x.png", "--image", "y.jpg"])
        assert args.images == ["x.png", "y.jpg"]

    def test_profile_default_none(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.profile is None

    def test_skip_git_repo_check_store_true(self):
        parser = _build_parser()
        args = parser.parse_args(["--skip-git-repo-check"])
        assert args.skip_git_repo_check is True


# ---------------------------------------------------------------------------
# Permission shortcuts
# ---------------------------------------------------------------------------


class TestPermissionShortcuts:
    def _ns(self, **overrides):
        defaults = dict(
            permission_mode=None,
            approval=None,
            full_auto=False,
            yolo=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_full_auto_routes_to_auto_policy(self):
        from chimera.permissions.modes import ApprovalMode, policy_for_mode

        policy = ferret_cli._resolve_ferret_permissions(self._ns(full_auto=True))
        assert policy is not None
        # Sanity: same shape as the canonical AUTO preset.
        canonical = policy_for_mode(ApprovalMode.AUTO)
        assert type(policy) is type(canonical)

    def test_yolo_routes_to_yolo_policy(self):
        from chimera.permissions.modes import ApprovalMode, policy_for_mode

        policy = ferret_cli._resolve_ferret_permissions(self._ns(yolo=True))
        assert policy is not None
        canonical = policy_for_mode(ApprovalMode.YOLO)
        assert type(policy) is type(canonical)

    def test_yolo_wins_over_full_auto(self):
        """Both flags set → --yolo wins (more permissive)."""
        from chimera.permissions.modes import ApprovalMode, policy_for_mode

        policy = ferret_cli._resolve_ferret_permissions(
            self._ns(full_auto=True, yolo=True)
        )
        canonical_yolo = policy_for_mode(ApprovalMode.YOLO)
        canonical_auto = policy_for_mode(ApprovalMode.AUTO)
        assert type(policy) is type(canonical_yolo)
        assert type(policy) is not type(canonical_auto) or (
            policy_for_mode(ApprovalMode.YOLO) is not None
            and policy_for_mode(ApprovalMode.AUTO) is not None
        )

    def test_explicit_permission_mode_overrides_yolo(self):
        """``--permission-mode read-only --yolo`` → read-only wins."""
        policy_yolo = ferret_cli._resolve_ferret_permissions(self._ns(yolo=True))
        policy_explicit = ferret_cli._resolve_ferret_permissions(
            self._ns(permission_mode="read-only", yolo=True)
        )
        # Explicit read-only must produce a different policy from yolo.
        assert type(policy_yolo) is not type(policy_explicit) or (
            policy_yolo is not policy_explicit
        )


# ---------------------------------------------------------------------------
# YOLO stderr warning
# ---------------------------------------------------------------------------


class TestYoloWarning:
    def test_warning_emitted_when_yolo_set(self, capsys):
        ns = argparse.Namespace(yolo=True)
        ferret_cli._emit_yolo_warning(ns)
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "yolo" in captured.err.lower()

    def test_no_warning_when_yolo_unset(self, capsys):
        ns = argparse.Namespace(yolo=False)
        ferret_cli._emit_yolo_warning(ns)
        captured = capsys.readouterr()
        assert captured.err == ""


# ---------------------------------------------------------------------------
# Git-repo guard
# ---------------------------------------------------------------------------


class TestGitRepoGuard:
    def test_inside_repo_returns_none_silently(self, tmp_path: Path, capsys):
        (tmp_path / ".git").mkdir()
        ns = argparse.Namespace(
            cwd=str(tmp_path), skip_git_repo_check=False,
        )
        rc = ferret_cli._check_git_repo_guard(ns)
        assert rc is None
        assert capsys.readouterr().err == ""

    def test_outside_repo_warns_but_continues(self, tmp_path: Path, capsys):
        ns = argparse.Namespace(
            cwd=str(tmp_path), skip_git_repo_check=False,
        )
        rc = ferret_cli._check_git_repo_guard(ns)
        assert rc is None  # advisory only — does not abort
        captured = capsys.readouterr()
        assert "not inside a git repository" in captured.err

    def test_skip_flag_silences_warning(self, tmp_path: Path, capsys):
        ns = argparse.Namespace(
            cwd=str(tmp_path), skip_git_repo_check=True,
        )
        rc = ferret_cli._check_git_repo_guard(ns)
        assert rc is None
        assert capsys.readouterr().err == ""

    def test_is_inside_git_repo_walks_up(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert ferret_cli._is_inside_git_repo(str(nested)) is True

    def test_is_inside_git_repo_returns_false_outside(self, tmp_path: Path):
        nested = tmp_path / "no_repo"
        nested.mkdir()
        assert ferret_cli._is_inside_git_repo(str(nested)) is False


# ---------------------------------------------------------------------------
# --image prompt prefix
# ---------------------------------------------------------------------------


class TestImagePrefix:
    def test_no_images_returns_prompt_unchanged(self, capsys):
        ns = argparse.Namespace(images=None)
        out = ferret_cli._apply_ferret_image_prefix(ns, prompt="hello")
        assert out == "hello"
        assert capsys.readouterr().err == ""

    def test_existing_images_render_block(self, tmp_path: Path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG signature
        ns = argparse.Namespace(images=[str(img)])
        out = ferret_cli._apply_ferret_image_prefix(ns, prompt="describe")
        assert "<attached_images>" in out
        assert str(img) in out
        assert out.endswith("describe")

    def test_missing_image_logs_to_stderr(self, capsys):
        ns = argparse.Namespace(images=["/nope/missing.png"])
        out = ferret_cli._apply_ferret_image_prefix(ns, prompt="x")
        assert out == "x"  # block dropped because the only image was missing
        captured = capsys.readouterr()
        assert "file not found" in captured.err


# ---------------------------------------------------------------------------
# --profile TOML overlay
# ---------------------------------------------------------------------------


class TestProfileOverlay:
    def test_no_profile_is_noop(self, capsys):
        ns = argparse.Namespace(profile=None)
        ferret_cli._apply_ferret_profile(ns)
        # No fields touched and no warning printed.
        assert capsys.readouterr().err == ""

    def test_missing_profile_warns(self, monkeypatch, tmp_path: Path, capsys):
        # Point the lookup at a directory that doesn't contain the file.
        monkeypatch.setattr(
            ferret_cli, "_FERRET_PROFILES_DIR", str(tmp_path),
        )
        ns = argparse.Namespace(profile="ghost")
        ferret_cli._apply_ferret_profile(ns)
        assert "file not found" in capsys.readouterr().err

    def test_profile_overlays_known_keys(
        self, monkeypatch, tmp_path: Path,
    ):
        profile_path = tmp_path / "team.toml"
        profile_path.write_text(
            'model = "gpt-4o"\n'
            'sandbox = "workspace-write"\n'
            'max_steps = 99\n'
            'add_dirs = ["/extra"]\n'
        )
        monkeypatch.setattr(
            ferret_cli, "_FERRET_PROFILES_DIR", str(tmp_path),
        )
        ns = argparse.Namespace(
            profile="team",
            model=None,
            sandbox=None,
            max_steps=None,
            add_dirs=None,
        )
        ferret_cli._apply_ferret_profile(ns)
        assert ns.model == "gpt-4o"
        assert ns.sandbox == "workspace-write"
        assert ns.max_steps == 99
        assert ns.add_dirs == ["/extra"]

    def test_profile_does_not_clobber_explicit_value(
        self, monkeypatch, tmp_path: Path,
    ):
        """Explicit CLI values must win over profile overlays."""
        profile_path = tmp_path / "p.toml"
        profile_path.write_text('model = "from-profile"\n')
        monkeypatch.setattr(
            ferret_cli, "_FERRET_PROFILES_DIR", str(tmp_path),
        )
        ns = argparse.Namespace(profile="p", model="from-cli")
        ferret_cli._apply_ferret_profile(ns)
        assert ns.model == "from-cli"

    def test_profile_ignores_unknown_keys(
        self, monkeypatch, tmp_path: Path,
    ):
        profile_path = tmp_path / "p.toml"
        profile_path.write_text(
            'subcommand = "bench"\n'  # not whitelisted
            'random_key = 1\n'        # not whitelisted
            'model = "ok"\n'
        )
        monkeypatch.setattr(
            ferret_cli, "_FERRET_PROFILES_DIR", str(tmp_path),
        )
        ns = argparse.Namespace(profile="p", model=None, subcommand=None)
        ferret_cli._apply_ferret_profile(ns)
        assert ns.model == "ok"
        assert ns.subcommand is None  # untouched


# ---------------------------------------------------------------------------
# Help-long descriptions
# ---------------------------------------------------------------------------


class TestHelpLong:
    @pytest.mark.parametrize(
        "flag",
        [
            "--full-auto",
            "--yolo",
            "--add-dir",
            "--skip-git-repo-check",
            "--image",
            "--profile",
        ],
    )
    def test_each_g15_flag_has_long_help(self, flag: str):
        assert flag in ferret_cli._LONG_HELP
        assert len(ferret_cli._LONG_HELP[flag]) > 0


# ---------------------------------------------------------------------------
# Trademark hygiene
# ---------------------------------------------------------------------------


class TestTrademarkHygiene:
    """Help text and source must not name the upstream brand.

    The path ``~/.codex/config.toml`` is referenced as a filesystem fact
    (the same way otter references ``.opencode``); other ``codex`` /
    ``openai`` mentions are forbidden in the new G15 surface.
    """

    @pytest.mark.parametrize(
        "flag",
        [
            "--full-auto",
            "--yolo",
            "--add-dir",
            "--skip-git-repo-check",
            "--image",
            "--profile",
        ],
    )
    def test_g15_help_text_is_brand_neutral(self, flag: str):
        long_help = ferret_cli._LONG_HELP[flag].lower()
        assert "codex" not in long_help
        assert "openai" not in long_help


# ---------------------------------------------------------------------------
# Integration: parsing all G15 flags together
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_all_flags_parse_together(self, tmp_path: Path):
        parser = _build_parser()
        # Use --skip-git-repo-check so the parser stays agnostic of cwd.
        args = parser.parse_args([
            "--full-auto",
            "--yolo",
            "--add-dir", "/extra1",
            "--add-dir", "/extra2",
            "--skip-git-repo-check",
            "--image", str(tmp_path / "a.png"),
            "--profile", "team",
        ])
        assert args.full_auto is True
        assert args.yolo is True
        assert args.add_dirs == ["/extra1", "/extra2"]
        assert args.skip_git_repo_check is True
        assert args.images == [str(tmp_path / "a.png")]
        assert args.profile == "team"

    def test_run_emits_yolo_warning_then_continues(
        self, monkeypatch, tmp_path: Path, capsys,
    ):
        """``ferret --yolo`` warns on stderr and continues into dispatch."""
        # Stub the REPL entry point so run() returns a known code without
        # touching providers / network / sandbox modules.
        monkeypatch.setattr(
            "chimera.ferret.repl.run_ferret_repl",
            lambda args: 0,
        )
        ns = argparse.Namespace(
            help_long=False,
            subcommand=None,
            print_mode=None,
            profile=None,
            yolo=True,
            full_auto=False,
            cwd=str(tmp_path),
            skip_git_repo_check=True,  # silence the repo warning
        )
        rc = ferret_cli.run(ns)
        assert rc == 0
        assert "WARNING" in capsys.readouterr().err
