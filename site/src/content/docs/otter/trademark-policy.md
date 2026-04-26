---
title: Otter Trademark Policy
description: Canonical trademark/branding policy for the chimera otter CLI and its docs, enforced by scripts/otter_trademark_scrub.sh.
---

# Otter Trademark Policy

## Scope

This policy governs how the `chimera otter` subcommand and its supporting
docs, tests, and CI machinery refer to the real-world server-first coding
agent that otter is modelled on.

Otter is **not** a fork, port, or rebrand of any upstream project. It is a
fresh implementation built from existing Chimera primitives that happens to
be shaped like that upstream so that users coming from there feel at home.

## The rule

In **live source, docs, error messages, and CLI text**, do not name the
upstream by its brand. Use one of:

- `otter` -- our subcommand
- "the upstream"
- "the open-source coding agent"

Comparative analysis under `research/otter/` -- other than the canonical
`SPEC.md` -- is fair-use research notes and is **out of scope** for the
scrub.

## Allowed exception: filesystem-fact paths

`~/.opencode/config.json`, `.opencode/agent/`, `.opencode/command/*.md`,
and similar dotted-directory references are allowed because they describe
an on-disk layout we ingest. They are filesystem facts, not brand claims.

The scrub script implements this exception by filtering matches that
contain `~/.opencode`, `.opencode/`, or a bare `.opencode` directory
reference.

## How we enforce it

Two layers:

1. **Local + CI script:** `scripts/otter_trademark_scrub.sh` runs
   `git grep -nE 'OpenCode|opencode\.ai|opencode-ai'` over the live tree
   (`chimera/otter/`, `docs/otter/`, `tests/otter/`,
   `research/otter/SPEC.md`, `README.md`), filters allowed filesystem
   paths, and exits 1 on any remaining hit.
2. **CI job:** `.github/workflows/ci.yml` registers a job
   `otter-trademark-scrub` that runs the script on every push and pull
   request.

## When the scrub fires

You'll see output like:

```
otter trademark scrub: FAIL
chimera/otter/cli.py:42:    print("compatible with OpenCode v1.x")
```

Rewrite the offending line to use `otter`, "the upstream", or
"the open-source coding agent". Re-run:

```bash
bash scripts/otter_trademark_scrub.sh
```

A `: OK` exit means the live tree is clean.

## Rationale

- Avoids implying endorsement, partnership, or derivation.
- Keeps our marketing surface honest: otter is a Chimera composition,
  not a re-skin.
- Forces us to describe behaviour in our own words, which surfaces
  divergences early and improves docs.
- Makes the live tree linkable from external write-ups without legal
  risk.

## Adding new paths

If a new live-source directory under otter lands (for example
`examples/otter/` or `chimera/otter/<subpkg>/`), add it to the `PATHS`
array in `scripts/otter_trademark_scrub.sh`. Research notes under
`research/otter/` should not be added.

## Adding new banned terms

If we discover an additional brand string that shouldn't leak in (for
example a product slug), add it to the `PATTERN` regex in the same
script. Keep the pattern an alternation so future additions stay
readable.
