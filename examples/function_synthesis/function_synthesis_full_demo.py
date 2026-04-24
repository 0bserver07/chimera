#!/usr/bin/env python3
"""Full-lifecycle function-synthesis demo — no GGUF required.

Walks through the whole path a user would take:
    1. Write a FunctionSpec (name + description + examples)
    2. Compile with MockCompiler (no network, no training)
    3. Install into the local registry (~/.chimera/function_synthesis/)
    4. Peek inside the resulting .chi bundle (ZIP archive format)
    5. Load the bundle with a local StubBackend (no llama.cpp needed)
    6. Call the compiled function
    7. Expose it as a Chimera agent tool via CompiledFunctionTool

For the real-model path, swap StubBackend for LlamaCppBackend with a base
GGUF on disk.

Usage:
    python examples/function_synthesis_full_demo.py
"""
from __future__ import annotations

import json
import os
import tempfile
import zipfile

from chimera.function_synthesis import (
    ChiBundle,
    CompiledFunction,
    FunctionSpec,
    RuntimeBackend,
)
from chimera.function_synthesis.cache import CacheDirs
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.registry import ProgramRegistry
from chimera.tools.compiled_function_tool import CompiledFunctionTool


class StubBackend(RuntimeBackend):
    """Tiny local backend that ignores the adapter and echoes a template.

    Useful for demos + tests. For real inference, use LlamaCppBackend.
    """

    def __init__(self) -> None:
        self._bundle: ChiBundle | None = None

    def load(self, bundle: ChiBundle) -> None:
        self._bundle = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        if self._bundle is None:
            raise RuntimeError("backend not loaded")
        spec = self._bundle.spec
        # Deterministic stub: echo the spec description + user input.
        return f"[{spec.name}] {spec.description.strip()} :: {user_input}"

    def close(self) -> None:
        self._bundle = None


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main() -> None:
    # Use a scratch dir so the demo doesn't pollute real state
    with tempfile.TemporaryDirectory(prefix="chimera-fs-demo-") as tmp:
        os.environ["CHIMERA_FS_HOME"] = tmp

        section("1. Define the function spec")
        spec = FunctionSpec(
            name="sentiment",
            description="Classify text as 'positive' or 'negative'.",
            examples=[
                {"input": "I love this product", "output": "positive"},
                {"input": "Total waste of money", "output": "negative"},
            ],
        )
        print(f"  name:        {spec.name}")
        print(f"  description: {spec.description}")
        print(f"  examples:    {len(spec.examples)}")

        section("2. Compile with MockCompiler (offline, deterministic)")
        compiler = MockCompiler()
        bundle = compiler.compile(spec)
        print(f"  adapter:     {len(bundle.adapter_bytes)} bytes")
        print(f"  system:      {bundle.prompts['system']!r}")
        print(f"  metadata:    {bundle.metadata}")

        section("3. Install into local registry")
        registry = ProgramRegistry(CacheDirs.default())
        slug = registry.install(spec=spec, bundle=bundle)
        entry = registry.resolve(slug)
        print(f"  slug:        {slug}")
        print(f"  path:        {entry.bundle_path}")

        section("4. Inspect the .chi bundle (it's a ZIP)")
        with zipfile.ZipFile(entry.bundle_path) as zf:
            for info in zf.infolist():
                print(f"  {info.filename:20s}  {info.file_size:6d} bytes")
            print()
            manifest = json.loads(zf.read("manifest.json"))
            print(f"  manifest.schema_version: {manifest['schema_version']}")
            print(f"  manifest.adapter_format: {manifest['adapter_format']}")

        section("5. Load + call via StubBackend (no GGUF needed)")
        fn = CompiledFunction.from_path(entry.bundle_path, backend=StubBackend())
        output = fn("The performance is amazing")
        print("  fn('The performance is amazing')")
        print(f"  -> {output}")
        fn.close()

        section("6. Expose as an agent tool")
        fn = CompiledFunction.from_path(entry.bundle_path, backend=StubBackend())
        tool = CompiledFunctionTool(fn)
        print(f"  tool.name:         {tool.name}")
        print(f"  tool.description:  {tool.description[:60]}")
        # Call the tool the way an agent would
        result = tool.execute({"user_input": "boring and slow"}, env=None)
        print("  tool.execute({'user_input': 'boring and slow'})")
        print(f"  -> success={result.success}")
        print(f"     output={result.output}")
        fn.close()

        section("Done")
        print("This demo used StubBackend — a local echo. For real inference,")
        print("use LlamaCppBackend:")
        print("  pip install 'chimera[function_synthesis]'")
        print("  from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend")
        print("  backend = LlamaCppBackend(base_model_path='path/to/base.gguf')")
        print()
        print(f"Registry lived at: {tmp}")
        print("(destroyed on exit so your real ~/.chimera/ is untouched)")


if __name__ == "__main__":
    main()
