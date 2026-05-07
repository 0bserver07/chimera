---
title: chimera which
description: Heuristic recommender that suggests the right Chimera CLI codename for a task.
---

# `chimera which`

A zero-network, zero-LLM heuristic that takes a free-form task
description and recommends which of the seven Chimera coding-agent CLIs
fits best. Use it when you know what you want to *do* but not which
codename owns that posture.

`chimera which` is a sibling of [`chimera agents`](./inspirations.md):
the latter lists every CLI; the former *picks* one for a given task.

## Usage

```sh
chimera which --task "I want a TUI panel for coding"
chimera which --task "spin up a small local llama model" --top-k 1
chimera which --task "headless rpc agent" --output json
```

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--task TEXT` | *(required)* | Free-form description. Tokenized lowercase, alpha-only. |
| `--output text\|json` | `text` | `text` prints a numbered list; `json` emits the schema below. |
| `--top-k N` | `3` | How many recommendations to return. Clamped to `[0, 7]`. |

## How scoring works

1. The task string is tokenized: lowercased, split on every non-letter
   character, empty fragments dropped.
2. For each codename we count how many of its associated keywords have
   *all* their tokens present in the task. `low-resource` only matches
   when both `low` and `resource` appear.
3. Codenames are sorted by score (descending), then by canonical
   codename order (ascending). Ties break deterministically.
4. The top `--top-k` rows print. Empty / digit-only / whitespace-only
   tasks return zero recommendations; tasks with no keyword overlap
   return `top_k` zero-score rows so the output shape stays stable.

## Keyword map (summary)

| Codename | Indicative keywords |
|---|---|
| **mink** | `tui`, `textual`, `ide`, `panel`, `gui` |
| **shrew** | `small`, `local`, `ollama`, `mini`, `tiny`, `low-resource`, `llama`, `qwen` |
| **stoat** | `shell`, `bash`, `command`, `repl`, `terminal`, `/shell` |
| **ferret** | `sandbox`, `docker`, `isolate`, `jail`, `security`, `untrusted` |
| **badger** | `strict`, `parity`, `validate`, `golden`, `deterministic` |
| **weasel** | `rpc`, `json-stdio`, `headless`, `programmatic`, `api` |
| **otter** | `server`, `http`, `multi-session`, `multi-user`, `port`, `daemon` |

The full map is the single source of truth in
[`chimera/cli/which_cmd.py`](https://github.com/0bserver07/chimera/blob/master/chimera/cli/which_cmd.py)
(`KEYWORD_MAP`). Add new keywords there when a CLI grows a new posture.

## Text output

```text
chimera which: task='I want a TUI panel for coding'

  1. mink (score 2) -- matched: tui, panel
  2. shrew (score 0) -- no keyword match
  3. stoat (score 0) -- no keyword match

Run ``chimera agents`` for the full catalogue of CLIs and aliases.
```

The numbered prefix and the `matched: ...` rationale are stable enough
to grep against in scripts; if you need a stricter contract use
`--output json`.

## JSON schema

```json
{
  "task": "spin up a small local llama",
  "recommendations": [
    { "name": "shrew", "score": 3, "rationale": ["small", "local", "llama"] },
    { "name": "mink",  "score": 0, "rationale": [] }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `task` | `string` | Echoes the raw `--task` value. |
| `recommendations` | `array<object>` | Length `<= --top-k`. Empty when the task tokenized to nothing. |
| `recommendations[].name` | `string` | One of the 7 canonical codenames. |
| `recommendations[].score` | `integer` | `>= 0`. Number of distinct keywords matched. |
| `recommendations[].rationale` | `array<string>` | The raw keywords (as stored in `KEYWORD_MAP`) that triggered the score. Empty when `score == 0`. |

## Limitations

* **No semantic matching.** `coding` doesn't match `code`; `IDEs`
  tokenizes to `ides` and matches nothing. The map is intentionally
  small — extend it rather than reaching for embeddings.
* **No LLM fallback.** When you want a generative recommender, pipe the
  task through `chimera mink -p "which CLI fits ..."` instead.
* **Tie-breaking is canonical-order.** When two codenames score the
  same, the one declared earlier in `KEYWORD_MAP` wins. This is
  documented behaviour and tested in
  `tests/cli/test_which_cmd.py::test_recommend_deterministic_tie_break`.

## See also

* [`chimera agents`](./inspirations.md) — full catalogue of the 7 CLIs
  with their aliases and one-liner pitches.
* [`chimera doctor`](./doctor.md) — diagnose your environment after you
  pick a CLI.
