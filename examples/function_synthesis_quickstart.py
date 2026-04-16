"""End-to-end quickstart for chimera.function_synthesis.

Runs fully offline: uses MockCompiler, no network, no GGUF required.
For the real-model path, see docs/function-synthesis.md.
"""
from __future__ import annotations

from chimera.function_synthesis import FunctionSpec
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.registry import ProgramRegistry


def main() -> None:
    spec = FunctionSpec(
        name="greet",
        description="Reply with a one-sentence friendly greeting.",
    )

    print(f"[1/3] Compiling spec {spec.name!r}...")
    bundle = MockCompiler().compile(spec)
    print(f"      adapter_bytes: {len(bundle.adapter_bytes)} bytes")

    print("[2/3] Installing into local registry...")
    registry = ProgramRegistry.default()
    slug = registry.install(spec=spec, bundle=bundle)
    print(f"      slug: {slug}")

    print("[3/3] Resolving back from the registry...")
    entry = registry.resolve(slug)
    print(f"      bundle_path: {entry.bundle_path}")
    print(f"      spec.description: {entry.spec.description}")

    print(
        "\nDone. To invoke this program against a real base model, see "
        "`chimera fs run --base-model ...` or docs/function-synthesis.md."
    )


if __name__ == "__main__":
    main()
