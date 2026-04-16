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

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.errors import CacheMissError, OfflineError


DEFAULT_HOME_ENV = "CHIMERA_FS_HOME"
OFFLINE_ENV = "CHIMERA_FS_OFFLINE"
_DEFAULT_SUBPATH = ".chimera/function_synthesis"


def _offline() -> bool:
    return os.environ.get(OFFLINE_ENV, "").lower() in {"1", "true", "yes"}


def _safe_segment(repo_id: str) -> str:
    # turn "org/repo" into "org--repo" (filesystem-safe, reversible-ish)
    return repo_id.replace("/", "--")


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


class BaseModelCache:
    """Resolves and downloads base GGUF model files to the local cache.

    The cache is content-addressed by ``(repo_id, filename)`` pairs and stored
    under ``<models>/<safe(repo_id)>/<filename>``.  Downloads are delegated to
    ``huggingface_hub`` (optional dep); passing ``CHIMERA_FS_OFFLINE=1`` forces
    a cache-only lookup that raises :class:`CacheMissError` on misses.
    """

    def __init__(self, dirs: CacheDirs) -> None:
        self.dirs = dirs
        dirs.ensure()

    def local_path(self, repo_id: str, filename: str) -> Path:
        return self.dirs.models / _safe_segment(repo_id) / filename

    def get(self, repo_id: str, filename: str) -> Path:
        target = self.local_path(repo_id, filename)
        if target.exists():
            return target
        if _offline():
            raise OfflineError(
                operation=f"download base model {repo_id!r}/{filename!r}"
            )
        try:
            import huggingface_hub  # type: ignore[import-not-found, unused-ignore]
        except ImportError as exc:
            raise ImportError(
                "BaseModelCache requires huggingface_hub. "
                "Install with: pip install 'chimera[function_synthesis]'"
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved = huggingface_hub.hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=target.parent,
        )
        return Path(resolved)


class BundleCache:
    """Stores compiled ``.chi`` bundles on disk, keyed by slug."""

    def __init__(self, dirs: CacheDirs) -> None:
        self.dirs = dirs
        dirs.ensure()

    def _path(self, slug: str) -> Path:
        return self.dirs.bundles / f"{slug}.chi"

    def install(self, *, slug: str, bundle: ChiBundle) -> Path:
        target = self._path(slug)
        bundle.save(target)
        return target

    def get(self, slug: str) -> Path:
        target = self._path(slug)
        if not target.exists():
            raise CacheMissError(kind="bundle", key=slug)
        return target

    def list(self) -> list[str]:
        if not self.dirs.bundles.exists():
            return []
        return sorted(p.stem for p in self.dirs.bundles.glob("*.chi"))

    def remove(self, slug: str) -> None:
        target = self._path(slug)
        if target.exists():
            target.unlink()
