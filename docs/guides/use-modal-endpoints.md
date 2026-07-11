---
title: "Use Modal managed Endpoints"
description: "Serve a catalog model (GLM, Qwen, DeepSeek, ...) on Modal with one command, then point any Chimera agent, TUI lane, or benchmark at it — scale-to-zero, proxy-token auth, OpenAI-compatible wire."
---

Modal's managed **Endpoints** deploy an inference endpoint for a catalog
model with one command — no vLLM app to write, no GPU plumbing. The endpoint
speaks the **OpenAI Chat Completions API under `/v1`**, autoscales, and
**scales to zero**: you pay only while it is actually serving. Chimera wires
it in as the `modal-endpoint` provider, so every agent, TUI lane, and
benchmark cell can talk to a model you serve yourself.

This complements the older self-deployed path
(`chimera/providers/modal.py`, where *you* deploy a vLLM app): use managed
Endpoints when you want Modal to own the serving stack.

## 1. Create an endpoint

The catalog covers the GLM, Qwen, Gemma, DeepSeek, Kimi, Nemotron, and
GPT-OSS families. `--model` takes the Hugging Face repo id:

```bash
modal endpoint create --model zai-org/GLM-5.2-FP8
```

Useful flags: `--name` (custom endpoint name), `--routing-region us-west`,
`--unauthenticated` (public endpoint, no tokens), `--env <env>` (Modal
environment). Manage the fleet with:

```bash
modal endpoint list --env <env>          # --json for machine-readable (URLs included)
modal endpoint stop <name> --env <env>   # permanent teardown
```

Requires a modal client new enough to know the `endpoint` subcommand — if
you see `No such command 'endpoint'`, run `pip install --upgrade modal`.

## 2. Create proxy tokens

Endpoints authenticate with **workspace proxy tokens**, sent as the request
headers `Modal-Key` / `Modal-Secret` — *not* an `Authorization: Bearer`
token:

```bash
modal workspace proxy-tokens create
export MODAL_PROXY_TOKEN_ID='wk-...'
export MODAL_PROXY_TOKEN_SECRET='ws-...'
```

Endpoints created with `--unauthenticated` skip all of this (pass
`unauthenticated=True` / `--unauthenticated` on the Chimera side).

## 3. Wire Chimera — three tiers

**Tier 1 — one-liner.** The model-string convention is
`modal-endpoint/<hf-repo-id>` — the prefix is the provider's registry name,
the same scheme as `vllm/...` and `sglang/...`. The endpoint URL is
discovered via `modal endpoint list --json`; tokens come from the env vars
above:

```python
from chimera.providers.factory import create_provider

provider = create_provider(model="modal-endpoint/zai-org/GLM-5.2-FP8")
```

**Tier 2 — configured.** Explicit URL (from the dashboard or
`modal endpoint list`; `/v1` optional — it is normalized) and tokens; the
`modal` CLI is never invoked:

```python
from chimera.providers.modal_endpoint import ModalEndpointProvider

provider = ModalEndpointProvider(
    model="zai-org/GLM-5.2-FP8",
    base_url="https://myworkspace--glm-5-2-fp8.modal.run",
    token_id="wk-...",
    token_secret="ws-...",
)
```

**Tier 3 — subclassable.** Override the discovery hook (or anything
inherited from the OpenAI-compatible provider) for a pinned fleet:

```python
class PinnedEndpoints(ModalEndpointProvider):
    def _discover_base_url(self, model: str) -> str:
        return MY_FLEET[model]
```

Misconfiguration fails loudly, never silently: missing tokens name the
exact `modal workspace proxy-tokens create` fix, a missing/old `modal` CLI
tells you to install/upgrade or pass `base_url=`, and an unmatched model
lists the endpoints that *do* exist.

### Smoke-test it

One real completion with token/latency stats (manual — never run by
CI/tests; the first call after idle includes the cold start):

```bash
uv run python scripts/modal_endpoint_smoke.py --model zai-org/GLM-5.2-FP8
```

### CLI and TUI

The same model string works anywhere a model name does — `chimera code`,
and each TUI multiplexer lane:

```bash
# single agent, your local REPL, Modal-served model
chimera code --model modal-endpoint/zai-org/GLM-5.2-FP8

# TUI: race a Modal-served lane against an API-served lane
chimera code --tui --models "modal-endpoint/zai-org/GLM-5.2-FP8,glm-5.2[1m]"
```

## 4. Mix and match

The provider is just another lane, which is the point — control the serving
variable like any other:

- **Local TUI ↔ Modal-served model.** Your keyboard, prompt, and workspace
  stay local; only inference runs on the endpoint. The `--tui --models`
  line above races your own served GLM against the hosted API of the same
  family — same agent, same task, serving stack isolated as the variable.
- **Modal-run benches ↔ Modal-served model (fully-cloud loop).**
  `scripts/modal_bench_app.py` is the other half: it runs
  agent × benchmark cells *on* Modal (see
  [Benchmarks on Modal](../benchmarks/modal-cloud-benches.md)). Point its
  cells at a `modal-endpoint/...` model and the whole loop — orchestration,
  inference, execution, grading — is cloud-side; your laptop just collects
  the grid.

## Economics, honestly

Scale-to-zero means an idle endpoint costs nothing — and it also means the
**first request after idle eats a cold start** (container boot + model
load; for large models this is minutes, not seconds). Interactive sessions
feel this once and then run warm; benchmark fan-outs amortize it across the
run. You are billed GPU-seconds while the endpoint serves — for steady
all-day traffic, compare against per-token API pricing before assuming the
endpoint is cheaper. `modal endpoint stop <name>` when you are done; check
spend in the Modal dashboard.

## Field notes from the first live run (2026-07-10, GLM-5.2-FP8)

Observed against a real endpoint (`chimera-glm52`, 8 × B200, us-west):

- **The endpoint URL only appears on the dashboard.** `modal endpoint list`
  (CLI 1.5.2, table AND `--json`) does not print it. The scheme observed:
  `https://<workspace>--ep-<name>-server.<routing-region>.modal.direct`
  — note `.modal.direct`, not `.modal.run`. Grab it from the endpoint's
  dashboard page ("Copy Endpoint URL") and pass `--base-url` /
  `base_url=` explicitly.
- **`create` requires proxy tokens to exist first** (or
  `--unauthenticated`): run `modal workspace proxy-tokens create` *before*
  `modal endpoint create`.
- **Cold start for a model this size was ~19 minutes** end-to-end
  (create → provisioning → `live` in ~17 min; first request then 503s while
  weights load; `/v1/models` flipped to 200 ~19 min after the first
  request). A cold endpoint answers **503**, not a queued slow response —
  poll `/v1/models` until 200 rather than retrying completions.
- **Warm behavior**: one-shot completion latency 1.16 s, 40.5 tok/s output
  on the smoke prompt. Scaledown window on the default recipe: 300 s.
