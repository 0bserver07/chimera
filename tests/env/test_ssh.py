"""Tests for the new SSH features in ``chimera.env.ssh``.

Covers the post-scaffold work for issue #127:

* ProxyJump kwarg + bastion handling
* OpenSSH control-master multiplexing
* Connection retries with exponential backoff
* Concurrent command pool (``run_bash_many``)
* SCP/SFTP file transfer (``upload_file`` / ``download_file``)
* Optional password / askpass helper
* Checkpoint / restore round-trip
* asyncssh-backed :class:`AsyncSSHEnvironment` (skipped when the
  optional ``ssh`` extra isn't installed)

The asyncssh tests use ``pytest.importorskip("asyncssh")`` and rely on
:class:`unittest.mock.AsyncMock` for the connection object — so they
don't need a real sshd, just the package being importable.
"""

from __future__ import annotations

import subprocess
from typing import Any
from unittest import mock

import pytest

# Skip the whole module if rich isn't installed (some imports below pull
# in chimera.mink.cli transitively in fixtures/asserts). Keeps the file
# parity with tests/env/test_ssh_environment.py.
pytest.importorskip("rich")

from chimera.env.ssh import AsyncSSHEnvironment, SSHEnvironment, _retry_with_backoff


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ---------------------------------------------------------------------------
# ProxyJump / bastion
# ---------------------------------------------------------------------------


def test_proxy_jump_kwarg_is_translated_to_option() -> None:
    """`proxy_jump=` constructor arg lands as ``-o ProxyJump=…``."""
    env = SSHEnvironment(host="dest", proxy_jump="bastion.example.com")
    argv = env._ssh_prefix()
    assert "-o" in argv
    assert "ProxyJump=bastion.example.com" in argv


def test_proxy_jump_kwarg_overrides_ssh_options_entry() -> None:
    """An explicit kwarg wins over a stale ``ssh_options`` entry."""
    env = SSHEnvironment(
        host="dest",
        ssh_options={"ProxyJump": "old-bastion"},
        proxy_jump="new-bastion",
    )
    argv = env._ssh_prefix()
    assert "ProxyJump=new-bastion" in argv
    assert "ProxyJump=old-bastion" not in argv


# ---------------------------------------------------------------------------
# Control-master / persistent connections
# ---------------------------------------------------------------------------


def test_persistent_setup_allocates_control_path() -> None:
    """``persistent=True`` materializes a per-instance control socket on setup."""
    env = SSHEnvironment(host="h", persistent=True)
    assert env._control_path is None
    with mock.patch(
        "chimera.env.ssh.subprocess.run", return_value=_completed(returncode=0)
    ):
        env.setup()
    assert env._control_path is not None
    assert env._control_path.endswith(".sock")


def test_persistent_argv_includes_control_options() -> None:
    """Once setup has run, every argv carries ControlMaster=auto."""
    env = SSHEnvironment(host="h", persistent=True)
    with mock.patch(
        "chimera.env.ssh.subprocess.run", return_value=_completed(returncode=0)
    ):
        env.setup()
    argv = env._ssh_prefix()
    assert "ControlMaster=auto" in argv
    assert any(part.startswith("ControlPath=") for part in argv)
    assert "ControlPersist=60s" in argv


def test_persistent_cleanup_attempts_master_close() -> None:
    """cleanup() should send ``ssh -O exit`` and reset state."""
    env = SSHEnvironment(host="h", persistent=True)
    with mock.patch(
        "chimera.env.ssh.subprocess.run", return_value=_completed(returncode=0)
    ):
        env.setup()
        env.cleanup()
    assert env._control_path is None


# ---------------------------------------------------------------------------
# Retries / backoff
# ---------------------------------------------------------------------------


def test_retry_helper_succeeds_after_transient_failure() -> None:
    """The shared backoff helper retries until success or exhaustion."""
    call_count = {"n": 0}

    def flaky() -> str:
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise OSError("transient")
        return "ok"

    out = _retry_with_backoff(
        flaky,
        retries=3,
        initial_delay=0,
        max_delay=0,
        exceptions=(OSError,),
        jitter=False,
    )
    assert out == "ok"
    assert call_count["n"] == 3


def test_retry_helper_raises_after_budget_exhausted() -> None:
    """When all attempts fail, the last exception is re-raised."""

    def always_fail() -> None:
        raise OSError("permanent")

    with pytest.raises(OSError, match="permanent"):
        _retry_with_backoff(
            always_fail,
            retries=2,
            initial_delay=0,
            max_delay=0,
            exceptions=(OSError,),
            jitter=False,
        )


def test_run_bash_retries_on_timeout() -> None:
    """With retries=N, transient TimeoutExpired retries before giving up."""
    env = SSHEnvironment(host="h", retries=2, retry_initial_delay=0, retry_max_delay=0)
    side_effects: list[Any] = [
        subprocess.TimeoutExpired(cmd="ssh", timeout=1),
        subprocess.TimeoutExpired(cmd="ssh", timeout=1),
        _completed(stdout="ok"),
    ]
    with (
        mock.patch("chimera.env.ssh.subprocess.run", side_effect=side_effects),
        mock.patch("chimera.env.ssh.time.sleep"),  # avoid real sleeps
    ):
        result = env.run_bash("true")
    assert result.exit_code == 0
    assert result.stdout == "ok"


# ---------------------------------------------------------------------------
# Concurrent command pool
# ---------------------------------------------------------------------------


def test_run_bash_many_falls_back_to_sequential_without_executor() -> None:
    """Before setup() (no executor) the pool degrades to in-thread iteration."""
    env = SSHEnvironment(host="h", max_concurrency=5)
    with mock.patch(
        "chimera.env.ssh.subprocess.run", return_value=_completed(stdout="x")
    ) as mocked:
        results = env.run_bash_many(["a", "b", "c"])
    assert [r.stdout for r in results] == ["x", "x", "x"]
    assert mocked.call_count == 3


def test_run_bash_many_uses_threadpool_after_setup() -> None:
    """Once setup() allocates the executor, calls fan out concurrently."""
    env = SSHEnvironment(host="h", max_concurrency=3)
    with mock.patch(
        "chimera.env.ssh.subprocess.run", return_value=_completed(stdout="ok")
    ) as mocked:
        env.setup()
        try:
            results = env.run_bash_many(["c1", "c2", "c3"])
        finally:
            env.cleanup()
    # 1 setup probe + 3 commands = 4 invocations.
    assert mocked.call_count == 4
    assert all(r.stdout == "ok" for r in results)


# ---------------------------------------------------------------------------
# scp upload / download
# ---------------------------------------------------------------------------


def test_upload_file_invokes_scp_with_port() -> None:
    """upload_file uses ``scp -P`` (capital P) and a host:path destination."""
    env = SSHEnvironment(host="user@h", port=2222)
    with mock.patch(
        "chimera.env.ssh.subprocess.run", return_value=_completed(returncode=0)
    ) as mocked:
        env.upload_file("/local/file.txt", "/remote/file.txt")
    # Two calls: mkdir -p via run_bash, then the scp itself.
    last_argv = mocked.call_args_list[-1][0][0]
    assert last_argv[0] == "scp"
    assert "-P" in last_argv
    assert "2222" in last_argv
    assert last_argv[-1] == "user@h:/remote/file.txt"


def test_upload_file_raises_on_scp_failure() -> None:
    """A non-zero scp exit must surface as OSError with stderr."""
    env = SSHEnvironment(host="h")
    # First call (mkdir) succeeds; second (scp) fails.
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        side_effect=[
            _completed(returncode=0),
            _completed(returncode=1, stderr="permission denied"),
        ],
    ):
        with pytest.raises(OSError, match="permission denied"):
            env.upload_file("/local/x", "/remote/x")


def test_download_file_invokes_scp_with_remote_source() -> None:
    """download_file constructs ``scp host:remote local``."""
    env = SSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run", return_value=_completed(returncode=0)
    ) as mocked:
        env.download_file("/remote/data.bin", "/tmp/data.bin")
    argv = mocked.call_args[0][0]
    assert argv[0] == "scp"
    assert argv[-2] == "h:/remote/data.bin"
    assert argv[-1] == "/tmp/data.bin"


def test_download_file_missing_raises_filenotfound() -> None:
    """Non-zero scp on download → FileNotFoundError."""
    env = SSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.subprocess.run",
        return_value=_completed(returncode=1, stderr="no such file"),
    ):
        with pytest.raises(FileNotFoundError, match="no such file"):
            env.download_file("/remote/missing", "/tmp/missing")


# ---------------------------------------------------------------------------
# Password / askpass helper
# ---------------------------------------------------------------------------


def test_password_writes_askpass_helper_on_setup(tmp_path: Any) -> None:
    """Setting ``password=`` materializes a 0o700 askpass script during setup."""
    del tmp_path  # silence unused
    env = SSHEnvironment(host="h", password="hunter2")
    assert env._askpass_path is None
    with mock.patch(
        "chimera.env.ssh.subprocess.run", return_value=_completed(returncode=0)
    ):
        env.setup()
        try:
            assert env._askpass_path is not None
            import os
            import stat

            mode = stat.S_IMODE(os.stat(env._askpass_path).st_mode)
            assert mode == 0o700
            with open(env._askpass_path) as fh:
                body = fh.read()
            assert "hunter2" in body
        finally:
            env.cleanup()
    assert env._askpass_path is None


# ---------------------------------------------------------------------------
# AsyncSSHEnvironment — skipped without the optional [ssh] extra
# ---------------------------------------------------------------------------


# Per-test skip rather than module-level so the subprocess tests above
# always run, regardless of whether asyncssh is installed.
def _require_asyncssh() -> None:
    pytest.importorskip("asyncssh")


@pytest.fixture()
def fake_conn() -> Any:
    _require_asyncssh()
    """Build a mock asyncssh connection with the methods we exercise."""
    conn = mock.MagicMock()

    async def _run(cmd: str, check: bool = False) -> Any:
        result = mock.MagicMock()
        result.stdout = f"ran:{cmd}"
        result.stderr = ""
        result.exit_status = 0
        return result

    conn.run = mock.AsyncMock(side_effect=_run)

    async def _wait_closed() -> None:
        return None

    conn.wait_closed = mock.AsyncMock(side_effect=_wait_closed)
    conn.close = mock.MagicMock()
    return conn


def test_async_ssh_construct_requires_host() -> None:
    _require_asyncssh()
    with pytest.raises(ValueError):
        AsyncSSHEnvironment(host="")


def test_async_ssh_proxy_jump_string_splits_on_comma() -> None:
    """Comma-separated proxy_jump string parses into a hop list."""
    _require_asyncssh()
    env = AsyncSSHEnvironment(host="dest", proxy_jump="jump1,jump2 ,jump3")
    assert env.proxy_jump == ["jump1", "jump2", "jump3"]


def test_async_ssh_run_bash_drives_async_run(fake_conn: Any) -> None:
    """Sync run_bash() proxies through the private loop into asyncssh.run."""
    env = AsyncSSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.asyncssh.connect",
        new=mock.AsyncMock(return_value=fake_conn),
    ):
        env.setup()
        try:
            result = env.run_bash("echo hi")
        finally:
            env.cleanup()
    assert result.exit_code == 0
    assert "echo hi" in result.stdout


def test_async_ssh_setup_failure_raises_connectionerror() -> None:
    """A bad asyncssh.connect must surface as ConnectionError."""
    _require_asyncssh()
    env = AsyncSSHEnvironment(host="h")
    with mock.patch(
        "chimera.env.ssh.asyncssh.connect",
        new=mock.AsyncMock(side_effect=OSError("connection refused")),
    ):
        with pytest.raises(ConnectionError, match="connection refused"):
            env.setup()


def test_async_ssh_run_bash_many_uses_semaphore(fake_conn: Any) -> None:
    """Concurrent command fan-out runs every cmd against the same conn."""
    env = AsyncSSHEnvironment(host="h", max_concurrency=2)
    with mock.patch(
        "chimera.env.ssh.asyncssh.connect",
        new=mock.AsyncMock(return_value=fake_conn),
    ):
        env.setup()
        try:
            results = env.run_bash_many(["a", "b", "c"])
        finally:
            env.cleanup()
    assert [r.stdout for r in results] == ["ran:a", "ran:b", "ran:c"]
    assert fake_conn.run.await_count == 3


def test_async_ssh_workdir_prefix_applied(fake_conn: Any) -> None:
    """The same cd-prefix wrapping logic as the subprocess env."""
    env = AsyncSSHEnvironment(host="h", workdir="/srv/app")
    with mock.patch(
        "chimera.env.ssh.asyncssh.connect",
        new=mock.AsyncMock(return_value=fake_conn),
    ):
        env.setup()
        try:
            env.run_bash("ls")
        finally:
            env.cleanup()
    # The mock side_effect captured the wrapped command in result.stdout.
    last_call = fake_conn.run.await_args
    assert last_call.args[0] == "cd /srv/app && ls"


def test_async_ssh_resolve_path_handles_absolute() -> None:
    """Absolute paths bypass the workdir prefix; relatives are joined."""
    _require_asyncssh()
    env = AsyncSSHEnvironment(host="h", workdir="/srv/app")
    assert env._resolve_path("/etc/hosts") == "/etc/hosts"
    assert env._resolve_path("conf/app.toml") == "/srv/app/conf/app.toml"

    env_default = AsyncSSHEnvironment(host="h")  # workdir="."
    assert env_default._resolve_path("rel/path") == "rel/path"


def test_async_ssh_require_conn_before_setup_raises() -> None:
    """Calling a method before setup() must raise — surfaces wiring bugs."""
    _require_asyncssh()
    env = AsyncSSHEnvironment(host="h")
    with pytest.raises(RuntimeError, match="setup"):
        env._require_conn()
