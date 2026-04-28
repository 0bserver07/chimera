"""Backend selection for ``chimera mink --remote`` (M2 follow-up, M8).

Wave-2 shipped :class:`chimera.env.ssh.AsyncSSHEnvironment` alongside the
existing subprocess :class:`SSHEnvironment`. Wave-3 wires the async
backend into the mink CLI behind the ``CHIMERA_SSH_BACKEND=async``
environment variable so users with the optional ``asyncssh`` extra can
opt in without breaking the zero-dependency default for everyone else.

These tests cover the selection matrix exhaustively:

1. No ``--remote`` flag → :class:`LocalEnvironment` regardless of the
   env var (the var should never affect local execution).
2. ``--remote …`` + env var unset → subprocess :class:`SSHEnvironment`
   (the wave-1 default; backwards compat).
3. ``--remote …`` + ``CHIMERA_SSH_BACKEND=async`` + asyncssh missing →
   subprocess :class:`SSHEnvironment` (graceful fall-through, no crash).
4. ``--remote …`` + ``CHIMERA_SSH_BACKEND=async`` + asyncssh present →
   :class:`AsyncSSHEnvironment` (the new opt-in path).
5. ``--remote …`` + unrecognized backend value → subprocess (defensive
   default; we don't want a typo to silently disable remote execution).

We avoid spinning up real SSH connections by patching
:func:`importlib.util.find_spec` (so we can pretend asyncssh is or
isn't installed regardless of the test runner's environment) and by
asserting on the returned class, not its behavior. The async path is
additionally guarded by ``pytest.importorskip("asyncssh")`` because
:class:`AsyncSSHEnvironment.__init__` raises ``ImportError`` when the
real module is missing — so we can only assert on the class type when
the real package is available.
"""

from __future__ import annotations

import argparse
import importlib.util
from typing import Any
from unittest import mock

import pytest

from chimera.mink import cli as mink_cli


def _make_args(remote: str | None) -> argparse.Namespace:
    """Build a minimal argparse Namespace mirroring what ``main()`` parses.

    ``_build_environment`` only reads ``args.remote``, so we don't need
    to populate the rest of the surface.
    """
    return argparse.Namespace(remote=remote)


# ---------------------------------------------------------------------------
# 1. No --remote flag — env var must never affect local execution
# ---------------------------------------------------------------------------


def test_local_when_remote_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``--remote``, the env var is irrelevant — Local always wins."""
    from chimera.env.local import LocalEnvironment

    monkeypatch.setenv("CHIMERA_SSH_BACKEND", "async")
    env = mink_cli._build_environment(_make_args(remote=None), cwd="/tmp")
    assert isinstance(env, LocalEnvironment)


# ---------------------------------------------------------------------------
# 2. --remote, env var unset — subprocess SSHEnvironment (wave-1 default)
# ---------------------------------------------------------------------------


def test_subprocess_default_when_env_var_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default backend stays subprocess — backwards compat."""
    from chimera.env.ssh import SSHEnvironment

    monkeypatch.delenv("CHIMERA_SSH_BACKEND", raising=False)
    env = mink_cli._build_environment(
        _make_args(remote="ssh://deploy@host.example.com:/srv/app"),
        cwd="/tmp",
    )
    assert isinstance(env, SSHEnvironment)
    assert env.host == "deploy@host.example.com"
    assert env.workdir == "/srv/app"


# ---------------------------------------------------------------------------
# 3. --remote + async env var, but asyncssh isn't installed → subprocess
# ---------------------------------------------------------------------------


def test_subprocess_when_async_requested_but_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing asyncssh must fall through silently — never crash."""
    from chimera.env.ssh import SSHEnvironment

    monkeypatch.setenv("CHIMERA_SSH_BACKEND", "async")

    real_find_spec = importlib.util.find_spec

    def _fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "asyncssh":
            return None
        return real_find_spec(name, *args, **kwargs)

    with mock.patch("importlib.util.find_spec", side_effect=_fake_find_spec):
        env = mink_cli._build_environment(
            _make_args(remote="ssh://user@host.example.com"),
            cwd="/tmp",
        )
    assert isinstance(env, SSHEnvironment)


# ---------------------------------------------------------------------------
# 4. --remote + async env var + asyncssh present → AsyncSSHEnvironment
# ---------------------------------------------------------------------------


def test_async_when_requested_and_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in path: env var + extra installed → AsyncSSHEnvironment.

    Skipped when the optional ``asyncssh`` extra isn't available — its
    constructor raises ``ImportError`` in that case and we cannot
    instantiate the class to assert on its type.
    """
    pytest.importorskip("asyncssh")
    from chimera.env.ssh import AsyncSSHEnvironment

    monkeypatch.setenv("CHIMERA_SSH_BACKEND", "async")
    env = mink_cli._build_environment(
        _make_args(remote="ssh://deploy@host.example.com:2222/srv/app"),
        cwd="/tmp",
    )
    assert isinstance(env, AsyncSSHEnvironment)
    # URL parsing must split user@host into username/host kwargs.
    assert env.host == "host.example.com"
    assert env.username == "deploy"
    assert env.port == 2222
    assert env.workdir == "/srv/app"


def test_async_handles_bare_host_without_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``user@`` in the URL → ``username`` stays ``None`` (asyncssh default)."""
    pytest.importorskip("asyncssh")
    from chimera.env.ssh import AsyncSSHEnvironment

    monkeypatch.setenv("CHIMERA_SSH_BACKEND", "async")
    env = mink_cli._build_environment(
        _make_args(remote="ssh://host.example.com"),
        cwd="/tmp",
    )
    assert isinstance(env, AsyncSSHEnvironment)
    assert env.host == "host.example.com"
    assert env.username is None


# ---------------------------------------------------------------------------
# 5. Defensive: unrecognized backend value falls through to subprocess
# ---------------------------------------------------------------------------


def test_unknown_backend_value_falls_through_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo or future value must not silently break remote execution."""
    from chimera.env.ssh import SSHEnvironment

    monkeypatch.setenv("CHIMERA_SSH_BACKEND", "tokio")  # nonsense
    env = mink_cli._build_environment(
        _make_args(remote="ssh://user@host.example.com"),
        cwd="/tmp",
    )
    assert isinstance(env, SSHEnvironment)


# ---------------------------------------------------------------------------
# Selection helper unit tests (independent of --remote plumbing)
# ---------------------------------------------------------------------------


def test_select_ssh_backend_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_SSH_BACKEND", raising=False)
    assert mink_cli._select_ssh_backend() == "subprocess"


def test_select_ssh_backend_async_value_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ASYNC`` / ``Async`` should behave like ``async`` (UX nicety)."""
    pytest.importorskip("asyncssh")
    monkeypatch.setenv("CHIMERA_SSH_BACKEND", "ASYNC")
    assert mink_cli._select_ssh_backend() == "async"


def test_select_ssh_backend_async_falls_back_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHIMERA_SSH_BACKEND", "async")
    real_find_spec = importlib.util.find_spec

    def _fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "asyncssh":
            return None
        return real_find_spec(name, *args, **kwargs)

    with mock.patch("importlib.util.find_spec", side_effect=_fake_find_spec):
        assert mink_cli._select_ssh_backend() == "subprocess"
