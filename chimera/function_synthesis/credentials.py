"""Credential storage for function-synthesis remote services.

Stores tokens in ``~/.chimera/function_synthesis/credentials.json`` with file
mode ``0o600``.  Tokens are keyed by service name (e.g. ``"huggingface"``,
``"s3"``, ``"compile.example.com"``).

This store is intentionally local-file-only: tokens are never logged, printed,
or returned in exception messages.  Callers should treat the value returned by
:meth:`CredentialStore.get` as opaque and avoid echoing it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = ["CredentialStore"]


def _default_path() -> Path:
    """Return the default credentials path under ``CHIMERA_FS_HOME``.

    Honours the same environment variable as :class:`CacheDirs` so a single
    setting can redirect both bundles and credentials during tests.
    """
    env = os.environ.get("CHIMERA_FS_HOME")
    if env:
        return Path(env) / "credentials.json"
    home = Path(os.environ.get("HOME") or Path.home())
    return home / ".chimera" / "function_synthesis" / "credentials.json"


class CredentialStore:
    """File-backed credential storage keyed by service name.

    The on-disk file is created with mode ``0o600`` (owner read/write only).
    All mutating operations atomically re-apply that mode after writing.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Initialise the store.

        Args:
            path: Optional explicit path to the JSON file.  When ``None``,
                defaults to ``$CHIMERA_FS_HOME/credentials.json`` (falling
                back to ``~/.chimera/function_synthesis/credentials.json``).
        """
        self._path = Path(path) if path is not None else _default_path()

    @property
    def path(self) -> Path:
        """Return the on-disk path backing this store."""
        return self._path

    def set(self, service: str, token: str) -> None:
        """Save ``token`` for ``service``.

        The credentials file is created with mode ``0o600`` if it does not
        already exist.  Existing entries for the same service are overwritten.

        Args:
            service: Non-empty service identifier (e.g. ``"huggingface"``).
            token: Non-empty token string to persist.

        Raises:
            ValueError: If ``service`` or ``token`` is empty.
        """
        if not service:
            raise ValueError("service must be a non-empty string")
        if not token:
            raise ValueError("token must be a non-empty string")
        data = self._load()
        data[service] = token
        self._write(data)

    def get(self, service: str) -> str | None:
        """Return the token for ``service``, or ``None`` if not stored."""
        data = self._load()
        value = data.get(service)
        if value is None:
            return None
        # Defensive: the JSON file is only supposed to contain strings, but
        # guard against external corruption by coercing to str.
        return str(value)

    def delete(self, service: str) -> None:
        """Remove credentials for ``service``.

        Silent no-op if the service is not present.  The file itself is left
        in place even if the last entry is removed, so callers can rely on
        stable permissions across deletions.
        """
        data = self._load()
        if service in data:
            data.pop(service)
            self._write(data)

    def list_services(self) -> list[str]:
        """Return service names with stored credentials (sorted)."""
        return sorted(self._load().keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text()
        except OSError:
            # Never leak token bytes through an OS-level error message.
            return {}
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt file: treat as empty.  Do not surface contents in errors.
            return {}
        if not isinstance(parsed, dict):
            return {}
        return parsed

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-create with 0o600 so the token bytes never briefly live in a
        # world-readable file.
        if not self._path.exists():
            fd = os.open(
                str(self._path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(json.dumps(data, sort_keys=True, indent=2))
            except BaseException:
                # Clean up on failure; re-raise without referencing token data.
                raise
        else:
            self._path.write_text(json.dumps(data, sort_keys=True, indent=2))
        os.chmod(self._path, 0o600)
