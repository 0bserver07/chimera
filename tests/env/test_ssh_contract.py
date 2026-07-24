"""AsyncSSHEnvironment — the remote-execution contract, pinned end to end.

Issue #127 asked for an SSH-backed "agent OS".  Chimera answers it with
:class:`chimera.env.ssh.AsyncSSHEnvironment`: asyncssh + native SFTP behind
the ordinary :class:`~chimera.env.base.Environment` ABC, selectable as
``create_environment("ssh-async", ...)``.  This file pins that contract —
connect, exec, transfer, cleanup — so the abstraction cannot rot.

**Why a fake module instead of ``importorskip``.**  The existing async tests in
``tests/env/test_ssh.py`` skip whenever the optional ``[ssh]`` extra is absent,
which is exactly CI's posture — so they never actually guard the merge.  These
tests inject a fake ``asyncssh`` at ``chimera.env.ssh.asyncssh`` and run in
*every* posture.  They assert the argument shapes and control flow the real
library sees; ``tests/env/test_ssh_live.py`` covers the wire itself against a
containerised sshd.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.env.ssh import AsyncSSHEnvironment

# ---------------------------------------------------------------------------
# Fake asyncssh
# ---------------------------------------------------------------------------


class _FakeSSHError(Exception):
    """Stands in for ``asyncssh.Error``."""


class _FakeSFTPError(Exception):
    """Stands in for ``asyncssh.SFTPError``."""


class _Attrs:
    def __init__(self, permissions: int) -> None:
        self.permissions = permissions


class _Entry:
    def __init__(self, filename: str, is_dir: bool) -> None:
        self.filename = filename
        self.attrs = _Attrs(0o040755 if is_dir else 0o100644)


class _RemoteFS:
    """A tiny in-memory POSIX filesystem shared by SFTP and shell commands."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/"}

    def children(self, directory: str) -> dict[str, bool]:
        base = directory.rstrip("/")
        prefix = f"{base}/"
        if base and base not in self.dirs and not any(
            p.startswith(prefix) for p in self.files
        ):
            raise _FakeSFTPError(f"no such directory: {directory}")
        found: dict[str, bool] = {}
        for path in list(self.files) + list(self.dirs):
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if not rest:
                continue
            head, _, tail = rest.partition("/")
            found[head] = found.get(head, False) or bool(tail) or path in self.dirs
        return found


class _FakeFile:
    def __init__(self, fs: _RemoteFS, path: str, mode: str) -> None:
        self._fs, self._path, self._mode = fs, path, mode
        if "r" in mode and path not in fs.files:
            raise _FakeSFTPError(f"no such file: {path}")

    async def __aenter__(self) -> _FakeFile:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def read(self) -> bytes:
        return self._fs.files[self._path]

    async def write(self, data: str | bytes) -> None:
        self._fs.files[self._path] = (
            data.encode("utf-8") if isinstance(data, str) else data
        )


class _FakeSFTP:
    def __init__(self, fs: _RemoteFS, log: list[tuple[str, Any]]) -> None:
        self._fs, self._log = fs, log

    async def __aenter__(self) -> _FakeSFTP:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def open(self, path: str, mode: str) -> _FakeFile:
        self._log.append(("open", (path, mode)))
        return _FakeFile(self._fs, path, mode)

    async def makedirs(self, path: str, exist_ok: bool = False) -> None:
        self._log.append(("makedirs", path))
        self._fs.dirs.add(path.rstrip("/"))

    async def readdir(self, path: str) -> list[_Entry]:
        return [_Entry(n, is_dir) for n, is_dir in self._fs.children(path).items()]

    async def put(self, local: str, remote: str) -> None:
        self._log.append(("put", (local, remote)))
        with open(local, "rb") as fh:
            self._fs.files[remote] = fh.read()

    async def get(self, remote: str, local: str) -> None:
        self._log.append(("get", (remote, local)))
        if remote not in self._fs.files:
            raise _FakeSFTPError(remote)
        with open(local, "wb") as fh:
            fh.write(self._fs.files[remote])


class _RunResult:
    def __init__(self, stdout: str, stderr: str, exit_status: int) -> None:
        self.stdout, self.stderr, self.exit_status = stdout, stderr, exit_status


class _FakeConn:
    def __init__(self, fs: _RemoteFS, kwargs: dict[str, Any]) -> None:
        self.fs = fs
        self.kwargs = kwargs
        self.commands: list[str] = []
        self.sftp_log: list[tuple[str, Any]] = []
        self.closed = False
        self.scripted: dict[str, _RunResult] = {}
        self.hang_on: set[str] = set()

    async def run(self, command: str, check: bool = False) -> _RunResult:
        self.commands.append(command)
        if command in self.hang_on:
            import asyncio as _a

            await _a.sleep(30)
        for key, result in self.scripted.items():
            if key in command:
                return result
        return _RunResult(stdout=f"ran:{command}", stderr="", exit_status=0)

    def start_sftp_client(self) -> _FakeSFTP:
        return _FakeSFTP(self.fs, self.sftp_log)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FakeAsyncSSH:
    """Stands in for the top-level ``asyncssh`` module."""

    Error = _FakeSSHError
    SFTPError = _FakeSFTPError

    def __init__(self) -> None:
        self.fs = _RemoteFS()
        self.connect_calls: list[dict[str, Any]] = []
        self.conns: list[_FakeConn] = []
        self.fail_times = 0

    async def connect(self, **kwargs: Any) -> _FakeConn:
        self.connect_calls.append(kwargs)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise _FakeSSHError("connection refused")
        conn = _FakeConn(self.fs, kwargs)
        self.conns.append(conn)
        return conn


@pytest.fixture()
def sshd(monkeypatch: pytest.MonkeyPatch) -> _FakeAsyncSSH:
    """Install the fake asyncssh so the async backend runs in any posture."""
    fake = _FakeAsyncSSH()
    monkeypatch.setattr("chimera.env.ssh.asyncssh", fake)
    monkeypatch.setattr("chimera.env.ssh._ASYNCSSH_AVAILABLE", True)
    return fake


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


def test_missing_asyncssh_raises_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("chimera.env.ssh._ASYNCSSH_AVAILABLE", False)
    with pytest.raises(ImportError, match=r"chimera-run\[ssh\]"):
        AsyncSSHEnvironment(host="h")


def test_empty_host_is_rejected(sshd: _FakeAsyncSSH) -> None:
    with pytest.raises(ValueError, match="non-empty host"):
        AsyncSSHEnvironment(host="")


def test_credentials_are_forwarded_to_connect(sshd: _FakeAsyncSSH) -> None:
    env = AsyncSSHEnvironment(
        host="build.example.com",
        port=2222,
        username="deploy",
        client_keys=["/home/me/.ssh/id_ed25519"],
        password="pw",
        passphrase="pp",
        known_hosts=(),
    )
    env.setup()
    assert sshd.connect_calls[0] == {
        "host": "build.example.com",
        "port": 2222,
        "username": "deploy",
        "client_keys": ["/home/me/.ssh/id_ed25519"],
        "password": "pw",
        "passphrase": "pp",
        "known_hosts": (),
    }
    env.cleanup()


def test_unset_optionals_are_omitted_not_passed_as_none(
    sshd: _FakeAsyncSSH,
) -> None:
    """asyncssh treats an explicit None differently from an absent kwarg."""
    AsyncSSHEnvironment(host="h").setup()
    assert set(sshd.connect_calls[0]) == {"host", "port"}


def test_ssh_options_override_managed_kwargs(sshd: _FakeAsyncSSH) -> None:
    env = AsyncSSHEnvironment(
        host="h", username="a", ssh_options={"username": "b", "compression": True}
    )
    env.setup()
    assert sshd.connect_calls[0]["username"] == "b"
    assert sshd.connect_calls[0]["compression"] is True


def test_proxy_jump_chain_tunnels_every_hop(sshd: _FakeAsyncSSH) -> None:
    """Two bastions => three connects, each tunnelled through the previous."""
    env = AsyncSSHEnvironment(host="dest", username="u", proxy_jump="j1,j2")
    env.setup()
    assert [c["host"] for c in sshd.connect_calls] == ["j1", "j2", "dest"]
    assert "tunnel" not in sshd.connect_calls[0]
    assert sshd.connect_calls[1]["tunnel"] is sshd.conns[0]
    assert sshd.connect_calls[2]["tunnel"] is sshd.conns[1]
    # Credentials belong to the destination, not the bastions.
    assert sshd.connect_calls[2]["username"] == "u"


def test_connect_failure_surfaces_as_connection_error(sshd: _FakeAsyncSSH) -> None:
    sshd.fail_times = 1
    with pytest.raises(ConnectionError, match="AsyncSSH connect to h failed"):
        AsyncSSHEnvironment(host="h").setup()


def test_retries_reconnect_with_backoff(sshd: _FakeAsyncSSH) -> None:
    sshd.fail_times = 2
    env = AsyncSSHEnvironment(host="h", retries=2, retry_initial_delay=0.0)
    env.setup()
    assert len(sshd.connect_calls) == 3
    assert env.run_bash("true").success


def test_retries_give_up_after_the_budget(sshd: _FakeAsyncSSH) -> None:
    sshd.fail_times = 5
    with pytest.raises(ConnectionError):
        AsyncSSHEnvironment(host="h", retries=1, retry_initial_delay=0.0).setup()
    assert len(sshd.connect_calls) == 2


def test_use_before_setup_raises_a_clear_error(sshd: _FakeAsyncSSH) -> None:
    env = AsyncSSHEnvironment(host="h")
    with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
        env.run_bash("ls")
    with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
        env.read_file("a.txt")


# ---------------------------------------------------------------------------
# Exec
# ---------------------------------------------------------------------------


def test_exec_prefixes_the_workdir_and_quotes_it(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/my app") as env:
        env.run_bash("git status")
    assert sshd.conns[0].commands == ["cd '/srv/my app' && git status"]


def test_exec_without_a_workdir_sends_the_bare_command(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h") as env:  # workdir defaults to "."
        env.run_bash("ls")
    assert sshd.conns[0].commands == ["ls"]


def test_exec_maps_streams_and_exit_status(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h") as env:
        sshd.conns[0].scripted["boom"] = _RunResult("out", "err", 42)
        result = env.run_bash("boom")
    assert (result.stdout, result.stderr, result.exit_code) == ("out", "err", 42)
    assert not result.success


def test_exec_timeout_returns_124_rather_than_raising(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h") as env:
        sshd.conns[0].hang_on.add("sleep 30")
        result = env.run_bash("sleep 30", timeout=1)
    assert result.exit_code == 124 and "timed out" in result.stderr


def test_run_command_is_the_abc_alias_for_run_bash(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/w") as env:
        env.run_command("ls", timeout=5, shell_name="ignored")
    assert sshd.conns[0].commands == ["cd /w && ls"]


def test_run_bash_many_executes_every_command(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", max_concurrency=2) as env:
        results = env.run_bash_many(["a", "b", "c"])
    assert [r.stdout for r in results] == ["ran:a", "ran:b", "ran:c"]
    assert sorted(sshd.conns[0].commands) == ["a", "b", "c"]


def test_run_tests_shells_out_to_the_configured_command(
    sshd: _FakeAsyncSSH,
) -> None:
    with AsyncSSHEnvironment(host="h", test_cmd="pytest -q") as env:
        report = env.run_tests()
    assert "pytest -q" in sshd.conns[0].commands[0]
    assert "ran:" in report.output


# ---------------------------------------------------------------------------
# Transfer (SFTP)
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips_over_sftp(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        env.write_file("pkg/mod.py", "print('hi')")
        assert env.read_file("pkg/mod.py") == "print('hi')"
    assert sshd.fs.files["/srv/app/pkg/mod.py"] == b"print('hi')"


def test_write_file_creates_parent_directories(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        env.write_file("deep/nest/x.txt", "1")
    assert ("makedirs", "/srv/app/deep/nest") in sshd.conns[0].sftp_log


def test_absolute_paths_bypass_the_workdir(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        env.write_file("/etc/thing.conf", "x")
    assert "/etc/thing.conf" in sshd.fs.files


def test_reading_a_missing_file_raises_file_not_found(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        with pytest.raises(FileNotFoundError, match="SFTP read failed"):
            env.read_file("nope.txt")


def test_upload_and_download_preserve_bytes(sshd: _FakeAsyncSSH, tmp_path: Any) -> None:
    """Binary safety is the headline reason to prefer SFTP over `ssh cat`."""
    blob = bytes(range(256))
    src = tmp_path / "payload.bin"
    src.write_bytes(blob)
    dst = tmp_path / "back.bin"

    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        env.upload_file(str(src), "/srv/app/payload.bin")
        env.download_file("/srv/app/payload.bin", str(dst))

    assert sshd.fs.files["/srv/app/payload.bin"] == blob
    assert dst.read_bytes() == blob


def test_upload_files_transfers_every_pair(sshd: _FakeAsyncSSH, tmp_path: Any) -> None:
    pairs = []
    for name in ("a.txt", "b.txt"):
        local = tmp_path / name
        local.write_text(name)
        pairs.append((str(local), f"/srv/app/{name}"))

    with AsyncSSHEnvironment(host="h", max_concurrency=2) as env:
        env.upload_files(pairs)

    assert sshd.fs.files["/srv/app/a.txt"] == b"a.txt"
    assert sshd.fs.files["/srv/app/b.txt"] == b"b.txt"


def test_list_files_walks_sftp_and_honours_globs(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        env.write_file("a.py", "1")
        env.write_file("sub/b.py", "2")
        env.write_file("sub/c.txt", "3")
        assert env.list_files() == ["a.py", "sub/b.py", "sub/c.txt"]
        assert env.list_files("sub/*.py") == ["sub/b.py"]


# ---------------------------------------------------------------------------
# Checkpoint / restore
# ---------------------------------------------------------------------------


def test_checkpoint_tars_the_workdir_and_returns_an_id(
    sshd: _FakeAsyncSSH,
) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        cid = env.checkpoint()
        assert len(cid) == 16
        cmd = sshd.conns[0].commands[-1]
        assert "tar -czf" in cmd and f"{cid}.tar.gz" in cmd and "-C /srv app" in cmd


def test_checkpoint_requires_an_explicit_workdir(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h") as env:
        with pytest.raises(NotImplementedError, match="explicit workdir"):
            env.checkpoint()


def test_checkpoint_surfaces_a_tar_failure(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        sshd.conns[0].scripted["tar -czf"] = _RunResult("", "disk full", 2)
        with pytest.raises(OSError, match="disk full"):
            env.checkpoint()


def test_restore_untars_over_the_workdir(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        env.restore("deadbeefdeadbeef")
        cmd = sshd.conns[0].commands[-1]
        assert "tar -xzf" in cmd and "deadbeefdeadbeef.tar.gz" in cmd


def test_restore_rejects_path_traversal_in_the_id(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        for bad in ("", "../etc", "a/b"):
            with pytest.raises(ValueError, match="invalid checkpoint id"):
                env.restore(bad)


def test_restore_reports_a_missing_checkpoint(sshd: _FakeAsyncSSH) -> None:
    with AsyncSSHEnvironment(host="h", workdir="/srv/app") as env:
        sshd.conns[0].scripted["tar -xzf"] = _RunResult("", "", 1)
        with pytest.raises(FileNotFoundError, match="not found on h"):
            env.restore("deadbeefdeadbeef")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_cleanup_closes_the_connection_and_the_loop(sshd: _FakeAsyncSSH) -> None:
    env = AsyncSSHEnvironment(host="h")
    env.setup()
    conn = sshd.conns[0]
    env.cleanup()
    assert conn.closed is True
    assert env._conn is None and env._loop is None


def test_cleanup_is_idempotent(sshd: _FakeAsyncSSH) -> None:
    env = AsyncSSHEnvironment(host="h")
    env.setup()
    env.cleanup()
    env.cleanup()  # must not raise


def test_context_manager_cleans_up_on_exception(sshd: _FakeAsyncSSH) -> None:
    with pytest.raises(ZeroDivisionError):
        with AsyncSSHEnvironment(host="h"):
            raise ZeroDivisionError
    assert sshd.conns[0].closed is True


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------


def test_selectable_through_the_universal_factory(sshd: _FakeAsyncSSH) -> None:
    from chimera.env.factory import available_providers, create_environment

    assert {"ssh", "ssh-async"} <= set(available_providers())
    env = create_environment("ssh-async", host="h", workdir="/srv/app")
    assert isinstance(env, AsyncSSHEnvironment)
    with env:
        assert env.run_bash("true").success
