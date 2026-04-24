"""Real end-to-end with LlamaCppBackend — no mocks, real GGUF."""
from __future__ import annotations

import os
import tempfile
import time

os.environ["CHIMERA_FS_HOME"] = tempfile.mkdtemp(prefix="chimera-real-llamacpp-")

from chimera.function_synthesis import ChiBundle, FunctionSpec
from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend
from chimera.function_synthesis.runtime import CompiledFunction

GGUF = os.path.expanduser(
    "~/.cache/huggingface/hub/models--TheBloke--TinyLlama-1.1B-Chat-v1.0-GGUF/"
    "snapshots/52e7645ba7c309695bec7ac98f4f005b139cf465/"
    "tinyllama-1.1b-chat-v1.0.Q2_K.gguf"
)

print(f"GGUF size: {os.path.getsize(GGUF) / 1024**2:.1f} MB")

# Build a bundle with an empty adapter — llama.cpp can run the base model
# without a LoRA attached. We just need a valid .chi shape.
spec = FunctionSpec(name="chat", description="Have a short friendly chat.")
bundle = ChiBundle(
    spec=spec,
    adapter_bytes=b"",  # no adapter — base model only
    prompts={
        "system": "You are a helpful assistant. Answer in one sentence.",
        "user_template": "{input}",
        "stop": ["</s>", "\n\n"],
    },
)

# Save + load via disk to exercise the full path
tmp_chi = os.path.join(os.environ["CHIMERA_FS_HOME"], "chat.chi")
bundle.save(tmp_chi)
print(f".chi saved: {os.path.getsize(tmp_chi)} bytes")

print("\nLoading LlamaCppBackend...")
t0 = time.time()
backend = LlamaCppBackend(base_model_path=GGUF, n_ctx=512)
fn = CompiledFunction.from_path(tmp_chi, backend=backend)
print(f"  loaded in {time.time() - t0:.1f}s")

print("\ninvoke() — real llama.cpp inference:")
for q in ["Hello!", "What is the capital of France?", "Count to 3."]:
    t0 = time.time()
    out = fn(q, max_tokens=40)
    print(f"  [{time.time() - t0:.1f}s] {q!r} -> {out!r}")

print("\nstream() — real token streaming:")
chunks = []
for chunk in backend.stream("Tell me a joke.", max_tokens=30):
    chunks.append(chunk)
    print(f"  chunk: {chunk!r}")
print(f"total chunks: {len(chunks)}")

fn.close()
print("\nDONE — real llama.cpp, real GGUF, real inference")
