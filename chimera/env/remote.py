"""Remote environment that delegates to an HTTP workspace server."""

from __future__ import annotations

from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class RemoteEnvironment(Environment):
    """Environment backed by a remote workspace server over HTTP.

    Every :class:`~chimera.env.base.Environment` method is translated into an
    HTTP request against a lightweight workspace API.

    Args:
        host: Hostname or IP of the remote workspace server.
        port: TCP port the server listens on.
        api_key: Optional bearer token sent in the ``Authorization`` header.
        working_dir: Working directory on the remote server.
        timeout: Default request timeout in seconds.
        tls: When ``True`` use ``https`` instead of ``http``.
    """

    def __init__(
        self,
        host: str,
        port: int = 8080,
        api_key: str | None = None,
        working_dir: str = "/workspace",
        timeout: int = 120,
        tls: bool = False,
    ) -> None:
        if httpx is None:
            raise ImportError(
                "httpx is required for RemoteEnvironment. Install it with: "
                "pip install 'chimera-ai[remote]'"
            )
        self._host = host
        self._port = port
        self._api_key = api_key
        self._working_dir = working_dir
        self._timeout = timeout
        self._tls = tls

        scheme = "https" if tls else "http"
        self._base_url = f"{scheme}://{host}:{port}"

        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client: httpx.Client = httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Check the remote server is reachable via ``GET /health``."""
        resp = self._client.get("/health")
        resp.raise_for_status()

    def cleanup(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> str:
        """Read a file from the remote workspace.

        Args:
            path: Workspace-relative file path.

        Returns:
            The full text content of the file.
        """
        resp = self._client.get("/files/read", params={"path": path})
        resp.raise_for_status()
        return resp.json()["content"]

    def write_file(self, path: str, content: str) -> None:
        """Write a file to the remote workspace.

        Args:
            path: Workspace-relative file path.
            content: The text content to write.
        """
        resp = self._client.post("/files/write", json={"path": path, "content": content})
        resp.raise_for_status()

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """List files matching a glob pattern on the remote workspace.

        Args:
            pattern: Glob pattern relative to the workspace root.

        Returns:
            A list of workspace-relative file paths.
        """
        resp = self._client.get("/files/list", params={"pattern": pattern})
        resp.raise_for_status()
        return resp.json()["files"]

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult:
        """Run a shell command on the remote workspace.

        Args:
            cmd: The command to execute.
            timeout: Max seconds to wait.
            shell_name: Target shell when a persistent session is active.

        Returns:
            A :class:`~chimera.types.CommandResult` with stdout, stderr,
            and the exit code.
        """
        resp = self._client.post(
            "/execute",
            json={"cmd": cmd, "timeout": timeout, "shell_name": shell_name},
        )
        resp.raise_for_status()
        data = resp.json()
        return CommandResult(
            stdout=data["stdout"],
            stderr=data["stderr"],
            exit_code=data["exit_code"],
        )

    def run_tests(self) -> TestResult:
        """Execute the test suite on the remote workspace.

        Returns:
            A :class:`~chimera.types.TestResult` summarising pass/fail
            counts and captured output.
        """
        resp = self._client.post("/tests/run")
        resp.raise_for_status()
        data = resp.json()
        return TestResult(
            passed=data["passed"],
            failed=data["failed"],
            errors=data["errors"],
            output=data["output"],
        )

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------

    def checkpoint(self) -> str:
        """Save current workspace state and return a checkpoint ID.

        Returns:
            A string checkpoint ID.
        """
        resp = self._client.post("/checkpoint")
        resp.raise_for_status()
        return resp.json()["checkpoint_id"]

    def restore(self, checkpoint_id: str) -> None:
        """Restore the workspace to a previous checkpoint.

        Args:
            checkpoint_id: ID returned by a prior :meth:`checkpoint` call.
        """
        resp = self._client.post("/restore", json={"checkpoint_id": checkpoint_id})
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Extra file-transfer helpers
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a local file to the remote workspace.

        Args:
            local_path: Path on the local filesystem.
            remote_path: Destination path in the remote workspace.
        """
        with open(local_path, "rb") as fh:
            resp = self._client.post(
                "/files/upload",
                files={"file": (remote_path, fh)},
                data={"path": remote_path},
            )
        resp.raise_for_status()

    def download_file(self, remote_path: str, local_path: str) -> None:
        """Download a file from the remote workspace to the local filesystem.

        Args:
            remote_path: Path in the remote workspace.
            local_path: Destination path on the local filesystem.
        """
        resp = self._client.get("/files/download", params={"path": remote_path})
        resp.raise_for_status()
        with open(local_path, "wb") as fh:
            fh.write(resp.content)
