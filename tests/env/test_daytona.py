"""DaytonaEnvironment against a faked SDK — no network, no credits, no creds.

Every test here drives :class:`chimera.env.daytona.DaytonaEnvironment` through
a stand-in for the ``daytona`` package injected at
``chimera.env.daytona._sdk``.  That keeps the whole file runnable in CI, which
installs no optional extras, while still pinning the contract the real SDK has
to satisfy: client construction, sandbox creation from image/snapshot,
``process.exec`` argument shape, ``fs`` round-trips, and teardown.

The one thing a mock cannot prove is that the live service still speaks this
shape — see ``docs/guides/remote-and-cloud-environments.md`` for the live
smoke commands.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.env.daytona import DaytonaEnvironment
from chimera.env.factory import available_providers, create_environment

# ---------------------------------------------------------------------------
# Fake SDK
# ---------------------------------------------------------------------------


class _FakeExecResponse:
    """Mirrors the real SDK: consolidated ``result`` plus ``exit_code``."""

    def __init__(self, result: str, exit_code: int = 0) -> None:
        self.result = result
        self.exit_code = exit_code


class _FakeFs:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self._sandbox = sandbox

    def download_file(self, remote_path: str) -> bytes:
        try:
            return self._sandbox.files[remote_path]
        except KeyError:
            raise FileNotFoundError(remote_path) from None

    def upload_file(self, file: bytes, remote_path: str) -> None:
        # The real SDK overloads this on the first arg's type: bytes means
        # "these are the contents", str means "upload this local path".
        assert isinstance(file, bytes), "adapter must upload bytes, not a path"
        self._sandbox.files[remote_path] = file

    def create_folder(self, path: str, mode: str) -> None:
        self._sandbox.folders.append((path, mode))


class _FakeProcess:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self._sandbox = sandbox

    def exec(
        self, command: str, cwd: str | None = None, timeout: int | None = None
    ) -> _FakeExecResponse:
        self._sandbox.exec_calls.append((command, cwd, timeout))
        scripted = self._sandbox.scripted.get(command)
        if scripted is not None:
            return scripted
        if command == "find . -type f":
            base = (cwd or "").rstrip("/")
            rel = [
                p[len(base) + 1 :]
                for p in sorted(self._sandbox.files)
                if p.startswith(base + "/")
            ]
            return _FakeExecResponse("\n".join(f"./{r}" for r in rel))
        return _FakeExecResponse(f"ran:{command}")


class _FakeSandbox:
    def __init__(self, sandbox_id: str = "sbx-fake-1") -> None:
        self.id = sandbox_id
        self.files: dict[str, bytes] = {}
        self.folders: list[tuple[str, str]] = []
        self.exec_calls: list[tuple[str, str | None, int | None]] = []
        self.scripted: dict[str, _FakeExecResponse] = {}
        self.fs = _FakeFs(self)
        self.process = _FakeProcess(self)


class _FakeConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeImageParams:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeSnapshotParams:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeClient:
    """Records every call so tests can assert the exact SDK argument shape."""

    instances: list[_FakeClient] = []

    def __init__(self, config: _FakeConfig) -> None:
        self.config = config
        self.created: list[tuple[Any, float | None]] = []
        self.deleted: list[Any] = []
        self.sandbox = _FakeSandbox()
        self.delete_raises = False
        _FakeClient.instances.append(self)

    def create(self, params: Any = None, timeout: float | None = None) -> _FakeSandbox:
        self.created.append((params, timeout))
        return self.sandbox

    def delete(self, sandbox: Any, timeout: float = 60, wait: bool = False) -> None:
        if self.delete_raises:
            raise RuntimeError("sandbox already reaped")
        self.deleted.append(sandbox)


class _FakeSDK:
    """Stand-in for the top-level ``daytona`` module."""

    Daytona = _FakeClient
    DaytonaConfig = _FakeConfig
    CreateSandboxFromImageParams = _FakeImageParams
    CreateSandboxFromSnapshotParams = _FakeSnapshotParams


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the fake SDK and scrub any real Daytona creds from the env."""
    _FakeClient.instances = []
    monkeypatch.setattr("chimera.env.daytona._sdk", _FakeSDK)
    for var in ("DAYTONA_API_KEY", "DAYTONA_API_URL", "DAYTONA_TARGET"):
        monkeypatch.delenv(var, raising=False)


def _client() -> _FakeClient:
    assert _FakeClient.instances, "setup() never built a client"
    return _FakeClient.instances[-1]


# ---------------------------------------------------------------------------
# Loud-failure posture: missing SDK, missing credentials
# ---------------------------------------------------------------------------


def test_missing_sdk_raises_with_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.env.daytona._sdk", None)
    with pytest.raises(ImportError, match=r"chimera-run\[daytona\]"):
        DaytonaEnvironment(api_key="k")


def test_missing_credentials_raises_instead_of_falling_back() -> None:
    """The whole point of the gate: never degrade to local execution."""
    with pytest.raises(ValueError, match="DAYTONA_API_KEY"):
        DaytonaEnvironment()


def test_api_key_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_API_KEY", "env-key")
    env = DaytonaEnvironment()
    env.setup()
    assert _client().config.kwargs["api_key"] == "env-key"


def test_api_url_and_target_forwarded_when_set() -> None:
    env = DaytonaEnvironment(
        api_key="k", api_url="https://daytona.internal/api", target="eu"
    )
    env.setup()
    assert _client().config.kwargs == {
        "api_key": "k",
        "api_url": "https://daytona.internal/api",
        "target": "eu",
    }


def test_api_url_and_target_omitted_when_unset() -> None:
    """Unset optionals must not be passed as None — the SDK has its own defaults."""
    DaytonaEnvironment(api_key="k").setup()
    assert set(_client().config.kwargs) == {"api_key"}


def test_image_and_snapshot_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="not both"):
        DaytonaEnvironment(api_key="k", image="python:3.11-slim", snapshot="snap")


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def test_setup_creates_sandbox_from_image() -> None:
    env = DaytonaEnvironment(api_key="k", image="python:3.11-slim", create_timeout=90.0)
    env.setup()
    params, timeout = _client().created[0]
    assert isinstance(params, _FakeImageParams)
    assert params.kwargs["image"] == "python:3.11-slim"
    assert timeout == 90.0
    assert env.sandbox_id == "sbx-fake-1"


def test_setup_creates_sandbox_from_snapshot() -> None:
    env = DaytonaEnvironment(api_key="k", snapshot="my-snap")
    env.setup()
    params, _ = _client().created[0]
    assert isinstance(params, _FakeSnapshotParams)
    assert params.kwargs["snapshot"] == "my-snap"


def test_setup_without_image_or_snapshot_uses_account_default() -> None:
    DaytonaEnvironment(api_key="k").setup()
    params, _ = _client().created[0]
    assert params is None


def test_env_vars_forwarded_to_create_params() -> None:
    env = DaytonaEnvironment(
        api_key="k", image="python:3.11-slim", env_vars={"FOO": "bar"}
    )
    env.setup()
    params, _ = _client().created[0]
    assert params.kwargs["env_vars"] == {"FOO": "bar"}


def test_env_vars_survive_without_an_explicit_image_or_snapshot() -> None:
    """Caller config must never be dropped just because the source is default."""
    DaytonaEnvironment(api_key="k", env_vars={"FOO": "bar"}).setup()
    params, _ = _client().created[0]
    assert params is not None and params.kwargs == {"env_vars": {"FOO": "bar"}}


def test_setup_raises_when_sdk_returns_no_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_FakeClient, "create", lambda self, *a, **k: None)
    with pytest.raises(RuntimeError, match="no sandbox handle"):
        DaytonaEnvironment(api_key="k").setup()


def test_unknown_params_class_raises_upgrade_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drifted SDK must produce an actionable error, not an AttributeError."""

    class _Old:
        Daytona = _FakeClient
        DaytonaConfig = _FakeConfig

    monkeypatch.setattr("chimera.env.daytona._sdk", _Old)
    with pytest.raises(ImportError, match="CreateSandboxFromImageParams"):
        DaytonaEnvironment(api_key="k", image="python:3.11-slim").setup()


# ---------------------------------------------------------------------------
# Environment ABC surface
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips_through_fs() -> None:
    with DaytonaEnvironment(api_key="k") as env:
        env.write_file("pkg/main.py", "print('hi')")
        assert env.read_file("pkg/main.py") == "print('hi')"
        # Written at an absolute path under the working dir, parent created.
        assert "/home/daytona/pkg/main.py" in _client().sandbox.files
        assert ("/home/daytona/pkg", "755") in _client().sandbox.folders


def test_absolute_paths_bypass_the_working_dir() -> None:
    with DaytonaEnvironment(api_key="k") as env:
        env.write_file("/etc/thing.conf", "x")
        assert "/etc/thing.conf" in _client().sandbox.files


def test_custom_working_dir_is_honoured() -> None:
    with DaytonaEnvironment(api_key="k", working_dir="/testbed/") as env:
        assert env.working_dir == "/testbed"
        env.write_file("a.txt", "1")
        assert "/testbed/a.txt" in _client().sandbox.files


def test_run_command_forwards_cwd_and_timeout_and_maps_result() -> None:
    with DaytonaEnvironment(api_key="k") as env:
        sandbox = _client().sandbox
        sandbox.scripted["boom"] = _FakeExecResponse("nope", exit_code=3)

        ok = env.run_command("echo hi", timeout=45)
        assert ok.stdout == "ran:echo hi" and ok.exit_code == 0 and ok.success
        assert sandbox.exec_calls[-1] == ("echo hi", "/home/daytona", 45)

        bad = env.run_command("boom")
        assert bad.exit_code == 3 and not bad.success and bad.stdout == "nope"


def test_run_command_falls_back_to_stdout_when_result_absent() -> None:
    """Drift tolerance: an SDK that returns stdout/stderr still maps cleanly."""

    class _Split:
        stdout = "out"
        stderr = "err"
        exit_code = 1

    with DaytonaEnvironment(api_key="k") as env:
        _client().sandbox.scripted["split"] = _Split()  # type: ignore[assignment]
        res = env.run_command("split")
    assert res.stdout == "out" and res.stderr == "err" and res.exit_code == 1


def test_list_files_returns_relative_paths_and_honours_globs() -> None:
    with DaytonaEnvironment(api_key="k") as env:
        env.write_file("a.py", "1")
        env.write_file("sub/b.py", "2")
        env.write_file("sub/c.txt", "3")
        assert env.list_files() == ["a.py", "sub/b.py", "sub/c.txt"]
        assert env.list_files("*.py") == ["a.py"]
        assert env.list_files("**/*.py") == ["a.py", "sub/b.py"]


def test_run_tests_maps_exit_code_to_pass_fail() -> None:
    with DaytonaEnvironment(api_key="k", test_command="pytest -q") as env:
        assert env.run_tests().all_passed
        _client().sandbox.scripted["pytest -q"] = _FakeExecResponse("F", exit_code=1)
        failed = env.run_tests()
    assert not failed.all_passed and failed.failed == 1


def test_checkpoint_and_restore_are_explicitly_unsupported() -> None:
    env = DaytonaEnvironment(api_key="k")
    with pytest.raises(NotImplementedError):
        env.checkpoint()
    with pytest.raises(NotImplementedError):
        env.restore("anything")


def test_use_before_setup_raises_a_clear_error() -> None:
    env = DaytonaEnvironment(api_key="k")
    with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
        env.read_file("a.txt")
    with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
        env.run_command("ls")


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


def test_cleanup_deletes_the_sandbox() -> None:
    env = DaytonaEnvironment(api_key="k")
    env.setup()
    sandbox = _client().sandbox
    env.cleanup()
    assert _client().deleted == [sandbox]


def test_keep_alive_skips_deletion() -> None:
    env = DaytonaEnvironment(api_key="k", keep_alive=True)
    env.setup()
    env.cleanup()
    assert _client().deleted == []


def test_cleanup_is_idempotent_and_survives_sdk_errors() -> None:
    env = DaytonaEnvironment(api_key="k")
    env.setup()
    _client().delete_raises = True
    env.cleanup()  # must not raise — teardown never fails a green run
    env.cleanup()  # second call is a no-op
    assert _client().deleted == []


def test_context_manager_tears_down_on_exception() -> None:
    with pytest.raises(ZeroDivisionError):
        with DaytonaEnvironment(api_key="k"):
            raise ZeroDivisionError
    assert len(_client().deleted) == 1


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------


def test_registered_in_the_universal_factory() -> None:
    assert "daytona" in available_providers()


def test_create_environment_builds_the_backend() -> None:
    env = create_environment("daytona", api_key="k", image="python:3.11-slim")
    assert isinstance(env, DaytonaEnvironment)


def test_factory_surfaces_the_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.env.daytona._sdk", None)
    with pytest.raises(ImportError, match=r"chimera-run\[daytona\]"):
        create_environment("daytona", api_key="k")
