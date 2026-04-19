"""Live smoke tests for :class:`TransformersBackend`.

These tests hit the real :mod:`transformers` + :mod:`peft` stack and are
opt-in via the ``live`` pytest marker.  They skip cleanly when:

* the ``CHIMERA_FS_LIVE_TRANSFORMERS_MODEL`` env var is unset, or
* the optional deps (``transformers``/``peft``/``torch``) are not installed.

Env vars:

* ``CHIMERA_FS_LIVE_TRANSFORMERS_MODEL`` -- a tiny HF causal-LM id suitable
  for CPU (e.g. ``"sshleifer/tiny-gpt2"``, ~100 MB).
* ``CHIMERA_FS_LIVE_PEFT_ADAPTER`` -- optional path to an on-disk PEFT
  adapter directory; when unset, the peft-load part is skipped and we
  exercise only the import+lifecycle checks.

Run with::

    CHIMERA_FS_LIVE_TRANSFORMERS_MODEL=sshleifer/tiny-gpt2 \\
        uv run pytest -m live tests/function_synthesis/test_live_transformers.py -v
"""
from __future__ import annotations

import importlib.util
import os
from itertools import islice
from pathlib import Path

import pytest

from chimera.function_synthesis.bundle import ADAPTER_FORMAT_PEFT, ChiBundle
from chimera.function_synthesis.spec import FunctionSpec

pytestmark = pytest.mark.live


def _require_transformers_stack() -> None:
    """Skip if any of the optional deps are missing."""
    for mod in ("transformers", "peft", "torch"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"{mod} not installed; skipping live transformers test")


@pytest.fixture
def live_base_model() -> str:
    name = os.environ.get("CHIMERA_FS_LIVE_TRANSFORMERS_MODEL")
    if not name:
        pytest.skip(
            "set CHIMERA_FS_LIVE_TRANSFORMERS_MODEL to a tiny HF causal-LM id "
            "(e.g. 'sshleifer/tiny-gpt2')"
        )
    _require_transformers_stack()
    return name


@pytest.fixture
def live_peft_adapter() -> Path | None:
    """Return a directory with a real PEFT adapter, or None when unset."""
    path = os.environ.get("CHIMERA_FS_LIVE_PEFT_ADAPTER")
    if not path:
        return None
    adapter_dir = Path(path)
    if not adapter_dir.is_dir():
        pytest.skip(
            f"CHIMERA_FS_LIVE_PEFT_ADAPTER={path} is not a directory; "
            "point it at a saved PeftModel directory"
        )
    return adapter_dir


def _bundle_from_peft_dir(adapter_dir: Path, base: str) -> ChiBundle:
    """Build a :class:`ChiBundle` by slurping every file under ``adapter_dir``."""
    files: dict[str, bytes] = {}
    for path in sorted(adapter_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(adapter_dir).as_posix()
            files[rel] = path.read_bytes()
    if not files:
        pytest.skip(f"adapter dir {adapter_dir} is empty")
    return ChiBundle(
        spec=FunctionSpec(
            name="live-smoke",
            description="Live smoke test of TransformersBackend.",
        ),
        prompts={
            "system": "You are a helpful assistant.",
            "user_template": "{input}",
            "stop": [],
        },
        base_model=base,
        adapter_format=ADAPTER_FORMAT_PEFT,
        adapter_peft_files=files,
    )


def test_live_transformers_invoke_and_stream(live_base_model, live_peft_adapter):
    """Load a real PEFT adapter on a tiny base model and run invoke()+stream()."""
    if live_peft_adapter is None:
        pytest.skip(
            "CHIMERA_FS_LIVE_PEFT_ADAPTER not set; skipping peft-load path"
        )

    from chimera.function_synthesis.backends.transformers import TransformersBackend

    bundle = _bundle_from_peft_dir(live_peft_adapter, live_base_model)
    backend = TransformersBackend(live_base_model, device="cpu")

    try:
        backend.load(bundle)

        out = backend.invoke("hello", max_tokens=8)
        assert isinstance(out, str)
        assert len(out) > 0

        chunks = list(islice(backend.stream("hi", max_tokens=8), 3))
        # Tiny models may emit fewer than 3 chunks; require at least one.
        assert len(chunks) >= 1
        assert all(isinstance(c, str) for c in chunks)
    finally:
        backend.close()

    # Best-effort GPU-leak probe: on CPU there is no GPU state, this is just a
    # sanity check that close() did not leave the CUDA cache in a broken state.
    try:
        import torch  # type: ignore[import-not-found]

        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def test_live_transformers_import_only(live_base_model):
    """Even without a PEFT adapter we can construct the backend and check deps.

    This guarantees that, on CI machines without a real adapter, the
    transformers stack imports cleanly for the configured base model id.
    """
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend(live_base_model, device="cpu")
    # No bundle loaded -> invoke() must raise a clear error.
    with pytest.raises(RuntimeError, match="not loaded"):
        backend.invoke("hello")
    backend.close()
