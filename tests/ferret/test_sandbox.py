"""Tests for :mod:`chimera.ferret.sandbox`.

Verify that each sandbox mode allows / blocks the right operations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.env.local import LocalEnvironment
from chimera.ferret.sandbox import (
    SandboxedEnvironment,
    SandboxMode,
    SandboxViolation,
    parse_sandbox_mode,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_env(tmp_path: Path) -> LocalEnvironment:
    env = LocalEnvironment(workdir=str(tmp_path))
    env.setup()
    # Seed a file so reads have something to do.
    (tmp_path / "hello.txt").write_text("hi\n")
    return env


# ---------------------------------------------------------------------------
# parse_sandbox_mode
# ---------------------------------------------------------------------------


def test_parse_sandbox_mode_default_is_read_only() -> None:
    assert parse_sandbox_mode(None) is SandboxMode.READ_ONLY


@pytest.mark.parametrize(
    "value,expected",
    [
        ("read-only", SandboxMode.READ_ONLY),
        ("READ-ONLY", SandboxMode.READ_ONLY),
        ("read_only", SandboxMode.READ_ONLY),
        ("workspace-write", SandboxMode.WORKSPACE_WRITE),
        ("workspace-write-network", SandboxMode.WORKSPACE_WRITE_NETWORK),
    ],
)
def test_parse_sandbox_mode_strings(value: str, expected: SandboxMode) -> None:
    assert parse_sandbox_mode(value) is expected


def test_parse_sandbox_mode_passthrough() -> None:
    assert parse_sandbox_mode(SandboxMode.WORKSPACE_WRITE) is SandboxMode.WORKSPACE_WRITE


def test_parse_sandbox_mode_invalid() -> None:
    with pytest.raises(ValueError, match="Unknown sandbox mode"):
        parse_sandbox_mode("danger-full-access")


# ---------------------------------------------------------------------------
# READ_ONLY
# ---------------------------------------------------------------------------


def test_read_only_allows_read(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    assert sandbox.read_file("hello.txt") == "hi\n"


def test_read_only_blocks_write(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="write_file"):
        sandbox.write_file("new.txt", "nope")
    assert not (local_env.workdir / "new.txt").exists()


def test_read_only_allows_safe_bash(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    result = sandbox.run_command("ls")
    assert result.exit_code == 0
    assert "hello.txt" in result.stdout


def test_read_only_allows_grep_pipeline(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    result = sandbox.run_command("cat hello.txt | grep hi")
    assert result.exit_code == 0


def test_read_only_blocks_rm(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="rm"):
        sandbox.run_command("rm hello.txt")
    assert (local_env.workdir / "hello.txt").exists()


def test_read_only_blocks_redirect(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="redirect"):
        sandbox.run_command("echo bye > hello.txt")


def test_read_only_blocks_sed_in_place(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="sed -i"):
        sandbox.run_command("sed -i 's/hi/bye/' hello.txt")


def test_read_only_blocks_pip_install(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="pip"):
        sandbox.run_command("pip install requests")


def test_read_only_blocks_curl(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="curl"):
        sandbox.run_command("curl https://example.com")


def test_read_only_blocks_git_commit(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="git"):
        sandbox.run_command("git commit -m 'wip'")


def test_read_only_allows_git_status(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    # ``git status`` in a non-repo returns nonzero, but the call itself must
    # not raise — that's the sandbox check.
    sandbox.run_command("git status")  # should not raise


def test_read_only_blocks_command_substitution(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="substitution"):
        sandbox.run_command("echo $(rm hello.txt)")


def test_read_only_blocks_run_tests(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="run_tests"):
        sandbox.run_tests()


def test_read_only_blocks_checkpoint(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(SandboxViolation, match="checkpoint"):
        sandbox.checkpoint()


# ---------------------------------------------------------------------------
# WORKSPACE_WRITE
# ---------------------------------------------------------------------------


def test_workspace_write_allows_write_inside_workdir(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    sandbox.write_file("inside.txt", "ok")
    assert (local_env.workdir / "inside.txt").read_text() == "ok"


def test_workspace_write_blocks_write_outside_workdir(
    local_env: LocalEnvironment, tmp_path: Path
) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    # Use a path that resolves outside the workdir.
    with pytest.raises(SandboxViolation, match="escapes workdir"):
        sandbox.write_file("../escape.txt", "nope")


def test_workspace_write_allows_rm_inside_workdir(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    result = sandbox.run_command("rm hello.txt")
    assert result.exit_code == 0
    assert not (local_env.workdir / "hello.txt").exists()


def test_workspace_write_blocks_curl(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    with pytest.raises(SandboxViolation, match="network"):
        sandbox.run_command("curl https://example.com")


def test_workspace_write_blocks_git_push(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    with pytest.raises(SandboxViolation, match="network"):
        sandbox.run_command("git push origin main")


def test_workspace_write_allows_git_commit(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    # Should not raise the sandbox violation. The command itself fails because
    # the dir is not a git repo, but exit code is irrelevant here.
    sandbox.run_command("git commit -m wip")


def test_workspace_write_allows_run_tests_call(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    # Should not raise SandboxViolation; pytest may fail to collect anything,
    # but the wrapper itself must permit the call.
    result = sandbox.run_tests()
    assert result is not None


# ---------------------------------------------------------------------------
# WORKSPACE_WRITE_NETWORK
# ---------------------------------------------------------------------------


def test_workspace_write_network_allows_curl_classification(
    local_env: LocalEnvironment,
) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE_NETWORK)
    # Use ``true`` after to keep the test hermetic — the point is that the
    # sandbox does not raise on the network classification.
    sandbox.run_command("curl --version")  # should not raise SandboxViolation


def test_workspace_write_network_allows_pip_install(local_env: LocalEnvironment) -> None:
    # pip install would touch the network in real life; we don't actually run
    # it, just confirm the wrapper doesn't block. Use ``--help`` to keep it
    # cheap and offline.
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE_NETWORK)
    sandbox.run_command("pip --help")  # must not raise SandboxViolation


def test_workspace_write_network_allows_git_push(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE_NETWORK)
    # Does not raise. The command itself fails (no remote), but that's fine.
    sandbox.run_command("git push origin main")


def test_workspace_write_network_still_contains_paths(
    local_env: LocalEnvironment,
) -> None:
    sandbox = SandboxedEnvironment(
        local_env, mode=SandboxMode.WORKSPACE_WRITE_NETWORK
    )
    with pytest.raises(SandboxViolation, match="escapes workdir"):
        sandbox.write_file("../escape.txt", "nope")


# ---------------------------------------------------------------------------
# Convenience properties
# ---------------------------------------------------------------------------


def test_allows_writes_property(local_env: LocalEnvironment) -> None:
    assert SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY).allows_writes is False
    assert (
        SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE).allows_writes is True
    )
    assert (
        SandboxedEnvironment(
            local_env, mode=SandboxMode.WORKSPACE_WRITE_NETWORK
        ).allows_writes
        is True
    )


def test_allows_network_property(local_env: LocalEnvironment) -> None:
    assert (
        SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY).allows_network is False
    )
    assert (
        SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE).allows_network
        is False
    )
    assert (
        SandboxedEnvironment(
            local_env, mode=SandboxMode.WORKSPACE_WRITE_NETWORK
        ).allows_network
        is True
    )


def test_default_mode_is_read_only(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env)
    assert sandbox.mode is SandboxMode.READ_ONLY


def test_violation_is_permission_error(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    with pytest.raises(PermissionError):
        sandbox.write_file("oops.txt", "x")


def test_clone_preserves_mode(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    cloned = sandbox.clone()
    try:
        assert cloned.mode is SandboxMode.WORKSPACE_WRITE
        assert isinstance(cloned, SandboxedEnvironment)
    finally:
        cloned.cleanup()


def test_workdir_property(local_env: LocalEnvironment) -> None:
    sandbox = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    assert sandbox.workdir == local_env.workdir
