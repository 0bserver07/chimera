---
title: "Model Catalog"
description: "The generated pricing and context catalog — 2453 models from models.dev, with a hand-maintained override table that always wins."
---

Chimera prices token usage from two sources: a small hand-maintained table for
the models it actively bills, and a large generated catalog for everything
else. The hand table always wins; the catalog is the fallback.

---

## Where pricing comes from

`chimera.providers.cost.get_model_pricing(model)` resolves a model to an
`(input_per_mtok, output_per_mtok)` pair — US dollars per **million** tokens:

```python
from chimera.providers.cost import get_model_pricing

get_model_pricing("claude-sonnet-4-5-20250929")  # (3.0, 15.0)  — hand table
get_model_pricing("mistral-large-latest")         # (0.5, 1.5)   — catalog fallback
get_model_pricing("no-such-model")                # None
```

Resolution order:

1. **Hand table (`PRICING`)** — consulted first, longest prefix wins. This is
   the source of truth for models Chimera actively bills, including billing
   nuances that can't be auto-derived (for example, a model served over one
   vendor's endpoint vs. a local bridge). An explicit entry here always
   overrides the catalog.
2. **Generated catalog (`MODEL_CATALOG`)** — the fallback for every other
   model, also matched by longest prefix so a dated or suffixed id (e.g.
   `gpt-4-turbo-2024-04-09`) resolves through its base entry.

The catalog is loaded lazily and cached, so the common path — a hand-table hit
— never pays the catalog's import cost.

---

## The generated catalog

`chimera/providers/model_catalog.py` is a pure-data module — **do not edit it
by hand**. It is emitted from the public [models.dev](https://models.dev)
catalog, which auto-syncs pricing and context limits across roughly 150
providers. The current file carries **2453 models**.

Each entry maps a model id to a record:

```python
MODEL_CATALOG = {
    "claude-opus-4-5": {"input": 5, "output": 25, "cache_read": 0.5,
                        "cache_write": 6.25, "context": 200000, "provider": "anthropic"},
    "mistral-large-latest": {"input": 0.5, "output": 1.5, "cache_read": None,
                             "cache_write": None, "context": 262144, "provider": "mistral"},
    # ...
}
```

| Field | Meaning |
|---|---|
| `input` / `output` | Price in USD per million tokens. |
| `cache_read` / `cache_write` | Cache token prices, or `None` if the source did not publish them. |
| `context` | Context window in tokens, or `None`. |
| `provider` | The provider id the record was taken from. |

When one model id is offered by several providers, the record is taken from
the first-party provider (the manufacturer / lab — e.g. `anthropic` for
`claude-*`, `mistral` for `mistral-*`) where recognised, otherwise the
alphabetically-first provider id. This keeps regeneration deterministic and
prefers authoritative rates over marked-up reseller ones. Only models with a
numeric `cost.input` in the source are included.

---

## Regenerating the catalog

The generator is `scripts/generate_model_catalog.py`. It is stdlib-only
(`urllib`), so it runs in any environment:

```bash
python scripts/generate_model_catalog.py            # rewrite the module
python scripts/generate_model_catalog.py --check    # CI drift check (no write)
python scripts/generate_model_catalog.py --url URL  # override the source URL
```

A normal run fetches `https://models.dev/api.json`, rebuilds the catalog, and
rewrites `chimera/providers/model_catalog.py`, printing the model count.

---

## The CI drift guard

`--check` regenerates the catalog in memory and diffs it against the committed
file **without writing**. It exits non-zero (printing a unified diff) when the
committed module is stale, and zero when it is current. The generated-on date
line is stripped from both sides before diffing, so a date-only delta is not
reported as drift.

```bash
python scripts/generate_model_catalog.py --check
# OK: chimera/providers/model_catalog.py is up to date
```

Wire this into CI to catch a stale catalog before it ships.

---

## Auditing the hand table

`--check` guards the *generated* catalog. But the small hand table (`PRICING`) —
the rates Chimera actually bills — has no such guard, and it is the one that
silently goes stale: a vendor cuts a price, the models.dev figure follows, and
the hand entry keeps quoting last year's rate. `scripts/audit_model_pricing.py`
reconciles the hand table against models.dev:

```bash
python scripts/audit_model_pricing.py               # audit vs the committed snapshot (offline)
python scripts/audit_model_pricing.py --live         # audit vs a fresh models.dev fetch
python scripts/audit_model_pricing.py --json          # machine-readable report
python scripts/audit_model_pricing.py --include-resellers  # also compare reseller-sourced ids
```

For each hand prefix it finds the models.dev record under the same id and
compares the input/output rate. It **reports only** — it never rewrites a price,
because hand corrections always win over upstream (that is the whole point of the
two-source design). It exits non-zero when any drift is found, so it is CI-able;
it is intentionally **not** wired into CI — run it by hand when refreshing prices.

The default run is high-signal: it compares only against **first-party**
(manufacturer / lab) figures, since a hand rate disagreeing with a reseller's
markup is margin, not drift. Reseller-only ids are surfaced separately for
eyeballing; `--include-resellers` compares those too. The offline default reads
the committed snapshot and touches no network.

### The override convention

Some hand rates diverge from upstream **on purpose** — a placeholder pending a
vendor's rate sheet, a cross-endpoint billing nuance, or a local / open-weight
family billed at `$0`. Those prefixes are listed in
`chimera.providers.cost.PRICING_OVERRIDES`, and the audit skips them so an
intentional divergence is never reported as drift:

```python
from chimera.providers.cost import PRICING_OVERRIDES

# A frozenset of PRICING prefixes whose divergence from models.dev is deliberate:
#   "glm-5.2", "glm-5", …          — placeholders pending a public rate sheet
#   "deepseek-v4-pro", …            — per-SKU rates not yet published
#   "qwen3-coder", "gpt-oss-20b", … — local / open-weight, billed $0
```

Membership does **not** change runtime resolution — `get_model_pricing` always
prefers the hand table regardless; the set is a marker for the auditor only.
When the audit flags a new entry, resolve it one of two ways: correct the rate in
`PRICING`, or — if the divergence is deliberate — add the prefix to
`PRICING_OVERRIDES` with an inline reason. Never silence the audit by editing the
script.

---

## Overriding a price at runtime

To register or override pricing for a prefix without regenerating anything,
use `register_model_cost` — this writes into the hand table, so it takes
precedence over the catalog:

```python
from chimera.providers.cost import register_model_cost, calculate_cost

register_model_cost("acme/internal-llm", 0.50, 1.50)  # USD per Mtok in / out
calculate_cost("acme/internal-llm", {"input_tokens": 1_000_000, "output_tokens": 500_000})
# 1.25
```

`calculate_cost(model, usage)` and `estimate_cost(model, input_tokens,
output_tokens)` both resolve pricing through `get_model_pricing`, so they see
the same hand-table-then-catalog order. Both return `0.0` for a model neither
source knows.

---

## Next Steps

- [Use with Third-Party Providers](/use-with-third-party-providers/) — bring up
  any catalog model with a single string.
- [Prompt Caching](/prompt-caching/) — cut input cost on repeated prefixes.
