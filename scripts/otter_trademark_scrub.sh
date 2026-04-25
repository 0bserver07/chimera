#!/usr/bin/env bash
# otter_trademark_scrub.sh
#
# Trademark hygiene check for the `chimera otter` subcommand.
#
# Otter is modelled on a real-world server-first coding agent. We must NOT
# embed the upstream brand name into our live source, docs, error messages,
# or CLI text. Comparative analysis under `research/otter/` is fair-use and
# intentionally untouched.
#
# Filesystem-fact path mentions (e.g. `~/.opencode/config.json`,
# `.opencode/agent/`, `.opencode/command/*.md`) ARE allowed because they
# describe an existing on-disk layout we ingest -- not a brand claim.
#
# Exit 1 if any branded mention slips into live source/docs/tests.
# Exit 0 otherwise.
#
# Usage:
#   bash scripts/otter_trademark_scrub.sh
#
# Designed to be run locally and from CI (.github/workflows/ci.yml job
# `otter-trademark-scrub`).

set -euo pipefail

# Move to the repo root so `git grep` and relative paths behave the same
# in CI and local runs.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths to scan.  We intentionally include only LIVE source and docs/tests;
# the comparative research notes under `research/otter/` (other than the
# canonical SPEC.md) are research artifacts and are out of scope.
PATHS=(
  "chimera/otter/"
  "docs/otter/"
  "tests/otter/"
  "research/otter/SPEC.md"
  "README.md"
)

# Pattern: any cased form of the upstream brand name plus its host and
# packaging slugs.
PATTERN='OpenCode|opencode\.ai|opencode-ai'

# Allow filesystem-fact mentions like `~/.opencode/config.json`,
# `.opencode/agent/...`, or a bare `.opencode` directory reference.
# Anything else flips the exit code.
ALLOW='~/\.opencode|\.opencode/|\.opencode\b'

# `git grep` exits 1 when there are no matches; tolerate that.
HITS="$(git grep -nE "${PATTERN}" -- "${PATHS[@]}" || true)"

# Filter out the allowed filesystem-fact references.
FILTERED="$(printf '%s\n' "${HITS}" | grep -vE "${ALLOW}" || true)"

# Drop empty lines so we can detect "no real hits" reliably.
FILTERED="$(printf '%s\n' "${FILTERED}" | sed '/^$/d')"

if [[ -n "${FILTERED}" ]]; then
  echo "otter trademark scrub: FAIL"
  echo "The following lines reference the upstream brand outside of"
  echo "filesystem-fact paths.  Please rephrase using 'otter',"
  echo "'the upstream', or 'the open-source coding agent'."
  echo
  printf '%s\n' "${FILTERED}"
  exit 1
fi

echo "otter trademark scrub: OK (no branded mentions in live source/docs)"
exit 0
