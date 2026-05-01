#!/usr/bin/env bash
# badger_trademark_scrub.sh
#
# Trademark hygiene check for the `chimera badger` subcommand.
#
# Badger is modelled on the harness-rewrite tradition — the public
# practice of porting an existing agent harness to a new language with
# the explicit goal of "better harness tools, not merely storing the
# archive". We must NOT embed the upstream brand name into our live
# source, docs, error messages, or CLI text. Comparative analysis
# under `research/badger/` (other than the canonical SPEC.md) is fair-
# use and intentionally untouched.
#
# Filesystem-fact path mentions (e.g. `~/.claw/`, `.claw/`) ARE allowed
# because they describe an existing on-disk layout we may ingest -- not
# a brand claim.
#
# Exit 1 if any banned phrase slips into live source/docs/tests.
# Exit 0 otherwise.
#
# Usage:
#   bash scripts/badger_trademark_scrub.sh
#
# Designed to be run locally and from CI.

set -euo pipefail

# Move to the repo root so `git grep` and relative paths behave the
# same in CI and local runs.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths to scan. We intentionally include only LIVE source and
# docs/tests; comparative research notes under `research/badger/`
# (other than the canonical SPEC.md) are research artifacts and out
# of scope.
PATHS=(
  "chimera/badger/"
  "docs/badger/"
  "tests/badger/"
  "site/src/content/docs/badger/"
)
# Optional paths — only added when present, so the scrub stays useful
# even before the site mirror or research SPEC have landed.
OPTIONAL_PATHS=(
  "research/badger/SPEC.md"
)
for opt in "${OPTIONAL_PATHS[@]}"; do
  if [[ -e "${opt}" ]]; then
    PATHS+=("${opt}")
  fi
done

# Pattern: any cased form of the upstream brand name + packaging slugs.
PATTERN='Claw Code|claw-code|clawhip'

# Allow filesystem-fact mentions like `~/.claw/`, `.claw/`, or a bare
# `.claw` directory reference. Anything else flips the exit code.
ALLOW='~/\.claw|\.claw/|\.claw\b'

# Skip the policy doc itself — it has to quote the regex + a sample
# failure line to document the rule. This is the canonical exception.
SKIP_FILES='^(docs|site/src/content/docs)/badger/security-and-trademarks\.md:'

# `git grep` exits 1 when there are no matches; tolerate that.
HITS="$(git grep -nE "${PATTERN}" -- "${PATHS[@]}" 2>/dev/null || true)"

# Filter out the allowed filesystem-fact references AND the policy doc.
FILTERED="$(printf '%s\n' "${HITS}" | grep -vE "${ALLOW}" | grep -vE "${SKIP_FILES}" || true)"

# Drop empty lines so we can detect "no real hits" reliably.
FILTERED="$(printf '%s\n' "${FILTERED}" | sed '/^$/d')"

if [[ -n "${FILTERED}" ]]; then
  echo "badger trademark scrub: FAIL"
  echo "The following lines reference the upstream brand outside of"
  echo "filesystem-fact paths. Please rephrase using 'badger',"
  echo "'the upstream', or 'the harness-rewrite tradition'."
  echo
  printf '%s\n' "${FILTERED}"
  exit 1
fi

echo "badger trademark scrub: OK (no branded mentions in live source/docs)"
exit 0
