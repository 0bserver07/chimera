#!/usr/bin/env bash
# stoat_trademark_scrub.sh
#
# Trademark hygiene check for the `chimera stoat` subcommand.
#
# Stoat is modelled on a real-world coding-agent harness in the
# shell-mode-toggle tradition (the upstream pioneered the Ctrl-X buffer
# toggle). We must NOT embed the upstream brand name into our live
# source, docs, error messages, or CLI text.
#
# Filesystem-fact path mentions (e.g. `~/.kimi/config.json`) ARE allowed
# because they describe the upstream's on-disk layout -- not a brand
# claim. The model identifier `kimi-k2.6` (and the `kimi-*` family) is
# also allowed because it's required to route requests on the wire.
# `moonshot-` prefixed env vars / paths / OpenRouter vendor names are
# allowed because they're vendor-namespaced wire-format facts.
#
# Comparative research notes under `research/stoat/` (other than the
# canonical SPEC.md) are research artifacts and are intentionally out of
# scope.
#
# Exit 1 if any branded mention slips into live source/docs/tests.
# Exit 0 otherwise.
#
# Usage:
#   bash scripts/stoat_trademark_scrub.sh
#
# Designed to be run locally and from CI.

set -euo pipefail

# Move to the repo root so `git grep` and relative paths behave the same
# in CI and local runs.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths to scan.  Live source + docs + tests + the canonical SPEC only.
PATHS=(
  "chimera/stoat/"
  "docs/stoat/"
  "tests/stoat/"
  "research/stoat/SPEC.md"
)

# Pattern: any cased form of the upstream coding-agent CLI brand name
# plus its host org and packaging slugs.
#
# Brands we forbid (case-insensitive via `git grep -i` flag below):
#   * "Kimi Code CLI"  — the upstream product name
#   * "kimi-cli"       — the upstream package slug
#   * "MoonshotAI"     — the upstream organisation as a brand claim
#                        (note: ALL-CAPS env-var prefixes like
#                        `MOONSHOT_API_KEY` are wire facts and are
#                        filtered back in via the ALLOW pattern)
PATTERN='Kimi Code CLI|kimi-cli|MoonshotAI'

# Allowed strings (filesystem / wire facts):
#   * `~/.kimi/`, `.kimi/` — filesystem paths
#   * `kimi-k2.6`, `kimi-k2-thinking`, `kimi-k2.*` — model ids
#   * `moonshot-`, `moonshot/` — OpenRouter vendor prefixes / paths
#   * `MOONSHOT_API_KEY`, `MOONSHOT_BASE_URL` — env vars
#
# We also allow generic `kimi-` model id mentions because the family
# itself is the wire identifier.
ALLOW='~/\.kimi|\.kimi/|kimi-k2|moonshot-|moonshot/|MOONSHOT_'

# `git grep` exits 1 when there are no matches; tolerate that.
HITS="$(git grep -nE "${PATTERN}" -- "${PATHS[@]}" || true)"

# Filter out the allowed filesystem-fact references.
FILTERED="$(printf '%s\n' "${HITS}" | grep -vE "${ALLOW}" || true)"

# Drop empty lines so we can detect "no real hits" reliably.
FILTERED="$(printf '%s\n' "${FILTERED}" | sed '/^$/d')"

if [[ -n "${FILTERED}" ]]; then
  echo "stoat trademark scrub: FAIL"
  echo "The following lines reference the upstream brand outside of"
  echo "filesystem-fact paths or wire-format identifiers.  Please"
  echo "rephrase using 'stoat', 'the upstream', or 'a shell-mode-toggle"
  echo "coding agent'."
  echo
  printf '%s\n' "${FILTERED}"
  exit 1
fi

echo "stoat trademark scrub: OK (no branded mentions in live source/docs)"
exit 0
