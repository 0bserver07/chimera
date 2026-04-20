# `docs/` — reference + archive

The **user-facing documentation site** lives in `../site/` (Astro + Starlight,
deployed to <https://0bserver07.github.io/chimera/>). Don't put user-facing
docs here — put them under `../site/src/content/docs/`.

This directory is for:

| Subdir | What's here |
|---|---|
| `plans/` | Dated implementation plans (design → execute → close) |
| `playbooks/` | Step-by-step playbooks for internal workflows |
| `superpowers/` | Superpowers/skills plans |
| `specs/` | Design specs for subsystems |
| `benchmarks/` | Benchmark transparency framework + raw data |
| `research/` | Research notes |
| `tutorials/` | Legacy tutorial drafts (most graduated to `site/`) |
| `_archive/` | Pre-migration content preserved for reference |
| root files | Status docs, migration notes, READMEs |

## What was removed (2026-04-19)

99 drifted duplicates were removed from `docs/modules/`, `docs/reference/`,
`docs/guides/`, `docs/workflows/`, `docs/concepts/`, and 2 root files. All
99 had newer, richer equivalents in `site/src/content/docs/` — the `docs/`
copies were pre-migration content that had silently fallen out of sync.

One file, `docs/architecture.md`, had a newer local rewrite (Apr 10, with
a `CodingAgent`-first framing) that the site hadn't picked up. It's
preserved as `_archive/architecture-2026-04-10.md` — merge into the site
version when/if you want that framing live.
