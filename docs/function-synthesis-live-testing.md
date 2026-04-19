# Function Synthesis -- Live Smoke Tests

Most tests for `chimera.function_synthesis` mock the heavy dependencies
(`transformers`, `peft`, `llama-cpp-python`) so the suite stays fast and
hermetic.  A small number of **live** tests exercise the real stack against
tiny models, so you can verify end-to-end that the code paths survive a real
tokenizer, real LoRA adapter, and real inference.

These tests are marked with `pytest.mark.live`.  They are **skipped by
default** so CI never pulls gigabytes of weights, and they only activate when
the relevant environment variables are set.

## Running

```bash
# Only run the opt-in live tests:
uv run pytest -m live -v

# Scope to the function_synthesis suite:
uv run pytest -m live tests/function_synthesis/ -v

# Run a single live suite:
uv run pytest -m live tests/function_synthesis/test_live_transformers.py -v
```

Without any env vars set, all live tests print as `SKIPPED` rather than
failing, so it is safe to run `-m live` anywhere.

## Environment variables

| Variable | What | Example | Approx. download |
|---|---|---|---|
| `CHIMERA_FS_LIVE_TRANSFORMERS_MODEL` | HF id for `TransformersBackend` runtime | `sshleifer/tiny-gpt2` | ~100 MB |
| `CHIMERA_FS_LIVE_PEFT_ADAPTER` | Local dir with a saved PEFT adapter (optional) | `~/.cache/chimera/adapters/echo/` | 0 (local) |
| `CHIMERA_FS_LIVE_BASE_MODEL` | Path to a chat-tuned GGUF file | `~/models/qwen2-0.5b.Q4_0.gguf` | ~300 MB |
| `CHIMERA_FS_LIVE_GGUF_LORA` | Path to a GGUF LoRA adapter (optional) | `~/models/echo.gguf` | adapter-specific |
| `CHIMERA_FS_LIVE_COMPILER_MODEL` | HF id for `LocalCompiler` fine-tune | `Qwen/Qwen2-0.5B` | ~1 GB |

## What each suite covers

- `test_live_transformers.py` -- `TransformersBackend.load/invoke/stream/close`
  against a real HF base model.  Requires
  `CHIMERA_FS_LIVE_TRANSFORMERS_MODEL` and (for the full peft-load path)
  `CHIMERA_FS_LIVE_PEFT_ADAPTER`.
- `test_live_llama_cpp.py` -- `LlamaCppBackend` smoke test + optional
  `PrefixCache` round-trip verification (cache file written on first call,
  reloaded on second).  Requires `CHIMERA_FS_LIVE_BASE_MODEL`; the LoRA
  assertions additionally require `CHIMERA_FS_LIVE_GGUF_LORA`.
- `test_live_local_compiler.py` -- `LocalCompiler.compile()` runs a real
  1-epoch LoRA fine-tune with `lora_r=2` (keeps the run under ~5 minutes on
  CPU), saves the `.chi` bundle, reloads it for byte equality, and
  (xfail-gated) tries one inference through `TransformersBackend`.
- `test_live_e2e.py` -- two end-to-end chains: the original mock-compiler +
  llama.cpp flow, and a real `LocalCompiler` -> `ProgramRegistry` ->
  `TransformersBackend.invoke()` round-trip.

## Caveats

- **First run downloads weights.**  Tiny GPT-2 is ~100 MB, Qwen2-0.5B is
  ~1 GB.  Subsequent runs use the HuggingFace cache
  (`~/.cache/huggingface/`).
- **CPU-only is supported** but slow; `LocalCompiler` with the defaults in
  `test_live_local_compiler.py` (`num_train_epochs=1`, `lora_r=2`) should
  finish in a few minutes on a modern laptop.
- **Offline CI is fine.**  The tests check env vars before importing heavy
  deps, and import-level optional-dep checks are wrapped in
  `importlib.util.find_spec(...)` so a missing `transformers`/`peft`/
  `llama-cpp-python` yields a clean skip rather than an ImportError.
- **xfail for small-model quality.**  The "compile + invoke" round-trip in
  `test_live_local_compiler.py` is marked `@pytest.mark.xfail(strict=False)`:
  a 0.5B model fine-tuned on 3-5 examples for one epoch rarely produces a
  useful response, so we exercise the wiring and accept either outcome.
