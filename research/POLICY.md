# research/ Directory Policy

This directory is **internal handoff scratch space** for agents and humans working on Chimera. It is intentionally excluded from version control (see `.gitignore`). The only file in `research/` that is tracked is this `POLICY.md`. Public, ratified documentation belongs in `docs/`; this tree is for in-flight planning, per-task reports, validation matrices, and wave handoffs that are useful while a wave is active but not part of the project's permanent record.

## Why this policy exists

Without it, `research/` accumulates hundreds of in-flight reports that are noise to anyone outside the active wave. They bloat clones, surface in code search, and create the false impression that internal scratch notes are part of Chimera's supported surface area. The policy keeps the directory useful for collaboration without polluting the public record.

## Core convention

`research/` is **not committed**. Anything written here is ephemeral by default. The `.gitignore` rule `research/*` ignores the contents of the tree, with one exception: `!research/POLICY.md` keeps this file tracked so the policy is discoverable at the repo root.

Existing files that were tracked before this policy was introduced (visible via `git ls-files research/`) remain tracked — git's ignore rules do not retroactively untrack files. Those files are the **ratified historical record**; do not untrack them as a side effect of adopting this policy. New files created from this point forward will not be tracked.

## What lives here

1. **Per-CLI SPEC.md and HANDOFF.md** (long-lived). Each codename (`mink/`, `otter/`, `ferret/`, `weasel/`, `shrew/`, `stoat/`, `badger/`) has a SPEC describing scope and a HANDOFF describing current state. These are continuously updated by the active wave.
2. **Per-task REPORT.md** (rotating). Files like `A1-W11-DOCTOR.md`, `B12-W11-RESUME.md`, `C5-W11-POLICY.md` — one per task per wave. Rotation: archive or delete after 30 days OR when the next major version ships, whichever comes first.
3. **Live verification artifacts** (kept until superseded). Outputs like `I4-LIVE-MATRIX.md`, `G2-DEEPSEEK-LIVE.md`, benchmark feasibility notes. Replace in place when re-run; do not accumulate dated copies.
4. **Wave handoff documents** (short-lived). Files like `wave-11-handoff.md` are the cross-task summary for an in-flight wave. Delete after the wave is validated and shipped.

## What does NOT belong here

- **Published docs** — user-facing guides, tutorials, API references go in `docs/` and are surfaced through the Starlight site.
- **README copy** — the project pitch, install instructions, and quick-start live in `README.md`.
- **Release notes** — version notes go in `docs/releases/` and are part of the public record.
- **Source of truth for any ratified design decision** — once a design is settled, promote the relevant content to `docs/` (see Promotion path below).

## Promotion path

When a research note matures into a stable, generally useful artifact:

1. Copy the content into the appropriate `docs/` location (playbook, guide, reference).
2. Edit the original `research/` file to point at the docs path and note "promoted to docs/...".
3. The `research/` original can then be archived or deleted at the next rotation.

Promoted content is the public-facing version. The `research/` original may keep working notes or follow-up tasks; the `docs/` version should be self-contained.

## Rotation

- **Archive** rather than delete when the content has long-term reference value but is stale: move to `research/_archive/` (also ignored by git). Useful for benchmark snapshots and historical handoffs.
- **Delete** when the content is genuinely transient: per-task REPORT files older than 30 days, wave handoffs after the wave ships, scratch analyses superseded by promoted docs.
- A periodic sweep (once per wave, or once per release) is the right cadence.

## Naming conventions

- Per-task reports: `<TASK_ID>-W<wave>-<NAME>.md`, e.g. `A1-W11-DOCTOR.md`, `C5-W11-POLICY.md`.
- Per-CLI specs: `<codename>/SPEC.md`, `<codename>/HANDOFF.md`, `<codename>/REPORT.md`.
- Live matrices: `<TASK_ID>-LIVE-<NAME>.md` or `<TASK_ID>-VALIDATION.md`.
- Wave handoffs: `wave-<N>-handoff.md`.
- Archive: `research/_archive/<original-path>` (preserve the original filename).

## Verifying the ignore rule

To confirm a new file will not be committed:

```
git check-ignore -v research/<your-file>.md
```

A matching `.gitignore` line means the file is ignored. To confirm the policy file itself is still tracked-eligible, `git status` should list `research/POLICY.md` as a normal file, not under "Ignored files".

## Quickref for agents

If you are an agent writing a report, write to `research/<TASK_ID>-W<wave>-<NAME>.md` and do not `git add` it. The orchestrator and human reviewers will read it from disk; nothing else is required.
