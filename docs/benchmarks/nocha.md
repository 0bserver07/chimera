---
title: "NoCha"
---

# NoCha (long-context novel claims)

`NoCha` (Karpinska et al., 2024) evaluates long-context reasoning by
giving the model a long document plus two competing claims and asking
which one is consistent with the document. The code split uses
concatenated repository sources (>50K tokens) and probes cross-file
reasoning.

References:

- GitHub: <https://github.com/marzenakrp/nocha>
- Paper: arXiv:2406.16264

## Status: SCAFFOLD

| Surface | State |
| --- | --- |
| `NoChaInstance` dataclass (id, document, true/false claim, token count, domain) | DONE |
| Loader (JSON / JSONL, domain + min/max token filters, limit) | DONE |
| Self-built `prompt` field that frames the A/B choice | DONE |
| In-process grading (parses agent's first standalone `A`/`B` token) | DONE |
| `long_context_share(threshold)` diagnostic | DONE |
| Discoverable via `chimera eval --benchmark nocha` | DONE |
| Public HuggingFace mirror of the code split | NOT AVAILABLE as of 2026-05 — load from local JSON |

## Quick start

```python
from chimera.eval.benchmarks import NoCha

bench = NoCha(
    dataset_path="nocha-code.jsonl",
    domain="code",
    min_tokens=50_000,
)
print(bench.name())                  # "nocha-code"
print(bench.long_context_share(50_000))   # 1.0
```

## Grading contract

Grading is in-process and side-effect-free. The agent's textual answer
is normalized and the **first standalone `A` or `B` token** is taken as
the pick. Convention: the dataset always lists the true claim as **A**,
so a passing answer is `A`.

```python
NoCha().evaluate(task, "Answer: A")    # True
NoCha().evaluate(task, "I think B.")   # False
NoCha().evaluate(task, "neither")      # False
```
