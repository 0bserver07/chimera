#!/usr/bin/env bash
# all_trademark_scrub.sh
#
# Unified trademark hygiene check for every codename-bearing subcommand.
#
# Runs each per-codename scrub script (mink, otter, ferret, weasel,
# shrew) sequentially, collects pass/fail status, prints an aggregate
# summary, and exits 1 if ANY of the five fail.  Each individual script
# already prints its own detailed failure block; this wrapper exists so
# developers and CI can run a single command and see one verdict.
#
# Usage:
#   bash scripts/all_trademark_scrub.sh
#
# Designed to be run locally and from CI.  Individual scripts are still
# wired as separate CI jobs so a failure in one shows up on the right
# job line.

set -uo pipefail

# Move to the repo root so each child script's `cd` is a no-op.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Codenames in the canonical order.  Add new codenames here as they
# come online (each must ship its own `scripts/<codename>_trademark_scrub.sh`).
CODENAMES=(mink otter ferret weasel shrew stoat badger)

PASSED=()
FAILED=()

for codename in "${CODENAMES[@]}"; do
  script="scripts/${codename}_trademark_scrub.sh"
  if [[ ! -f "${script}" ]]; then
    echo "all trademark scrub: missing ${script}"
    FAILED+=("${codename} (missing script)")
    continue
  fi

  echo "=== ${codename} ==="
  if bash "${script}"; then
    PASSED+=("${codename}")
  else
    FAILED+=("${codename}")
  fi
  echo
done

echo "==============================="
echo "all trademark scrub: SUMMARY"
echo "==============================="
echo "passed: ${#PASSED[@]} (${PASSED[*]:-})"
echo "failed: ${#FAILED[@]} (${FAILED[*]:-})"

if (( ${#FAILED[@]} > 0 )); then
  echo
  echo "all trademark scrub: FAIL"
  exit 1
fi

echo
echo "all trademark scrub: OK"
exit 0
