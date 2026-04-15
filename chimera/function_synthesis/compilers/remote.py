"""RemoteCompiler: HTTP client that delegates compilation to an external service.

The service contract is intentionally small:

- POST ``{endpoint}`` with JSON ``{"spec": <FunctionSpec.to_json parsed>}``
- Optional ``Authorization: Bearer <api_key>`` header
- Response: raw ``.chi`` bundle bytes (``application/zip``)

This keeps chimera free of training infrastructure while letting users plug
in any compatible backend (self-hosted or third-party).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend, CompilerError
from chimera.function_synthesis.spec import FunctionSpec

_Client: Any = None
try:  # pragma: no cover - import guard
    import httpx as _httpx

    _Client = _httpx.Client
except ImportError:
    _Client = None


class RemoteCompiler(CompilerBackend):
    """Compile function specs by POSTing them to an external HTTP service.

    Args:
        endpoint: Full URL of the compile endpoint.
        api_key: Optional bearer token.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = timeout

    def compile(self, spec: FunctionSpec) -> ChiBundle:
        if _Client is None:
            raise ImportError(
                "RemoteCompiler requires httpx. Install with: pip install 'chimera[remote]'"
            )
        headers: dict[str, str] = {"Accept": "application/zip"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {"spec": json.loads(spec.to_json())}
        with _Client() as client:
            response = client.post(
                self._endpoint,
                json=body,
                headers=headers,
                timeout=self._timeout,
            )
        if response.status_code >= 400:
            raise CompilerError(
                f"remote compile failed: HTTP {response.status_code}: {getattr(response, 'text', '')}"
            )
        return _bytes_to_bundle(response.content)


def _bytes_to_bundle(data: bytes) -> ChiBundle:
    """Write ``data`` to a tempfile and load it as a :class:`ChiBundle`."""
    tmp = tempfile.NamedTemporaryFile(suffix=".chi", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        return ChiBundle.load(Path(tmp.name))
    finally:
        try:
            Path(tmp.name).unlink()
        except OSError:
            pass
