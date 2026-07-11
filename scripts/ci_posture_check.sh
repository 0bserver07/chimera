#!/usr/bin/env bash
# Replicate CI's exact environment and gates locally, BEFORE pushing.
#
# WHY THIS EXISTS: CI installs no optional extras beyond dev+anthropic+openai
# (.github/workflows/ci.yml) — no textual, no rich. Local dev usually has the
# [tui] extra installed, so a green local gate can still be a red CI: mypy
# flags textual imports that the pyproject override doesn't cover, and tests
# that import textual/rich without pytest.importorskip fail at collection.
# Both happened on 2026-07-10 (runs 29135401556 and 29135515534) when a
# 26-commit batch met CI for the first time. This script is the pre-push gate
# that would have caught both.
#
# What it does:
#   1. uv sync to CI's extras (REMOVES textual/rich from the venv)
#   2. cold-cache mypy (incremental caches poison across extra flips)
#   3. pytest with CI's exact invocation
#   4. restores whatever textual version was installed, and re-colds the
#      mypy cache so the next local run isn't poisoned in the other direction
set -euo pipefail
cd "$(dirname "$0")/.."

TEXTUAL_V="$(uv run python -c 'import textual; print(textual.__version__)' 2>/dev/null | tail -1 || true)"

echo "== 1/3 sync to CI env (dev+anthropic+openai — no tui extra) =="
uv sync --extra dev --extra anthropic --extra openai >/dev/null

echo "== 2/3 mypy, cold cache, CI posture =="
rm -rf .mypy_cache
uv run mypy chimera/

echo "== 3/3 pytest, CI invocation =="
uv run pytest tests/ --ignore=tests/benchmarks -m "not live" --tb=line -q

if [ -n "${TEXTUAL_V}" ]; then
  echo "== restoring textual==${TEXTUAL_V} =="
  uv pip install -q "textual==${TEXTUAL_V}"
else
  echo "== textual was not installed before; leaving the env as CI has it =="
fi
rm -rf .mypy_cache   # cache is posture-specific; leave it cold either way

echo "CI-POSTURE GREEN"
