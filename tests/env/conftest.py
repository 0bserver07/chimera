"""Pytest fixtures for live SSH testing against a containerized sshd.

This module provides a single session-scoped fixture, :func:`docker_sshd`,
that boots `linuxserver/openssh-server` on an ephemeral local port, wires
in a freshly-generated SSH key pair, and yields connection details to
opt-in tests marked ``@pytest.mark.live_ssh``.

Tests that aren't tagged with the ``live_ssh`` marker are unaffected —
the fixture is only instantiated when a live test requests it. This
keeps the regular unit suite (``tests/env/test_ssh.py``,
``tests/env/test_ssh_environment.py``) Docker-free.

Opt-in invocation::

    uv run pytest -m live_ssh

When Docker isn't reachable, every live test is skipped (not failed)
with a helpful message.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest


# ---------------------------------------------------------------------------
# Marker registration
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``live_ssh`` marker so ``-m live_ssh`` works without warnings."""
    config.addinivalue_line(
        "markers",
        "live_ssh: opt-in tests that exercise SSHEnvironment against a "
        "Docker-hosted sshd (run with `pytest -m live_ssh`). Skipped "
        "automatically when Docker is unreachable.",
    )


# ---------------------------------------------------------------------------
# Fixture data class
# ---------------------------------------------------------------------------


class SshdEndpoint(NamedTuple):
    """Connection details for a live sshd container.

    Attributes:
        host: Hostname to dial. Always ``127.0.0.1`` for the in-Docker fixture.
        port: Host-side TCP port forwarded to container port 2222.
        username: Username configured inside the container (``test``).
        key_path: Absolute path to the *private* key matching the
            container's authorized_keys.
        container_id: Docker container id (returned by ``docker run -d``).
            Tests don't normally need this, but it's exposed for diagnostics.
    """

    host: str
    port: int
    username: str
    key_path: str
    container_id: str


# ---------------------------------------------------------------------------
# Probe helpers (Docker availability + ssh readiness)
# ---------------------------------------------------------------------------


def _docker_available() -> tuple[bool, str]:
    """Return ``(ok, reason)`` describing whether Docker can be used.

    We probe with ``docker info`` rather than ``docker version`` because
    the former exits non-zero when the daemon socket is unreachable
    (the version subcommand only checks the client binary).
    """
    if shutil.which("docker") is None:
        return False, "`docker` CLI not found on PATH"
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"`docker info` probe failed: {exc}"
    if result.returncode != 0:
        # Concatenate stderr for a one-line skip reason; trim long banners.
        msg = (result.stderr or result.stdout or "").strip().splitlines()
        first = msg[0] if msg else "non-zero exit"
        return False, f"docker daemon not reachable: {first}"
    return True, ""


def _wait_for_port(host: str, port: int, *, timeout: float = 30.0) -> bool:
    """Block until ``host:port`` accepts a TCP connection or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _wait_for_sshd_banner(
    host: str, port: int, *, timeout: float = 30.0
) -> bool:
    """Block until the remote socket emits the ``SSH-`` protocol banner.

    A bare TCP-accept isn't enough — linuxserver/openssh-server takes
    another second or two after the listener binds before it generates
    host keys and starts speaking the protocol.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0) as sock:
                sock.settimeout(2.0)
                banner = sock.recv(4)
                if banner.startswith(b"SSH-"):
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def _get_host_port(container_id: str) -> int:
    """Return the host-side port mapped to container port 2222.

    ``docker port <id> 2222/tcp`` prints lines like ``0.0.0.0:54321``.
    We pick the first one and parse off the port suffix.
    """
    result = subprocess.run(
        ["docker", "port", container_id, "2222/tcp"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"`docker port {container_id} 2222/tcp` failed: "
            f"{result.stderr.strip() or '(no stderr)'}"
        )
    # Take the first mapping line, then the part after the last ':'.
    first_line = result.stdout.strip().splitlines()[0]
    return int(first_line.rsplit(":", 1)[1])


def _generate_keypair(dest_dir: Path) -> Path:
    """Generate an ed25519 keypair under ``dest_dir`` and return the private path.

    Layout:
        ``<dest_dir>/id_ed25519``       — private key (mode 0o600)
        ``<dest_dir>/id_ed25519.pub``   — public key (mode 0o644)

    Raises:
        RuntimeError: When ``ssh-keygen`` is missing or fails.
    """
    if shutil.which("ssh-keygen") is None:
        raise RuntimeError("`ssh-keygen` not found on PATH; cannot generate test key")
    priv = dest_dir / "id_ed25519"
    result = subprocess.run(
        [
            "ssh-keygen",
            "-t", "ed25519",
            "-N", "",                # no passphrase
            "-C", "chimera-live-ssh-test",
            "-f", str(priv),
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ssh-keygen failed: {result.stderr.strip() or '(no stderr)'}"
        )
    os.chmod(priv, 0o600)
    return priv


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_sshd(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SshdEndpoint]:
    """Boot a linuxserver/openssh-server container for the test session.

    The fixture is **session-scoped** so the container starts at most
    once per ``pytest`` invocation, even when many ``live_ssh`` tests
    consume it.

    Behavior:

    * Probes Docker daemon reachability via ``docker info``. If unreachable,
      every consuming test is skipped with a helpful reason.
    * Generates a fresh ed25519 keypair into a session-scoped tmp dir.
    * Runs ``docker run -d --rm -p 0:2222 -e USER_NAME=test
      -e PUBLIC_KEY=<pub> linuxserver/openssh-server:latest``.
    * Waits up to 30s for the SSH banner before yielding.
    * Tears the container down via ``docker rm -f`` in the finalizer.

    Yields:
        :class:`SshdEndpoint` with ``(host, port, username, key_path,
        container_id)``.
    """
    ok, reason = _docker_available()
    if not ok:
        pytest.skip(f"docker_sshd fixture skipped: {reason}")

    # Session-scoped tmp dir for the keypair. Survives until the session ends.
    keys_dir = tmp_path_factory.mktemp("chimera-live-ssh-keys")
    try:
        priv_key = _generate_keypair(keys_dir)
    except RuntimeError as exc:
        pytest.skip(f"docker_sshd fixture skipped: {exc}")

    pub_key = (keys_dir / "id_ed25519.pub").read_text().strip()

    # Pull the image up front so the run probe doesn't time out on slow networks.
    # We tolerate a non-zero exit from `pull` (e.g. offline with cached image)
    # — the `docker run` below will surface a real failure.
    subprocess.run(
        ["docker", "pull", "linuxserver/openssh-server:latest"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    # Run with --rm so a hard-killed pytest still leaves no garbage. Pass
    # PUBLIC_KEY directly (image supports it) so we don't need a bind mount,
    # which would race on macOS/Windows Docker Desktop file-sharing.
    run_cmd = [
        "docker", "run", "-d", "--rm",
        "-p", "0:2222",
        "-e", "USER_NAME=test",
        "-e", "USER_UID=1000",
        "-e", "USER_GID=1000",
        "-e", "SUDO_ACCESS=false",
        "-e", "PASSWORD_ACCESS=false",
        "-e", f"PUBLIC_KEY={pub_key}",
        "linuxserver/openssh-server:latest",
    ]
    result = subprocess.run(
        run_cmd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            f"docker_sshd fixture skipped: `docker run` failed: "
            f"{result.stderr.strip() or '(no stderr)'}"
        )
    container_id = result.stdout.strip()
    if not container_id:
        pytest.skip("docker_sshd fixture skipped: empty container id from docker run")

    try:
        port = _get_host_port(container_id)
    except (RuntimeError, ValueError) as exc:
        # Best-effort cleanup before bailing.
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True, check=False, timeout=10,
        )
        pytest.skip(f"docker_sshd fixture skipped: {exc}")

    host = "127.0.0.1"
    if not _wait_for_port(host, port, timeout=30.0):
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True, check=False, timeout=10,
        )
        pytest.skip(
            f"docker_sshd fixture skipped: container {container_id[:12]} "
            f"never opened TCP {host}:{port}"
        )
    if not _wait_for_sshd_banner(host, port, timeout=30.0):
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True, check=False, timeout=10,
        )
        pytest.skip(
            f"docker_sshd fixture skipped: sshd in {container_id[:12]} "
            f"never advertised an SSH banner"
        )

    endpoint = SshdEndpoint(
        host=host,
        port=port,
        username="test",
        key_path=str(priv_key),
        container_id=container_id,
    )
    try:
        yield endpoint
    finally:
        # ``--rm`` plus ``docker rm -f`` guarantees teardown even if the
        # container is still running. We swallow errors — the fixture has
        # already done its job; a stale container is a worse outcome than a
        # noisy traceback in finalization.
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
