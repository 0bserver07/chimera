"""ChiBundle: the ``.chi`` file format for compiled neural functions.

A ``.chi`` file is a ZIP archive containing a GGUF LoRA adapter and metadata
describing the function it encodes.  See the architecture section of
``docs/superpowers/plans/2026-04-14-function-synthesis.md`` for the layout.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from chimera.function_synthesis.spec import FunctionSpec

SCHEMA_VERSION = 1
_REQUIRED_MEMBERS = {"manifest.json", "adapter.gguf", "prompts.json", "spec.json", "metadata.json"}


class ChiBundleError(ValueError):
    """Raised when a ``.chi`` file is malformed or unsupported."""


@dataclass
class ChiBundle:
    """In-memory representation of a ``.chi`` compiled-function bundle.

    Attributes:
        spec: The :class:`FunctionSpec` that was compiled.
        adapter_bytes: Raw GGUF LoRA adapter bytes.
        prompts: Dict with keys ``system``, ``user_template``, ``stop``.
        metadata: Free-form dict (compiler backend info, base model hash, ...).
        base_model: Identifier of the required base GGUF model.
    """

    spec: FunctionSpec
    adapter_bytes: bytes
    prompts: dict
    metadata: dict = field(default_factory=dict)
    base_model: str = "qwen3-4b-instruct-q4_0"

    def save(self, path: str | Path) -> None:
        """Write the bundle to ``path`` as a ``.chi`` ZIP archive."""
        path = Path(path)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "name": self.spec.name,
            "description": self.spec.description,
            "base_model": self.base_model,
            "adapter_format": "gguf-lora",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "chimera_version": _chimera_version(),
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            zf.writestr("adapter.gguf", self.adapter_bytes)
            zf.writestr("prompts.json", json.dumps(self.prompts, sort_keys=True))
            zf.writestr("spec.json", self.spec.to_json())
            zf.writestr("metadata.json", json.dumps(self.metadata, sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> ChiBundle:
        """Load and validate a ``.chi`` bundle from ``path``."""
        path = Path(path)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            missing = _REQUIRED_MEMBERS - names
            if missing:
                raise ChiBundleError(f"bundle missing required members: {sorted(missing)}")
            manifest = json.loads(zf.read("manifest.json"))
            if manifest.get("schema_version") != SCHEMA_VERSION:
                raise ChiBundleError(
                    f"unsupported schema_version {manifest.get('schema_version')!r}"
                    f"; expected {SCHEMA_VERSION}"
                )
            adapter_bytes = zf.read("adapter.gguf")
            prompts = json.loads(zf.read("prompts.json"))
            spec = FunctionSpec.from_json(zf.read("spec.json").decode())
            metadata = json.loads(zf.read("metadata.json"))
        return cls(
            spec=spec,
            adapter_bytes=adapter_bytes,
            prompts=prompts,
            metadata=metadata,
            base_model=manifest["base_model"],
        )


def _chimera_version() -> str:
    try:
        from importlib.metadata import version

        return version("chimera")
    except Exception:
        return "unknown"
