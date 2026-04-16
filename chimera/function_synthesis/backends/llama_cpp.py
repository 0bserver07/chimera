"""llama.cpp runtime backend (optional dependency: llama-cpp-python)."""
from __future__ import annotations

import tempfile
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
            When provided and the underlying ``Llama`` instance supports
            ``save_state``/``load_state``, post-prefill state is cached to disk
            so subsequent calls skip the system-prompt prefill.
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

    def load(self, bundle: ChiBundle) -> None:
        try:
            import llama_cpp  # type: ignore[import-not-found, unused-ignore]
        except ImportError as exc:
            raise ImportError(
                "LlamaCppBackend requires llama-cpp-python. "
                "Install with: pip install 'chimera[function_synthesis]'"
            ) from exc

        # llama.cpp reads the adapter from disk; extract it to a tempfile.
        tmp = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        tmp.write(bundle.adapter_bytes)
        tmp.close()
        self._adapter_tmp = Path(tmp.name)

        kwargs: dict[str, Any] = {
            "model_path": str(self._base_model_path),
            "lora_path": str(self._adapter_tmp),
            "n_ctx": self._n_ctx,
        }
        if self._n_threads is not None:
            kwargs["n_threads"] = self._n_threads

        self._llm = llama_cpp.Llama(**kwargs)
        self._bundle = bundle

        # Compute base-model sha once per load; reused by PrefixCache key.
        if self._prefix_cache is not None and self._base_model_path.exists():
            from hashlib import sha256

            self._base_model_sha = sha256(
                self._base_model_path.read_bytes()
            ).hexdigest()

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        if self._llm is None or self._bundle is None:
            raise RuntimeError("backend not loaded; call load() first")
        prompts = self._bundle.prompts
        system = prompts.get("system", "")
        user_msg = prompts.get("user_template", "{input}").format(input=user_input)

        cache_key: str | None = None
        if (
            self._prefix_cache is not None
            and self._base_model_sha is not None
            and hasattr(self._llm, "save_state")
            and hasattr(self._llm, "load_state")
        ):
            slug = self._bundle.metadata.get("slug", self._bundle.spec.name)
            cache_key = self._prefix_cache.key(
                base_model_sha=self._base_model_sha,
                slug=slug,
                system_prompt=system,
            )
            cached = self._prefix_cache.load(cache_key)
            if cached is not None:
                self._llm.load_state(cached)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]
        result = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            stop=prompts.get("stop") or None,
        )

        if cache_key is not None and self._prefix_cache is not None:
            if self._prefix_cache.load(cache_key) is None:
                try:
                    self._prefix_cache.store(cache_key, self._llm.save_state())
                except Exception:  # pragma: no cover — best-effort cache save
                    pass

        return str(result["choices"][0]["message"]["content"])

    def close(self) -> None:
        self._llm = None
        self._bundle = None
        self._base_model_sha = None
        if self._adapter_tmp is not None and self._adapter_tmp.exists():
            try:
                self._adapter_tmp.unlink()
            except OSError:
                pass
        self._adapter_tmp = None
