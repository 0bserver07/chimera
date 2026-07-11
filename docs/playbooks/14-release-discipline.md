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

## What a release needs (unchanged gates)

- Full suite green locally (excluding the documented live-infra files),
  `ruff`, `mypy`, trademark scrubs, docs-sync.
- README/status refreshed to match reality.
- Publish pipeline per `docs/playbooks/` release notes and
  the release-ops history (tag → CI publish → uvx verification).

## Why this is written down

Version inflation is a ratchet: every unearned bump makes the next one
cheaper, and the number stops meaning anything. Keeping 0.9.x honest is what
makes an eventual 1.0 a signal instead of noise.
