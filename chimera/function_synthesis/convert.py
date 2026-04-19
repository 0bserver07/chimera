"""Convert an on-disk HuggingFace PEFT adapter directory into a ``.chi`` bundle.

The PEFT workflow on HF typically produces a directory like::

    my_adapter/
        adapter_config.json
        adapter_model.safetensors   (or adapter_model.bin)

:func:`import_peft` packages that directory — config + weights — into a
standard ``.chi`` ZIP.  The archive keeps all of :class:`ChiBundle`'s
required members so downstream code that uses ``ChiBundle.load`` still
works; the PEFT-specific files live under an ``adapter_peft/`` prefix and
the manifest records ``adapter_format: "peft"``.

This module is zero-dependency: it neither imports ``peft`` nor ``torch``.
It only reads raw bytes and parses ``adapter_config.json`` as plain JSON.
A PEFT-aware runtime backend (not included here) would consume the
``adapter_peft/`` members at inference time.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import SCHEMA_VERSION, ChiBundle
from chimera.function_synthesis.spec import FunctionSpec

# Files we try to read from the PEFT directory.  The config is required; the
# weight files are tried in priority order and at least one must exist.
_PEFT_CONFIG_FILENAME = "adapter_config.json"
_PEFT_WEIGHT_CANDIDATES = ("adapter_model.safetensors", "adapter_model.bin")
_PEFT_OPTIONAL_FILENAMES = (
    # Also ship these if present — useful for tokenizer-aware adapters.
    "tokenizer_config.json",
    "tokenizer.json",
    "special_tokens_map.json",
    "README.md",
)

# Archive layout inside the .chi file.
_PEFT_PREFIX = "adapter_peft/"


class PeftImportError(ValueError):
    """Raised when a PEFT directory is missing required files or malformed."""


@dataclass(frozen=True)
class _PeftContents:
    """The raw bytes we ship inside the .chi archive."""

    config_bytes: bytes
    config: dict[str, Any]
    weights_filename: str
    weights_bytes: bytes
    optional: dict[str, bytes]  # filename -> bytes

    def config_hash(self) -> str:
        return hashlib.sha256(self.config_bytes).hexdigest()

    def weights_hash(self) -> str:
        return hashlib.sha256(self.weights_bytes).hexdigest()


def _read_peft_dir(peft_dir: Path) -> _PeftContents:
    config_path = peft_dir / _PEFT_CONFIG_FILENAME
    if not config_path.exists():
        raise PeftImportError(
            f"{_PEFT_CONFIG_FILENAME!r} not found in {peft_dir}. "
            "Expected a directory saved with `model.save_pretrained(...)` "
            "from HuggingFace PEFT."
        )
    config_bytes = config_path.read_bytes()
    try:
        config = json.loads(config_bytes)
    except json.JSONDecodeError as exc:
        raise PeftImportError(f"malformed {_PEFT_CONFIG_FILENAME}: {exc}") from exc
    if not isinstance(config, dict):
        raise PeftImportError(
            f"{_PEFT_CONFIG_FILENAME} must decode to a JSON object"
        )

    weights_filename: str | None = None
    weights_bytes: bytes | None = None
    for candidate in _PEFT_WEIGHT_CANDIDATES:
        path = peft_dir / candidate
        if path.exists():
            weights_filename = candidate
            weights_bytes = path.read_bytes()
            break
    if weights_filename is None or weights_bytes is None:
        raise PeftImportError(
            f"no adapter weights found in {peft_dir}. "
            f"Expected one of: {', '.join(_PEFT_WEIGHT_CANDIDATES)}"
        )

    optional: dict[str, bytes] = {}
    for name in _PEFT_OPTIONAL_FILENAMES:
        path = peft_dir / name
        if path.exists() and path.is_file():
            optional[name] = path.read_bytes()

    return _PeftContents(
        config_bytes=config_bytes,
        config=config,
        weights_filename=weights_filename,
        weights_bytes=weights_bytes,
        optional=optional,
    )


def import_peft(
    peft_dir: str | Path,
    *,
    spec: FunctionSpec,
    prompts: dict[str, Any],
    base_model: str | None = None,
    metadata_extras: dict[str, Any] | None = None,
) -> ChiBundle:
    """Package a HuggingFace PEFT adapter directory as a :class:`ChiBundle`.

    The returned bundle, when saved via :meth:`ChiBundle.save`, produces a
    ``.chi`` file whose manifest records ``adapter_format: "peft"`` and that
    embeds the original PEFT files under ``adapter_peft/``.  Call
    :func:`save_peft_bundle` to write that archive in a single step.

    Args:
        peft_dir: Path to a PEFT adapter directory (must contain
            ``adapter_config.json`` and one of ``adapter_model.safetensors``
            or ``adapter_model.bin``).
        spec: Caller-supplied :class:`FunctionSpec` describing the function.
        prompts: System / user_template / stop strings for invocation.
        base_model: Override the base model identifier.  When ``None``, falls
            back to ``adapter_config["base_model_name_or_path"]``; raises if
            neither is set.
        metadata_extras: Optional free-form keys merged into the bundle
            metadata.

    Returns:
        A :class:`ChiBundle` ready to pass to :func:`save_peft_bundle`.

    Raises:
        PeftImportError: The directory is missing required files or the
            base model cannot be determined.
    """
    peft_path = Path(peft_dir)
    if not peft_path.is_dir():
        raise PeftImportError(f"{peft_path} is not a directory")
    contents = _read_peft_dir(peft_path)

    resolved_base = base_model or contents.config.get("base_model_name_or_path")
    if not resolved_base:
        raise PeftImportError(
            "base_model could not be determined: pass base_model=... or "
            "ensure adapter_config.json has 'base_model_name_or_path'"
        )

    metadata: dict[str, Any] = {
        "adapter_source": "peft",
        "peft_config_hash": contents.config_hash(),
        "peft_weights_hash": contents.weights_hash(),
        "peft_weights_filename": contents.weights_filename,
        "peft_config": {
            k: contents.config.get(k)
            for k in (
                "peft_type",
                "task_type",
                "r",
                "lora_alpha",
                "target_modules",
                "base_model_name_or_path",
            )
            if k in contents.config
        },
        "peft_optional_files": sorted(contents.optional),
    }
    if metadata_extras:
        metadata.update(metadata_extras)

    # We keep adapter_bytes = weights_bytes so downstream tooling that only
    # reads the canonical weights stream still works; the full per-file
    # layout is preserved inside the archive by save_peft_bundle().
    return ChiBundle(
        spec=spec,
        adapter_bytes=contents.weights_bytes,
        prompts=prompts,
        metadata=metadata,
        base_model=str(resolved_base),
    )


def save_peft_bundle(
    bundle: ChiBundle,
    peft_dir: str | Path,
    out_path: str | Path,
) -> Path:
    """Write ``bundle`` to ``out_path`` as a ``.chi`` archive with PEFT files.

    Unlike :meth:`ChiBundle.save`, this writes an extended archive: the
    manifest's ``adapter_format`` is set to ``"peft"``, and the raw PEFT
    directory contents are preserved under ``adapter_peft/`` (so the original
    ``adapter_config.json`` + weights bytes can be recovered byte-for-byte).

    The standard ``.chi`` members (``adapter.gguf``, ``manifest.json``,
    ``prompts.json``, ``spec.json``, ``metadata.json``) are all present so
    existing :meth:`ChiBundle.load` call sites remain compatible.  The
    ``adapter.gguf`` member is a stub — a PEFT-aware runtime should read
    the ``adapter_peft/`` members instead.

    Args:
        bundle: The bundle produced by :func:`import_peft`.
        peft_dir: Same directory that was passed to :func:`import_peft`; the
            files are re-read here to keep the function independent of bundle
            internals.
        out_path: Destination ``.chi`` file.

    Returns:
        The path written to, as a :class:`Path`.
    """
    peft_path = Path(peft_dir)
    contents = _read_peft_dir(peft_path)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": bundle.spec.name,
        "description": bundle.spec.description,
        "base_model": bundle.base_model,
        "adapter_format": "peft",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chimera_version": _chimera_version(),
        "peft_weights_filename": contents.weights_filename,
    }

    out = Path(out_path)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        # Required ChiBundle members — keeps ChiBundle.load() happy.
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        # Stub GGUF adapter for format compatibility.  Empty content is
        # invalid for llama.cpp, which is correct: a peft-format bundle
        # should be refused by the llama.cpp backend at runtime.
        zf.writestr("adapter.gguf", b"")
        zf.writestr("prompts.json", json.dumps(bundle.prompts, sort_keys=True))
        zf.writestr("spec.json", bundle.spec.to_json())
        zf.writestr("metadata.json", json.dumps(bundle.metadata, sort_keys=True))
        # PEFT-specific payload.
        zf.writestr(_PEFT_PREFIX + _PEFT_CONFIG_FILENAME, contents.config_bytes)
        zf.writestr(
            _PEFT_PREFIX + contents.weights_filename,
            contents.weights_bytes,
        )
        for name, data in contents.optional.items():
            zf.writestr(_PEFT_PREFIX + name, data)

    return out


def extract_peft_files(chi_path: str | Path, out_dir: str | Path) -> Path:
    """Extract the ``adapter_peft/`` members of a ``.chi`` archive.

    Restores the PEFT directory layout byte-for-byte to ``out_dir`` so the
    original HuggingFace adapter can be round-tripped.

    Args:
        chi_path: A ``.chi`` file produced by :func:`save_peft_bundle`.
        out_dir: Destination directory; created if missing.

    Returns:
        ``out_dir`` as a :class:`Path`.

    Raises:
        PeftImportError: The archive has no ``adapter_peft/`` members.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(chi_path) as zf:
        for name in zf.namelist():
            if not name.startswith(_PEFT_PREFIX):
                continue
            relative = name[len(_PEFT_PREFIX):]
            if not relative or relative.endswith("/"):
                continue
            target = out_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
            count += 1
    if count == 0:
        raise PeftImportError(
            f"{chi_path} has no {_PEFT_PREFIX!r} members; "
            "it was not created by save_peft_bundle()"
        )
    return out_dir


def _chimera_version() -> str:
    try:
        from importlib.metadata import version

        return version("chimera")
    except Exception:
        return "unknown"
