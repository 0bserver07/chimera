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
```

## Integration Tests (require API credentials)

```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="your-token"
export ANTHROPIC_MODEL="glm-5"
uv run pytest tests/test_integration_live.py -v
```

## License

By contributing, you agree that your contributions will be licensed under MIT.
