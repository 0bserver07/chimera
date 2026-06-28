"""Tests for the universal environment factory (chimera.env.factory)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from chimera.env import factory
from chimera.env.factory import (
    available_providers,
    create_environment,
    register_environment,
    unregister_environment,
)


def test_available_providers_includes_builtins() -> None:
    providers = available_providers()
    for name in ("local", "git", "docker", "ssh", "remote", "cloud", "modal", "e2b"):
        assert name in providers


def test_create_environment_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown environment provider 'nope'"):
        create_environment("nope")


def test_create_environment_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        factory._BUILTIN, "broken", ("chimera.env._does_not_exist_xyz", "X", "broken")
    )
    with pytest.raises(ImportError, match=r"chimera-run\[broken\]"):
        create_environment("broken")


def test_create_local_roundtrip(tmp_path: Any) -> None:
    with create_environment("local", workdir=str(tmp_path)) as env:
        env.write_file("hello.txt", "hi there")
        assert env.read_file("hello.txt") == "hi there"
        result = env.run_command("echo hello")
        assert "hello" in result.stdout
        assert result.exit_code == 0
        assert "hello.txt" in env.list_files()


def test_register_and_unregister_custom(tmp_path: Any) -> None:
    sentinel = create_environment("local", workdir=str(tmp_path))
    register_environment("my-custom", lambda **_: sentinel)
    try:
        assert "my-custom" in available_providers()
        assert create_environment("my-custom") is sentinel
    finally:
        unregister_environment("my-custom")
    assert "my-custom" not in available_providers()


def test_modal_is_registered() -> None:
    # Modal is the primary cloud provider; it must be reachable via the factory.
    assert "modal" in available_providers()


# --- E2B adapter against a faked SDK (no real network/credits) ---------------


class _FakeFiles:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def write(self, path: str, content: str) -> None:
        self.store[path] = content

    def read(self, path: str) -> str:
        return self.store[path]


class _FakeResult:
    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _FakeCommands:
    def __init__(self, files: _FakeFiles, working_dir: str) -> None:
        self._files = files
        self._wd = working_dir

    def run(self, cmd: str, timeout: int = 120) -> _FakeResult:
        if "find . -type f" in cmd:
            rels = ["./" + os.path.relpath(p, self._wd) for p in self._files.store]
            return _FakeResult(stdout="\n".join(rels))
        if "echo hi" in cmd:
            return _FakeResult(stdout="hi\n")
        return _FakeResult()


class _FakeSandbox:
    def __init__(self, template: str | None = None, api_key: str | None = None,
                 timeout: int | None = None) -> None:
        self.files = _FakeFiles()
        self.commands = _FakeCommands(self.files, "/home/user")
        self.sandbox_id = "fake-sbx-123"
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def test_e2b_adapter_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.env.e2b as e2b

    monkeypatch.setattr(e2b, "Sandbox", _FakeSandbox)
    with create_environment("e2b", api_key="test-key") as env:
        assert getattr(env, "sandbox_id") == "fake-sbx-123"
        env.write_file("a.txt", "hello")
        assert env.read_file("a.txt") == "hello"
        assert env.run_command("echo hi").stdout.strip() == "hi"
        assert "a.txt" in env.list_files()


def test_e2b_requires_package(monkeypatch: pytest.MonkeyPatch) -> None:
    # When the e2b package is absent, instantiation raises a helpful ImportError.
    import chimera.env.e2b as e2b

    monkeypatch.setattr(e2b, "Sandbox", None)
    with pytest.raises(ImportError, match=r"chimera-run\[e2b\]"):
        create_environment("e2b", api_key="x")
