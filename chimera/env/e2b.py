"""E2B cloud sandbox environment.

Wraps the E2B Sandbox SDK (https://e2b.dev) behind Chimera's
:class:`~chimera.env.base.Environment` interface so any agent or benchmark can
run inside an ephemeral E2B microVM via the universal env factory:

    from chimera.env.factory import create_environment

    with create_environment("e2b", api_key="...", template="base") as env:
        env.write_file("main.py", "print('hi')")
        print(env.run_command("python main.py").stdout)

Requires the ``e2b`` package: ``pip install 'chimera-run[e2b]'`` (or
``pip install e2b``).  The API key is read from the ``api_key`` argument or the
``E2B_API_KEY`` environment variable.

**Failure posture — deliberately loud.**  A cloud sandbox backend that quietly
degrades to local execution silently invalidates every benchmark cell it
produces, so both the missing-SDK and the missing-credential cases raise at
construction time rather than at first use.  This mirrors the Modal path
(:mod:`chimera.env.modal_sandbox` plus the ``bench-matrix --env modal``
credential gate) and the sibling :mod:`chimera.env.daytona` backend.
"""

from __future__ import annotations

import os
from typing import Any

from chimera.env.base import Environment, glob_match
from chimera.types import CommandResult, TestResult

try:  # optional dependency
    from e2b import Sandbox  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised via factory ImportError path
    Sandbox = None  # type: ignore[assignment, misc]


_CREDS_HINT = (
    "E2BEnvironment requires an E2B API key. Pass api_key=... or set "
    "$E2B_API_KEY. Refusing to continue: a cloud sandbox backend must never "
    "silently fall back to local execution, because results produced locally "
    "would be indistinguishable from results produced in the cloud."
)


class E2BEnvironment(Environment):
    """Ephemeral E2B sandbox exposed through the :class:`Environment` contract.

    Args:
        api_key: E2B API key.  Falls back to ``$E2B_API_KEY``.
        template: E2B sandbox template/image name.
        working_dir: Directory inside the sandbox that workspace-relative paths
            resolve against.
        timeout: Sandbox lifetime in seconds (E2B auto-kills after this).
        test_command: Command used by :meth:`run_tests`.
        sandbox_id: Connect to an existing sandbox instead of creating one.
        keep_alive: When ``True``, :meth:`cleanup` does not kill the sandbox.

    Raises:
        ImportError: When the ``e2b`` package is not installed.
        ValueError: When no API key can be resolved.
    """

    def __init__(
        self,
        api_key: str | None = None,
        template: str = "base",
        working_dir: str = "/home/user",
        timeout: int = 300,
        test_command: str = "python -m pytest -q",
        sandbox_id: str | None = None,
        keep_alive: bool = False,
    ) -> None:
        if Sandbox is None:
            raise ImportError(
                "E2BEnvironment requires the 'e2b' package. Install it with: "
                "pip install 'chimera-run[e2b]'"
            )
        resolved_key = api_key or os.environ.get("E2B_API_KEY")
        if not resolved_key:
            raise ValueError(_CREDS_HINT)
        self._api_key = resolved_key
        self._template = template
        self._working_dir = working_dir.rstrip("/") or "/"
        self._timeout = timeout
        self._test_command = test_command
        self._sandbox_id = sandbox_id
        self._keep_alive = keep_alive
        self._sbx: Any = None

    @property
    def sandbox_id(self) -> str | None:
        """Return the live sandbox identifier, or ``None`` before setup."""
        return self._sandbox_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create a new sandbox (or connect to *sandbox_id*)."""
        assert Sandbox is not None  # __init__ raises ImportError when unavailable
        if self._sandbox_id is not None and hasattr(Sandbox, "connect"):
            self._sbx = Sandbox.connect(self._sandbox_id, api_key=self._api_key)
        else:
            self._sbx = Sandbox(
                template=self._template,
                api_key=self._api_key,
                timeout=self._timeout,
            )
        self._sandbox_id = (
            getattr(self._sbx, "sandbox_id", None) or getattr(self._sbx, "id", None)
        )

    def cleanup(self) -> None:
        """Kill the sandbox unless *keep_alive* was set."""
        if self._sbx is not None and not self._keep_alive:
            try:
                self._sbx.kill()
            except Exception:
                pass
        self._sbx = None

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------

    def _require_sandbox(self) -> Any:
        """Return the live sandbox handle, or explain that setup() was skipped.

        Returns:
            The E2B ``Sandbox`` created by :meth:`setup`.

        Raises:
            RuntimeError: When :meth:`setup` has not run (or cleanup already did).
        """
        if self._sbx is None:
            raise RuntimeError(
                "E2BEnvironment.setup() must be called before use "
                "(or the environment was already cleaned up)."
            )
        return self._sbx

    def _abs(self, path: str) -> str:
        return path if path.startswith("/") else f"{self._working_dir}/{path}"

    def read_file(self, path: str) -> str:
        content: Any = self._require_sandbox().files.read(self._abs(path))
        return content if isinstance(content, str) else content.decode("utf-8")

    def write_file(self, path: str, content: str) -> None:
        self._require_sandbox().files.write(self._abs(path), content)

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """List files under *working_dir* matching a glob pattern.

        E2B has no native glob, so this enumerates with ``find`` and filters
        client-side through :func:`~chimera.env.base.glob_match` — the same
        pathlib semantics :class:`~chimera.env.local.LocalEnvironment` uses.

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
        full = f"cd {self._working_dir} && {cmd}"
        result: Any = self._require_sandbox().commands.run(full, timeout=timeout)
        return CommandResult(
            stdout=getattr(result, "stdout", "") or "",
            stderr=getattr(result, "stderr", "") or "",
            exit_code=int(getattr(result, "exit_code", 0) or 0),
        )

    def run_tests(self) -> TestResult:
        res = self.run_command(self._test_command, timeout=600)
        output = res.stdout + (("\n" + res.stderr) if res.stderr else "")
        if res.success:
            return TestResult(passed=1, failed=0, errors=0, output=output)
        return TestResult(passed=0, failed=1, errors=0, output=output)

    # ------------------------------------------------------------------
    # Checkpointing (not supported by ephemeral E2B sandboxes)
    # ------------------------------------------------------------------

    def checkpoint(self) -> str:
        raise NotImplementedError("E2BEnvironment does not support checkpoint()")

    def restore(self, checkpoint_id: str) -> None:
        raise NotImplementedError("E2BEnvironment does not support restore()")
