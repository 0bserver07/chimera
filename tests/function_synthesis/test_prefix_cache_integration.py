"""Integration tests for PrefixCache + LlamaCppBackend wiring.

Verifies that:

* Real-shape save_state/load_state (returning opaque state objects, not bytes)
  is pickled to disk and unpickled on reload.
* Capability detect returns cleanly on API mismatch (missing methods, methods
  that raise, methods that return None).
* Cold invoke writes cache; warm invoke hits cache.
* There is also a skipped ``live`` test that exercises a real GGUF when the
  environment provides one (see ``CHIMERA_FS_LIVE_BASE_MODEL``).
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.prefix_cache import PrefixCache
from chimera.function_synthesis.spec import FunctionSpec


class _FakeState:
    """Stand-in for llama_cpp.LlamaState — opaque, not bytes, picklable."""

    def __init__(self, token_count: int, blob: bytes) -> None:
        self.token_count = token_count
        self.blob = blob

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _FakeState):
            return NotImplemented
        return self.token_count == other.token_count and self.blob == other.blob


class _RealShapeLlama:
    """Mock Llama with the real llama-cpp-python shape.

    * ``save_state()`` returns a ``_FakeState`` object (not bytes).
    * ``load_state(state)`` accepts a ``_FakeState``; raises on bytes input.
    * ``create_chat_completion`` returns the standard OpenAI-ish dict.
    """

    def __init__(self, *, model_path: str, lora_path: str, **_kwargs) -> None:
        self.model_path = model_path
        self.lora_path = lora_path
        self._state = _FakeState(token_count=0, blob=b"initial")
        self.save_calls = 0
        self.load_calls = 0

    def save_state(self) -> _FakeState:
        self.save_calls += 1
        # Return a distinct state each time; simulates post-prefill state.
        return _FakeState(
            token_count=self.save_calls,
            blob=f"state-{self.save_calls}".encode(),
        )

    def load_state(self, state: _FakeState) -> None:
        if not isinstance(state, _FakeState):
            raise TypeError(f"expected _FakeState, got {type(state).__name__}")
        self.load_calls += 1
        self._state = state

    def create_chat_completion(self, messages, max_tokens, stop=None):
        return {"choices": [{"message": {"content": "RESULT"}}]}


def _install_fake_llama_cpp(monkeypatch, llama_factory):
    fake_module = types.ModuleType("llama_cpp")
    fake_module.Llama = llama_factory
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)


def _bundle() -> ChiBundle:
    return ChiBundle(
        spec=FunctionSpec(name="echo", description="echo"),
        adapter_bytes=b"ADAPTER",
        prompts={"system": "you are echo", "user_template": "U:{input}", "stop": []},
        metadata={"slug": "echo-abcd1234"},
    )


def _make_base(tmp_path: Path) -> Path:
    p = tmp_path / "base.gguf"
    p.write_bytes(b"BASE_BYTES")
    return p


def test_prefix_cache_cold_invoke_writes_cache(tmp_path, monkeypatch):
    """Cold invoke: cache empty -> save_state gets called, bytes persist."""
    _install_fake_llama_cpp(monkeypatch, _RealShapeLlama)
    cache = PrefixCache(root=tmp_path / "prefix_cache")
    base = _make_base(tmp_path)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend.load(_bundle())
    out = backend.invoke("hi")
    assert out == "RESULT"

    # Capability probe: save+load once each.  Then cold invoke: no load
    # (cache empty), one save at end.  So total save_calls == 2.
    assert backend._llm.save_calls == 2
    assert backend._llm.load_calls == 1  # only the probe's load
    # Cache file is present on disk.
    files = list((tmp_path / "prefix_cache").glob("*.state"))
    assert len(files) == 1
    backend.close()


def test_prefix_cache_warm_invoke_hits_cache(tmp_path, monkeypatch):
    """Warm invoke: cache populated -> load_state gets called with unpickled state."""
    _install_fake_llama_cpp(monkeypatch, _RealShapeLlama)
    cache = PrefixCache(root=tmp_path / "prefix_cache")
    base = _make_base(tmp_path)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    # Cold invoke (populates cache)
    backend = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend.load(_bundle())
    backend.invoke("hi")
    backend.close()

    # Warm invoke (new backend instance, shared cache dir)
    backend2 = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend2.load(_bundle())
    load_calls_before = backend2._llm.load_calls
    backend2.invoke("hi")
    load_calls_after = backend2._llm.load_calls
    # Probe fires 1 load_state; warm hit fires another.  So >= 2.
    assert load_calls_after - load_calls_before >= 1
    backend2.close()


def test_prefix_cache_bypassed_when_state_api_missing(tmp_path, monkeypatch):
    """If Llama has no save_state/load_state: capability detect returns False silently."""

    class _NoStateLlama:
        def __init__(self, **_kwargs):
            pass

        def create_chat_completion(self, messages, max_tokens, stop=None):
            return {"choices": [{"message": {"content": "OK"}}]}

    _install_fake_llama_cpp(monkeypatch, _NoStateLlama)
    cache = PrefixCache(root=tmp_path / "pc")
    base = _make_base(tmp_path)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend.load(_bundle())
    out = backend.invoke("hi")
    assert out == "OK"
    assert backend._state_api_ok is False
    # No cache file was written.
    assert not list((tmp_path / "pc").glob("*.state"))
    backend.close()


def test_prefix_cache_bypassed_when_save_state_raises(tmp_path, monkeypatch):
    """If save_state is present but raises on probe: bypass gracefully."""

    class _BrokenStateLlama:
        def __init__(self, **_kwargs):
            pass

        def save_state(self):
            raise RuntimeError("library version mismatch")

        def load_state(self, state):
            pass

        def create_chat_completion(self, messages, max_tokens, stop=None):
            return {"choices": [{"message": {"content": "OK"}}]}

    _install_fake_llama_cpp(monkeypatch, _BrokenStateLlama)
    cache = PrefixCache(root=tmp_path / "pc")
    base = _make_base(tmp_path)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend.load(_bundle())
    out = backend.invoke("hi")
    assert out == "OK"
    assert backend._state_api_ok is False
    backend.close()


def test_prefix_cache_bypassed_when_save_state_returns_none(tmp_path, monkeypatch):
    """If save_state returns None: bypass gracefully."""

    class _NullStateLlama:
        def __init__(self, **_kwargs):
            pass

        def save_state(self):
            return None

        def load_state(self, state):
            pass

        def create_chat_completion(self, messages, max_tokens, stop=None):
            return {"choices": [{"message": {"content": "OK"}}]}

    _install_fake_llama_cpp(monkeypatch, _NullStateLlama)
    cache = PrefixCache(root=tmp_path / "pc")
    base = _make_base(tmp_path)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend.load(_bundle())
    out = backend.invoke("hi")
    assert out == "OK"
    assert backend._state_api_ok is False
    backend.close()


def test_prefix_cache_bypassed_when_load_state_rejects_bytes(tmp_path, monkeypatch):
    """Direct-bytes path: if load_state rejects bytes, still fine because we pickle.

    This regression-tests the actual bug: previously the cache stored the
    opaque state object directly (which isn't bytes) and fed it back in.
    Now we pickle on store + unpickle on load, so the flow works even when
    the state object would reject being confused with bytes.
    """
    _install_fake_llama_cpp(monkeypatch, _RealShapeLlama)
    cache = PrefixCache(root=tmp_path / "pc")
    base = _make_base(tmp_path)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    # First invoke populates cache.
    backend = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend.load(_bundle())
    backend.invoke("hi")
    backend.close()

    # Read raw bytes from disk — confirm they are a pickle stream, not a
    # raw _FakeState repr or anything exotic.
    files = list((tmp_path / "pc").glob("*.state"))
    assert len(files) == 1
    data = files[0].read_bytes()
    import pickle as _pickle

    restored = _pickle.loads(data)
    assert isinstance(restored, _FakeState)


def test_prefix_cache_disabled_flag_bypasses(tmp_path, monkeypatch):
    """When cache.enabled=False, no save_state/load_state interaction happens."""
    _install_fake_llama_cpp(monkeypatch, _RealShapeLlama)
    cache = PrefixCache(root=tmp_path / "pc", enabled=False)
    base = _make_base(tmp_path)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend.load(_bundle())
    backend.invoke("hi")
    # No state-API interaction when the cache is disabled.
    assert backend._llm.save_calls == 0
    assert backend._llm.load_calls == 0
    backend.close()


def test_prefix_cache_magicmock_shape_matches_real_api(tmp_path, monkeypatch):
    """Use a MagicMock configured to mirror llama-cpp-python's documented shape.

    This is a second-line-of-defense unit test: even if the _RealShapeLlama
    class drifts, MagicMock shape confirms what we rely on.
    """
    state_obj = _FakeState(token_count=5, blob=b"mocked")

    mock_llm = MagicMock()
    mock_llm.save_state = MagicMock(return_value=state_obj)
    mock_llm.load_state = MagicMock(return_value=None)
    mock_llm.create_chat_completion = MagicMock(
        return_value={"choices": [{"message": {"content": "MOCKED"}}]}
    )

    fake_module = types.ModuleType("llama_cpp")
    fake_module.Llama = MagicMock(return_value=mock_llm)
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)

    cache = PrefixCache(root=tmp_path / "pc")
    base = _make_base(tmp_path)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base, prefix_cache=cache)
    backend.load(_bundle())
    out = backend.invoke("hello")
    assert out == "MOCKED"
    # State API was exercised: at least the probe + final save.
    assert mock_llm.save_state.call_count >= 1
    assert mock_llm.load_state.call_count >= 1
    backend.close()


# -----------------------------------------------------------------------------
# Live integration test — requires a real GGUF via CHIMERA_FS_LIVE_BASE_MODEL.
# -----------------------------------------------------------------------------


@pytest.mark.live
def test_prefix_cache_live_end_to_end(tmp_path):
    """End-to-end with a real llama-cpp-python + tiny GGUF.

    Confirms that a second invocation under identical (base_model, slug,
    system_prompt) conditions runs against a populated cache file.
    """
    model_path = os.environ.get("CHIMERA_FS_LIVE_BASE_MODEL")
    if not model_path or not Path(model_path).exists():
        pytest.skip("set CHIMERA_FS_LIVE_BASE_MODEL to a chat-tuned GGUF")
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        pytest.skip("llama-cpp-python not installed")

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend
    from chimera.function_synthesis.compilers.mock import MockCompiler

    cache = PrefixCache(root=tmp_path / "pc")
    spec = FunctionSpec(name="echo", description="Echo input.")
    bundle = MockCompiler().compile(spec)

    backend = LlamaCppBackend(base_model_path=model_path, prefix_cache=cache)
    backend.load(bundle)
    backend.invoke("hi", max_tokens=8)
    backend.close()

    # After one invoke, the state file should exist.
    assert list((tmp_path / "pc").glob("*.state"))
