"""CompiledFunction: callable wrapper around a loaded ``.chi`` bundle.

The runtime is backend-agnostic: :class:`RuntimeBackend` is an ABC, and
``chimera.function_synthesis.backends.llama_cpp`` provides the reference
implementation using ``llama-cpp-python``.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.schema import SchemaError, validate


class RuntimeBackend(ABC):
    """Abstract inference backend for compiled functions.

    Implementations must be able to load a :class:`ChiBundle` and run
    inference against it.  Backends are responsible for loading the base
    model and attaching the adapter contained in the bundle.
    """

    @abstractmethod
    def load(self, bundle: ChiBundle) -> None:
        """Load the bundle into the backend, preparing it for inference."""

    @abstractmethod
    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        """Run the loaded function against ``user_input`` and return text."""

    def stream(self, user_input: str, *, max_tokens: int = 256) -> Iterator[str]:
        """Run the loaded function and yield text chunks as they are produced.

        The default implementation raises :class:`NotImplementedError` so
        existing backends that predate streaming continue to work unchanged.
        Backends that support incremental decoding should override this to
        return an iterator that yields non-empty text chunks.

        Args:
            user_input: The user-facing input to the compiled function.
            max_tokens: Maximum number of new tokens to produce.

        Yields:
            Non-empty text chunks in generation order.

        Raises:
            NotImplementedError: If the backend does not support streaming.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement stream(); "
            "call invoke() for a single-shot response."
        )

    @abstractmethod
    def close(self) -> None:
        """Release any resources held by the backend."""


class CompiledFunction:
    """A loaded ``.chi`` bundle you can call like a Python function.

    Args:
        bundle: The loaded :class:`ChiBundle`.
        backend: The :class:`RuntimeBackend` that will execute it.
        validate: If True, validate the user input against
            ``bundle.spec.input_schema`` before invoking and validate the
            backend output against ``bundle.spec.output_schema``
            afterwards. Opt-in to avoid breaking existing callers that
            rely on free-form strings.
    """

    def __init__(
        self,
        bundle: ChiBundle,
        backend: RuntimeBackend,
        *,
        validate: bool = False,
    ) -> None:
        self._bundle = bundle
        self._backend = backend
        self._validate = validate
        backend.load(bundle)

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        backend: RuntimeBackend,
        validate: bool = False,
    ) -> CompiledFunction:
        """Load a ``.chi`` bundle from ``path`` and bind it to ``backend``."""
        return cls(ChiBundle.load(path), backend, validate=validate)

    @property
    def name(self) -> str:
        return self._bundle.spec.name

    @property
    def spec(self) -> Any:
        return self._bundle.spec

    def __call__(self, user_input: str, *, max_tokens: int = 256) -> str:
        if self._validate:
            self._validate_input(user_input)
        output = self._backend.invoke(user_input, max_tokens=max_tokens)
        if self._validate:
            self._validate_output(output)
        return output

    def stream(self, user_input: str, *, max_tokens: int = 256) -> Iterator[str]:
        """Stream text chunks from the loaded function.

        Delegates to :meth:`RuntimeBackend.stream`, which raises
        :class:`NotImplementedError` for backends without streaming support.

        Args:
            user_input: The user-facing input to the compiled function.
            max_tokens: Maximum number of new tokens to produce.

        Yields:
            Non-empty text chunks in generation order.
        """
        return self._backend.stream(user_input, max_tokens=max_tokens)

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> CompiledFunction:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # --- internal validation helpers ----------------------------------
    def _validate_input(self, user_input: str) -> None:
        """Validate raw user input against ``spec.input_schema``.

        When the schema expects a string we pass the input through
        unchanged. For any other declared type we try to JSON-decode
        first, since the calling convention for a CompiledFunction is
        that non-string inputs arrive as JSON-encoded strings.

        Raises:
            SchemaError: If the input is malformed JSON for a
                non-string schema, or if it fails validation.
        """
        schema = self._bundle.spec.input_schema
        if schema is None:
            return
        decoded = _decode_for_schema(user_input, schema, where="input")
        validate(decoded, schema, path="input")

    def _validate_output(self, output: str) -> None:
        """Validate backend output against ``spec.output_schema``.

        Same decoding rule as :meth:`_validate_input`.

        Raises:
            SchemaError: If the output violates the declared schema.
        """
        schema = self._bundle.spec.output_schema
        if schema is None:
            return
        decoded = _decode_for_schema(output, schema, where="output")
        validate(decoded, schema, path="output")


def _decode_for_schema(raw: str, schema: dict[str, Any], *, where: str) -> Any:
    """Return ``raw`` coerced to the shape the schema expects.

    Strings pass through untouched; everything else is json-decoded
    first. A JSON decode error is raised as :class:`SchemaError` to
    keep the error surface narrow for callers.
    """
    declared = schema.get("type")
    # Both "string" and ["string", ...] with string first/only member
    # should skip JSON decoding. A list of types that contains both
    # "string" and non-string types is ambiguous — we optimistically
    # try JSON first, fall back to the raw string.
    if declared == "string":
        return raw
    if isinstance(declared, list) and declared == ["string"]:
        return raw
    if isinstance(declared, list) and "string" in declared:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaError(
            f"{where}: expected JSON matching schema type "
            f"{declared!r}, but decode failed: {exc}"
        ) from exc
