"""CLI tests for `chimera fs login` and `chimera fs rename`."""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from unittest import mock

import pytest

from chimera.cli.fs import cmd_login, cmd_rename, register
from chimera.function_synthesis.credentials import CredentialStore


# ---------------------------------------------------------------------------
# Helpers — shared with test_fs_cli.py style
# ---------------------------------------------------------------------------


def _run_subprocess(args: list[str], env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "chimera", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _env(tmp_path) -> dict:
    return {
        **os.environ,
        "CHIMERA_FS_HOME": str(tmp_path),
        "CHIMERA_FS_OFFLINE": "1",
    }


def _write_spec(tmp_path, name: str = "a", desc: str = "x"):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"name": name, "description": desc}))
    return spec_file


def _make_parser() -> argparse.ArgumentParser:
    """Build a standalone parser that wires in `register`."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    return parser


def _parse(argv: list[str]) -> argparse.Namespace:
    return _make_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# `fs login --token <t>` in-process (fast)
# ---------------------------------------------------------------------------


def test_login_with_token_flag_saves_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    args = _parse(["fs", "login", "huggingface", "--token", "hf_secret"])
    rc = cmd_login(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "Saved credentials for huggingface" in captured.out
    # Token must not appear in output.
    assert "hf_secret" not in captured.out
    assert "hf_secret" not in captured.err

    # Verify the store holds the token.
    store = CredentialStore()
    assert store.get("huggingface") == "hf_secret"


def test_login_without_token_uses_getpass(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    args = _parse(["fs", "login", "svc1"])
    with mock.patch("chimera.cli.fs.getpass.getpass", return_value="pw-from-stdin") as mocked:
        rc = cmd_login(args)
    assert rc == 0
    mocked.assert_called_once()
    captured = capsys.readouterr()
    # Token never echoed.
    assert "pw-from-stdin" not in captured.out
    assert "pw-from-stdin" not in captured.err
    assert CredentialStore().get("svc1") == "pw-from-stdin"


def test_login_list_prints_services(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    CredentialStore().set("svc-a", "tok-a")
    CredentialStore().set("svc-b", "tok-b")
    args = _parse(["fs", "login", "--list"])
    rc = cmd_login(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "svc-a" in out
    assert "svc-b" in out
    # Tokens must never appear.
    assert "tok-a" not in out
    assert "tok-b" not in out


def test_login_delete_removes_entry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    CredentialStore().set("svc", "tok")
    args = _parse(["fs", "login", "svc", "--delete"])
    rc = cmd_login(args)
    assert rc == 0
    assert CredentialStore().get("svc") is None
    out = capsys.readouterr().out
    assert "Removed credentials for svc" in out
    assert "tok" not in out


def test_login_empty_token_from_getpass_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    args = _parse(["fs", "login", "svc"])
    with mock.patch("chimera.cli.fs.getpass.getpass", return_value=""):
        with pytest.raises(SystemExit) as exc:
            cmd_login(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "empty token" in err
    assert CredentialStore().get("svc") is None


def test_login_eof_during_getpass_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    args = _parse(["fs", "login", "svc"])
    with mock.patch("chimera.cli.fs.getpass.getpass", side_effect=EOFError):
        with pytest.raises(SystemExit) as exc:
            cmd_login(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no token provided" in err


def test_login_without_service_and_without_list_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    args = _parse(["fs", "login"])
    with pytest.raises(SystemExit) as exc:
        cmd_login(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "service" in err


def test_login_file_mode_0600_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    args = _parse(["fs", "login", "svc", "--token", "tok"])
    cmd_login(args)
    path = tmp_path / "credentials.json"
    assert path.exists()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# `fs rename` in-process
# ---------------------------------------------------------------------------


def _install_mock(env: dict, tmp_path, name: str) -> str:
    spec = _write_spec(tmp_path, name=name, desc=name)
    result = _run_subprocess(
        ["fs", "compile", str(spec), "--compiler", "mock"], env=env
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_rename_happy_path_via_subprocess(tmp_path):
    env = _env(tmp_path)
    slug = _install_mock(env, tmp_path, "orig")
    result = _run_subprocess(["fs", "rename", slug, "new-name"], env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "new-name"

    # Verify via `fs list`.
    listed = _run_subprocess(["fs", "list"], env=env)
    assert "new-name" in listed.stdout
    assert slug not in listed.stdout

    # Bundle file was renamed on disk.
    assert (tmp_path / "bundles" / "new-name.chi").exists()
    assert not (tmp_path / "bundles" / f"{slug}.chi").exists()


def test_rename_missing_slug_nonzero(tmp_path):
    env = _env(tmp_path)
    result = _run_subprocess(
        ["fs", "rename", "ghost-123", "new-ghost"], env=env
    )
    assert result.returncode != 0
    assert "not found" in result.stderr


def test_rename_collision_nonzero(tmp_path):
    env = _env(tmp_path)
    slug_a = _install_mock(env, tmp_path, "aa")
    slug_b = _install_mock(env, tmp_path, "bb")
    result = _run_subprocess(
        ["fs", "rename", slug_a, slug_b], env=env
    )
    assert result.returncode != 0
    assert "already exists" in result.stderr


def test_rename_invalid_slug_format_nonzero(tmp_path):
    env = _env(tmp_path)
    slug = _install_mock(env, tmp_path, "orig")
    # '/' is not valid — would escape the bundles directory.
    result = _run_subprocess(
        ["fs", "rename", slug, "bad/slug"], env=env
    )
    assert result.returncode != 0
    assert "invalid slug" in result.stderr


# Unit-level rename handler tests (faster, no subprocess).


def test_cmd_rename_invalid_slug_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    args = _parse(["fs", "rename", "old", "has spaces"])
    with pytest.raises(SystemExit) as exc:
        cmd_rename(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid slug" in err


def test_cmd_rename_missing_returns_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    args = _parse(["fs", "rename", "not-installed", "new-one"])
    with pytest.raises(SystemExit) as exc:
        cmd_rename(args)
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `fs login` via subprocess (end-to-end smoke)
# ---------------------------------------------------------------------------


def test_login_subprocess_with_token_flag(tmp_path):
    env = _env(tmp_path)
    result = _run_subprocess(
        ["fs", "login", "hf", "--token", "hf_pat_x"], env=env
    )
    assert result.returncode == 0, result.stderr
    assert "Saved credentials for hf" in result.stdout
    # Token must not appear in any output stream.
    assert "hf_pat_x" not in result.stdout
    assert "hf_pat_x" not in result.stderr
    # And the file mode is 0o600.
    path = tmp_path / "credentials.json"
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_login_subprocess_list(tmp_path):
    env = _env(tmp_path)
    _run_subprocess(["fs", "login", "a", "--token", "ta"], env=env)
    _run_subprocess(["fs", "login", "b", "--token", "tb"], env=env)
    result = _run_subprocess(["fs", "login", "--list"], env=env)
    assert result.returncode == 0
    assert "a" in result.stdout
    assert "b" in result.stdout
    assert "ta" not in result.stdout
    assert "tb" not in result.stdout


def test_login_subprocess_delete(tmp_path):
    env = _env(tmp_path)
    _run_subprocess(["fs", "login", "svc", "--token", "t"], env=env)
    result = _run_subprocess(["fs", "login", "svc", "--delete"], env=env)
    assert result.returncode == 0
    assert "Removed credentials for svc" in result.stdout


# Sanity: parser registers both commands.


@pytest.mark.parametrize("cmd", ["login", "rename"])
def test_parser_registers_command(cmd):
    parser = _make_parser()
    # Should not SystemExit when the subcommand is present with --help'able
    # shape.  We just check that the subparser exists.
    sub_actions = [
        a for a in parser._subparsers._actions  # type: ignore[union-attr]
        if isinstance(a, argparse._SubParsersAction)
    ]
    assert sub_actions, "no subparsers found"
    fs_action = sub_actions[0]
    fs_parser = fs_action.choices["fs"]
    fs_sub_actions = [
        a for a in fs_parser._subparsers._actions  # type: ignore[union-attr]
        if isinstance(a, argparse._SubParsersAction)
    ]
    assert fs_sub_actions
    assert cmd in fs_sub_actions[0].choices
