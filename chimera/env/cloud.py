"""Cloud sandbox environment backed by a provisioning API."""

from __future__ import annotations

import time

from chimera.env.remote import RemoteEnvironment

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class CloudEnvironment(RemoteEnvironment):
    """Environment that provisions an on-demand cloud sandbox.

    Extends :class:`RemoteEnvironment` to add lifecycle management for
    ephemeral cloud workspaces.  A sandbox is created on :meth:`setup` and
    torn down on :meth:`cleanup` (unless *keep_alive* is ``True``).

    Args:
        cloud_api_url: Base URL of the cloud provisioning API
            (e.g. ``"https://api.cloud.example.com"``).
        cloud_api_key: API key for the provisioning service.
        image: Container image to use for the sandbox (e.g.
            ``"python:3.11-slim"``).  ``None`` lets the service choose a
            default.
        working_dir: Working directory inside the sandbox.
        keep_alive: When ``True``, :meth:`cleanup` will *not* destroy the
            sandbox so it can be reused later.
        init_timeout: Maximum seconds to wait for the sandbox to become
            ready.
        sandbox_id: If provided, connect to an existing sandbox instead of
            creating a new one.
    """

    def __init__(
        self,
        cloud_api_url: str,
        cloud_api_key: str,
        image: str | None = None,
        working_dir: str = "/workspace",
        keep_alive: bool = False,
        init_timeout: int = 120,
        sandbox_id: str | None = None,
    ) -> None:
        if httpx is None:
            raise ImportError(
                "httpx is required for CloudEnvironment. Install it with: "
                "pip install 'chimera-ai[remote]'"
            )
        self._cloud_api_url = cloud_api_url.rstrip("/")
        self._cloud_api_key = cloud_api_key
        self._image = image
        self._working_dir = working_dir
        self._keep_alive = keep_alive
        self._init_timeout = init_timeout
        self._sandbox_id: str | None = sandbox_id

        # Build a temporary httpx client for the cloud provisioning API.
        self._cloud_client: httpx.Client = httpx.Client(
            base_url=self._cloud_api_url,
            headers={"Authorization": f"Bearer {cloud_api_key}"},
            timeout=init_timeout,
        )

    @property
    def sandbox_id(self) -> str | None:
        """Return the current sandbox identifier, or ``None`` if not yet provisioned."""
        return self._sandbox_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Provision a new sandbox or connect to an existing one.

        If *sandbox_id* was supplied at construction time the method connects
        to that sandbox.  Otherwise it creates a new one and polls the
        provisioning API until the status is ``"ready"`` (or the
        *init_timeout* is exceeded).
        """
        if self._sandbox_id is None:
            # Create a new sandbox
            payload: dict[str, object] = {"working_dir": self._working_dir}
            if self._image is not None:
                payload["image"] = self._image
            resp = self._cloud_client.post("/sandboxes", json=payload)
            resp.raise_for_status()
            data = resp.json()
            self._sandbox_id = data["sandbox_id"]

        # Poll until the sandbox is ready (or timeout)
        deadline = time.monotonic() + self._init_timeout
        while True:
            resp = self._cloud_client.get(f"/sandboxes/{self._sandbox_id}")
            resp.raise_for_status()
            status = resp.json()["status"]
            if status == "ready":
                break
            if status == "error":
                raise RuntimeError(
                    f"Sandbox {self._sandbox_id} entered error state"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Sandbox {self._sandbox_id} not ready within "
                    f"{self._init_timeout}s"
                )
            time.sleep(1)

        # Extract connection details and initialise the parent
        info = resp.json()
        host = info.get("host", "localhost")
        port = int(info.get("port", 8080))
        super().__init__(
            host=host,
            port=port,
            api_key=self._cloud_api_key,
            working_dir=self._working_dir,
            tls=self._cloud_api_url.startswith("https"),
        )
        super().setup()

    def cleanup(self) -> None:
        """Tear down the sandbox unless *keep_alive* was set."""
        super().cleanup()
        if not self._keep_alive and self._sandbox_id is not None:
            try:
                self._cloud_client.delete(f"/sandboxes/{self._sandbox_id}")
            except Exception:
                pass
        self._cloud_client.close()
