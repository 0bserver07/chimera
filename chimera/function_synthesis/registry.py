"""Local program registry: slug -> installed bundle + metadata."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.cache import BundleCache, CacheDirs
from chimera.function_synthesis.errors import CacheMissError
from chimera.function_synthesis.spec import FunctionSpec


def slug_for(spec: FunctionSpec) -> str:
    """Return a deterministic slug ``<name>-<hash8>`` for the spec."""
    digest = hashlib.sha256(spec.to_json().encode()).hexdigest()[:8]
    return f"{spec.name}-{digest}"


@dataclass
class ProgramEntry:
    """An installed program."""

    slug: str
    bundle_path: Path
    spec: FunctionSpec
    metadata: dict[str, Any] = field(default_factory=dict)


class ProgramRegistry:
    """Local registry that installs bundles and resolves slugs to paths."""

    def __init__(self, dirs: CacheDirs) -> None:
        self.dirs = dirs
        self.dirs.ensure()
        self._bundles = BundleCache(dirs)

    @classmethod
    def default(cls) -> ProgramRegistry:
        return cls(CacheDirs.default())

    def _load_index(self) -> dict[str, Any]:
        if not self.dirs.index_file.exists():
            return {}
        result: dict[str, Any] = json.loads(self.dirs.index_file.read_text())
        return result

    def _save_index(self, index: dict[str, Any]) -> None:
        self.dirs.index_file.write_text(json.dumps(index, sort_keys=True, indent=2))

    def install(self, *, spec: FunctionSpec, bundle: ChiBundle) -> str:
        slug = slug_for(spec)
        path = self._bundles.install(slug=slug, bundle=bundle)
        index = self._load_index()
        index[slug] = {
            "bundle_path": str(path),
            "spec": json.loads(spec.to_json()),
            "metadata": bundle.metadata,
        }
        self._save_index(index)
        return slug

    def resolve(self, slug: str) -> ProgramEntry:
        index = self._load_index()
        if slug not in index:
            raise CacheMissError(kind="program", key=slug)
        entry = index[slug]
        spec = FunctionSpec.from_json(json.dumps(entry["spec"]))
        return ProgramEntry(
            slug=slug,
            bundle_path=Path(entry["bundle_path"]),
            spec=spec,
            metadata=entry.get("metadata", {}),
        )

    def list(self) -> list[ProgramEntry]:
        index = self._load_index()
        return [self.resolve(slug) for slug in sorted(index)]

    def remove(self, slug: str) -> None:
        index = self._load_index()
        if slug in index:
            self._bundles.remove(slug)
            del index[slug]
            self._save_index(index)

    def rename(self, old_slug: str, new_slug: str) -> None:
        """Rename an installed program from ``old_slug`` to ``new_slug``.

        Moves the on-disk ``.chi`` bundle to the new slug filename and
        updates the registry index.  The operation is best-effort atomic:
        the index is only rewritten after the bundle file has been moved.

        Args:
            old_slug: Slug of the program to rename; must exist.
            new_slug: Target slug; must not already exist.

        Raises:
            CacheMissError: If ``old_slug`` is not installed.
            ValueError: If ``new_slug`` already exists, is empty, or equals
                ``old_slug``.
        """
        if not new_slug:
            raise ValueError("new_slug must be a non-empty string")
        if old_slug == new_slug:
            raise ValueError("new_slug is identical to old_slug")

        index = self._load_index()
        if old_slug not in index:
            raise CacheMissError(kind="program", key=old_slug)
        if new_slug in index:
            raise ValueError(f"slug already exists: {new_slug!r}")

        entry = index[old_slug]
        old_path = Path(entry["bundle_path"])
        new_path = self.dirs.bundles / f"{new_slug}.chi"
        if new_path.exists():
            # On-disk collision even though the index did not know about it.
            raise ValueError(f"bundle file already exists: {new_path}")

        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)

        new_entry = dict(entry)
        new_entry["bundle_path"] = str(new_path)
        index[new_slug] = new_entry
        del index[old_slug]
        self._save_index(index)
