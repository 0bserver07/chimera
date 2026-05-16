# Contributing to Chimera

Thanks for your interest in contributing.

## Setup

```bash
git clone https://github.com/0bserver07/chimera.git
cd chimera
uv sync --extra dev --extra anthropic
uv run pytest
```

## Development Workflow

1. Create a branch from `master`
2. Write tests first (`tests/test_*.py`)
3. Implement the feature
4. Run `uv run pytest` — all tests must pass
5. Run `uv run ruff check chimera/` — no lint errors
6. Submit a PR

## Code Style

- Python 3.11+
- Google-style docstrings (Args/Returns/Raises)
- `TYPE_CHECKING` imports for cross-module type hints
- Tests mirror source: `chimera/foo/bar.py` → `tests/test_bar.py`
- Zero-dependency core — only stdlib in main package

## Running Tests

```bash
uv run pytest                    # all tests
uv run pytest tests/test_foo.py  # specific file
uv run pytest -x --tb=short     # stop on first failure
uv run pytest -m "not live"     # skip live (network/LLM) tests
uv run pytest -m live            # only live tests
uv run pytest -m integration     # only integration tests
```

### Integration Tests (require API credentials)

```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="your-token"
export ANTHROPIC_MODEL="glm-5"
uv run pytest tests/test_integration_live.py -v
```

## What we expect in a PR

Every PR is gated on:

- `uv run ruff check chimera/` — zero errors
- `uv run mypy chimera/` — zero errors (the project is fully typed)
- `uv run pytest` — all non-live tests pass
- `bash scripts/all_trademark_scrub.sh` — all 7 codename scrubs green
- New surfaces have tests (`tests/test_<module>.py`)
- New public APIs have Google-style docstrings (Args/Returns/Raises)

If you change a CLI surface, also run the matching per-codename scrub:
`bash scripts/<codename>_trademark_scrub.sh` (mink, otter, ferret,
weasel, shrew, stoat, badger).

If you change a docs page, the docs site must build:
`cd site && pnpm install && pnpm build`.

## Where to Start

Good first issues are labeled
[`good first issue`](https://github.com/0bserver07/chimera/labels/good%20first%20issue);
larger items that we'd love a hand with are labeled
[`help wanted`](https://github.com/0bserver07/chimera/labels/help%20wanted).
A few current pointers:

- **Add a benchmark adapter** — pick a public coding-benchmark dataset
  not yet covered in `chimera/eval/benchmarks/` and wire it up. See
  "How to add a benchmark" below.
- **Add a tool** — anything missing from the
  [tool list](https://0bserver07.github.io/chimera/modules/tools/) is
  fair game; the `@tool` decorator path is one file. See "How to add a
  tool" below.
- **Add a provider** — implement `Provider` for an LLM API we don't
  cover (xAI / Grok, Mistral, DeepSeek native, etc.). Self-register
  via `chimera/providers/registry.py`.
- **Improve a CLI codename's quickstart** — the 7 quickstart scripts in
  `examples/*_quickstart.py` are short and welcoming targets; the
  docstring tells you the contract.
- **Wire a new MCP server** — `chimera/mcp_servers/` already hosts six;
  proposals for new ones are tracked under the
  [`mcp`](https://github.com/0bserver07/chimera/labels/mcp) label.

## How to add a benchmark

A benchmark is a subclass of `chimera.eval.harness.Benchmark` with
three methods. Drop a file in `chimera/eval/benchmarks/<name>.py`:

```python
from __future__ import annotations
from typing import Any
from chimera.eval.harness import Benchmark


class MyBench(Benchmark):
    """One-line description of what the benchmark measures."""

    def name(self) -> str:
        return "my-bench"

    def tasks(self) -> list[dict[str, Any]]:
        # Load problems from disk or HF Hub; return a list of dicts.
        return [...]

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        # Return True on pass, False on fail. May execute in `env`.
        return ...
```

Then add tests in `tests/test_<name>.py` (mirror an existing one such
as `tests/test_human_eval.py`). For benchmarks that should run in CI,
add a small fixture under `tests/fixtures/` rather than fetching the
real dataset at import time. Look at `human_eval.py`, `mbpp.py`, and
`aider_polyglot.py` for the three common shapes (single-file, sandbox,
multi-file repo edits).

To wire it into `chimera eval` and `chimera bench`, register it in
`chimera/eval/benchmarks/__init__.py` and add a row to
[`docs/benchmarks/README.md`](docs/benchmarks/README.md) — the
transparency framework requires every benchmark to publish a status
(working / broken / preliminary), methodology, and known gaps.

## How to add a tool

Tools subclass `chimera.core.tool.BaseTool` (or use the `@tool`
decorator for trivial cases). Drop a file in `chimera/tools/<name>.py`:

```python
from __future__ import annotations
from chimera.core.tool import BaseTool


class MyTool(BaseTool):
    """One-line description shown to the model."""

    name = "my_tool"
    description = "What it does, when to use it."

    # Optional: JSON-schema for input validation.
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def run(self, path: str) -> str:
        return f"Did the thing on {path}"
```

Register it by adding to `DEFAULT_TOOLS` in
`chimera/core/tool_group.py` (or, for opt-in tools, leave it
out — users pull it in by name). Mirror existing tools for testing
patterns — `tests/test_<name>.py`. If the tool touches the filesystem,
add a path-escape test (see `tests/test_write.py`). If it shells out,
classify its risk in `chimera/permissions/risk.py`.

The full tool taxonomy lives in
[`docs/modules/tools/`](docs/modules/tools/) — one page per tool with
the same fields (purpose, parameters, examples, gotchas) so a new
tool's doc page is mostly fill-in-the-blank.

## How to add a CLI codename

A "codename" is a thin posture over the shared agent loop — different
defaults, different tool subset, different transport — but the same
`LoopConfig`, the same provider factory, the same event-sourced
session store. The seven existing ones (mink, otter, ferret, weasel,
shrew, stoat, badger) all follow the same layout:

```
chimera/<codename>/
├── __init__.py
├── cli.py            # argparse, --help, -p / -m / --model surface
├── repl.py           # interactive loop (if applicable)
├── server.py         # HTTP / ACP transport (if applicable)
└── ...
```

Then:

1. Wire the entry point in `chimera/cli/main.py` (add a row to the
   subcommand dispatch).
2. Add a quickstart at `examples/<codename>_quickstart.py` that runs
   via `subprocess.run` and skips cleanly with rc=0 when no
   credentials are present (mirror `examples/mink_quickstart.py`).
3. Add a trademark scrub at `scripts/<codename>_trademark_scrub.sh`
   and add the name to the `CODENAMES` array in
   `scripts/all_trademark_scrub.sh`. Wire a separate job in
   `.github/workflows/ci.yml`.
4. Write the doc tree at `docs/<codename>/` — at minimum
   `quickstart.md`. Use the existing seven trees as a template.
5. Add a row to `chimera/cli/agents.py` (the `chimera agents` table)
   and to the comparative tour at `docs/coding-agents.md`.

Read `docs/coding-agents.md` first — it explains the design space
(TUI-first vs server-first vs sandbox-first vs minimal vs small-model
vs shell-toggle vs strict-harness) so a new codename can claim a
posture that doesn't overlap an existing one.

## How to add a provider

`chimera/providers/<name>.py` implements `chimera.providers.base.Provider`:

```python
from chimera.providers.base import Provider, Response
from chimera.providers.registry import register_provider


class MyProvider(Provider):
    def __init__(self, model: str, api_key: str | None = None) -> None:
        ...

    async def complete(self, messages, *, tools=None, **kw) -> Response:
        ...


# Self-register at import time.
register_provider("my-prefix-*", lambda model, **kw: MyProvider(model, **kw))
```

The registry's prefix match (`"glm-*"`, `"gpt-*"`, etc.) routes
`create_provider(model=...)` calls; users get auto-detection for free.
Add the dependency under a new `[project.optional-dependencies]`
extra in `pyproject.toml` so the core install stays zero-dep.

## License

By contributing, you agree that your contributions will be licensed
under MIT.
