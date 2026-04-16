"""On-disk cache for base models and compiled bundles.

Layout::

    $CHIMERA_FS_HOME/  (default: ~/.chimera/function_synthesis/)
      models/         # base model files (GGUF)
      bundles/        # installed .chi bundles, one per slug
      index.json      # slug -> bundle path + metadata
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOME_ENV = "CHIMERA_FS_HOME"
_DEFAULT_SUBPATH = ".chimera/function_synthesis"


@dataclass(frozen=True)
class CacheDirs:
    """Resolves and owns the on-disk cache layout."""

    root: Path

    @classmethod
    def default(cls) -> CacheDirs:
        env = os.environ.get(DEFAULT_HOME_ENV)
        if env:
            return cls(root=Path(env))
        home = Path(os.environ.get("HOME") or Path.home())
        return cls(root=home / _DEFAULT_SUBPATH)

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def bundles(self) -> Path:
        return self.root / "bundles"

    @property
    def index_file(self) -> Path:
        return self.root / "index.json"

    def ensure(self) -> None:
        """Create all cache subdirectories if missing."""
        self.models.mkdir(parents=True, exist_ok=True)
        self.bundles.mkdir(parents=True, exist_ok=True)
