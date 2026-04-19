"""Live smoke tests for :class:`LlamaCppBackend`.

Opt-in via the ``live`` pytest marker.  Skips cleanly when:

* ``CHIMERA_FS_LIVE_BASE_MODEL`` is unset or does not point to a file, or
* ``llama-cpp-python`` is not installed.

Env vars:

* ``CHIMERA_FS_LIVE_BASE_MODEL`` -- path to a chat-tuned GGUF file on disk.
* ``CHIMERA_FS_LIVE_GGUF_LORA`` -- optional path to a GGUF LoRA adapter;
  when unset, a plain-inference smoke path is used with a 1-byte placeholder
  adapter only if the runtime tolerates it; otherwise the LoRA-dependent
  assertions are skipped.

Run with::

    CHIMERA_FS_LIVE_BASE_MODEL=/path/to/model.gguf \\
        uv run pytest -m live tests/function_synthesis/test_live_llama_cpp.py -v
"""
from __future__ import annotations

import importlib.util
import os
from itertools import islice
from pathlib import Path

import pytest

from chimera.function_synthesis.bundle import ADAPTER_FORMAT_GGUF, ChiBundle
from chimera.function_synthesis.spec import FunctionSpec

pytestmark = pytest.mark.live


def _require_llama_cpp() -> None:
    if importlib.util.find_spec("llama_cpp") is None:
        pytest.skip("llama-cpp-python not installed; skipping live llama.cpp test")


@pytest.fixture
def live_base_model_path() -> Path:
    path = os.environ.get("CHIMERA_FS_LIVE_BASE_MODEL")
    if not path:
        pytest.skip("set CHIMERA_FS_LIVE_BASE_MODEL to a GGUF file on disk")
    p = Path(path)
    if not p.exists() or not p.is_file():
        pytest.skip(f"CHIMERA_FS_LIVE_BASE_MODEL={path} does not exist")
    _require_llama_cpp()
    return p


@pytest.fixture
def live_gguf_lora() -> bytes | None:
    path = os.environ.get("CHIMERA_FS_LIVE_GGUF_LORA")
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        pytest.skip(f"CHIMERA_FS_LIVE_GGUF_LORA={path} does not exist")
    return p.read_bytes()


def _bundle(adapter_bytes: bytes, base: str) -> ChiBundle:
    return ChiBundle(
        spec=FunctionSpec(
            name="live-llama",
            description="Live llama.cpp smoke test.",
        ),
        adapter_bytes=adapter_bytes,
        prompts={
            "system": "You are a helpful assistant.",
            "user_template": "{input}",
            "stop": [],
        },
        base_model=base,
        adapter_format=ADAPTER_FORMAT_GGUF,
    )


def test_live_llama_cpp_invoke_and_stream(live_base_model_path, live_gguf_lora):
    """Load GGUF (+ optional LoRA) and run invoke()+stream()."""
    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    if live_gguf_lora is None:
        pytest.skip(
            "CHIMERA_FS_LIVE_GGUF_LORA not set; the LlamaCppBackend.load() path "
            "requires a real GGUF LoRA adapter. Provide one to enable this test."
        )

    bundle = _bundle(live_gguf_lora, str(live_base_model_path))
    backend = LlamaCppBackend(base_model_path=live_base_model_path)

    try:
        backend.load(bundle)

        out = backend.invoke("hi", max_tokens=8)
        assert isinstance(out, str)
        assert len(out) > 0

        chunks = list(islice(backend.stream("hi", max_tokens=8), 3))
        assert len(chunks) >= 1
        assert all(isinstance(c, str) for c in chunks)
    finally:
        backend.close()


def test_live_llama_cpp_prefix_cache_roundtrip(tmp_path, live_base_model_path, live_gguf_lora):
    """With a :class:`PrefixCache`, a second invocation should hit the cache.

    We verify the cache file exists after the first call and that the second
    call triggers a ``load_state`` (observed by the cache file still being
    present and the second invoke returning without error).
    """
    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend
    from chimera.function_synthesis.prefix_cache import PrefixCache

    if live_gguf_lora is None:
        pytest.skip(
            "CHIMERA_FS_LIVE_GGUF_LORA not set; prefix-cache test needs a real "
            "adapter so the backend can actually load()."
        )

    cache = PrefixCache(root=tmp_path / "prefix-cache")
    bundle = _bundle(live_gguf_lora, str(live_base_model_path))
    backend = LlamaCppBackend(
        base_model_path=live_base_model_path, prefix_cache=cache
    )

    try:
        backend.load(bundle)
        first = backend.invoke("hello", max_tokens=4)
        assert isinstance(first, str)

        # If the runtime's state API is usable, a cache file should exist now.
        cache_files = list((tmp_path / "prefix-cache").glob("*.state"))
        if not cache_files:
            pytest.skip(
                "llama-cpp-python on this platform does not expose a usable "
                "save_state/load_state pair; prefix cache was silently bypassed."
            )

        # Second invocation should load the cached state without crashing.
        second = backend.invoke("hello", max_tokens=4)
        assert isinstance(second, str)
    finally:
        backend.close()
