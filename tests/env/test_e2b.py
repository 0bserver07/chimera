"""E2BEnvironment against a faked SDK — no network, no credits, no creds.

``tests/test_env_factory.py`` already covers the factory round-trip; this file
pins the parts a benchmark run depends on: the loud-failure posture (missing
SDK, missing credentials), the use-before-setup guard, path resolution, and
:meth:`list_files` glob semantics matching
:class:`~chimera.env.local.LocalEnvironment`.

What a mock cannot prove is that the live service still speaks this shape —
see ``docs/guides/remote-and-cloud-environments.md`` for the live smoke.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.env.e2b import E2BEnvironment

# ---------------------------------------------------------------------------
# Fake SDK
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _FakeCommands:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self._sandbox = sandbox

    def run(self, cmd: str, timeout: int | None = None) -> _FakeResult:
        self._sandbox.commands_run.append((cmd, timeout))
        scripted = self._sandbox.scripted.get(cmd)
        if scripted is not None:
            return scripted
        if cmd.endswith("find . -type f"):
            base = self._sandbox.working_dir.rstrip("/")
            rel = [
                p[len(base) + 1 :]
                for p in sorted(self._sandbox.stored)
                if p.startswith(base + "/")
            ]
            return _FakeResult(stdout="\n".join(f"./{r}" for r in rel))
        return _FakeResult(stdout=f"ran:{cmd}")


class _FakeFiles:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self._sandbox = sandbox

    def read(self, path: str) -> str:
        return self._sandbox.stored[path]

    def write(self, path: str, content: str) -> None:
        self._sandbox.stored[path] = content


class _FakeSandbox:
    """Stand-in for ``e2b.Sandbox``."""

    instances: list[_FakeSandbox] = []
    working_dir = "/home/user"

    def __init__(
        self,
        template: str = "base",
        api_key: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.template = template
        self.api_key = api_key
        self.timeout = timeout
        self.sandbox_id = "sbx-fake"
        self.killed = False
        self.kill_raises = False
        self.stored: dict[str, str] = {}
        self.commands_run: list[tuple[str, int | None]] = []
        self.scripted: dict[str, _FakeResult] = {}
        self.files = _FakeFiles(self)
        self.commands = _FakeCommands(self)
        _FakeSandbox.instances.append(self)

    @classmethod
    def connect(cls, sandbox_id: str, api_key: str | None = None) -> _FakeSandbox:
        sbx = cls(api_key=api_key)
        sbx.sandbox_id = sandbox_id
        sbx.connected = True  # type: ignore[attr-defined]
        return sbx

    def kill(self) -> None:
        if self.kill_raises:
            raise RuntimeError("sandbox already gone")
        self.killed = True


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSandbox.instances = []
    monkeypatch.setattr("chimera.env.e2b.Sandbox", _FakeSandbox)
    monkeypatch.delenv("E2B_API_KEY", raising=False)


def _sbx() -> _FakeSandbox:
    assert _FakeSandbox.instances, "setup() never built a sandbox"
    return _FakeSandbox.instances[-1]


# ---------------------------------------------------------------------------
# Loud-failure posture
# ---------------------------------------------------------------------------


def test_missing_sdk_raises_with_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.env.e2b.Sandbox", None)
    with pytest.raises(ImportError, match=r"chimera-run\[e2b\]"):
        E2BEnvironment(api_key="k")


def test_missing_credentials_raises_instead_of_falling_back() -> None:
    """A cloud backend must never silently degrade to local execution."""
    with pytest.raises(ValueError, match="E2B_API_KEY"):
        E2BEnvironment()


def test_api_key_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "env-key")
    E2BEnvironment().setup()
    assert _sbx().api_key == "env-key"


def test_use_before_setup_raises_a_clear_error() -> None:
    env = E2BEnvironment(api_key="k")
    with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
        env.read_file("a.txt")
    with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
        env.write_file("a.txt", "x")
    with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
        env.run_command("ls")


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def test_setup_creates_a_sandbox_from_the_template() -> None:
    env = E2BEnvironment(api_key="k", template="python3", timeout=600)
    env.setup()
    assert _sbx().template == "python3" and _sbx().timeout == 600
    assert env.sandbox_id == "sbx-fake"


def test_sandbox_id_reconnects_instead_of_creating() -> None:
    env = E2BEnvironment(api_key="k", sandbox_id="sbx-existing")
    env.setup()
    assert getattr(_sbx(), "connected", False) is True
    assert env.sandbox_id == "sbx-existing"


# ---------------------------------------------------------------------------
# Environment ABC surface
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips_under_the_working_dir() -> None:
    with E2BEnvironment(api_key="k") as env:
        env.write_file("pkg/main.py", "print('hi')")
        assert env.read_file("pkg/main.py") == "print('hi')"
        assert "/home/user/pkg/main.py" in _sbx().stored


def test_absolute_paths_bypass_the_working_dir() -> None:
    with E2BEnvironment(api_key="k") as env:
        env.write_file("/tmp/x.txt", "1")
        assert "/tmp/x.txt" in _sbx().stored


def test_read_file_decodes_bytes() -> None:
    with E2BEnvironment(api_key="k") as env:
        _sbx().stored["/home/user/b.bin"] = b"bytes-content"  # type: ignore[assignment]
        assert env.read_file("b.bin") == "bytes-content"


def test_run_command_prefixes_cd_and_forwards_timeout() -> None:
    with E2BEnvironment(api_key="k") as env:
        res = env.run_command("echo hi", timeout=45)
    assert _sbx().commands_run[-1] == ("cd /home/user && echo hi", 45)
    assert res.stdout == "ran:cd /home/user && echo hi" and res.success


def test_run_command_maps_a_failing_exit_code() -> None:
    with E2BEnvironment(api_key="k") as env:
        _sbx().scripted["cd /home/user && boom"] = _FakeResult(
            stdout="", stderr="bad", exit_code=7
        )
        res = env.run_command("boom")
    assert res.exit_code == 7 and res.stderr == "bad" and not res.success


def test_list_files_uses_pathlib_glob_semantics_not_fnmatch() -> None:
    """Regression: raw fnmatch let ``*`` cross ``/`` and leak nested files."""
    with E2BEnvironment(api_key="k") as env:
        env.write_file("a.py", "1")
        env.write_file("sub/b.py", "2")
        env.write_file("sub/c.txt", "3")
        assert env.list_files() == ["a.py", "sub/b.py", "sub/c.txt"]
        assert env.list_files("*.py") == ["a.py"]
        assert env.list_files("**/*.py") == ["a.py", "sub/b.py"]
        assert env.list_files("sub/*") == ["sub/b.py", "sub/c.txt"]


def test_run_tests_maps_exit_code_to_pass_fail() -> None:
    with E2BEnvironment(api_key="k", test_command="pytest -q") as env:
        assert env.run_tests().all_passed
        _sbx().scripted["cd /home/user && pytest -q"] = _FakeResult(
            stdout="F", exit_code=1
        )
        assert not env.run_tests().all_passed


def test_checkpoint_and_restore_are_explicitly_unsupported() -> None:
    env = E2BEnvironment(api_key="k")
    with pytest.raises(NotImplementedError):
        env.checkpoint()
    with pytest.raises(NotImplementedError):
        env.restore("x")


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


def test_cleanup_kills_the_sandbox() -> None:
    env = E2BEnvironment(api_key="k")
    env.setup()
    env.cleanup()
    assert _sbx().killed is True


def test_keep_alive_skips_the_kill() -> None:
    env = E2BEnvironment(api_key="k", keep_alive=True)
    env.setup()
    env.cleanup()
    assert _sbx().killed is False


def test_cleanup_survives_sdk_errors_and_is_idempotent() -> None:
    env = E2BEnvironment(api_key="k")
    env.setup()
    _sbx().kill_raises = True
    env.cleanup()  # must not raise
    env.cleanup()


def test_context_manager_tears_down_on_exception() -> None:
    sandbox: Any = None
    with pytest.raises(ZeroDivisionError):
        with E2BEnvironment(api_key="k"):
            sandbox = _sbx()
            raise ZeroDivisionError
    assert sandbox.killed is True
