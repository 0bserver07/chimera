"""ONNX Runtime backend for compiled functions.

This backend runs ``.chi`` bundles on :mod:`onnxruntime` via
:mod:`optimum.onnxruntime`. It is designed for edge / mobile deployments
where a full ``torch + transformers`` install is too heavy.

The backend supports three adapter formats:

- ``onnx`` — adapter files were exported ahead of time and live under
  ``adapter_onnx/`` inside the bundle. Loading materializes them to a
  temp directory and calls ``ORTModelForCausalLM.from_pretrained``.
- ``peft`` — LoRA weights live under ``adapter_peft/``. The backend
  merges them into the base model (via :mod:`peft`'s
  ``merge_and_unload``) and exports the result to ONNX on first load.
  Expensive, but ergonomic for folks who only have a PEFT bundle.
- ``gguf-lora`` — not supported here; use
  :class:`chimera.function_synthesis.backends.llama_cpp.LlamaCppBackend`
  instead.

All heavy imports (``onnxruntime``, ``optimum``, ``transformers``,
``peft``, ``torch``) are deferred to :meth:`load`, so merely importing
this module with none of the optional deps installed succeeds.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import (
    ADAPTER_FORMAT_GGUF,
    ADAPTER_FORMAT_ONNX,
    ADAPTER_FORMAT_PEFT,
    ChiBundle,
)
from chimera.function_synthesis.runtime import RuntimeBackend

_MISSING_DEP_MSG = (
    "OnnxBackend requires the optional dependency group "
    "'function_synthesis_onnx'. Install with: "
    "pip install 'chimera-ai[function_synthesis_onnx]' "
    "(or: pip install onnxruntime 'optimum[onnxruntime]' transformers)."
)

_DEFAULT_CACHE_DIR = Path.home() / ".chimera" / "function_synthesis" / "onnx_cache"


class OnnxBackend(RuntimeBackend):
    """Run compiled functions via ONNX Runtime.

    Loads a base causal-LM ONNX model (or exports + caches one from a HF
    id) and attaches an adapter stored inside the ``.chi`` bundle. Useful
    for edge / mobile deployment where torch + transformers is too heavy.

    Concurrent :meth:`invoke` / :meth:`stream` calls are serialized with
    an internal :class:`threading.RLock`, matching
    :class:`TransformersBackend`: the underlying ORT session is safe to
    call sequentially, but mixing streams from multiple threads produces
    interleaved output.

    Args:
        base_model: Either a filesystem path to an ONNX model directory,
            or a HuggingFace Hub model id. If given an HF id and the
            model hasn't been exported to ONNX yet, the first
            :meth:`load` call uses ``optimum.exporters.onnx`` (through
            ``ORTModelForCausalLM.from_pretrained(..., export=True)``) to
            export + cache it locally.
        providers: ONNX execution providers in priority order. Defaults
            to ``["CPUExecutionProvider"]``. Apple Silicon users can
            pass ``["CoreMLExecutionProvider", "CPUExecutionProvider"]``.
        cache_dir: Where to cache exported ONNX models. Defaults to
            ``~/.chimera/function_synthesis/onnx_cache/``.
    """

    def __init__(
        self,
        *,
        base_model: str | Path,
        providers: list[str] | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._base_model = base_model
        self._providers = list(providers) if providers is not None else ["CPUExecutionProvider"]
        self._cache_dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_CACHE_DIR
        self._lock = threading.RLock()
        self._tokenizer: Any = None
        self._model: Any = None
        self._bundle: ChiBundle | None = None
        self._adapter_dir: Path | None = None
        self._exported_dir: Path | None = None

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _import_core_deps() -> tuple[Any, Any]:
        """Import ``ORTModelForCausalLM`` and ``AutoTokenizer``.

        Returns:
            ``(ORTModelForCausalLM, AutoTokenizer)``.

        Raises:
            ImportError: If :mod:`optimum`, :mod:`onnxruntime` or
                :mod:`transformers` is not installed.
        """
        try:
            from optimum.onnxruntime import (  # type: ignore[import-not-found, unused-ignore]
                ORTModelForCausalLM,
            )
            from transformers import (  # type: ignore[import-not-found, unused-ignore]
                AutoTokenizer,
            )
        except ImportError as exc:
            raise ImportError(_MISSING_DEP_MSG) from exc
        return ORTModelForCausalLM, AutoTokenizer

    @staticmethod
    def _import_peft_deps() -> tuple[Any, Any, Any]:
        """Import the heavy deps needed to merge a PEFT adapter + re-export.

        Returns:
            ``(AutoModelForCausalLM, AutoTokenizer, PeftModel)``.

        Raises:
            ImportError: If :mod:`transformers`, :mod:`peft`, or
                :mod:`torch` is not installed.
        """
        try:
            from peft import PeftModel  # type: ignore[import-not-found, unused-ignore]
            from transformers import (  # type: ignore[import-not-found, unused-ignore]
                AutoModelForCausalLM,
                AutoTokenizer,
            )
        except ImportError as exc:
            raise ImportError(
                "OnnxBackend.load(peft_bundle) additionally requires "
                "transformers + peft + torch. Install with: "
                "pip install 'chimera-ai[function_synthesis_onnx,"
                "function_synthesis_transformers]'."
            ) from exc
        return AutoModelForCausalLM, AutoTokenizer, PeftModel

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

    def _extract_onnx_adapter(self, bundle: ChiBundle) -> Path:
        """Materialize ``bundle.adapter_onnx_files`` into a fresh temp dir.

        Args:
            bundle: Bundle whose ``adapter_onnx_files`` should be written
                to disk.

        Returns:
            Path to the temp directory holding the ONNX adapter.
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="chimera-onnx-"))
        for rel_name, blob in bundle.adapter_onnx_files.items():
            out = tmp_dir / rel_name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
        return tmp_dir

    def _extract_peft_adapter(self, bundle: ChiBundle) -> Path:
        """Materialize ``bundle.adapter_peft_files`` into a fresh temp dir.

        Args:
            bundle: Bundle whose ``adapter_peft_files`` should be written
                to disk.

        Returns:
            Path to the temp directory holding the PEFT adapter.
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="chimera-onnx-peft-"))
        for rel_name, blob in bundle.adapter_peft_files.items():
            out = tmp_dir / rel_name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
        return tmp_dir

    def _load_onnx_model(
        self, ort_cls: Any, model_dir: str | Path, *, export: bool
    ) -> Any:
        """Call ``ORTModelForCausalLM.from_pretrained`` with our providers.

        Args:
            ort_cls: The ``ORTModelForCausalLM`` class (or a stand-in for
                tests).
            model_dir: Directory or HF model id to load.
            export: Whether to pass ``export=True`` so optimum exports
                the base model to ONNX on first load.

        Returns:
            The loaded ORT model instance.
        """
        kwargs: dict[str, Any] = {
            "provider": self._providers[0],
            "providers": list(self._providers),
        }
        if export:
            kwargs["export"] = True
            kwargs["cache_dir"] = str(self._cache_dir)
        return ort_cls.from_pretrained(str(model_dir), **kwargs)

    def _merge_peft_and_export(self, bundle: ChiBundle) -> Path:
        """Merge a PEFT adapter into the base model and export to ONNX.

        Loads the base model with :mod:`transformers`, attaches the
        adapter with :mod:`peft`, calls ``merge_and_unload`` to fold
        the LoRA weights into the base weights, saves the merged model
        to a temp directory, and returns that directory. The caller is
        expected to pass the returned directory to
        ``ORTModelForCausalLM.from_pretrained(..., export=True)``.

        Args:
            bundle: The PEFT bundle to merge.

        Returns:
            Path to a directory containing the merged HF model.
        """
        auto_model_cls, auto_tok_cls, peft_model_cls = self._import_peft_deps()

        peft_dir = self._extract_peft_adapter(bundle)
        self._adapter_dir = peft_dir

        base = auto_model_cls.from_pretrained(str(self._base_model))
        wrapped = peft_model_cls.from_pretrained(base, str(peft_dir))
        merged = wrapped.merge_and_unload()

        # Also save the tokenizer so ORTModelForCausalLM can find it alongside
        # the merged weights during export.
        tok = auto_tok_cls.from_pretrained(str(self._base_model))

        out_dir = Path(tempfile.mkdtemp(prefix="chimera-onnx-merged-"))
        merged.save_pretrained(str(out_dir))
        tok.save_pretrained(str(out_dir))
        return out_dir

    # ------------------------------------------------------------------
    # RuntimeBackend interface
    # ------------------------------------------------------------------
    def load(self, bundle: ChiBundle) -> None:
        """Load ``bundle`` onto an ONNX Runtime session.

        Supports ``adapter_format`` in ``{"onnx", "peft"}``.

        For ``"onnx"``: extracts ``adapter_onnx_files`` into a tempdir
        and passes that directory to ``ORTModelForCausalLM.from_pretrained``.

        For ``"peft"``: merges the PEFT adapter into the base model via
        ``merge_and_unload``, saves the merged model to disk, then loads
        it through ``ORTModelForCausalLM.from_pretrained(..., export=True)``
        to produce the ONNX graph. Expensive but ergonomic.

        For ``"gguf-lora"``: raises :class:`NotImplementedError` pointing
        at :class:`LlamaCppBackend`.

        Args:
            bundle: The :class:`ChiBundle` to load.

        Raises:
            ImportError: If the optional dependency group is not installed.
            NotImplementedError: If the bundle uses the GGUF format.
            ValueError: If the bundle declares an unknown adapter format.
        """
        if bundle.adapter_format == ADAPTER_FORMAT_GGUF:
            raise NotImplementedError(
                "OnnxBackend cannot load 'gguf-lora' bundles; "
                "use LlamaCppBackend for gguf adapters. "
                "Rebuild the bundle with adapter_format='onnx' "
                "(or 'peft') to use OnnxBackend."
            )
        if bundle.adapter_format not in (ADAPTER_FORMAT_ONNX, ADAPTER_FORMAT_PEFT):
            raise ValueError(
                f"OnnxBackend: unknown adapter_format "
                f"{bundle.adapter_format!r}; expected 'onnx' or 'peft'."
            )

        ort_cls, auto_tok_cls = self._import_core_deps()
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        if bundle.adapter_format == ADAPTER_FORMAT_ONNX:
            self._adapter_dir = self._extract_onnx_adapter(bundle)
            try:
                tokenizer = auto_tok_cls.from_pretrained(str(self._adapter_dir))
            except Exception:
                # Adapter dir may not carry a tokenizer; fall back to base.
                tokenizer = auto_tok_cls.from_pretrained(str(self._base_model))
            model = self._load_onnx_model(ort_cls, self._adapter_dir, export=False)
        else:  # ADAPTER_FORMAT_PEFT
            self._exported_dir = self._merge_peft_and_export(bundle)
            tokenizer = auto_tok_cls.from_pretrained(str(self._exported_dir))
            model = self._load_onnx_model(ort_cls, self._exported_dir, export=True)

        if getattr(tokenizer, "pad_token_id", None) is None:
            eos_id = getattr(tokenizer, "eos_token_id", None)
            if eos_id is not None:
                tokenizer.pad_token_id = eos_id

        self._tokenizer = tokenizer
        self._model = model
        self._bundle = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        """Run the loaded function and return the full decoded output.

        Uses ``ORTModelForCausalLM.generate()`` under the hood.

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

        try:
            new_tokens = output[0][prompt_len:]
        except (TypeError, IndexError):
            new_tokens = output[prompt_len:]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        return str(text).strip()

    def stream(self, user_input: str, *, max_tokens: int = 256) -> Iterator[str]:
        """Yield text chunks using ``transformers.TextIteratorStreamer``.

        ``ORTModelForCausalLM`` accepts a ``streamer=`` kwarg the same
        way regular HF transformers models do, so a background thread
        runs ``generate`` while the main thread drains the streamer.

        Args:
            user_input: The user-facing input to the compiled function.
            max_tokens: Maximum number of new tokens to produce.

        Yields:
            Non-empty text chunks in generation order.

        Raises:
            RuntimeError: If :meth:`load` has not been called.
            ImportError: If :mod:`transformers` is not installed.
        """
        if self._model is None or self._tokenizer is None or self._bundle is None:
            raise RuntimeError("backend not loaded; call load() first")
        try:
            from transformers import (  # type: ignore[import-not-found, unused-ignore]
                TextIteratorStreamer,
            )
        except ImportError as exc:  # pragma: no cover — handled by _import_core_deps
            raise ImportError(_MISSING_DEP_MSG) from exc

        messages = self._build_messages(user_input)
        rendered = self._render_prompt(messages)
        tokenizer = self._tokenizer
        model = self._model

        enc = tokenizer(rendered, return_tensors="pt")
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
        """Release the ORT session and scrub extracted temp directories.

        Clears the model + tokenizer references, removes the adapter
        tempdir (if any) and the merged-export tempdir (if any). Best
        effort — filesystem errors during cleanup are swallowed.
        """
        self._model = None
        self._tokenizer = None
        self._bundle = None

        for attr in ("_adapter_dir", "_exported_dir"):
            d: Path | None = getattr(self, attr, None)
            setattr(self, attr, None)
            if d is None or not d.exists():
                continue
            try:
                shutil.rmtree(d)
            except OSError:
                # Best-effort cleanup; leave leftover dirs rather than crash.
                pass
