# Security and Trademarks

Badger is modelled on the harness-rewrite tradition — the public
practice of porting an existing agent harness to a new language /
runtime with the explicit goal of "better harness tools, not merely
storing the archive". We must **not** name the upstream by brand in
live source, docs, error messages, or CLI text.

## Banned phrases (live source/docs/tests)

The following phrases are forbidden in `chimera/badger/`,
`docs/badger/`, `tests/badger/`, and the `site/src/content/docs/badger/`
mirror:

- `Claw Code` (any case)
- `claw-code` (any case)
- `clawhip` (any case)

## Allowed: filesystem-fact paths

Bare path mentions are allowed because they describe an existing
on-disk layout we may ingest (settings, history, etc.). The following
patterns are explicitly permitted:

- `~/.claw/`
- `.claw/`
- `~/.claw\b`

These describe **filesystem facts**, not brand claims. They will not
trigger the trademark scrub.

## Allowed: comparative phrasing

When referring to the upstream we use neutral phrasing:

- `badger`
- `the upstream`
- `the harness-rewrite tradition`

## Scrub script

`scripts/badger_trademark_scrub.sh` enforces the rules above. It runs
in CI and locally:

```bash
bash scripts/badger_trademark_scrub.sh
```

Exit 0 = clean. Exit 1 = a banned phrase slipped in. The script grep's
the live tree only — comparative research notes under `research/badger/`
are out of scope (they're allowed to quote the upstream verbatim).

## Why the policy exists

The harness-rewrite tradition explicitly disclaims affiliation with the
upstream. Naming the upstream in live source confuses users about
provenance, exposes the project to needless legal questions, and
distracts from the technique. Keeping the upstream's name out of badger
keeps the focus where it belongs: on the harness.

## Reporting a violation

Open a GitHub issue tagged `trademark-hygiene`. Include the file path
and the offending phrase. We treat reports as low-noise / high-priority
even when the diff is trivial.
