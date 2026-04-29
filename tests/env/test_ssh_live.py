"""Live integration tests for :class:`SSHEnvironment` against Docker sshd.

Each test in this module is tagged with ``@pytest.mark.live_ssh`` so it
is skipped by default. To run them, opt in explicitly::

    uv run pytest -m live_ssh

The :func:`docker_sshd` fixture (see ``conftest.py``) boots
``linuxserver/openssh-server`` on an ephemeral local port, generates a
throw-away ed25519 keypair, and tears the container down at session
end. When Docker is unreachable the fixture skips — these tests never
fail spuriously on CI without a daemon.

Why exercise these against a real sshd?

* The mocked tests in ``test_ssh.py`` / ``test_ssh_environment.py``
  pin argv shape and error handling, but don't catch wire-format /
  permission / line-ending issues that only surface in a live session.
* Checkpoint / restore round-trips through tar — the only way to
  validate the parent-dir + leaf wiring is to actually create files,
  snapshot, mutate, and restore.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Some Chimera test modules transitively pull rich via chimera.mink.cli;
# match that here so the file degrades cleanly when the [mink] extra is
# missing. The conftest fixture itself only depends on stdlib + Docker.
pytest.importorskip("rich")

from chimera.env.ssh import SSHEnvironment
from tests.env.conftest import SshdEndpoint


# Apply the marker to every test in this module.
pytestmark = pytest.mark.live_ssh


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(endpoint: SshdEndpoint, *, workdir: str = ".") -> SSHEnvironment:
    """Construct an :class:`SSHEnvironment` wired to the live fixture.

    StrictHostKeyChecking is disabled because the container's host key is
    freshly generated on every session and we don't want to taint the
    user's known_hosts file. UserKnownHostsFile=/dev/null prevents
    accidental persistence.
    """
    return SSHEnvironment(
        host=f"{endpoint.username}@{endpoint.host}",
        port=endpoint.port,
        identity_file=endpoint.key_path,
        workdir=workdir,
        ssh_options={
            "StrictHostKeyChecking": "no",
            "UserKnownHostsFile": "/dev/null",
            "LogLevel": "ERROR",
            # The container only enables pubkey auth — be explicit so the
            # test doesn't hang prompting for a password if something is off.
            "PasswordAuthentication": "no",
            "BatchMode": "yes",
        },
        timeout=30,
    )


# ---------------------------------------------------------------------------
# 1. run_bash — basic command execution
# ---------------------------------------------------------------------------


def test_run_bash_against_live_container(docker_sshd: SshdEndpoint) -> None:
    """``run_bash`` round-trips through a real sshd and returns command output."""
    env = _make_env(docker_sshd)
    env.setup()
    try:
        result = env.run_bash("echo 'hello from container' && whoami")
        assert result.exit_code == 0, (
            f"run_bash exited {result.exit_code}; stderr={result.stderr!r}"
        )
        assert "hello from container" in result.stdout
        # The fixture configures USER_NAME=test inside the container.
        assert "test" in result.stdout

        # A failing command must surface its non-zero exit verbatim.
        bad = env.run_bash("exit 7")
        assert bad.exit_code == 7
    finally:
        env.cleanup()


# ---------------------------------------------------------------------------
# 2. upload_file — scp round-trip
# ---------------------------------------------------------------------------


def test_upload_file_round_trip(
    docker_sshd: SshdEndpoint, tmp_path: Path
) -> None:
    """``upload_file`` puts bytes on the remote; ``read_file`` reads them back."""
    # Use the test user's home (~/) as workdir — guaranteed writable.
    env = _make_env(docker_sshd, workdir="/config")
    env.setup()
    try:
        local = tmp_path / "payload.txt"
        local.write_text("alpha\nbeta\ngamma\n")

        # Upload to an absolute remote path under /config (writable home in
        # linuxserver/openssh-server).
        remote_path = "/config/uploaded.txt"
        env.upload_file(str(local), remote_path)

        # Read it back via the env's read_file (which uses `ssh cat`).
        contents = env.read_file(remote_path)
        assert contents == "alpha\nbeta\ngamma\n"

        # And via run_bash for an independent verification path.
        result = env.run_bash(f"wc -l < {remote_path}")
        assert result.exit_code == 0
        assert result.stdout.strip() == "3"
    finally:
        env.cleanup()


# ---------------------------------------------------------------------------
# 3. checkpoint / restore — tar snapshot round-trip
# ---------------------------------------------------------------------------


def test_checkpoint_restore_round_trip(docker_sshd: SshdEndpoint) -> None:
    """Snapshot a workdir, mutate it, then restore should bring it back.

    Previously xfail-ed: surfaced a real bug where the snapshot path
    was wrapped in :func:`shlex.quote`, single-quoting ``$HOME`` and
    blocking POSIX shell expansion. Fixed in wave-4 (L2) by leaving
    ``$HOME`` unquoted in the remote command — see research/mink/M9
    and L2 reports.
    """
    workdir = "/config/chimera-ckpt-test"

    # SSHEnvironment.run_bash prepends ``cd <workdir>`` before every
    # command, so we materialize the workdir via a helper env rooted at
    # the parent before switching to the real one.
    bootstrap = _make_env(docker_sshd, workdir="/config")
    bootstrap.setup()
    try:
        prep = bootstrap.run_bash(f"mkdir -p {workdir}")
        assert prep.exit_code == 0, f"workdir bootstrap failed: {prep.stderr!r}"
    finally:
        bootstrap.cleanup()

    env = _make_env(docker_sshd, workdir=workdir)
    env.setup()
    try:
        # Seed a known file inside the now-existing workdir.
        seed = env.run_bash("echo original > marker.txt")
        assert seed.exit_code == 0, f"seed failed: {seed.stderr!r}"

        # Snapshot. The checkpoint helper builds a remote command of the
        # form ``mkdir -p $HOME/.chimera/... && tar -czf $HOME/...``;
        # both legs leave ``$HOME`` unquoted so the remote shell expands
        # it before tar opens the destination file.
        cid = env.checkpoint()
        assert cid and len(cid) == 16, f"unexpected checkpoint id: {cid!r}"

        # Mutate the workdir AFTER checkpoint.
        mutate = env.run_bash("echo modified > marker.txt && echo new > extra.txt")
        assert mutate.exit_code == 0

        pre_restore = env.read_file("/config/chimera-ckpt-test/marker.txt")
        assert pre_restore.strip() == "modified"

        # Restore.
        env.restore(cid)
        post_restore = env.read_file("/config/chimera-ckpt-test/marker.txt")
        assert post_restore.strip() == "original"

        # ``extra.txt`` was added after the snapshot — restore wipes the
        # workdir before extracting, so it must be gone.
        check = env.run_bash("test -f extra.txt && echo exists || echo gone")
        assert "gone" in check.stdout
    finally:
        env.cleanup()
        # Best-effort cleanup of any residue inside the container via a
        # parent-rooted env so we don't try to ``cd`` into a directory
        # we're about to delete. The container itself is torn down by
        # the fixture finalizer regardless.
        cleaner = _make_env(docker_sshd, workdir="/config")
        cleaner.setup()
        try:
            cleaner.run_bash(f"rm -rf {workdir}")
        finally:
            cleaner.cleanup()
