"""Docker-based sandboxed environment."""
from __future__ import annotations

import re
import uuid
from typing import Any

from chimera.env.base import Environment
from chimera.security.sandbox import SandboxPolicy
from chimera.types import CommandResult, TestResult

try:
    import docker
except ImportError:
    docker = None  # type: ignore[assignment]


class DockerEnvironment(Environment):
    """Docker-based sandboxed environment.

    Runs code inside a Docker container for isolation.  Falls back to
    an in-memory file store when no real container is available (useful
    for unit testing without Docker).
    """

    def __init__(
        self,
        image: str = "python:3.11-slim",
        workdir: str = "/workspace",
        test_cmd: str = "python -m pytest",
        sandbox: SandboxPolicy | None = None,
    ) -> None:
        if docker is None:
            raise ImportError("pip install docker")
        self._image = image
        self._workdir = workdir
        self._test_cmd = test_cmd
        self._sandbox = sandbox
        self._container: Any = None
        self._client: Any = None
        self._checkpoints: dict[str, dict[str, str]] = {}
        self._files: dict[str, str] = {}  # In-memory store (mock testing)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _build_container_kwargs(self) -> dict[str, Any]:
        """Build Docker container kwargs from sandbox policy."""
        kwargs: dict[str, Any] = {
            "image": self._image,
            "command": "sleep infinity",
            "detach": True,
            "working_dir": self._workdir,
            "remove": True,
        }
        if self._sandbox is None:
            return kwargs

        # Network isolation
        if self._sandbox.network_rules:
            all_denied = all(not r.allow for r in self._sandbox.network_rules)
            if all_denied:
                kwargs["network_mode"] = "none"

        # Resource limits
        if self._sandbox.max_memory_mb:
            kwargs["mem_limit"] = f"{self._sandbox.max_memory_mb}m"
        if self._sandbox.max_processes:
            kwargs["pids_limit"] = self._sandbox.max_processes
        if self._sandbox.timeout_seconds:
            kwargs["stop_timeout"] = self._sandbox.timeout_seconds

        # Filesystem: read-only root if no write rules
        write_rules = [
            r for r in self._sandbox.path_rules
            if r.access.value in ("write", "execute")
        ]
        if not write_rules:
            kwargs["read_only"] = True
            # Still need a writable tmpfs for /tmp
            kwargs["tmpfs"] = {"/tmp": "size=100m"}

        return kwargs

    def setup(self) -> None:
        self._client = docker.from_env()
        container_kwargs = self._build_container_kwargs()
        self._container = self._client.containers.run(**container_kwargs)

    def cleanup(self) -> None:
        if self._container:
            try:
                self._container.stop(timeout=5)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> str:
        if self._container is None:
            if path in self._files:
                return self._files[path]
            raise FileNotFoundError(path)
        exit_code, output = self._container.exec_run(f"cat {self._workdir}/{path}")
        if exit_code != 0:
            raise FileNotFoundError(path)
        return output.decode()

    def write_file(self, path: str, content: str) -> None:
        if self._container is None:
            self._files[path] = content
            return
        # Create parent directories
        if "/" in path:
            parent = "/".join(path.split("/")[:-1])
            self._container.exec_run(f"mkdir -p {self._workdir}/{parent}")
        # Use base64 encoding to safely write arbitrary content
        import base64
        encoded = base64.b64encode(content.encode()).decode()
        self._container.exec_run(
            ["sh", "-c", f"echo '{encoded}' | base64 -d > {self._workdir}/{path}"],
        )

    def list_files(self, pattern: str = "**/*") -> list[str]:
        if self._container is None:
            return sorted(self._files.keys())
        exit_code, output = self._container.exec_run(
            f"find {self._workdir} -type f -name '*'"
        )
        if exit_code != 0:
            return []
        lines = output.decode().strip().split("\n")
        return [
            line.replace(f"{self._workdir}/", "")
            for line in lines
            if line.strip()
        ]

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult:
        if self._container is None:
            return CommandResult(stdout="", stderr="No container", exit_code=1)
        exit_code, output = self._container.exec_run(
            ["sh", "-c", cmd], workdir=self._workdir
        )
        text = output.decode()
        return CommandResult(stdout=text, stderr="", exit_code=exit_code)

    def run_tests(self) -> TestResult:
        result = self.run_command(self._test_cmd)
        output = result.stdout + result.stderr
        passed = failed = errors = 0
        m = re.search(r"(\d+) passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", output)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) error", output)
        if m:
            errors = int(m.group(1))
        return TestResult(passed=passed, failed=failed, errors=errors, output=output)

    # ------------------------------------------------------------------
    # Checkpointing (in-memory)
    # ------------------------------------------------------------------

    def checkpoint(self) -> str:
        cp_id = uuid.uuid4().hex[:8]
        self._checkpoints[cp_id] = dict(self._files)
        return cp_id

    def restore(self, checkpoint_id: str) -> None:
        if checkpoint_id not in self._checkpoints:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")
        self._files = dict(self._checkpoints[checkpoint_id])
