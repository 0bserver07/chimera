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
"""

from __future__ import annotations

import fnmatch
import os
from typing import Any

from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult

try:  # optional dependency
    from e2b import Sandbox  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised via factory ImportError path
    Sandbox = None  # type: ignore[assignment, misc]


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
        self._api_key = api_key or os.environ.get("E2B_API_KEY")
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

    def _abs(self, path: str) -> str:
        return path if path.startswith("/") else f"{self._working_dir}/{path}"

    def read_file(self, path: str) -> str:
        content: Any = self._sbx.files.read(self._abs(path))
        return content if isinstance(content, str) else content.decode("utf-8")

    def write_file(self, path: str, content: str) -> None:
        self._sbx.files.write(self._abs(path), content)

    def list_files(self, pattern: str = "**/*") -> list[str]:
        # E2B has no native glob; enumerate then filter with fnmatch.
        res = self.run_command("find . -type f")
        files = [
            line[2:] if line.startswith("./") else line
            for line in res.stdout.splitlines()
            if line.strip()
        ]
        if pattern in ("**/*", "*", ""):
            return files
        norm = pattern.replace("**/", "")
        return [f for f in files if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(f, norm)]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_command(
        self, cmd: str, timeout: int = 120, shell_name: str = "main"
    ) -> CommandResult:
        full = f"cd {self._working_dir} && {cmd}"
        result: Any = self._sbx.commands.run(full, timeout=timeout)
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
