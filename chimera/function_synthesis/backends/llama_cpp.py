"""llama.cpp runtime backend (optional dependency: llama-cpp-python)."""
from __future__ import annotations

import pickle
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import RuntimeBackend

if TYPE_CHECKING:
    from chimera.function_synthesis.prefix_cache import PrefixCache


class LlamaCppBackend(RuntimeBackend):
    """Runs compiled functions via ``llama-cpp-python``.

    The base GGUF model is loaded once; each :meth:`load` swaps the LoRA
    adapter carried inside the bundle.

    Args:
        base_model_path: Path to the base GGUF model file.
        n_ctx: Context window size.
        n_threads: CPU threads (None = library default).
        prefix_cache: Optional :class:`PrefixCache` for cold-start elimination.
            When provided and the underlying ``Llama`` instance exposes a
            working ``save_state``/``load_state`` pair, post-prefill state is
            pickled to disk so subsequent calls with the same (base_model,
            slug, system_prompt) triple skip the system-prompt prefill.  On
            any capability mismatch the cache is silently bypassed and
            generation proceeds normally.
    """

    def __init__(
        self,
        *,
        base_model_path: str | Path,
        n_ctx: int = 2048,
        n_threads: int | None = None,
        prefix_cache: PrefixCache | None = None,
    ) -> None:
        self._base_model_path = Path(base_model_path)
        self._n_ctx = n_ctx
        self._n_threads = n_threads
        self._prefix_cache = prefix_cache
        self._llm: Any = None
        self._bundle: ChiBundle | None = None
        self._adapter_tmp: Path | None = None
        self._base_model_sha: str | None = None
        # Populated on first load(); cached so we don't re-probe every invoke.
        self._state_api_ok: bool | None = None

    def load(self, bundle: ChiBundle) -> None:
        try:
            import llama_cpp  # type: ignore[import-not-found, unused-ignore]
        except ImportError as exc:
            raise ImportError(
                "LlamaCppBackend requires llama-cpp-python. "
                "Install with: pip install 'chimera[function_synthesis]'"
            ) from exc

        kwargs: dict[str, Any] = {
            "model_path": str(self._base_model_path),
            "n_ctx": self._n_ctx,
        }
        if self._n_threads is not None:
            kwargs["n_threads"] = self._n_threads

        # Only attach a LoRA if the bundle actually carries one. Passing an
        # empty path to lora_path makes llama.cpp try to read an empty file
        # as a LoRA and fail with "failed to read magic".
        if bundle.adapter_bytes:
            tmp = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
            tmp.write(bundle.adapter_bytes)
            tmp.close()
            self._adapter_tmp = Path(tmp.name)
            kwargs["lora_path"] = str(self._adapter_tmp)
        else:
            self._adapter_tmp = None

        self._llm = llama_cpp.Llama(**kwargs)
        self._bundle = bundle
        self._state_api_ok = None  # re-probe on this fresh Llama instance.

        # Compute base-model sha once per load; reused by PrefixCache key.
        if self._prefix_cache is not None and self._base_model_path.exists():
            from hashlib import sha256

            self._base_model_sha = sha256(
                self._base_model_path.read_bytes()
            ).hexdigest()

    def _has_state_api(self) -> bool:
        """Detect whether the Llama instance exposes a usable state API.

        llama-cpp-python's ``save_state``/``load_state`` signatures have shifted
        across versions.  We probe once and cache the answer so a single
        mismatch doesn't re-cost per-invoke.  Any exception during probing is
        treated as "cache unavailable" and the path is disabled silently —
        generation still succeeds, it just pays the prefill cost each call.
        """
        if self._state_api_ok is not None:
            return self._state_api_ok
        llm = self._llm
        if llm is None:
            self._state_api_ok = False
            return False
        if not (hasattr(llm, "save_state") and hasattr(llm, "load_state")):
            self._state_api_ok = False
            return False
        try:
            # Shape check: save_state() must return a non-None object and
            # load_state(that_object) must be callable without raising.
            probe = llm.save_state()
            if probe is None:
                self._state_api_ok = False
                return False
            llm.load_state(probe)
        except Exception:
            self._state_api_ok = False
            return False
        self._state_api_ok = True
        return True

    def _serialize_state(self, state: Any) -> bytes | None:
        """Pickle a ``LlamaState`` (or equivalent) to bytes for disk storage.

        Returns None on any serialization failure so the caller can bypass.
        """
        try:
            return pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            return None

    def _deserialize_state(self, blob: bytes) -> Any:
        """Unpickle bytes back into a ``LlamaState``-compatible object.

        Returns None on any deserialization failure so the caller can bypass.
        """
        try:
            return pickle.loads(blob)
        except Exception:
            return None

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        if self._llm is None or self._bundle is None:
            raise RuntimeError("backend not loaded; call load() first")
        prompts = self._bundle.prompts
        system = prompts.get("system", "")
        user_msg = prompts.get("user_template", "{input}").format(input=user_input)

        cache_key: str | None = None
        cache_usable = (
            self._prefix_cache is not None
            and self._prefix_cache.enabled
            and self._base_model_sha is not None
            and self._has_state_api()
        )
        if cache_usable:
            assert self._prefix_cache is not None  # for type checkers
            assert self._base_model_sha is not None
            slug = self._bundle.metadata.get("slug", self._bundle.spec.name)
            cache_key = self._prefix_cache.key(
                base_model_sha=self._base_model_sha,
                slug=slug,
                system_prompt=system,
            )
            cached_blob = self._prefix_cache.load(cache_key)
            if cached_blob is not None:
                state = self._deserialize_state(cached_blob)
                if state is not None:
                    try:
                        self._llm.load_state(state)
                    except Exception:
                        # Corrupt/incompatible state — disable for this call,
                        # fall through to a normal prefill.  Next invoke will
                        # attempt to refresh the cache entry.
                        pass

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]
        result = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            stop=prompts.get("stop") or None,
        )

        if cache_usable and cache_key is not None:
            assert self._prefix_cache is not None
            if self._prefix_cache.load(cache_key) is None:
                try:
                    raw = self._llm.save_state()
                except Exception:
                    raw = None
                if raw is not None:
                    blob = self._serialize_state(raw)
                    if blob is not None:
                        try:
                            self._prefix_cache.store(cache_key, blob)
                        except Exception:
                            # best-effort cache save; ignore write failures
                            pass

        return str(result["choices"][0]["message"]["content"])

    def stream(self, user_input: str, *, max_tokens: int = 256) -> Iterator[str]:
        """Stream text chunks from the llama.cpp chat completion API.

        Uses ``create_chat_completion(stream=True)`` and yields each non-empty
        delta content string. Prefix-cache save/load is skipped in streaming
        mode — the cache layer is an offline optimization for full-response
        invocations and mixing it with streaming produces inconsistent state.

        Args:
            user_input: The user-facing input to the compiled function.
            max_tokens: Maximum number of new tokens to produce.

        Yields:
            Non-empty text chunks in generation order.

        Raises:
            RuntimeError: If the backend is not loaded.
        """
        if self._llm is None or self._bundle is None:
            raise RuntimeError("backend not loaded; call load() first")
        prompts = self._bundle.prompts
        system = prompts.get("system", "")
        user_msg = prompts.get("user_template", "{input}").format(input=user_input)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]
        chunks = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            stop=prompts.get("stop") or None,
            stream=True,
        )
        for chunk in chunks:
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield str(content)

    def close(self) -> None:
        self._llm = None
        self._bundle = None
        self._base_model_sha = None
        self._state_api_ok = None
        if self._adapter_tmp is not None and self._adapter_tmp.exists():
            try:
                self._adapter_tmp.unlink()
            except OSError:
                pass
        self._adapter_tmp = None
