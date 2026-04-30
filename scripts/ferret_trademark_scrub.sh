#!/usr/bin/env bash
# ferret_trademark_scrub.sh
#
# Trademark hygiene check for the `chimera ferret` subcommand.
#
# Ferret is modelled on a real-world CLI-first coding agent. We must NOT
# embed the upstream brand name into our live source, docs, error
# messages, or CLI text. Comparative analysis under `research/ferret/`
# (other than the canonical SPEC.md) is fair-use and intentionally
# untouched.
#
# Filesystem-fact path mentions (e.g. `~/.codex/config.toml`,
# `.codex/agents/`) ARE allowed because they describe an existing
# on-disk layout we ingest -- not a brand claim.
#
# Exit 1 if any branded mention slips into live source/docs/tests.
# Exit 0 otherwise.
#
# Usage:
#   bash scripts/ferret_trademark_scrub.sh
#
# Designed to be run locally and from CI (.github/workflows/ci.yml job
# `ferret-trademark-scrub`).

set -euo pipefail

# Move to the repo root so `git grep` and relative paths behave the same
# in CI and local runs.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths to scan.  We intentionally include only LIVE source and docs/tests;
# the comparative research notes under `research/ferret/` (other than the
# canonical SPEC.md) are research artifacts and are out of scope.
PATHS=(
  "chimera/ferret/"
  "docs/ferret/"
  "tests/ferret/"
  "research/ferret/SPEC.md"
  "README.md"
)

# Pattern: any cased form of the upstream brand name plus its
# packaging slugs.
PATTERN='Codex CLI|codex-cli|@openai/codex'

# Allow filesystem-fact mentions like `~/.codex/config.toml`,
# `.codex/agents/...`, or a bare `.codex` directory reference.
# Anything else flips the exit code.
ALLOW='~/\.codex|\.codex/|\.codex\b'

# Skip the policy doc itself — it has to quote the regex + a sample
# failure line to document the rule.  This is the canonical exception.
SKIP_FILES='^docs/ferret/trademark-policy\.md:'

# `git grep` exits 1 when there are no matches; tolerate that.
HITS="$(git grep -nE "${PATTERN}" -- "${PATHS[@]}" || true)"

# Filter out the allowed filesystem-fact references AND the policy doc.
FILTERED="$(printf '%s\n' "${HITS}" | grep -vE "${ALLOW}" | grep -vE "${SKIP_FILES}" || true)"

# Drop empty lines so we can detect "no real hits" reliably.
FILTERED="$(printf '%s\n' "${FILTERED}" | sed '/^$/d')"

if [[ -n "${FILTERED}" ]]; then
  echo "ferret trademark scrub: FAIL"
  echo "The following lines reference the upstream brand outside of"
  echo "filesystem-fact paths.  Please rephrase using 'ferret',"
  echo "'the upstream', or 'a CLI-first coding agent'."
  echo
  printf '%s\n' "${FILTERED}"
  exit 1
fi

echo "ferret trademark scrub: OK (no branded mentions in live source/docs)"
exit 0
