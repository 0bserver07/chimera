"""Unit + opt-in live tests for :class:`chimera.env.ssh.SSHEnvironment`.

Mocked tests assert that every public method constructs the expected
``ssh`` argv (so we can audit the wire format without a real network
connection). Live tests are skipped unless the ``CHIMERA_SSH_TEST_HOST``
env var names a reachable SSH destination — they only sanity-check the
end-to-end flow against a real sshd.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any
from unittest import mock

import pytest

from chimera.env.ssh import SSHEnvironment, _dirname

LIVE_HOST = os.environ.get("CHIMERA_SSH_TEST_HOST")


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    """Build a mock ``CompletedProcess`` for ``subprocess.run`` patching."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ---------------------------------------------------------------------------
# Construction + argv shape
# ---------------------------------------------------------------------------


def test_construct_requires_host() -> None:
    """Empty hosts must be rejected at construction time."""
    with pytest.raises(ValueError):
        SSHEnvironment(host="")


def test_ssh_prefix_default_port() -> None:
    """Port 22 should be omitted from argv (it's the ssh(1) default)."""
    env = SSHEnvironment(host="user@example.com")
    assert env._ssh_prefix() == ["ssh", "user@example.com"]


def test_ssh_prefix_custom_port_and_identity() -> None:
    """``-p`` + ``-i`` both flow into argv when explicitly set."""
    env = SSHEnvironment(
        host="user@example.com",
        port=2222,
        identity_file="/home/u/.ssh/id_ed25519",
    )
    assert env._ssh_prefix() == [
        "ssh",
        "-p",
        "2222",
        "-i",
        "/home/u/.ssh/id_ed25519",
        "user@example.com",
    ]


def test_ssh_prefix_emits_options_in_order() -> None:
    """``ssh_options`` are appended as ``-o key=value`` flags."""
    env = SSHEnvironment(
        host="bastion",
        ssh_options={
            "StrictHostKeyChecking": "no",
            "ProxyJump": "jump.example.com",
        },
    )
    argv = env._ssh_prefix()
    # Spot-check both flags landed; ordering follows dict insertion order.
    assert "-o" in argv
    assert "StrictHostKeyChecking=no" in argv
    assert "ProxyJump=jump.example.com" in argv
    assert argv[-1] == "bastion"


def test_wrap_remote_prefixes_cd_when_workdir_set() -> None:
    """Workdir prefix lets relative tool paths land in the project tree."""
    env = SSHEnvironment(host="h", workdir="/srv/app")
    assert env._wrap_remote("ls") == "cd /srv/app && ls"


def test_wrap_remote_quotes_workdir_with_spaces() -> None:
    """Workdir is shlex-quoted to survive spaces / metacharacters."""
    env = SSHEnvironment(host="h", workdir="/tmp/has space")
    wrapped = env._wrap_remote("pwd")
    assert wrapped == "cd '/tmp/has space' && pwd"


def test_wrap_remote_skips_cd_for_default_workdir() -> None:
    """``workdir='.'`` (default) should not emit a ``cd`` prefix."""
    env = SSHEnvironment(host="h")
    assert env._wrap_remote("whoami") == "whoami"


# ---------------------------------------------------------------------------
# run_bash / run_command
# ---------------------------------------------------------------------------


def test_run_bash_invokes_ssh_with_workdir_prefix() -> None:
    """The full argv passed to subprocess.run must include cd + cmd."""
    env = SSHEnvironment(host="user@host", workdir="/srv/app")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(stdout="hello\n", returncode=0),
    ) as mocked:
        result = env.run_bash("echo hello")

    assert result.stdout == "hello\n"
    assert result.exit_code == 0
    args, _kwargs = mocked.call_args
    argv: list[str] = args[0]
    assert argv[0] == "ssh"
    assert argv[1] == "user@host"
    # The remote command is the last argv element, single-string form.
    assert argv[-1] == "cd /srv/app && echo hello"


def test_run_bash_timeout_returns_exit_124() -> None:
    """Hitting ``subprocess.TimeoutExpired`` must surface as exit 124."""
    env = SSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=1),
    ):
        result = env.run_bash("sleep 99", timeout=1)
    assert result.exit_code == 124
    assert "timed out" in result.stderr.lower()


def test_run_command_alias_delegates_to_run_bash() -> None:
    """``run_command`` is the ABC name; should match ``run_bash`` output."""
    env = SSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(stdout="ok"),
    ):
        result = env.run_command("true", timeout=10, shell_name="ignored")
    assert result.stdout == "ok"
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# read_file / write_file
# ---------------------------------------------------------------------------


def test_read_file_uses_remote_cat() -> None:
    """``read_file`` should shell out to ``cat <quoted-path>``."""
    env = SSHEnvironment(host="h", workdir="/srv")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(stdout="file body\n"),
    ) as mocked:
        body = env.read_file("conf/app.toml")
    assert body == "file body\n"
    argv = mocked.call_args[0][0]
    assert argv[-1] == "cd /srv && cat conf/app.toml"


def test_read_file_raises_on_nonzero_exit() -> None:
    """Missing remote files must raise FileNotFoundError with stderr."""
    env = SSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(stderr="No such file", returncode=1),
    ):
        with pytest.raises(FileNotFoundError, match="No such file"):
            env.read_file("missing.txt")


def test_write_file_pipes_content_via_tee() -> None:
    """``write_file`` must mkdir parent + pipe stdin to remote tee."""
    env = SSHEnvironment(host="h", workdir="/srv/app")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(returncode=0),
    ) as mocked:
        env.write_file("logs/run.txt", "line one\n")

    args, kwargs = mocked.call_args
    argv: list[str] = args[0]
    assert argv[0] == "ssh"
    assert argv[-1] == "cd /srv/app && mkdir -p logs && tee logs/run.txt > /dev/null"
    # Content flows through stdin (input=), not the argv.
    assert kwargs.get("input") == "line one\n"


def test_write_file_raises_on_remote_failure() -> None:
    """Non-zero exit from remote tee is surfaced as OSError."""
    env = SSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(stderr="Permission denied", returncode=1),
    ):
        with pytest.raises(OSError, match="Permission denied"):
            env.write_file("/etc/shadow", "evil")


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


def test_list_files_filters_by_pattern() -> None:
    """``find`` output is client-side fnmatch-filtered against pattern."""
    env = SSHEnvironment(host="h")
    find_output = "./a.py\n./b.py\n./README.md\n"
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(stdout=find_output),
    ):
        py_files = env.list_files("*.py")
    assert py_files == ["a.py", "b.py"]


def test_list_files_default_pattern_returns_all() -> None:
    """Default ``**/*`` pattern is the no-filter shortcut."""
    env = SSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(stdout="./x\n./y\n"),
    ):
        assert env.list_files() == ["x", "y"]


# ---------------------------------------------------------------------------
# setup / cleanup / not-implemented
# ---------------------------------------------------------------------------


def test_setup_runs_reachability_probe() -> None:
    """setup() must invoke ``ssh <host> true`` and accept exit 0."""
    env = SSHEnvironment(host="user@h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(returncode=0),
    ) as mocked:
        env.setup()
    argv = mocked.call_args[0][0]
    assert argv[-1] == "true"


def test_setup_raises_on_failed_probe() -> None:
    """A non-zero probe exit must raise ConnectionError."""
    env = SSHEnvironment(host="user@h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(stderr="Permission denied", returncode=255),
    ):
        with pytest.raises(ConnectionError, match="Permission denied"):
            env.setup()


def test_setup_raises_on_probe_timeout() -> None:
    """Probe timeouts surface as ConnectionError, not bare TimeoutExpired."""
    env = SSHEnvironment(host="user@h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=5),
    ):
        with pytest.raises(ConnectionError, match="timed out"):
            env.setup()


def test_cleanup_is_noop() -> None:
    """No persistent state — cleanup is purely for ABC compliance."""
    env = SSHEnvironment(host="h")
    assert env.cleanup() is None


def test_checkpoint_and_restore_not_implemented() -> None:
    """Scaffold leaves checkpoint/restore for the follow-up issue."""
    env = SSHEnvironment(host="h")
    with pytest.raises(NotImplementedError):
        env.checkpoint()
    with pytest.raises(NotImplementedError):
        env.restore("0")


def test_dirname_helper() -> None:
    """``_dirname`` mirrors POSIX ``dirname`` for the cases we use."""
    assert _dirname("a/b/c.txt") == "a/b"
    assert _dirname("a.txt") == "."
    assert _dirname("/etc/hosts") == "/etc"
    assert _dirname("/single") == "/"


# ---------------------------------------------------------------------------
# CLI integration — --remote URL parsing
# ---------------------------------------------------------------------------


def test_parse_remote_url_full_form() -> None:
    """``ssh://user@host:port/path`` round-trips into SSHEnvironment kwargs."""
    from chimera.mink.cli import _parse_remote_url

    kwargs = _parse_remote_url("ssh://alice@example.com:2200/srv/app")
    assert kwargs == {
        "host": "alice@example.com",
        "port": 2200,
        "workdir": "/srv/app",
    }


def test_parse_remote_url_bare_user_host() -> None:
    """Bare ``user@host`` (no scheme) is accepted as a convenience form."""
    from chimera.mink.cli import _parse_remote_url

    kwargs = _parse_remote_url("alice@example.com")
    assert kwargs["host"] == "alice@example.com"
    assert kwargs["port"] == 22
    assert kwargs["workdir"] == "."


def test_parse_remote_url_missing_host_raises() -> None:
    """Empty / malformed URLs surface as ValueError."""
    from chimera.mink.cli import _parse_remote_url

    with pytest.raises(ValueError):
        _parse_remote_url("ssh://")


def test_build_environment_routes_to_ssh_when_remote_set() -> None:
    """``_build_environment`` returns SSHEnvironment iff ``--remote`` is set."""
    import argparse

    from chimera.env.ssh import SSHEnvironment
    from chimera.mink.cli import _build_environment

    args = argparse.Namespace(remote="ssh://u@h/srv")
    env: Any = _build_environment(args, cwd="/local/cwd")
    assert isinstance(env, SSHEnvironment)
    assert env.host == "u@h"
    assert env.workdir == "/srv"


def test_build_environment_falls_back_to_local() -> None:
    """No ``--remote`` flag means the legacy LocalEnvironment is used."""
    import argparse

    from chimera.env.local import LocalEnvironment
    from chimera.mink.cli import _build_environment

    args = argparse.Namespace(remote=None)
    env: Any = _build_environment(args, cwd="/tmp")
    assert isinstance(env, LocalEnvironment)


# ---------------------------------------------------------------------------
# Live tests (opt-in via CHIMERA_SSH_TEST_HOST)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not LIVE_HOST,
    reason="set CHIMERA_SSH_TEST_HOST=user@host to run live SSH tests",
)
def test_live_run_bash_round_trip() -> None:
    """End-to-end: setup + echo + read_file/write_file against a real host."""
    assert LIVE_HOST is not None  # narrow for type-checker
    env = SSHEnvironment(host=LIVE_HOST, workdir="/tmp")
    env.setup()
    try:
        result = env.run_bash("echo live-ok")
        assert result.exit_code == 0
        assert "live-ok" in result.stdout

        env.write_file("/tmp/chimera_ssh_probe.txt", "round-trip\n")
        body = env.read_file("/tmp/chimera_ssh_probe.txt")
        assert body == "round-trip\n"
    finally:
        env.cleanup()
