"""Daytona cloud sandbox environment.

Wraps the Daytona Python SDK (<https://www.daytona.io>) behind Chimera's
:class:`~chimera.env.base.Environment` interface, so any agent, benchmark, or
loop that already speaks ``Environment`` runs inside an ephemeral Daytona
sandbox with no code changes::

    from chimera.env.factory import create_environment

    with create_environment("daytona", image="python:3.11-slim") as env:
        env.write_file("main.py", "print('hi')")
        print(env.run_command("python main.py").stdout)

Requires the optional ``daytona`` package (``pip install
'chimera-run[daytona]'``) and a Daytona API key, supplied either as the
``api_key`` argument or via ``$DAYTONA_API_KEY``.

**Failure posture — deliberately loud.**  A cloud sandbox backend that quietly
degrades to local execution silently invalidates every benchmark cell it
produces, so both the missing-SDK and the missing-credential cases raise at
construction time rather than at first use.  This mirrors the Modal path
(:mod:`chimera.env.modal_sandbox` plus the ``bench-matrix --env modal``
credential gate).

Reference:
    * :class:`chimera.env.e2b.E2BEnvironment` — sibling managed-sandbox backend.
    * :class:`chimera.env.modal_sandbox.ModalSandboxEnvironment` — Modal.
    * :class:`chimera.env.cloud.CloudEnvironment` — generic HTTP provisioning.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from chimera.env.base import Environment, glob_match
from chimera.types import CommandResult, TestResult

if TYPE_CHECKING:
    from collections.abc import Mapping

try:  # optional dependency — the whole module stays importable without it
    import daytona as _sdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised via the missing-SDK tests
    _sdk = None  # type: ignore[assignment]


_EXTRA_HINT = (
    "DaytonaEnvironment requires the 'daytona' package. Install it with: "
    "pip install 'chimera-run[daytona]'"
)

_CREDS_HINT = (
    "DaytonaEnvironment requires a Daytona API key. Pass api_key=... or set "
    "$DAYTONA_API_KEY. Refusing to continue: a cloud sandbox backend must "
    "never silently fall back to local execution, because results produced "
    "locally would be indistinguishable from results produced in the cloud."
)


def _require_sdk() -> Any:
    """Return the imported ``daytona`` module or raise a friendly ImportError.

    Centralises the optional-dependency gate so the class body never touches
    the module global directly.  Tests monkeypatch
    ``chimera.env.daytona._sdk`` with a fake module to exercise the wiring
    without the real SDK or any network.

    Returns:
        The imported ``daytona`` module.

    Raises:
        ImportError: When the ``daytona`` package is not importable.
    """
    if _sdk is None:
        raise ImportError(_EXTRA_HINT)
    return _sdk


class DaytonaEnvironment(Environment):
    """Ephemeral Daytona sandbox exposed through the :class:`Environment` contract.

    The lifecycle is:

    1. :meth:`setup` builds a ``daytona.Daytona`` client from the resolved
       credentials and creates a sandbox — from *image* when given, from
       *snapshot* when given, otherwise from the account default.
    2. :meth:`run_command` dispatches through ``sandbox.process.exec``.
    3. :meth:`read_file` / :meth:`write_file` round-trip through the SDK's
       filesystem surface (``sandbox.fs.download_file`` /
       ``sandbox.fs.upload_file``); :meth:`list_files` shells out to ``find``
       so it stays image-agnostic.
    4. :meth:`cleanup` deletes the sandbox unless *keep_alive* is set.

    Args:
        api_key: Daytona API key.  Falls back to ``$DAYTONA_API_KEY``.
        api_url: Daytona API base URL.  ``None`` lets the SDK use its default
            (``$DAYTONA_API_URL``, else the hosted endpoint).
        target: Target runner region, e.g. ``"us"``.  ``None`` uses the SDK
            default (``$DAYTONA_TARGET``).
        image: Docker image for the sandbox (e.g. ``"python:3.11-slim"``).
            Mutually exclusive with *snapshot*.
        snapshot: Name of a pre-built Daytona snapshot.  Mutually exclusive
            with *image*.
        working_dir: Directory inside the sandbox that workspace-relative
            paths resolve against.
        env_vars: Environment variables injected into the sandbox at creation.
        test_command: Command used by :meth:`run_tests`.
        create_timeout: Seconds to wait for the sandbox to become usable.
        keep_alive: When ``True``, :meth:`cleanup` leaves the sandbox running
            (useful for post-mortem debugging — it will still bill).

    Raises:
        ImportError: When the ``daytona`` package is not installed.
        ValueError: When no API key can be resolved, or when both *image* and
            *snapshot* are supplied.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        target: str | None = None,
        image: str | None = None,
        snapshot: str | None = None,
        working_dir: str = "/home/daytona",
        env_vars: Mapping[str, str] | None = None,
        test_command: str = "python -m pytest -q",
        create_timeout: float = 180.0,
        keep_alive: bool = False,
    ) -> None:
        _require_sdk()
        resolved_key = api_key or os.environ.get("DAYTONA_API_KEY")
        if not resolved_key:
            raise ValueError(_CREDS_HINT)
        if image and snapshot:
            raise ValueError(
                "DaytonaEnvironment accepts image= or snapshot=, not both "
                f"(got image={image!r}, snapshot={snapshot!r})"
            )
        self._api_key = resolved_key
        self._api_url = api_url or os.environ.get("DAYTONA_API_URL")
        self._target = target or os.environ.get("DAYTONA_TARGET")
        self._image = image
        self._snapshot = snapshot
        self._working_dir = working_dir.rstrip("/") or "/"
        self._env_vars = dict(env_vars) if env_vars else None
        self._test_command = test_command
        self._create_timeout = create_timeout
        self._keep_alive = keep_alive
        self._client: Any = None
        self._sandbox: Any = None
        self._sandbox_id: str | None = None

    @property
    def sandbox_id(self) -> str | None:
        """Return the live sandbox identifier, or ``None`` before setup."""
        return self._sandbox_id

    @property
    def working_dir(self) -> str:
        """Return the sandbox-side directory relative paths resolve against."""
        return self._working_dir

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _build_params(self, sdk: Any) -> Any:
        """Build the SDK's create-params object for the configured source.

        Args:
            sdk: The imported ``daytona`` module.

        Returns:
            A ``CreateSandboxFrom*Params`` instance, or ``None`` when nothing
            needs configuring (no image, no snapshot, no env vars) — the SDK
            then picks the account default.

        Raises:
            ImportError: When the installed SDK lacks the params class needed
                for the requested source.
        """
        if self._image:
            cls = getattr(sdk, "CreateSandboxFromImageParams", None)
            if cls is None:
                raise ImportError(
                    "The installed 'daytona' SDK has no "
                    "CreateSandboxFromImageParams; upgrade with "
                    "pip install -U daytona"
                )
            kwargs: dict[str, Any] = {"image": self._image}
        elif self._snapshot or self._env_vars:
            # env_vars still need a params object even with no explicit
            # source — dropping them silently would strip the caller's
            # configuration without a word.
            cls = getattr(sdk, "CreateSandboxFromSnapshotParams", None)
            if cls is None:
                raise ImportError(
                    "The installed 'daytona' SDK has no "
                    "CreateSandboxFromSnapshotParams; upgrade with "
                    "pip install -U daytona"
                )
            kwargs = {"snapshot": self._snapshot} if self._snapshot else {}
        else:
            return None
        if self._env_vars:
            kwargs["env_vars"] = dict(self._env_vars)
        return cls(**kwargs)

    def setup(self) -> None:
        """Create the Daytona client and provision a sandbox.

        Raises:
            ImportError: When the ``daytona`` package is not installed.
            RuntimeError: When the SDK returns no sandbox handle.
        """
        sdk = _require_sdk()
        config_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._api_url:
            config_kwargs["api_url"] = self._api_url
        if self._target:
            config_kwargs["target"] = self._target
        self._client = sdk.Daytona(sdk.DaytonaConfig(**config_kwargs))

        params = self._build_params(sdk)
        if params is None:
            self._sandbox = self._client.create(timeout=self._create_timeout)
        else:
            self._sandbox = self._client.create(params, timeout=self._create_timeout)
        if self._sandbox is None:
            raise RuntimeError("Daytona create() returned no sandbox handle")
        self._sandbox_id = getattr(self._sandbox, "id", None) or getattr(
            self._sandbox, "sandbox_id", None
        )

    def cleanup(self) -> None:
        """Delete the sandbox unless *keep_alive* was set.

        Teardown is best-effort: an already-reaped sandbox must not turn a
        successful run into a failure, so SDK errors here are swallowed.
        """
        sandbox, self._sandbox = self._sandbox, None
        client, self._client = self._client, None
        if sandbox is not None and not self._keep_alive:
            try:
                if client is not None and hasattr(client, "delete"):
                    client.delete(sandbox)
                else:  # pragma: no cover - older SDKs expose it on the sandbox
                    sandbox.delete()
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass

    def _require_sandbox(self) -> Any:
        """Return the live sandbox handle, or explain that setup() was skipped.

        Returns:
            The SDK sandbox object created by :meth:`setup`.

        Raises:
            RuntimeError: When :meth:`setup` has not run (or cleanup already did).
        """
        if self._sandbox is None:
            raise RuntimeError(
                "DaytonaEnvironment.setup() must be called before use "
                "(or the environment was already cleaned up)."
            )
        return self._sandbox

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------

    def _abs(self, path: str) -> str:
        """Resolve *path* against :attr:`working_dir` when it is relative."""
        return path if path.startswith("/") else f"{self._working_dir}/{path}"

    def read_file(self, path: str) -> str:
        """Read a file from the sandbox via the SDK filesystem surface.

        Args:
            path: Sandbox-relative (or absolute) file path.

        Returns:
            The file's text content, decoded as UTF-8.
        """
        sandbox = self._require_sandbox()
        content: Any = sandbox.fs.download_file(self._abs(path))
        if isinstance(content, bytes):
            return content.decode("utf-8")
        return str(content)

    def write_file(self, path: str, content: str) -> None:
        """Write a text file into the sandbox, creating parent directories.

        Args:
            path: Sandbox-relative (or absolute) file path.
            content: Text to write (encoded as UTF-8 before upload).
        """
        sandbox = self._require_sandbox()
        full = self._abs(path)
        parent = full.rsplit("/", 1)[0]
        if parent and parent != full:
            try:
                sandbox.fs.create_folder(parent, "755")
            except Exception:  # noqa: BLE001 - already-exists is not an error
                pass
        # Passing bytes selects the SDK's ``upload_file(file, remote_path)``
        # overload; a str first argument would mean "upload this local path".
        sandbox.fs.upload_file(content.encode("utf-8"), full)

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """List files under :attr:`working_dir` matching a glob pattern.

        Uses ``find`` rather than the SDK's directory listing so behaviour is
        identical across sandbox images and matches the E2B backend.

        Args:
            pattern: Glob pattern relative to the working directory.

        Returns:
            Sorted workspace-relative paths that match *pattern*.
        """
        res = self.run_command("find . -type f")
        files = [
            line[2:] if line.startswith("./") else line
            for line in res.stdout.splitlines()
            if line.strip()
        ]
        if pattern in ("**/*", ""):
            return sorted(files)
        return sorted(f for f in files if glob_match(f, pattern))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_command(
        self, cmd: str, timeout: int = 120, shell_name: str = "main"
    ) -> CommandResult:
        """Run a shell command inside the sandbox.

        Daytona consolidates a command's output into a single ``result``
        field, so *stderr* is populated only when the SDK exposes it
        separately; otherwise everything lands in *stdout*.

        Args:
            cmd: The command to execute.
            timeout: Max seconds to wait.
            shell_name: Ignored — Daytona's ``exec`` is stateless per call.

        Returns:
            The command's stdout, stderr, and exit code.
        """
        del shell_name
        sandbox = self._require_sandbox()
        response: Any = sandbox.process.exec(
            cmd, cwd=self._working_dir, timeout=timeout
        )
        stdout = getattr(response, "result", None)
        if stdout is None:
            stdout = getattr(response, "stdout", "")
        return CommandResult(
            stdout=stdout or "",
            stderr=getattr(response, "stderr", "") or "",
            exit_code=int(getattr(response, "exit_code", 0) or 0),
        )

    def run_tests(self) -> TestResult:
        """Run *test_command* inside the sandbox.

        Returns:
            A coarse pass/fail :class:`~chimera.types.TestResult` — Daytona
            returns raw output, so per-test counts are not parsed here.
        """
        res = self.run_command(self._test_command, timeout=600)
        output = res.stdout + (("\n" + res.stderr) if res.stderr else "")
        if res.success:
            return TestResult(passed=1, failed=0, errors=0, output=output)
        return TestResult(passed=0, failed=1, errors=0, output=output)

    # ------------------------------------------------------------------
    # Checkpointing (not supported by ephemeral Daytona sandboxes)
    # ------------------------------------------------------------------

    def checkpoint(self) -> str:
        """Not supported.

        Raises:
            NotImplementedError: Always — snapshotting a running sandbox is
                not part of the SDK surface this backend targets.
        """
        raise NotImplementedError("DaytonaEnvironment does not support checkpoint()")

    def restore(self, checkpoint_id: str) -> None:
        """Not supported.

        Args:
            checkpoint_id: Ignored.

        Raises:
            NotImplementedError: Always — see :meth:`checkpoint`.
        """
        raise NotImplementedError("DaytonaEnvironment does not support restore()")


__all__ = ["DaytonaEnvironment"]
