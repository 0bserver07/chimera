"""LocalCompiler: fine-tune a PEFT LoRA adapter in-process from a FunctionSpec.

The compiler takes a :class:`FunctionSpec` whose ``examples`` carry
input/output demonstrations, fine-tunes a small LoRA adapter on top of a
HuggingFace base model using the ``peft`` + ``transformers`` + ``datasets``
stack, saves the adapter with ``PeftModel.save_pretrained(...)``, and packs
the resulting directory into a ``.chi`` bundle with
``manifest.adapter_format == "peft"``.

Optional dependencies (installed via ``pip install 'chimera[function_synthesis_compile]'``):

- transformers
- peft
- torch
- datasets

If any is missing, :meth:`LocalCompiler.compile` raises :class:`ImportError`
with a hint.  The compiler never phones home and never downloads anything
itself --- any model pulls are done by the underlying HF libraries using
whatever cache/credentials the caller has configured.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import ADAPTER_FORMAT_PEFT, ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend, CompilerError
from chimera.function_synthesis.spec import FunctionSpec

_INSTALL_HINT = (
    "LocalCompiler requires transformers, peft, torch, and datasets. "
    "Install with: pip install 'chimera[function_synthesis_compile]'"
)


class LocalCompiler(CompilerBackend):
    """Compile a :class:`FunctionSpec` by fine-tuning a PEFT LoRA adapter locally.

    The adapter is trained on the ``spec.examples`` (rendered as
    ``prompt`` → ``completion`` rows) and packaged into a PEFT-format
    :class:`ChiBundle`.

    Args:
        base_model_name_or_path: Any identifier accepted by
            ``AutoModelForCausalLM.from_pretrained``.
        num_train_epochs: Epochs for the underlying ``Trainer``.
        learning_rate: Learning rate for the underlying ``Trainer``.
        lora_r: LoRA rank.
        lora_alpha: LoRA alpha.
        output_dir: Optional directory for ``TrainingArguments.output_dir``;
            a fresh tempdir is used when ``None``.
    """

    def __init__(
        self,
        base_model_name_or_path: str,
        *,
        num_train_epochs: int = 3,
        learning_rate: float = 1e-4,
        lora_r: int = 8,
        lora_alpha: int = 16,
        output_dir: str | Path | None = None,
    ) -> None:
        self._base_model = base_model_name_or_path
        self._num_train_epochs = num_train_epochs
        self._learning_rate = learning_rate
        self._lora_r = lora_r
        self._lora_alpha = lora_alpha
        self._output_dir = output_dir

    def compile(self, spec: FunctionSpec) -> ChiBundle:
        """Fine-tune a LoRA adapter on ``spec.examples`` and return the bundle.

        Args:
            spec: The function specification.  ``spec.examples`` must be
                non-empty; each example is rendered as
                ``{"prompt": user_template.format(input=...), "completion": ...}``.

        Returns:
            A :class:`ChiBundle` with ``adapter_format == "peft"``.

        Raises:
            CompilerError: If ``spec.examples`` is empty.
            ImportError: If any of transformers/peft/torch/datasets is missing.
        """
        if not spec.examples:
            raise CompilerError(
                "LocalCompiler requires spec.examples to fine-tune on"
            )

        # Import optional deps lazily so chimera's zero-dep core stays clean.
        deps = _import_deps()

        prompts = _default_prompts(spec)
        user_template: str = prompts["user_template"]

        rows = [
            {
                "prompt": user_template.format(input=_example_field(ex, "input")),
                "completion": _example_field(ex, "output"),
            }
            for ex in spec.examples
        ]
        dataset = deps["Dataset"].from_list(rows)

        tokenizer = deps["AutoTokenizer"].from_pretrained(self._base_model)
        if getattr(tokenizer, "pad_token", None) is None:
            tokenizer.pad_token = getattr(tokenizer, "eos_token", None)

        def _tokenize(row: dict[str, str]) -> dict[str, Any]:
            text = row["prompt"] + row["completion"]
            enc = tokenizer(
                text,
                truncation=True,
                padding="max_length",
                max_length=256,
            )
            out: dict[str, Any] = dict(enc)
            out["labels"] = list(out["input_ids"])
            return out

        tokenized = dataset.map(_tokenize)

        model = deps["AutoModelForCausalLM"].from_pretrained(self._base_model)

        try:
            lora_config = deps["LoraConfig"](
                r=self._lora_r,
                lora_alpha=self._lora_alpha,
                target_modules="all-linear",
                bias="none",
                task_type="CAUSAL_LM",
            )
        except (TypeError, ValueError):
            # Older peft releases reject ``target_modules="all-linear"``.
            lora_config = deps["LoraConfig"](
                r=self._lora_r,
                lora_alpha=self._lora_alpha,
                target_modules=["q_proj", "v_proj"],
                bias="none",
                task_type="CAUSAL_LM",
            )

        peft_model = deps["get_peft_model"](model, lora_config)

        with tempfile.TemporaryDirectory() as train_tmp:
            out_dir = str(self._output_dir) if self._output_dir else train_tmp
            training_args = deps["TrainingArguments"](
                output_dir=out_dir,
                num_train_epochs=self._num_train_epochs,
                learning_rate=self._learning_rate,
                per_device_train_batch_size=1,
                logging_strategy="no",
                save_strategy="no",
                report_to=[],
                disable_tqdm=True,
            )
            trainer = deps["Trainer"](
                model=peft_model,
                args=training_args,
                train_dataset=tokenized,
            )
            trainer.train()

            # Serialise adapter to a fresh tempdir regardless of training dir.
            with tempfile.TemporaryDirectory() as adapter_tmp:
                peft_model.save_pretrained(adapter_tmp)
                adapter_files = _read_directory(Path(adapter_tmp))

        metadata = {
            "compiler_backend": "local",
            "base_model": self._base_model,
            "num_examples": len(spec.examples),
            "lora_r": self._lora_r,
            "lora_alpha": self._lora_alpha,
            "num_train_epochs": self._num_train_epochs,
            "learning_rate": self._learning_rate,
        }

        return ChiBundle(
            spec=spec,
            prompts=prompts,
            metadata=metadata,
            base_model=self._base_model,
            adapter_format=ADAPTER_FORMAT_PEFT,
            adapter_peft_files=adapter_files,
        )


def _default_prompts(spec: FunctionSpec) -> dict[str, Any]:
    """Build a minimal prompt dict for the bundle.

    Args:
        spec: The source function specification.

    Returns:
        Dict with ``system``, ``user_template``, and ``stop`` keys.
    """
    return {
        "system": f"You are a compiled function. {spec.description}",
        "user_template": "{input}",
        "stop": [],
    }


def _example_field(example: Any, key: str) -> str:
    """Extract ``key`` from an example, supporting dict and attribute access.

    :class:`FunctionSpec.examples` is declared as ``list[dict[str, str]]`` but
    we accept attribute-style examples as well so callers can pass lightweight
    dataclasses without reshaping their data.

    Args:
        example: An example item from ``spec.examples``.
        key: Field name (``"input"`` or ``"output"``).

    Returns:
        The field value, coerced to ``str``.

    Raises:
        CompilerError: If the example lacks the requested field.
    """
    if isinstance(example, dict):
        if key not in example:
            raise CompilerError(f"example is missing required key {key!r}: {example!r}")
        return str(example[key])
    if hasattr(example, key):
        return str(getattr(example, key))
    raise CompilerError(f"example is missing required field {key!r}: {example!r}")


def _read_directory(root: Path) -> dict[str, bytes]:
    """Read every file under ``root`` into a ``{relname: bytes}`` mapping.

    Args:
        root: Directory whose contents should be slurped.

    Returns:
        Mapping from POSIX-style relative path to file bytes.
    """
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            files[rel] = path.read_bytes()
    return files


def _import_deps() -> dict[str, Any]:
    """Import the transformers/peft/torch/datasets stack, one at a time.

    Each dependency is imported separately so a missing one yields an
    ``ImportError`` naming the specific package plus the install hint.

    Returns:
        Mapping of symbol names to the imported objects used by the compiler.

    Raises:
        ImportError: If any of the four required packages is missing.
    """
    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise ImportError(f"torch missing: {_INSTALL_HINT}") from exc
    try:
        from transformers import (  # type: ignore[import-not-found]
            AutoModelForCausalLM,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        raise ImportError(f"transformers missing: {_INSTALL_HINT}") from exc
    try:
        from peft import LoraConfig, get_peft_model  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(f"peft missing: {_INSTALL_HINT}") from exc
    try:
        from datasets import Dataset  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(f"datasets missing: {_INSTALL_HINT}") from exc

    return {
        "AutoModelForCausalLM": AutoModelForCausalLM,
        "AutoTokenizer": AutoTokenizer,
        "Trainer": Trainer,
        "TrainingArguments": TrainingArguments,
        "LoraConfig": LoraConfig,
        "get_peft_model": get_peft_model,
        "Dataset": Dataset,
    }
