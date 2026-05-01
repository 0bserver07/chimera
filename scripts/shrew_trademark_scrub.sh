#!/usr/bin/env bash
# shrew_trademark_scrub.sh
#
# Trademark hygiene check for the `chimera shrew` subcommand.
#
# Shrew is modelled on a real-world minimal coding agent. We must NOT
# embed the upstream brand name or author handle into our live source,
# docs, error messages, or CLI text. Comparative analysis under
# `research/shrew/` (other than the canonical SPEC.md) is fair-use and
# intentionally untouched.
#
# Filesystem-fact path mentions (e.g. `~/.shrew/config.json`,
# `.shrew/agents/`) ARE allowed because they describe our own on-disk
# layout -- not a brand claim.
#
# Exit 1 if any branded mention slips into live source/docs/tests.
# Exit 0 otherwise.
#
# Usage:
#   bash scripts/shrew_trademark_scrub.sh
#
# Designed to be run locally and from CI (.github/workflows/ci.yml job
# `shrew-trademark-scrub`).

set -euo pipefail

# Move to the repo root so `git grep` and relative paths behave the same
# in CI and local runs.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths to scan.  We intentionally include only LIVE source and docs/tests;
# the comparative research notes under `research/shrew/` (other than the
# canonical SPEC.md) are research artifacts and are out of scope.
PATHS=(
  "chimera/shrew/"
  "docs/shrew/"
  "tests/shrew/"
  "research/shrew/SPEC.md"
  "README.md"
)

# Pattern: any cased form of the upstream brand name plus the author's
# GitHub handle.
PATTERN='little-coder|itayinbarr'

# Allow filesystem-fact mentions like `~/.shrew/config.json` or
# `.shrew/agents/...`.  Anything else flips the exit code.
ALLOW='~/\.shrew|\.shrew/|\.shrew\b'

# Skip files that legitimately have to quote the brand to document or
# enforce the rule (policy docs + the meta-test that ASSERTS cli.py
# stays brand-clean).  These are the canonical exceptions.
SKIP_FILES='^(docs/shrew/(trademark-policy|security-and-trademarks)\.md|tests/shrew/test_cli\.py):'

# `git grep` exits 1 when there are no matches; tolerate that.
HITS="$(git grep -nE "${PATTERN}" -- "${PATHS[@]}" || true)"

# Filter out the allowed filesystem-fact references AND the policy doc.
FILTERED="$(printf '%s\n' "${HITS}" | grep -vE "${ALLOW}" | grep -vE "${SKIP_FILES}" || true)"

# Drop empty lines so we can detect "no real hits" reliably.
FILTERED="$(printf '%s\n' "${FILTERED}" | sed '/^$/d')"

if [[ -n "${FILTERED}" ]]; then
  echo "shrew trademark scrub: FAIL"
  echo "The following lines reference the upstream brand outside of"
  echo "filesystem-fact paths.  Please rephrase using 'shrew',"
  echo "'the upstream', or 'a minimal coding agent'."
  echo
  printf '%s\n' "${FILTERED}"
  exit 1
fi

echo "shrew trademark scrub: OK (no branded mentions in live source/docs)"
exit 0
