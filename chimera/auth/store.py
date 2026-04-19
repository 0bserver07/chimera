from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from chimera.auth.base import Credential

__all__ = ["CredentialStore"]


class CredentialStore:
    """File-based credential storage with restrictive permissions."""

    def __init__(self, path: str = "~/.chimera/credentials.json") -> None:
        self._path = Path(path).expanduser()

    def get(self, provider: str) -> Credential | None:
        """Return the stored credential for *provider*, or ``None``."""
        data = self._load()
        entry = data.get(provider)
        if entry is None:
            return None
        return Credential(**entry)

    def save(self, credential: Credential) -> None:
        """Persist a credential to disk."""
        data = self._load()
        data[credential.provider] = {
            "provider": credential.provider,
            "token": credential.token,
            "refresh_token": credential.refresh_token,
            "expires_at": credential.expires_at,
            "metadata": credential.metadata,
        }
        self._write(data)

    def delete(self, provider: str) -> None:
        """Remove the credential for *provider* if it exists."""
        data = self._load()
        data.pop(provider, None)
        self._write(data)

    def list_providers(self) -> list[str]:
        """Return the names of all stored providers."""
        return list(self._load().keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())  # type: ignore[no-any-return]

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))
        os.chmod(self._path, 0o600)
