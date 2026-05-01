# Friendly Error Diagnostics

All seven Chimera coding-agent CLIs (`mink`, `otter`, `ferret`, `weasel`,
`shrew`, `stoat`, `badger`) wrap their `run(args)` entry point with the
`@friendly_errors` decorator from `chimera.errors`. The decorator
catches the most common provider / network failures and replaces raw
stack traces with a single-line message plus a remediation hint.

## What gets wrapped

| Raw exception | Category | Friendly message | Hint |
| --- | --- | --- | --- |
| `anthropic.AuthenticationError`, `openai.AuthenticationError` | `auth` | Provider rejected request — no valid API key found. | Run `chimera auth login`, set the right env var, or run `chimera doctor`. |
| `httpx.ConnectError` to `localhost:11434` | `connect` | Ollama daemon at http://localhost:11434 not running. | `ollama serve` |
| `httpx.ConnectError` to `localhost:8888` | `connect` | llama.cpp daemon at http://localhost:8888 not running. | `./server -m model.gguf --port 8888` |
| `httpx.ConnectError` to `localhost:8000` | `connect` | vLLM daemon at http://localhost:8000 not running. | `vllm serve <model> --port 8000` |
| `httpx.ConnectError` to `localhost:30000` | `connect` | SGLang daemon at http://localhost:30000 not running. | `python -m sglang.launch_server …` |
| `httpx.ConnectError` (other host) | `connect` | Cannot reach upstream at `<url>`. | Run `chimera doctor`. |
| `ValueError("Cannot infer provider from model name '…'")` | `routing` | Model id `<X>` didn't match any provider chain. | Suggestion list (known prefixes, `--provider`, base-URL env var). |
| `httpx.HTTPStatusError` 401 / 403 | `auth` | Auth rejected by upstream (HTTP 4xx). | `chimera auth login`, then `chimera doctor`. |
| `httpx.HTTPStatusError` 429 | `rate_limit` | Rate-limited by upstream (HTTP 429). | Wait ~30s, retry, or switch model. |
| `httpx.HTTPStatusError` 5xx | `upstream` | Upstream issue (HTTP 5xx). | Retry, switch model, check provider status. |

Anything not in the table re-raises unchanged — we never silently swallow
errors we don't understand.

## Output

When the wrapped function raises one of the above, the decorator prints
a single-line `error: …` to stderr, followed by one or more `hint: …`
lines. Output is colored when stderr is a TTY (red `error:`, dim grey
`hint:`); plain text otherwise.

The decorator returns `ChimeraUserError.exit_code` (default `1`).

## `--debug` passthrough

If `args.debug` is truthy (e.g. the user passed `--debug` on the CLI),
the decorator becomes a no-op: the original exception bubbles up
unaltered so users get the full traceback. This is the supported path
for filing bug reports against Chimera. CLIs that don't yet expose a
`--debug` flag still get the friendly path by default; adding the flag
is a one-line `parser.add_argument("--debug", action="store_true")`.

## API

```python
from chimera.errors import (
    ChimeraUserError,
    friendly_errors,
    wrap_provider_errors,
)
```

* `ChimeraUserError(message, *, hint="", category="unknown", exit_code=1)`
  — the friendly wrapper exception.
* `wrap_provider_errors()` — context manager / decorator that performs
  the raw-exception → `ChimeraUserError` mapping.
* `friendly_errors(func)` — decorator for `run(args)` entry points.

## Adding a new mapping

To recognise a new failure mode, edit `chimera/errors/friendly.py`:

1. Add a classifier helper (`_classify_<thing>`) that returns
   `ChimeraUserError | None`.
2. Wire it into `wrap_provider_errors()` in the right `except` branch.
3. Cover it in `tests/errors/test_friendly.py` with a synthetic
   exception fixture.

Keep messages under 100 characters; put detail in `hint`.

## Verifying

```
uv run pytest tests/errors/test_friendly.py
uv run ruff check chimera/errors/
uv run mypy chimera/errors/
```
