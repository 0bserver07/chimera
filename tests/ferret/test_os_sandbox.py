"""Tests for :mod:`chimera.ferret.os_sandbox`.

Static-only — these tests *never* try to actually break out of the
sandbox or exec anything inside it. They check:

* Profile / argv generation is well-formed.
* Availability detection returns a sensible snapshot for the running
  platform.
* Fail-open behavior on unsupported platforms surfaces a clear bool.

Each platform-specific block is gated by ``pytest.importorskip`` /
``platform.system()`` so the whole file passes on CI runners regardless
of OS.
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from chimera.ferret.os_sandbox import (
    OSSandboxAvailability,
    describe_os_sandbox,
    detect_availability,
    is_landlock_available,
    is_seatbelt_available,
    parse_os_sandbox_flag,
    seatbelt_profile,
    wrap_bash_command,
)
from chimera.ferret.sandbox import SandboxMode


# ---------------------------------------------------------------------------
# CLI flag parsing
# ---------------------------------------------------------------------------


def test_parse_os_sandbox_flag_default() -> None:
    assert parse_os_sandbox_flag(None) == "auto"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("auto", "auto"),
        ("AUTO", "auto"),
        ("on", "on"),
        ("ON", "on"),
        ("off", "off"),
        ("  off ", "off"),
    ],
)
def test_parse_os_sandbox_flag_valid(raw: str, expected: str) -> None:
    assert parse_os_sandbox_flag(raw) == expected


def test_parse_os_sandbox_flag_invalid() -> None:
    with pytest.raises(ValueError, match="Unknown --os-sandbox"):
        parse_os_sandbox_flag("yolo")


# ---------------------------------------------------------------------------
# Availability detection
# ---------------------------------------------------------------------------


def test_detect_availability_returns_snapshot() -> None:
    snap = detect_availability()
    assert isinstance(snap, OSSandboxAvailability)
    assert snap.platform == platform.system()
    # Exactly one of (seatbelt, landlock) should be true on a supported
    # host, both false on others. The boolean type is what matters.
    assert isinstance(snap.seatbelt, bool)
    assert isinstance(snap.landlock, bool)


def test_describe_os_sandbox_is_human_readable() -> None:
    desc = describe_os_sandbox()
    assert isinstance(desc, str)
    assert "os-sandbox" in desc


def test_availability_consistent_with_helpers() -> None:
    snap = detect_availability()
    assert is_seatbelt_available() is snap.seatbelt
    assert is_landlock_available() is snap.landlock


# ---------------------------------------------------------------------------
# Seatbelt profile generation — pure function, runs on every platform
# ---------------------------------------------------------------------------


def test_seatbelt_profile_read_only_basic_shape(tmp_path: Path) -> None:
    profile = seatbelt_profile(SandboxMode.READ_ONLY, tmp_path)
    assert profile.startswith("(version 1)")
    assert "(deny default)" in profile
    # Workdir read should be allowed.
    assert f'(subpath "{tmp_path}")' in profile
    # READ_ONLY must NOT contain a generic file-write* allow.
    assert "(allow file-write* (subpath" not in profile
    # READ_ONLY must NOT contain (allow network*).
    assert "(allow network*)" not in profile


def test_seatbelt_profile_workspace_write(tmp_path: Path) -> None:
    profile = seatbelt_profile(SandboxMode.WORKSPACE_WRITE, tmp_path)
    assert "(deny default)" in profile
    assert f'(allow file-write* (subpath "{tmp_path}"))' in profile
    # Network still denied in workspace-write.
    assert "(allow network*)" not in profile


def test_seatbelt_profile_workspace_write_network(tmp_path: Path) -> None:
    profile = seatbelt_profile(
        SandboxMode.WORKSPACE_WRITE_NETWORK, tmp_path,
    )
    assert "(deny default)" in profile
    assert f'(allow file-write* (subpath "{tmp_path}"))' in profile
    assert "(allow network*)" in profile


def test_seatbelt_profile_extra_read_paths(tmp_path: Path) -> None:
    extra = tmp_path / "shared-cache"
    extra.mkdir()
    profile = seatbelt_profile(
        SandboxMode.READ_ONLY, tmp_path, extra_read_paths=[str(extra)],
    )
    assert f'(allow file-read-data (subpath "{extra}"))' in profile


def test_seatbelt_profile_quotes_paths_with_special_chars(
    tmp_path: Path,
) -> None:
    weird = tmp_path / 'name with "quote"'
    weird.mkdir()
    profile = seatbelt_profile(SandboxMode.READ_ONLY, weird)
    # Backslash-escape of the embedded double-quote must appear.
    assert '\\"quote\\"' in profile


def test_seatbelt_profile_rejects_bogus_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown sandbox mode"):
        seatbelt_profile("garbage", tmp_path)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# wrap_bash_command — argv shape is platform-aware
# ---------------------------------------------------------------------------


def test_wrap_bash_command_returns_list(tmp_path: Path) -> None:
    argv = wrap_bash_command(
        "echo hello", mode=SandboxMode.READ_ONLY, workdir=tmp_path,
    )
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    assert "echo hello" in argv  # the cmd is the last arg


def test_wrap_bash_command_falls_open_when_no_seatbelt(tmp_path: Path) -> None:
    argv = wrap_bash_command(
        "echo hi", mode=SandboxMode.READ_ONLY, workdir=tmp_path,
    )
    if is_seatbelt_available():
        assert argv[0] == "sandbox-exec"
        assert argv[1] == "-p"
        assert argv[3] == "bash"
        assert argv[4] == "-c"
        assert argv[5] == "echo hi"
    else:
        # Fall-through form on Linux / Windows / unsupported macOS.
        assert argv == ["bash", "-c", "echo hi"]


# ---------------------------------------------------------------------------
# macOS-specific assertions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="seatbelt is macOS-only",
)
def test_seatbelt_profile_runnable_shape_on_macos(tmp_path: Path) -> None:
    """On macOS we expect detection to succeed and the wrap form to use
    sandbox-exec. We do *not* actually invoke sandbox-exec here — that
    would be an integration concern."""
    pytest.importorskip("ctypes")
    # detect_availability is cached; just sanity-check the snapshot.
    snap = detect_availability()
    if not snap.seatbelt:
        pytest.skip("sandbox-exec not on PATH on this macOS host")
    argv = wrap_bash_command(
        "true", mode=SandboxMode.WORKSPACE_WRITE, workdir=tmp_path,
    )
    assert argv[0] == "sandbox-exec"
    # Embedded profile string is well-formed.
    profile = argv[2]
    assert profile.startswith("(version 1)")
    assert profile.count("(deny default)") == 1


# ---------------------------------------------------------------------------
# Linux-specific assertions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    platform.system() != "Linux", reason="landlock is Linux-only",
)
def test_landlock_ctypes_import_smoke() -> None:
    """The Landlock probe path is pure ctypes; importing it should never
    raise. Whether the kernel actually supports Landlock is host-
    dependent and therefore *informational* — we simply ensure the
    ctypes machinery loaded."""
    import ctypes
    import ctypes.util

    libc = ctypes.util.find_library("c")
    if libc is None:
        pytest.skip("libc not loadable on this host")
    # Just confirm we can wrap libc — we don't make any real syscalls.
    ctypes.CDLL(libc, use_errno=True)


@pytest.mark.skipif(
    platform.system() != "Linux", reason="landlock is Linux-only",
)
def test_is_landlock_available_returns_bool() -> None:
    """Detection must return a boolean even on hosts that don't support
    Landlock (older kernels, containers with restricted syscalls)."""
    assert isinstance(is_landlock_available(), bool)


# ---------------------------------------------------------------------------
# SandboxedEnvironment composition smoke
# ---------------------------------------------------------------------------


def test_sandboxed_env_accepts_os_sandbox_flag(tmp_path: Path) -> None:
    """The SandboxedEnvironment constructor must accept the
    ``os_sandbox=`` kwarg without error — this is the wiring contract
    the CLI relies on."""
    from chimera.env.local import LocalEnvironment
    from chimera.ferret.sandbox import SandboxedEnvironment

    inner = LocalEnvironment(workdir=str(tmp_path))
    inner.setup()
    try:
        for flag in ("auto", "on", "off"):
            sb = SandboxedEnvironment(
                inner, mode=SandboxMode.READ_ONLY, os_sandbox=flag,
            )
            assert sb.os_sandbox == flag
    finally:
        inner.cleanup()


def test_sandboxed_env_clone_preserves_os_sandbox(tmp_path: Path) -> None:
    from chimera.env.local import LocalEnvironment
    from chimera.ferret.sandbox import SandboxedEnvironment

    inner = LocalEnvironment(workdir=str(tmp_path))
    inner.setup()
    try:
        sb = SandboxedEnvironment(
            inner, mode=SandboxMode.WORKSPACE_WRITE, os_sandbox="off",
        )
        cloned = sb.clone()
        try:
            assert cloned.os_sandbox == "off"
            assert cloned.mode is SandboxMode.WORKSPACE_WRITE
        finally:
            cloned.cleanup()
    finally:
        inner.cleanup()
