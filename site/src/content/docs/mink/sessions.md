---
title: Mink Sessions and Compaction
description: How chimera mink persists conversations so they can be replayed, resumed, and compacted.
---

# Sessions, Resume, and Compaction

Chimera's mink harness persists every conversation so it can be replayed,
forked, branched, undone, and steered — providing ecosystem-parity `/resume`,
`/compact`, and conversation-history features.

This page documents the on-disk layout, the resume contract, and the
auto-compaction story exposed through `chimera mink`.

## Storage Backends

Two interchangeable persistence engines ship in-tree:

| Backend                | Path                              | Layout                | Use when |
| ---------------------- | --------------------------------- | --------------------- | -------- |
| `FileStorage`          | `~/.chimera/sessions/<sid>.json`  | One JSON file per id  | default REPL session save |
| `EventSourcedSession`  | `~/.chimera/eventlog/<sid>/`      | Append-only JSONL log | crash-safe replay, branching |

`FileStorage` is the default the REPL writes after every turn. The event-log
backend is opt-in for users who want crash-recovery (`fsync`-aligned writes,
file-locked appends, gap detection) at the cost of multi-file directories.

`/resume <sid>` inside the REPL — and `chimera mink --resume <sid>` from the
CLI — try the event log first, then fall through to FileStorage. Either
path injects the restored messages back into the live session and rewrites
`session._session_id` so subsequent turns extend the same id.

## Resume Contract

`Session.resume(session_id, agent, storage)` (in `chimera/sessions/session.py`)
returns a fresh `Session` whose `messages` mirror the persisted log. The
caller's `agent` argument is duck-typed: only `agent.prompt` and
`agent.tools` are read while seeding the initial Context. The mink CLI
takes advantage of this by passing a tiny stub agent when re-hydrating
state ahead of an interactive REPL.

`EventSourcedSession.resume(log_dir, session_id, agent)` performs the same
shape but rebuilds state by replaying every event in the JSONL log.

Side-effect: persisted state on disk includes the live TodoTool list when
`TodoTool(persist=True)` is configured, so `/resume` faithfully restores
in-flight task lists alongside the message history. See
`tests/test_resume_persists_todos.py` for the round-trip contract.

## Branching and SessionTree

`chimera/sessions/tree.py` implements an in-place branching model: each
saved entry carries `parent_id`, so forking from any historical point
creates a new leaf without rewriting the past. The REPL exposes this via
the `/tree`, `/branch`, and `/switch` slash commands (see
[slash-commands.md](slash-commands.md)).

Branches are persisted to the same JSONL log; concurrent writers are
file-locked.

## Auto-Compaction

`chimera/compaction/` provides three layered strategies. By default the mink
harness wires `ThresholdCompaction` with a SOFT/HARD pair, plus
`SummaryCompaction` (file-aware) for the actual rewrite:

| Layer                | Behavior                                                  |
| -------------------- | --------------------------------------------------------- |
| `ThresholdCompaction`| Watches token counts; signals SOFT then HARD compaction.  |
| `SummaryCompaction`  | LLM-summarizes pruned segments; preserves file tracker.   |
| `Prune` / `Counter`  | Stateless fallbacks for tests / cheap models.             |

When auto-compaction fires, the message list is rewritten in-place; the
operation is recorded as a single atomic event in the event log so
`/undo` (see `chimera/checkpoints.py`) can roll it back. The
`tests/test_compact_undo_round_trip.py` suite asserts the round-trip.

## CLAUDE.md Memory Survives Compaction

Memory files (loaded by `chimera/context/agent_memory.py`) are wrapped in a
`<memory source="CLAUDE.md">...</memory>` sentinel that
`FileAwareCompaction` is taught to skip. The user-facing effect: `/compact`
never silently drops your project guidance.

## File and Tool Tracking

`chimera/core/file_tracker.py` records every file the agent reads or writes
across the lifetime of a session, even across compaction boundaries. The
list is persisted alongside messages so `/resume` can re-orient an agent
to the same working set.

## Slash Commands at a Glance

| Command           | Effect                                              |
| ----------------- | --------------------------------------------------- |
| `/session`        | Show id and message count for the current session.  |
| `/resume <sid>`   | Replay a stored session (event-log or FileStorage). |
| `/tree`           | Show the branching tree of the current session.     |
| `/branch <name>`  | Fork the current point into a new branch.           |
| `/switch <id>`    | Switch the live session to another branch.          |
| `/compact`        | Force an immediate compaction pass.                 |
| `/checkpoint`     | Manage named checkpoints / `undo`.                  |

## Tests

- `tests/sessions/test_resume_persists_todos.py` — TodoTool state survives `/resume`.
- `tests/sessions/test_compact_undo_round_trip.py` — `/compact` then `/undo` is
  a no-op on the message list.
- `tests/mink/test_mink_cli.py` — REPL slash command smoke tests for
  the mink subcommand.

## Cross-CLI sessions

Mink shares `~/.chimera/eventlog/` with every other Chimera CLI —
`mink-<utc>-<uuid>/` directories sit alongside `otter-`, `ferret-`,
`weasel-`, `shrew-`, `stoat-`, and `badger-` prefixes. Per-CLI
`sessions list` commands default to filtering on their own prefix (the
historic behavior). Pass `--all-clis` to drop the filter and surface a
combined chronological view with an `ORIGIN` column. Every per-CLI
`sessions show <id>` resolves a session by directory name regardless of
origin, so an operator can inspect any CLI's session transcript by id
alone. The shared walker lives at
`chimera/sessions/eventlog/cross_cli.py`.
