"""MockCompiler: produces bundles without network or training.

Use for unit tests, examples, and offline development.  The adapter bytes are
a deterministic placeholder --- the resulting bundle cannot be invoked against
a real base model, but it round-trips through save/load and works with any
backend that doesn't validate adapter contents.
"""
from __future__ import annotations

import hashlib

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.spec import FunctionSpec


class MockCompiler(CompilerBackend):
    """Compiler that emits a deterministic, non-functional bundle."""

    def compile(self, spec: FunctionSpec) -> ChiBundle:
        digest = hashlib.sha256(spec.to_json().encode()).digest()
        return ChiBundle(
            spec=spec,
            adapter_bytes=b"MOCK_ADAPTER:" + digest,
            prompts={
                "system": f"You are a compiled function. {spec.description}",
                "user_template": "{input}",
                "stop": [],
            },
            metadata={"compiler_backend": "mock", "deterministic": True},
        )
