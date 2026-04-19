"""HuggingFace Transformers + PEFT runtime backend.

This backend loads a base causal-LM with :mod:`transformers` and attaches
a PEFT adapter extracted from a :class:`ChiBundle`. It supports both
one-shot :meth:`invoke` and token-level :meth:`stream` using
``TextIteratorStreamer``.

All heavy imports (``torch``, ``transformers``, ``peft``) happen inside
:meth:`load` so merely importing this module does not require the
optional dependency group ``function_synthesis_transformers`` to be
installed.
"""
from __future__ import annotations

import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import (
    ADAPTER_FORMAT_GGUF_LORA,
    ADAPTER_FORMAT_PEFT,
    ChiBundle,
)
from chimera.function_synthesis.runtime import RuntimeBackend

_MISSING_DEP_MSG = (
    "TransformersBackend requires the optional dependency group "
    "'function_synthesis_transformers'. Install with: "
    "pip install 'chimera-ai[function_synthesis_transformers]' "
    "(or: pip install transformers peft torch safetensors)."
)


class TransformersBackend(RuntimeBackend):
    """Runs compiled functions via HuggingFace ``transformers`` + ``peft``.

    The base model is loaded with :func:`transformers.AutoModelForCausalLM`
    and wrapped with :func:`peft.PeftModel.from_pretrained` to attach the
    adapter carried inside the ``.chi`` bundle. Concurrent ``generate()``
    calls are serialized with an internal :class:`threading.RLock` because
    the underlying HF models are not safe to call from multiple threads
    at once when they share a past-key-value cache.

    Args:
        base_model_name_or_path: HuggingFace model id (e.g. ``"Qwen/Qwen3-4B"``)
            or a local path to a directory containing a saved base model.
        device: Torch device string (``"cpu"``, ``"cuda"``, ``"mps"``, ...).
            When ``None``, autoselects CUDA if available, else CPU.
        dtype: Torch dtype (e.g. ``torch.float16`` or the string
            ``"float16"``). When ``None``, the library default is used.
    """

    def __init__(
        self,
        base_model_name_or_path: str,
        *,
        device: str | None = None,
        dtype: Any = None,
    ) -> None:
        self._base = base_model_name_or_path
        self._device = device
        self._dtype = dtype
        self._lock = threading.RLock()
        self._tokenizer: Any = None
        self._model: Any = None
        self._bundle: ChiBundle | None = None
        self._adapter_dir: Path | None = None

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _import_deps() -> tuple[Any, Any, Any, Any]:
        """Import the optional heavy dependencies with a friendly error.

        Returns:
            ``(torch, AutoModelForCausalLM, AutoTokenizer, PeftModel)``.

        Raises:
            ImportError: If any of ``torch``, ``transformers`` or ``peft``
                is not installed.
        """
        try:
            import torch  # type: ignore[import-not-found, unused-ignore]
            from transformers import (  # type: ignore[import-not-found, unused-ignore]
                AutoModelForCausalLM,
                AutoTokenizer,
            )
            from peft import PeftModel  # type: ignore[import-not-found, unused-ignore]
        except ImportError as exc:
            raise ImportError(_MISSING_DEP_MSG) from exc
        return torch, AutoModelForCausalLM, AutoTokenizer, PeftModel

    def _auto_device(self, torch_mod: Any) -> str:
        if self._device is not None:
            return self._device
        if getattr(torch_mod, "cuda", None) is not None and torch_mod.cuda.is_available():
            return "cuda"
        return "cpu"

    def _build_messages(self, user_input: str) -> list[dict[str, str]]:
        assert self._bundle is not None
        prompts = self._bundle.prompts
        system = prompts.get("system", "")
        user_msg = prompts.get("user_template", "{input}").format(input=user_input)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]

    def _render_prompt(self, messages: list[dict[str, str]]) -> str:
        tokenizer = self._tokenizer
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                return str(
                    tokenizer.apply_chat_template(
                        messages,
                        add_generation_prompt=True,
                        tokenize=False,
                    )
                )
            except Exception:
                # Fall through to manual rendering if the template is missing.
                pass
        system = messages[0].get("content", "") if messages else ""
        user = messages[1].get("content", "") if len(messages) > 1 else ""
        return f"{system}\n\n{user}".strip()

    def _extract_peft_adapter(self, bundle: ChiBundle) -> Path:
        """Materialize ``bundle.peft_files`` into a fresh temp directory.

        Args:
            bundle: The bundle whose ``peft_files`` should be written to disk.

        Returns:
            Path to the temp directory holding the PEFT adapter.
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="chimera-peft-"))
        for rel_name, blob in bundle.peft_files.items():
            out = tmp_dir / rel_name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
        return tmp_dir

    # ------------------------------------------------------------------
    # RuntimeBackend interface
    # ------------------------------------------------------------------
    def load(self, bundle: ChiBundle) -> None:
        """Load the PEFT adapter carried by ``bundle`` onto the base model.

        Args:
            bundle: A :class:`ChiBundle` with ``adapter_format == "peft"``.

        Raises:
            ImportError: If the optional dependency group is not installed.
            NotImplementedError: If the bundle uses ``adapter_format='gguf-lora'``
                (use :class:`LlamaCppBackend` for GGUF adapters).
            ValueError: If the bundle declares an unknown adapter format.
        """
        if bundle.adapter_format == ADAPTER_FORMAT_GGUF_LORA:
            raise NotImplementedError(
                "TransformersBackend cannot load 'gguf-lora' bundles; "
                "use LlamaCppBackend for gguf adapters. "
                "Rebuild the bundle with adapter_format='peft' to use "
                "TransformersBackend."
            )
        if bundle.adapter_format != ADAPTER_FORMAT_PEFT:
            raise ValueError(
                f"TransformersBackend: unknown adapter_format "
                f"{bundle.adapter_format!r}; expected 'peft'."
            )

        torch_mod, auto_model_cls, auto_tok_cls, peft_model_cls = self._import_deps()

        self._adapter_dir = self._extract_peft_adapter(bundle)
        device = self._auto_device(torch_mod)

        tokenizer = auto_tok_cls.from_pretrained(self._base)
        if getattr(tokenizer, "pad_token_id", None) is None:
            eos_id = getattr(tokenizer, "eos_token_id", None)
            if eos_id is not None:
                tokenizer.pad_token_id = eos_id

        model_kwargs: dict[str, Any] = {}
        if self._dtype is not None:
            model_kwargs["torch_dtype"] = self._dtype
        base_model = auto_model_cls.from_pretrained(self._base, **model_kwargs)
        model = peft_model_cls.from_pretrained(base_model, str(self._adapter_dir))

        if hasattr(model, "to"):
            try:
                model = model.to(device)
            except Exception:
                # Some fakes / sharded models don't support .to() — continue.
                pass
        if hasattr(model, "eval"):
            try:
                model.eval()
            except Exception:
                pass

        self._tokenizer = tokenizer
        self._model = model
        self._bundle = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        """Run the loaded function and return the full decoded output.

        Args:
            user_input: The user-facing input to the compiled function.
            max_tokens: Maximum number of new tokens to produce.

        Returns:
            Decoded output string with special tokens stripped.

        Raises:
            RuntimeError: If :meth:`load` has not been called.
        """
        if self._model is None or self._tokenizer is None or self._bundle is None:
            raise RuntimeError("backend not loaded; call load() first")

        messages = self._build_messages(user_input)
        rendered = self._render_prompt(messages)
        tokenizer = self._tokenizer
        model = self._model

        enc = tokenizer(rendered, return_tensors="pt")
        model_device = getattr(model, "device", None)
        if model_device is not None and hasattr(enc, "to"):
            try:
                enc = enc.to(model_device)
            except Exception:
                pass
        input_ids = enc["input_ids"]
        attention_mask = enc.get("attention_mask")
        prompt_len = int(input_ids.shape[-1])

        gen_kwargs: dict[str, Any] = {
            "input_ids": input_ids,
            "max_new_tokens": max_tokens,
            "do_sample": False,
            "pad_token_id": (
                getattr(tokenizer, "pad_token_id", None)
                or getattr(tokenizer, "eos_token_id", None)
            ),
        }
        if attention_mask is not None:
            gen_kwargs["attention_mask"] = attention_mask

        with self._lock:
            output = model.generate(**gen_kwargs)

        # model.generate returns [batch, seq] token ids including the prompt.
        try:
            new_tokens = output[0][prompt_len:]
        except (TypeError, IndexError):
            # If a fake returned a bare list of ids, fall back to slicing it.
            new_tokens = output[prompt_len:]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        return str(text).strip()

    def stream(self, user_input: str, *, max_tokens: int = 256) -> Iterator[str]:
        """Yield text chunks using ``transformers.TextIteratorStreamer``.

        A background thread runs ``model.generate`` while the main thread
        iterates over the streamer to produce chunks.

        Args:
            user_input: The user-facing input to the compiled function.
            max_tokens: Maximum number of new tokens to produce.

        Yields:
            Non-empty text chunks in generation order.

        Raises:
            RuntimeError: If :meth:`load` has not been called.
            ImportError: If the optional dependency group is not installed.
        """
        if self._model is None or self._tokenizer is None or self._bundle is None:
            raise RuntimeError("backend not loaded; call load() first")
        try:
            from transformers import (  # type: ignore[import-not-found, unused-ignore]
                TextIteratorStreamer,
            )
        except ImportError as exc:  # pragma: no cover — handled by _import_deps
            raise ImportError(_MISSING_DEP_MSG) from exc

        messages = self._build_messages(user_input)
        rendered = self._render_prompt(messages)
        tokenizer = self._tokenizer
        model = self._model

        enc = tokenizer(rendered, return_tensors="pt")
        model_device = getattr(model, "device", None)
        if model_device is not None and hasattr(enc, "to"):
            try:
                enc = enc.to(model_device)
            except Exception:
                pass
        input_ids = enc["input_ids"]
        attention_mask = enc.get("attention_mask")

        streamer = TextIteratorStreamer(
            tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
        )
        gen_kwargs: dict[str, Any] = {
            "input_ids": input_ids,
            "max_new_tokens": max_tokens,
            "do_sample": False,
            "pad_token_id": (
                getattr(tokenizer, "pad_token_id", None)
                or getattr(tokenizer, "eos_token_id", None)
            ),
            "streamer": streamer,
        }
        if attention_mask is not None:
            gen_kwargs["attention_mask"] = attention_mask

        lock = self._lock

        def _run() -> None:
            with lock:
                model.generate(**gen_kwargs)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            for chunk in streamer:
                if chunk:
                    yield str(chunk)
        finally:
            thread.join()

    def close(self) -> None:
        """Release the model, tokenizer and extracted adapter files.

        Also clears the CUDA cache when a GPU was in use.
        """
        model = self._model
        self._model = None
        self._tokenizer = None
        self._bundle = None
        del model

        adapter_dir = self._adapter_dir
        self._adapter_dir = None
        if adapter_dir is not None and adapter_dir.exists():
            for p in sorted(adapter_dir.rglob("*"), reverse=True):
                try:
                    if p.is_file() or p.is_symlink():
                        p.unlink()
                    elif p.is_dir():
                        p.rmdir()
                except OSError:
                    pass
            try:
                adapter_dir.rmdir()
            except OSError:
                pass

        try:
            import torch  # type: ignore[import-not-found, unused-ignore]
        except ImportError:
            return
        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
            except Exception:  # pragma: no cover — best-effort cleanup
                pass
