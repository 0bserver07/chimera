# Playbook 14 — Release discipline: don't inflate the version

The version number is a **claim about maturity**, not a changelog counter.
Chimera's policy, set explicitly by the project owner:

## The rules

1. **Stay in 0.9.x for a long time.** March through 0.9.1, 0.9.2, … 0.9.N —
   patch bumps only. Feature accumulation does not justify a minor bump.
2. **1.0 is reserved for a major breakthrough.** Not "lots of good releases" —
   a capability step-change that redefines what the framework is. Default
   answer to "is this 1.0?" is *no*.
3. **Slow the cadence.** Not every improvement needs a release. Batch changes,
   let them settle on master, release when a coherent story has accumulated.
   Shipping is a deliberate act, not a reflex after each merged branch.
4. **A spec is not a feature.** `docs/specs/*.md` files are plans with zero
   code. Release notes, docs, and status reports say "designed / spec'd" until
   the code exists, is tested, and has been exercised against a real model.
5. **No inflated claims anywhere the version travels.** Release notes cite
   verified numbers (test counts, benchmark scores with their `data/*.json`
   provenance) — the same standard as the benchmark scorecards.

## The changelog habit

`CHANGELOG.md` is where batching becomes visible. The mechanics:

1. **Entries land with the work.** When a batch of commits merges to master,
   append curated entries (with commit shas as receipts) to the
   `## Unreleased` section — grouped Added / Fixed / Changed / Deprecated.
   Curate, don't enumerate: the changelog tells the story; `git log` holds
   the inventory.
2. **Batches get names.** Every released version block is
   `## X.Y.Z — YYYY-MM-DD — <name>` where the name states the theme
   ("the TUI multiplexer", "the daily driver"). A proposed name may sit in
   the Unreleased section as a comment until release time.
3. **A release = roll the accumulator.** Cutting X.Y.Z renames `Unreleased`
   to the versioned, dated, named block and starts a fresh empty
   `Unreleased`. No release without the roll; no roll without the release.
4. **Same truth standard as the scorecards.** Numbers in changelog entries
   (test counts, benchmark scores) must trace to command output or
   `data/*.json` — an entry nobody can verify doesn't go in.

## What a release needs (unchanged gates)

- Full suite green locally (excluding the documented live-infra files),
  `ruff`, `mypy`, trademark scrubs, docs-sync.
- **CI-posture gate** (`bash scripts/ci_posture_check.sh`): CI installs no
  `tui` extra — replicate its exact env + cold-cache mypy + its pytest
  invocation locally before the push. Added after two red CI runs on the
  0.9.1 batch proved local-green ≠ CI-green.
- README/status refreshed to match reality.
- Publish pipeline per `docs/playbooks/` release notes and
  the release-ops history (tag → CI publish → uvx verification).

## Why this is written down

Version inflation is a ratchet: every unearned bump makes the next one
cheaper, and the number stops meaning anything. Keeping 0.9.x honest is what
makes an eventual 1.0 a signal instead of noise.
