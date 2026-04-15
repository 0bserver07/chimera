"""llama.cpp runtime backend (optional dependency: llama-cpp-python)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import RuntimeBackend


class LlamaCppBackend(RuntimeBackend):
    """Runs compiled functions via ``llama-cpp-python``.

    The base GGUF model is loaded once; each :meth:`load` swaps the LoRA
    adapter carried inside the bundle.

    Args:
        base_model_path: Path to the base GGUF model file.
        n_ctx: Context window size.
        n_threads: CPU threads (None = library default).
    """

    def __init__(
        self,
        *,
        base_model_path: str | Path,
        n_ctx: int = 2048,
        n_threads: int | None = None,
    ) -> None:
        self._base_model_path = Path(base_model_path)
        self._n_ctx = n_ctx
        self._n_threads = n_threads
        self._llm = None
        self._bundle: ChiBundle | None = None
        self._adapter_tmp: Path | None = None

    def load(self, bundle: ChiBundle) -> None:
        try:
            import llama_cpp  # type: ignore[import-not-found]
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

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        if self._llm is None or self._bundle is None:
            raise RuntimeError("backend not loaded; call load() first")
        prompts = self._bundle.prompts
        user_msg = prompts.get("user_template", "{input}").format(input=user_input)
        messages = [
            {"role": "system", "content": prompts.get("system", "")},
            {"role": "user", "content": user_msg},
        ]
        result = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            stop=prompts.get("stop") or None,
        )
        return result["choices"][0]["message"]["content"]

    def close(self) -> None:
        self._llm = None
        self._bundle = None
        if self._adapter_tmp is not None and self._adapter_tmp.exists():
            try:
                self._adapter_tmp.unlink()
            except OSError:
                pass
        self._adapter_tmp = None
