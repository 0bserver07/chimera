"""Real end-to-end: compile a tiny function + invoke it with real transformers.

No mocks. No skips. Uses the smallest practical base model (sshleifer/tiny-gpt2,
~20MB). Fine-tunes a real LoRA adapter on 3 examples, saves it as .chi, loads
it through TransformersBackend, calls invoke(), prints what comes out.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

# Isolate state in a tempdir so this doesn't pollute real ~/.chimera
os.environ["CHIMERA_FS_HOME"] = tempfile.mkdtemp(prefix="chimera-real-e2e-")

from chimera.function_synthesis import FunctionSpec
from chimera.function_synthesis.backends.transformers import TransformersBackend
from chimera.function_synthesis.compilers.local import LocalCompiler
from chimera.function_synthesis.registry import ProgramRegistry
from chimera.function_synthesis.runtime import CompiledFunction


BASE_MODEL = os.environ.get("CHIMERA_FS_LIVE_COMPILER_MODEL", "sshleifer/tiny-gpt2")


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


def main() -> None:
    section(f"1. Setup (base model = {BASE_MODEL})")
    print(f"  CHIMERA_FS_HOME = {os.environ['CHIMERA_FS_HOME']}")

    section("2. Build FunctionSpec")
    spec = FunctionSpec(
        name="yes-no",
        description="Answer yes or no based on the question.",
        examples=[
            {"input": "Is water wet?", "output": "yes"},
            {"input": "Do cats bark?", "output": "no"},
            {"input": "Is fire hot?", "output": "yes"},
            {"input": "Can fish fly?", "output": "no"},
        ],
    )
    print(f"  name: {spec.name}")
    print(f"  examples: {len(spec.examples)}")

    section("3. LocalCompiler.compile() — real PEFT LoRA fine-tune")
    compiler = LocalCompiler(
        base_model_name_or_path=BASE_MODEL,
        num_train_epochs=1,
        lora_r=2,
        lora_alpha=4,
    )
    t0 = time.time()
    bundle = compiler.compile(spec)
    t1 = time.time()
    print(f"  compiled in {t1 - t0:.1f}s")
    print(f"  adapter_format: {bundle.adapter_format}")
    print(f"  adapter_peft_files: {len(bundle.adapter_peft_files)} files")
    for name in sorted(bundle.adapter_peft_files.keys())[:10]:
        size = len(bundle.adapter_peft_files[name])
        print(f"    {name:40s} {size:>10d} bytes")
    print(f"  metadata: {bundle.metadata}")

    section("4. Install into ProgramRegistry")
    registry = ProgramRegistry.default()
    slug = registry.install(spec=spec, bundle=bundle)
    entry = registry.resolve(slug)
    print(f"  slug: {slug}")
    print(f"  bundle_path: {entry.bundle_path}")
    print(f"  size on disk: {entry.bundle_path.stat().st_size} bytes")

    section("5. Load via TransformersBackend — real model, real adapter")
    backend = TransformersBackend(BASE_MODEL, device="cpu")
    t0 = time.time()
    fn = CompiledFunction.from_path(entry.bundle_path, backend=backend)
    t1 = time.time()
    print(f"  loaded in {t1 - t0:.1f}s")

    section("6. invoke() — real inference")
    for question in ["Is the sky blue?", "Can dogs read?", "Is 2+2=4?"]:
        t0 = time.time()
        answer = fn(question, max_tokens=16)
        t1 = time.time()
        print(f"  [{t1 - t0:.2f}s] fn({question!r}) -> {answer!r}")

    section("7. stream() — real token streaming")
    print("  fn.stream('Is ice cold?'):", end="")
    for chunk in backend.stream("Is ice cold?", max_tokens=12):
        print(f" {chunk!r}", end="", flush=True)
    print()

    section("8. close")
    fn.close()
    shutil.rmtree(os.environ["CHIMERA_FS_HOME"], ignore_errors=True)
    print(f"  cleaned up {os.environ['CHIMERA_FS_HOME']}")

    section("DONE — all real, no mocks")


if __name__ == "__main__":
    main()
