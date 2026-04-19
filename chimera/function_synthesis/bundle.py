"""ChiBundle: the ``.chi`` file format for compiled neural functions.

A ``.chi`` file is a ZIP archive containing either:

- a GGUF LoRA adapter at ``adapter.gguf`` (``adapter_format == "gguf-lora"``), or
- a PEFT LoRA adapter directory packed under ``adapter_peft/`` (``adapter_format
  == "peft"``).  The directory mirrors the output of
  ``PeftModel.save_pretrained(...)`` (``adapter_config.json``,
  ``adapter_model.safetensors``, tokenizer files, etc.).

See the architecture section of
``docs/superpowers/plans/2026-04-14-function-synthesis.md`` for the layout.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chimera.function_synthesis.spec import FunctionSpec

SCHEMA_VERSION = 1

# Members every bundle must carry, regardless of adapter format.
_COMMON_REQUIRED_MEMBERS = {"manifest.json", "prompts.json", "spec.json", "metadata.json"}

# Valid values for ``manifest.adapter_format`` in version-1 bundles.
ADAPTER_FORMAT_GGUF = "gguf-lora"
ADAPTER_FORMAT_PEFT = "peft"
_VALID_ADAPTER_FORMATS = frozenset({ADAPTER_FORMAT_GGUF, ADAPTER_FORMAT_PEFT})

# Subdirectory inside the ZIP that holds PEFT adapter files.
_PEFT_DIR = "adapter_peft/"


class ChiBundleError(ValueError):
    """Raised when a ``.chi`` file is malformed or unsupported."""


@dataclass
class ChiBundle:
    """In-memory representation of a ``.chi`` compiled-function bundle.

    Either ``adapter_bytes`` (for ``adapter_format == "gguf-lora"``) or
    ``adapter_peft_files`` (for ``adapter_format == "peft"``) must be populated,
    but not both.

    Attributes:
        spec: The :class:`FunctionSpec` that was compiled.
        adapter_bytes: Raw GGUF LoRA adapter bytes.  Empty for PEFT bundles.
        prompts: Dict with keys ``system``, ``user_template``, ``stop``.
        metadata: Free-form dict (compiler backend info, base model hash, ...).
        base_model: Identifier of the required base model.
        adapter_format: ``"gguf-lora"`` or ``"peft"``.
        adapter_peft_files: Mapping of relative filename to raw bytes for a
            PEFT adapter directory (e.g. ``"adapter_config.json"`` → bytes).
            Only used when ``adapter_format == "peft"``.
    """

    spec: FunctionSpec
    adapter_bytes: bytes = b""
    prompts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    base_model: str = "qwen3-4b-instruct-q4_0"
    adapter_format: str = ADAPTER_FORMAT_GGUF
    adapter_peft_files: dict[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.adapter_format not in _VALID_ADAPTER_FORMATS:
            raise ChiBundleError(
                f"unsupported adapter_format {self.adapter_format!r}; "
                f"expected one of {sorted(_VALID_ADAPTER_FORMATS)}"
            )
        if self.adapter_format == ADAPTER_FORMAT_GGUF:
            if not self.adapter_bytes:
                # Preserve the historical laxness: tests build gguf bundles
                # with tiny placeholder bytes.  Only reject an obvious mix-up.
                if self.adapter_peft_files:
                    raise ChiBundleError(
                        "gguf-lora bundle must not include adapter_peft_files"
                    )
        else:  # ADAPTER_FORMAT_PEFT
            if not self.adapter_peft_files:
                raise ChiBundleError(
                    "peft bundle must include at least one file in adapter_peft_files"
                )
            if self.adapter_bytes:
                raise ChiBundleError(
                    "peft bundle must not include gguf adapter_bytes"
                )

    def save(self, path: str | Path) -> None:
        """Write the bundle to ``path`` as a ``.chi`` ZIP archive."""
        path = Path(path)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "name": self.spec.name,
            "description": self.spec.description,
            "base_model": self.base_model,
            "adapter_format": self.adapter_format,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "chimera_version": _chimera_version(),
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            zf.writestr("prompts.json", json.dumps(self.prompts, sort_keys=True))
            zf.writestr("spec.json", self.spec.to_json())
            zf.writestr("metadata.json", json.dumps(self.metadata, sort_keys=True))
            if self.adapter_format == ADAPTER_FORMAT_GGUF:
                zf.writestr("adapter.gguf", self.adapter_bytes)
            else:
                for relname, data in self.adapter_peft_files.items():
                    zf.writestr(_PEFT_DIR + relname, data)

    @classmethod
    def load(cls, path: str | Path) -> ChiBundle:
        """Load and validate a ``.chi`` bundle from ``path``."""
        path = Path(path)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            missing_common = _COMMON_REQUIRED_MEMBERS - names
            if missing_common:
                raise ChiBundleError(
                    f"bundle missing required members: {sorted(missing_common)}"
                )
            manifest = json.loads(zf.read("manifest.json"))
            if manifest.get("schema_version") != SCHEMA_VERSION:
                raise ChiBundleError(
                    f"unsupported schema_version {manifest.get('schema_version')!r}"
                    f"; expected {SCHEMA_VERSION}"
                )
            adapter_format = manifest.get("adapter_format", ADAPTER_FORMAT_GGUF)
            if adapter_format not in _VALID_ADAPTER_FORMATS:
                raise ChiBundleError(
                    f"unsupported adapter_format {adapter_format!r}"
                )
            prompts = json.loads(zf.read("prompts.json"))
            spec = FunctionSpec.from_json(zf.read("spec.json").decode())
            metadata = json.loads(zf.read("metadata.json"))
            adapter_bytes = b""
            adapter_peft_files: dict[str, bytes] = {}
            if adapter_format == ADAPTER_FORMAT_GGUF:
                if "adapter.gguf" not in names:
                    raise ChiBundleError(
                        "gguf-lora bundle missing required member: adapter.gguf"
                    )
                adapter_bytes = zf.read("adapter.gguf")
            else:
                peft_members = [n for n in names if n.startswith(_PEFT_DIR) and not n.endswith("/")]
                if not peft_members:
                    raise ChiBundleError(
                        f"peft bundle missing adapter files under {_PEFT_DIR!r}"
                    )
                for name in peft_members:
                    rel = name[len(_PEFT_DIR) :]
                    adapter_peft_files[rel] = zf.read(name)
        return cls(
            spec=spec,
            adapter_bytes=adapter_bytes,
            prompts=prompts,
            metadata=metadata,
            base_model=manifest["base_model"],
            adapter_format=adapter_format,
            adapter_peft_files=adapter_peft_files,
        )


def _chimera_version() -> str:
    try:
        from importlib.metadata import version

        return version("chimera")
    except Exception:
        return "unknown"
