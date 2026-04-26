#!/usr/bin/env bash
# mink_trademark_scrub.sh
#
# Trademark hygiene check for the `chimera mink` subcommand.
#
# Mink is modelled on a real-world TUI-first coding agent. We must NOT
# embed the upstream brand name into our live source, docs, error
# messages, or CLI text. Comparative analysis under `research/mink/`,
# `docs/benchmarks/`, `docs/plans/`, and `docs/superpowers/` is fair-use
# integration history and intentionally untouched (out of live-source
# scope per HANDOFF Pitfall #5).
#
# Filesystem-fact path mentions (e.g. `~/.claude/settings.json`,
# `.claude/settings.json`) ARE allowed because they describe an existing
# on-disk layout we ingest -- not a brand claim. The legacy `cc-clone`
# slug and the unrelated `claude-code-acp` upstream package name are
# also explicitly allow-listed.
#
# Exit 1 if any branded mention slips into live source/docs.
# Exit 0 otherwise.
#
# Usage:
#   bash scripts/mink_trademark_scrub.sh
#
# Designed to be run locally and from CI (.github/workflows/ci.yml job
# `mink-trademark-scrub`).

set -euo pipefail

# Move to the repo root so `git grep` and relative paths behave the same
# in CI and local runs.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths to scan.  We intentionally include only LIVE source, user docs,
# code, examples, and the package manifest.  Out-of-scope (intentionally
# NOT scanned) per HANDOFF Pitfall #5:
#   - research/mink/      (internal handoff + comparative analysis)
#   - docs/benchmarks/    (factual integration history)
#   - docs/plans/         (factual integration history)
#   - docs/superpowers/   (factual integration history)
PATHS=(
  "README.md"
  "docs/mink/"
  "chimera/"
  "examples/"
  "pyproject.toml"
)

# Pattern: any cased form of the upstream brand name plus the
# CC-parity-style references we scrubbed in wave 1.
PATTERN='Claude Code|claude-code|CC parity|Claude-Code-like'

# Allow filesystem-fact mentions like `~/.claude/settings.json` or
# `.claude/`, the legacy `cc-clone` slug, and the unrelated
# `claude-code-acp` upstream package name.  Anything else flips the
# exit code.
ALLOW='cc-clone|claude-code-acp|~/\.claude|\.claude/'

# `git grep` exits 1 when there are no matches; tolerate that.
HITS="$(git grep -nE "${PATTERN}" -- "${PATHS[@]}" || true)"

# Filter out the allowed filesystem-fact references.
FILTERED="$(printf '%s\n' "${HITS}" | grep -vE "${ALLOW}" || true)"

# Drop empty lines so we can detect "no real hits" reliably.
FILTERED="$(printf '%s\n' "${FILTERED}" | sed '/^$/d')"

if [[ -n "${FILTERED}" ]]; then
  echo "mink trademark scrub: FAIL"
  echo "The following lines reference the upstream brand outside of"
  echo "filesystem-fact paths.  Please rephrase using 'mink',"
  echo "'the upstream', or 'a TUI-first coding agent'."
  echo
  printf '%s\n' "${FILTERED}"
  exit 1
fi

echo "mink trademark scrub: OK (no branded mentions in live source/docs)"
exit 0
