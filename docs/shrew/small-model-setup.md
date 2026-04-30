---
title: Shrew small-model setup
description: Build llama.cpp, download a Qwen MoE GGUF, and serve it with the experts-in-RAM, attention-on-GPU trick that lets a 22 GB model run on an 8 GB consumer laptop GPU.
---

# Small-model setup

Shrew is small-model-first. The default model is `qwen3.6-35b-a3b`,
a Mixture-of-Experts checkpoint that is **22 GB on disk** at Q4
quantisation but uses only **~3B active parameters per token**.
The trick that makes this practical on consumer hardware is to keep
the experts in system RAM and only the attention layers plus KV
cache on the GPU. This page walks the entire setup end to end:
build llama.cpp, download a GGUF, and serve it with the right
flags.

If you already have a llama.cpp HTTP server on `127.0.0.1:8888`,
skip to [`quickstart.md`](quickstart.md). If you'd rather use Ollama
(simpler but slower for MoE), see the [Ollama section](#alternative-ollama).

## Why llama.cpp

Two reasons shrew defaults to llama.cpp over Ollama:

1. **MoE offload control.** llama.cpp exposes `--n-cpu-moe` (and
   `-ngl`), which is what makes the experts-in-RAM trick work.
   Ollama does not expose these knobs in stable form yet.
2. **Tool-calling shape.** llama.cpp's OpenAI-compatible shim
   speaks the `tools=[...]` schema cleanly with the right `--jinja`
   chat template. Tool calling is the single biggest small-model
   capability gap; getting the wire shape right is non-negotiable.

The trade-off is that you compile llama.cpp yourself and download
GGUFs by hand. The payoff is full control over the inference stack.

## Step 1 — Build llama.cpp

```bash
cd ~/src                                     # or wherever you keep sources
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
```

Pick the build for your accelerator:

```bash
# Apple Silicon (Metal)
cmake -B build -DLLAMA_METAL=on
cmake --build build --config Release -j

# NVIDIA (CUDA)
cmake -B build -DGGML_CUDA=on
cmake --build build --config Release -j

# CPU only
cmake -B build
cmake --build build --config Release -j
```

The relevant binaries land under `build/bin/`. The two we care
about are `llama-server` (the HTTP server shrew talks to) and
`llama-cli` (handy for sanity checks).

```bash
./build/bin/llama-server --version
./build/bin/llama-cli --help | head -20
```

## Step 2 — Download a GGUF

Shrew's default is `qwen3.6-35b-a3b`. The official Qwen MoE
checkpoint is published as a GGUF on the Hugging Face hub. Pick a
quantisation that fits your RAM budget:

| Quant | Size on disk | RAM (approx) | Quality |
|---|---|---|---|
| `Q4_K_M` | 22 GB | 24-26 GB | recommended (default) |
| `Q5_K_M` | 26 GB | 28-30 GB | slightly better |
| `Q6_K` | 30 GB | 32-34 GB | near-fp16 quality |
| `Q3_K_S` | 16 GB | 18 GB | tight laptops only |

Using `huggingface-cli`:

```bash
mkdir -p ~/models
huggingface-cli download \
  Qwen/Qwen3.6-35B-A3B-Instruct-GGUF \
  --include "Q4_K_M/*.gguf" \
  --local-dir ~/models/qwen3.6-35b-a3b
```

If the upstream publishes the GGUF as a single file, the
`--include` filter narrows the download. Multi-shard GGUFs are
loaded by passing the **first** shard to `-m`; llama.cpp finds the
others automatically.

Alternative (smaller dense model — easier to start with):

```bash
huggingface-cli download \
  Qwen/Qwen3.5-9B-Instruct-GGUF \
  --include "Q4_K_M/*.gguf" \
  --local-dir ~/models/qwen3.5-9b
```

## Step 3 — Serve with the canonical incantation

This is the load-bearing command. Memorise it:

```bash
~/src/llama.cpp/build/bin/llama-server \
  -m ~/models/qwen3.6-35b-a3b/Q4_K_M/qwen3.6-35b-a3b-q4_k_m.gguf \
  --host 127.0.0.1 --port 8888 \
  --jinja \
  -c 16384 \
  -ngl 99 \
  --n-cpu-moe 999 \
  --flash-attn on
```

Flag-by-flag, in order of importance:

- **`--n-cpu-moe 999`** — keep all MoE expert tensors in system RAM,
  not VRAM. This is the trick. With `999` (a sentinel meaning "all
  layers"), the GPU stops carrying the experts and the only thing
  scaling with VRAM is the KV cache.
- **`-ngl 99`** — offload **all** non-expert layers to the GPU.
  The attention blocks live there. With `--n-cpu-moe 999` and
  `-ngl 99` set together, you get the canonical split: attention on
  GPU, experts on CPU.
- **`-c 16384`** — context window. 16k is a good default for an
  8 GB GPU. Bigger GPUs can go higher; the
  [`moe_offload`](extensions.md#moe-offload) extension picks a
  safe value automatically when you pass `--vram-gb` to shrew.
- **`--jinja`** — enable the model's Jinja chat template. Required
  for the `tools=[...]` OpenAI-compatible tool-calling shape to
  parse. **Do not omit this.** Without `--jinja`, tool calls return
  as plain text and shrew's loop won't see them.
- **`--flash-attn on`** — flash-attention. Faster on supported
  GPUs (Metal 3, CUDA SM>=70).
- **`--host 127.0.0.1 --port 8888`** — bind to localhost on shrew's
  default port.

Verify it's up:

```bash
curl http://127.0.0.1:8888/health
# {"status":"ok"}

curl http://127.0.0.1:8888/v1/models
# {"object":"list","data":[{"id":"qwen3.6-35b-a3b",...}]}
```

Now `chimera shrew -p "say hi"` will route through it
automatically.

### Why this fits on an 8 GB laptop GPU

The MoE-offload arithmetic, in round numbers:

- **Total weights:** 22 GB (Q4_K_M).
- **Experts (kept in RAM):** ~19 GB.
- **Attention + non-expert weights (on GPU):** ~2.5 GB.
- **CUDA workspace + activation reserve:** ~0.8 GB.
- **KV cache headroom on an 8 GB GPU:** ~4.7 GB → ~16k tokens.

The shrew [`moe_offload`](extensions.md#moe-offload) extension
implements this arithmetic. Pass `--vram-gb 8` and it returns
`16384`; pass `--vram-gb 24` and it returns `32768` (clamped at the
model's architectural maximum). The numbers are conservative — we
favour smaller windows over OOMs.

## Step 4 — Run shrew against it

```bash
export LLAMACPP_BASE_URL=http://127.0.0.1:8888/v1   # default; explicit
chimera shrew -p "explain this repo"
```

Or override per-invocation:

```bash
LLAMACPP_BASE_URL=http://gpu-box.lan:8888/v1 \
  chimera shrew --model qwen3.6-35b-a3b -p "audit dependencies"
```

If the server gates on a key (`--api-key foo` on `llama-server`),
pass it through:

```bash
export LLAMACPP_API_KEY=foo
chimera shrew -p "..."
```

## Sanity checks

- **Did llama.cpp pick the right GPU?** Look for `ggml_cuda_init`
  or `ggml_metal_init` in the server log; the device id and free
  VRAM should both be reported.
- **Are the experts actually on CPU?** The startup log prints
  `offload to GPU: <n>` per layer. With `--n-cpu-moe 999` you'll
  see CPU lines for the FFN/expert layers and GPU lines for
  attention/embedding.
- **Is `--jinja` working?** A round-trip `curl
  http://127.0.0.1:8888/v1/chat/completions -H "Content-Type:
  application/json" -d '{"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"hi"}],"tools":[{"type":"function","function":{"name":"echo","parameters":{"type":"object","properties":{}}}}]}'`
  should return a `tool_calls` field, not a plain `content` field.
- **Is shrew finding the server?** `chimera shrew --list-models`
  should print `qwen3.6-35b-a3b   llama.cpp @ http://127.0.0.1:8888/v1`
  as the first row.

## Tuning notes

- **Context vs. quality trade-off.** A bigger `-c` means more KV
  cache, which means less VRAM left for activations and more
  attention compute per token. On an 8 GB laptop, 16k is the
  ceiling; on a 24 GB workstation, 32k is fine. Don't push past the
  model's architectural max (32k for Qwen3 family).
- **`--n-cpu-moe` is a counter, not a flag.** Setting it to `999`
  is just "all"; setting it to `28` would mean "first 28 expert
  layers stay on CPU, the rest can go on GPU". The latter is rarely
  useful unless you've measured.
- **Batch size.** llama.cpp's `--batch-size` is forgivable to leave
  at default. The bottleneck is the GPU↔CPU transfer of expert
  activations, not batch size.
- **`--mlock`.** On Linux with enough RAM headroom, `--mlock` keeps
  expert tensors pinned and avoids page-faults during expert
  routing. Speeds things up by 5-15% in practice. Costs
  `ulimit -l unlimited` configuration.

## Alternative — Ollama

For a lower-friction setup at the cost of MoE-offload control, run
the dense Qwen3.5-9B via Ollama:

```bash
ollama serve &
ollama pull qwen3.5:cloud
chimera shrew --model qwen3.5:cloud -p "audit this repo"
```

Shrew probes Ollama at `$OLLAMA_BASE_URL` (default
`http://localhost:11434`) and falls back to it when llama.cpp isn't
reachable. The Ollama path uses the OpenAI-compatible shim under
`/v1` so the wire shape matches llama.cpp — useful when you want
shrew to behave consistently across both backends.

The trade-off is that the dense 9B model needs ~6 GB of VRAM all
to itself. There is no MoE trick available; it just runs on the
GPU. On an 8 GB laptop GPU you'll want a small `-c` (`8192` is
plenty for Qwen3.5-9B at this size).

## Further reading

- [`extensions.md`](extensions.md) — how `moe_offload`,
  `scaffold_fit`, and `tool_filter` work together at runtime.
- [`benchmarks.md`](benchmarks.md) — how to evaluate the local
  model against Aider Polyglot and GAIA.
- [`parity-matrix.md`](parity-matrix.md) — what shrew exposes from
  the upstream small-model coding agent and what it doesn't.
- [`security-and-trademarks.md`](security-and-trademarks.md) — how
  shrew refers to upstream concepts in source / docs.
