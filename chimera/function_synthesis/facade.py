"""Top-level convenience API for :mod:`chimera.function_synthesis`.

This module offers a 2-line compile + invoke flow on top of the lower-level
primitives (:class:`CompilerBackend`, :class:`RuntimeBackend`, the program
:class:`ProgramRegistry`).  It is opinionated about defaults but never hides
a backend behind a mystery string: every alias resolves to a real public
class in this package.

Example:

    import chimera.function_synthesis as fs

    slug = fs.compile(spec)              # defaults to LocalCompiler
    fn = fs.load(slug)                   # auto-detects backend from bundle
    print(fn("hello"))
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.function_synthesis.bundle import (
    ADAPTER_FORMAT_GGUF,
    ADAPTER_FORMAT_PEFT,
    ChiBundle,
)
from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.registry import ProgramRegistry
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


# Env var controlling the default base model used when the caller doesn't
# supply a compiler instance.  Kept small on purpose so a first-time user
# doesn't pull a multi-gigabyte checkpoint.
_DEFAULT_COMPILE_MODEL_ENV = "CHIMERA_FS_DEFAULT_COMPILE_MODEL"
_DEFAULT_COMPILE_MODEL = "Qwen/Qwen2-0.5B"

# Env var controlling the default base GGUF model path used by
# :class:`LlamaCppBackend` when the caller doesn't supply one.
_DEFAULT_GGUF_MODEL_ENV = "CHIMERA_FS_DEFAULT_GGUF_MODEL"


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def compile(  # noqa: A001 - intentionally shadows builtin to mirror the public surface
    spec: FunctionSpec,
    *,
    compiler: str | CompilerBackend | None = None,
    base_model: str | None = None,
) -> str:
    """Compile ``spec`` and install the resulting bundle into the local registry.

    Args:
        spec: The function specification to compile.
        compiler: Either a :class:`CompilerBackend` instance, a string alias
            (``"local"``, ``"mock"``), or ``None``.  When ``None``, a
            :class:`LocalCompiler` is constructed using ``base_model`` or the
            default model (see :data:`CHIMERA_FS_DEFAULT_COMPILE_MODEL`).
        base_model: Optional override for the base model identifier.  Only
            applied when ``compiler`` is ``None`` or a string alias that
            accepts a base-model argument (``"local"``).

    Returns:
        The slug under which the compiled bundle was installed.  Pass this
        slug to :func:`load` to get a callable :class:`CompiledFunction`.

    Raises:
        TypeError: If ``compiler`` is not a supported type.
        ValueError: If ``compiler`` is a string that is not a known alias.
    """
    backend = _resolve_compiler(compiler, base_model=base_model)
    bundle = backend.compile(spec)
    registry = ProgramRegistry.default()
    return registry.install(spec=spec, bundle=bundle)


def load(
    slug_or_path: str | Path,
    *,
    backend: str | RuntimeBackend | None = None,
    base_model: str | None = None,
) -> CompiledFunction:
    """Load a compiled function from the registry or from a ``.chi`` file.

    Args:
        slug_or_path: Either a slug previously returned by :func:`compile`
            (looked up in the default registry) or a filesystem path to a
            ``.chi`` bundle.  Paths are detected by looking for ``.chi``
            suffix or by checking that the value points to an existing file.
        backend: Either a :class:`RuntimeBackend` instance, a string alias
            (``"transformers"``, ``"llama_cpp"``), or ``None``.  When
            ``None``, the backend is chosen from the bundle's
            ``adapter_format``:

            - ``"peft"`` → :class:`TransformersBackend`
            - ``"gguf-lora"`` → :class:`LlamaCppBackend`
        base_model: Optional override for the base model identifier.  For
            ``TransformersBackend`` this is a HuggingFace model id; for
            :class:`LlamaCppBackend` it is a path to a GGUF file.  When
            ``None``, the bundle's recorded ``base_model`` is used (after
            checking the matching env var override for GGUF).

    Returns:
        A loaded :class:`CompiledFunction` ready to be called.

    Raises:
        ImportError: If the selected backend's optional dependency group
            is not installed.  The message includes an install hint.
        FileNotFoundError: If ``slug_or_path`` looks like a path but does
            not exist on disk.
        ValueError: If the bundle declares an unknown ``adapter_format``,
            or if ``backend`` is a string alias that isn't recognised.
    """
    bundle_path = _resolve_bundle_path(slug_or_path)
    bundle = ChiBundle.load(bundle_path)
    runtime = _resolve_backend(backend, bundle=bundle, base_model=base_model)
    return CompiledFunction(bundle, runtime)


def installed() -> list[str]:
    """Return the slugs of all programs in the default registry.

    Returns:
        A sorted list of slug strings.  Empty if no programs are installed.
    """
    registry = ProgramRegistry.default()
    return [entry.slug for entry in registry.list()]


def uninstall(slug: str) -> None:
    """Remove a program from the default registry.

    Args:
        slug: The slug (as returned by :func:`compile` or :func:`installed`)
            of the program to remove.  If the slug is not installed, this
            call is a silent no-op — consistent with
            :meth:`ProgramRegistry.remove`.
    """
    registry = ProgramRegistry.default()
    registry.remove(slug)


# ---------------------------------------------------------------------------
# compiler resolution
# ---------------------------------------------------------------------------


def _resolve_compiler(
    compiler: str | CompilerBackend | None,
    *,
    base_model: str | None,
) -> CompilerBackend:
    """Return a concrete :class:`CompilerBackend` for the facade's ``compile``.

    Args:
        compiler: The caller-provided compiler spec.
        base_model: Optional base-model override forwarded to string-alias
            factories that accept one.

    Returns:
        A :class:`CompilerBackend` instance ready to compile.

    Raises:
        TypeError: If ``compiler`` is not a string, instance, or None.
        ValueError: If ``compiler`` is a string but not a known alias.
    """
    if compiler is None:
        return _default_local_compiler(base_model)
    if isinstance(compiler, CompilerBackend):
        return compiler
    if isinstance(compiler, str):
        alias = compiler.lower()
        if alias == "local":
            return _default_local_compiler(base_model)
        if alias == "mock":
            from chimera.function_synthesis.compilers.mock import MockCompiler

            return MockCompiler()
        raise ValueError(
            f"unknown compiler alias {compiler!r}; expected one of "
            f"'local', 'mock' (or pass a CompilerBackend instance)"
        )
    raise TypeError(
        f"compiler must be a CompilerBackend, str alias, or None; "
        f"got {type(compiler).__name__}"
    )


def _default_local_compiler(base_model: str | None) -> CompilerBackend:
    """Construct the default :class:`LocalCompiler` for facade callers.

    Args:
        base_model: Explicit base-model override, or ``None`` to read from
            the :data:`CHIMERA_FS_DEFAULT_COMPILE_MODEL` env var, falling
            back to :data:`_DEFAULT_COMPILE_MODEL`.

    Returns:
        A :class:`LocalCompiler` instance bound to the resolved base model.
    """
    from chimera.function_synthesis.compilers.local import LocalCompiler

    resolved = base_model or os.environ.get(
        _DEFAULT_COMPILE_MODEL_ENV, _DEFAULT_COMPILE_MODEL
    )
    return LocalCompiler(resolved)


# ---------------------------------------------------------------------------
# backend resolution
# ---------------------------------------------------------------------------


def _resolve_backend(
    backend: str | RuntimeBackend | None,
    *,
    bundle: ChiBundle,
    base_model: str | None,
) -> RuntimeBackend:
    """Return a concrete :class:`RuntimeBackend` for :func:`load`.

    Args:
        backend: The caller-provided backend spec.
        bundle: The loaded bundle whose ``adapter_format`` drives auto-detect.
        base_model: Optional override for the base model identifier/path.

    Returns:
        A :class:`RuntimeBackend` instance ready to run inference.

    Raises:
        ImportError: If the selected backend's optional deps are missing.
        ValueError: If ``backend`` is an unknown string alias, or the bundle
            declares an unsupported adapter format.
        TypeError: If ``backend`` is not a string, instance, or None.
    """
    if isinstance(backend, RuntimeBackend):
        return backend
    if backend is None:
        alias = _auto_detect_backend(bundle)
    elif isinstance(backend, str):
        alias = backend.lower()
    else:
        raise TypeError(
            f"backend must be a RuntimeBackend, str alias, or None; "
            f"got {type(backend).__name__}"
        )

    if alias in {"transformers", "peft", "hf"}:
        return _build_transformers_backend(bundle, base_model)
    if alias in {"llama_cpp", "llama-cpp", "llamacpp", "gguf"}:
        return _build_llama_cpp_backend(bundle, base_model)
    raise ValueError(
        f"unknown backend alias {alias!r}; expected one of "
        f"'transformers', 'llama_cpp' (or pass a RuntimeBackend instance)"
    )


def _auto_detect_backend(bundle: ChiBundle) -> str:
    """Pick a backend alias from a bundle's adapter format.

    Args:
        bundle: The loaded :class:`ChiBundle`.

    Returns:
        ``"transformers"`` for PEFT bundles, ``"llama_cpp"`` for GGUF.

    Raises:
        ValueError: If ``bundle.adapter_format`` is unknown.
    """
    fmt = bundle.adapter_format
    if fmt == ADAPTER_FORMAT_PEFT:
        return "transformers"
    if fmt == ADAPTER_FORMAT_GGUF:
        return "llama_cpp"
    raise ValueError(
        f"cannot auto-detect backend: unknown adapter_format {fmt!r}"
    )


def _build_transformers_backend(
    bundle: ChiBundle, base_model: str | None
) -> RuntimeBackend:
    """Construct a :class:`TransformersBackend` with a friendly import error.

    Args:
        bundle: The bundle whose ``base_model`` provides the default.
        base_model: Optional override for the HuggingFace model id.

    Returns:
        A :class:`TransformersBackend` bound to the chosen base model.

    Raises:
        ImportError: Re-raised with the backend's install hint when
            importing the backend module fails because its optional deps
            are missing.
    """
    try:
        from chimera.function_synthesis.backends.transformers import (
            TransformersBackend,
        )
    except ImportError as exc:
        raise ImportError(
            "TransformersBackend requires the optional dependency group "
            "'function_synthesis_transformers'. Install with: "
            "pip install 'chimera-ai[function_synthesis_transformers]' "
            "(or: pip install transformers peft torch safetensors)."
        ) from exc
    resolved = base_model or bundle.base_model
    return TransformersBackend(resolved)


def _build_llama_cpp_backend(
    bundle: ChiBundle, base_model: str | None
) -> RuntimeBackend:
    """Construct a :class:`LlamaCppBackend` with a friendly import error.

    Args:
        bundle: The bundle whose ``base_model`` may hint at a GGUF file.
        base_model: Optional override for the GGUF model path.  When
            ``None``, we check :data:`CHIMERA_FS_DEFAULT_GGUF_MODEL` first
            then fall back to ``bundle.base_model``.

    Returns:
        A :class:`LlamaCppBackend` pointing at the chosen GGUF file.

    Raises:
        ImportError: Re-raised with the backend's install hint when
            importing the backend module fails because its optional deps
            are missing.
    """
    try:
        from chimera.function_synthesis.backends.llama_cpp import (
            LlamaCppBackend,
        )
    except ImportError as exc:
        raise ImportError(
            "LlamaCppBackend requires llama-cpp-python. "
            "Install with: pip install 'chimera-ai[function_synthesis]' "
            "(or: pip install llama-cpp-python)."
        ) from exc
    resolved = (
        base_model
        or os.environ.get(_DEFAULT_GGUF_MODEL_ENV)
        or bundle.base_model
    )
    return LlamaCppBackend(base_model_path=resolved)


# ---------------------------------------------------------------------------
# slug / path helpers
# ---------------------------------------------------------------------------


def _looks_like_path(value: str | Path) -> bool:
    """Return True when ``value`` should be treated as a filesystem path.

    Heuristic: either a :class:`Path`, a string ending in ``.chi``, or a
    string that already points to an existing file.  Registry slugs
    generated by :func:`slug_for` never end in ``.chi`` and never contain
    path separators, so this lets us keep the two namespaces disjoint
    without ambiguity.

    Args:
        value: The caller-provided identifier.

    Returns:
        True if ``value`` should be interpreted as a path, False for a slug.
    """
    if isinstance(value, Path):
        return True
    if value.endswith(".chi"):
        return True
    if os.sep in value or "/" in value:
        return True
    return Path(value).is_file()


def _resolve_bundle_path(slug_or_path: str | Path) -> Path:
    """Resolve a slug or path argument to a concrete ``.chi`` path on disk.

    Args:
        slug_or_path: Either a slug or a path to a ``.chi`` file.

    Returns:
        The path to the bundle on disk.

    Raises:
        FileNotFoundError: If ``slug_or_path`` looks like a path but does
            not exist.
    """
    if isinstance(slug_or_path, Path) or _looks_like_path(slug_or_path):
        path = Path(slug_or_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"bundle file not found: {path}"
            )
        return path
    registry = ProgramRegistry.default()
    entry = registry.resolve(str(slug_or_path))
    return entry.bundle_path
