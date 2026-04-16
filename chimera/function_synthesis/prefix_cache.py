"""Disk-backed cache for llama.cpp post-prefill state (cold-start elimination)."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PrefixCache:
    """Key-value store for serialized backend state.

    Keys are ``sha256(base_model_sha || slug || system_prompt)``.  Values are
    opaque bytes produced by the backend (e.g., llama.cpp ``save_state``
    output).  When :attr:`enabled` is False, ``load`` always returns None and
    ``store`` is a no-op, preserving existing behavior.
    """

    root: Path
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def key(self, *, base_model_sha: str, slug: str, system_prompt: str) -> str:
        h = hashlib.sha256()
        h.update(base_model_sha.encode())
        h.update(b"\x00")
        h.update(slug.encode())
        h.update(b"\x00")
        h.update(system_prompt.encode())
        return h.hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.state"

    def load(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def store(self, key: str, state: bytes) -> None:
        if not self.enabled:
            return
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_bytes(state)
        os.replace(tmp, self._path(key))
