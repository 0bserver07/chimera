#!/usr/bin/env bash
# weasel_trademark_scrub.sh
#
# Trademark hygiene check for the `chimera weasel` subcommand.
#
# Weasel is modelled on a real-world coding agent. We must NOT embed the
# upstream brand name into our live source, docs, error messages, or CLI
# text. Comparative analysis under `research/weasel/` (other than the
# canonical SPEC.md) is fair-use and intentionally untouched.
#
# Filesystem-fact path mentions (e.g. `~/.weasel/config.json`,
# `.weasel/agents/`) ARE allowed because they describe our own on-disk
# layout -- not a brand claim.
#
# Exit 1 if any branded mention slips into live source/docs/tests.
# Exit 0 otherwise.
#
# Usage:
#   bash scripts/weasel_trademark_scrub.sh
#
# Designed to be run locally and from CI (.github/workflows/ci.yml job
# `weasel-trademark-scrub`).

set -euo pipefail

# Move to the repo root so `git grep` and relative paths behave the same
# in CI and local runs.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths to scan.  We intentionally include only LIVE source and docs/tests;
# the comparative research notes under `research/weasel/` (other than the
# canonical SPEC.md) are research artifacts and are out of scope.
PATHS=(
  "chimera/weasel/"
  "docs/weasel/"
  "tests/weasel/"
  "research/weasel/SPEC.md"
  "README.md"
)

# Pattern: any cased form of the upstream brand name plus its host and
# packaging slugs.
PATTERN='pi-coding-agent|pi\.dev|@mariozechner/pi|pi-mono'

# Allow filesystem-fact mentions like `~/.weasel/config.json` or
# `.weasel/agents/...`.  Anything else flips the exit code.
ALLOW='~/\.weasel|\.weasel/|\.weasel\b'

# Skip the policy doc itself — it has to quote the regex + a sample
# failure line to document the rule.  This is the canonical exception.
SKIP_FILES='^docs/weasel/trademark-policy\.md:'

# `git grep` exits 1 when there are no matches; tolerate that.
HITS="$(git grep -nE "${PATTERN}" -- "${PATHS[@]}" || true)"

# Filter out the allowed filesystem-fact references AND the policy doc.
FILTERED="$(printf '%s\n' "${HITS}" | grep -vE "${ALLOW}" | grep -vE "${SKIP_FILES}" || true)"

# Drop empty lines so we can detect "no real hits" reliably.
FILTERED="$(printf '%s\n' "${FILTERED}" | sed '/^$/d')"

if [[ -n "${FILTERED}" ]]; then
  echo "weasel trademark scrub: FAIL"
  echo "The following lines reference the upstream brand outside of"
  echo "filesystem-fact paths.  Please rephrase using 'weasel',"
  echo "'the upstream', or 'a minimal coding agent'."
  echo
  printf '%s\n' "${FILTERED}"
  exit 1
fi

echo "weasel trademark scrub: OK (no branded mentions in live source/docs)"
exit 0
