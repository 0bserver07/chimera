# ATIF v1.7 Trajectory Emission

**Date:** 2026-05-28
**Status:** Proposal — pending schema extraction from Pier source
**Layer:** 4 (Agent) — event consumption
**Team roles:** `researcher` (extract ATIF v1.7 schema from Pier), `executor` (emitter + reader), `reviewer` (interop validation), `planner` (decide field mappings)
**Depends on:** none code-wise; need the ATIF v1.7 schema extracted from a local Pier source checkout
**Unblocks:** Pier ecosystem interop — `pier view` + `pier critique run` consuming Chimera runs, and Chimera consuming Pier-generated trajectories

## Problem

Pier emits ATIF v1.7 (Agent Trajectory Interchange Format). Chimera emits its native event log. Trajectories do not cross frameworks. Pier's `pier view` and `pier critique run` cannot consume Chimera runs; Chimera's analyzers cannot consume Pier's DeepSWE leaderboard trajectories. Two communities, two formats. ATIF v1.7 also has stricter guarantees than Chimera's native log (one step per API turn, strict reasoning vs message separation, no fabricated assistant text, real upstream timestamps, required `peak_context_tokens` / `summarization_count` / `llm_call_count`) — adopting it is both a standards play and a quality win for Chimera's own trajectories.

## What This Enables

- `pier view` opens Chimera-generated trajectories without modification.
- `pier critique run` runs Pier's critique pipeline on Chimera trajectories.
- Chimera can read and analyze Pier-generated trajectories (DeepSWE leaderboard runs, third-party Pier users).
- Trajectories become an interchange format, not a framework-specific artifact.

## ATIF v1.7 Requirements (from Pier README — verify against schema)

- **One step per API turn** — every model invocation maps to exactly one trajectory step.
- **Strict reasoning vs agent message separation** — reasoning content stored in a dedicated field, never inlined into assistant message.
- **No fabricated assistant text** — emitter must not synthesize content the model did not produce.
- **Required fields:** `peak_context_tokens`, `summarization_count`, `llm_call_count`.
- **Real upstream timestamps** — not approximated locally; sourced from provider response headers when available.

## Pre-work: Schema Extraction

Before implementation: clone the Pier source locally, extract the ATIF v1.7 JSON schema from its trajectory module, copy it into `docs/atif/atif-1.7.schema.json` and `chimera/atif/schema.json` so the team has a frozen target before any emitter code is written.

This is a `researcher` task that gates all `executor` work.

## Design Sketch

### ATIFEmitter

```python
class ATIFEmitter:
    """Subscribe to Chimera's EventBus and emit ATIF v1.7 trajectories.

    Event mapping:
        ModelRequest        -> step.input
        ModelResponse       -> step.output (reasoning + message split)
        ToolCall            -> step.tool_calls[]
        ToolResult          -> step.tool_results[]
        Compaction          -> trajectory.summarization_count++
        AgentEnd / TurnEnd  -> trajectory.close()
    """

    def __init__(self, output_path: Path, schema_version: str = "1.7") -> None: ...
    def subscribe(self, bus: EventBus) -> None: ...
    def flush(self) -> None: ...
```

### ATIFReader

```python
class ATIFReader:
    """Parse an ATIF v1.7 trajectory file into Chimera-internal events.

    Useful for consuming Pier-generated trajectories for critique or
    cross-framework analysis.
    """

    def load(self, path: Path) -> list[Event]: ...
    def validate(self, path: Path) -> ValidationResult: ...
```

### ATIFValidator

```python
class ATIFValidator:
    """Validate a trajectory against ATIF v1.7 schema + structural rules.

    Structural rules go beyond JSON schema:
    - one step per API turn (count consistency)
    - no fabricated assistant text (assistant content fields cross-checked
      against tool_calls + reasoning split)
    - timestamps monotonically non-decreasing
    """

    def check(self, trajectory: dict) -> ValidationResult: ...
```

## File Layout

- `chimera/atif/__init__.py`
- `chimera/atif/emitter.py` — `ATIFEmitter`.
- `chimera/atif/reader.py` — `ATIFReader`.
- `chimera/atif/validator.py` — schema + structural checks.
- `chimera/atif/schema.json` — frozen ATIF v1.7 JSON schema (copied from Pier).
- `docs/atif/atif-1.7.schema.json` — public copy of the schema.
- `tests/atif/test_emitter.py` — event-to-record mapping + round-trip.
- `tests/atif/test_reader.py` — parse fixture Pier trajectories.
- `tests/atif/test_validator.py` — structural-rule edge cases.
- `tests/atif/test_interop_live.py` — `pier view` opens a Chimera trajectory; gated on `pier` binary.

## Wiring

- Add `--emit-atif PATH` flag to `chimera bench` (single-agent) and to `chimera bench compare` (per-config trajectory paths).
- When set, instantiate `ATIFEmitter` and subscribe to the session event bus before the agent runs.

## Acceptance Criteria

- [ ] ATIF v1.7 schema extracted from Pier source and frozen into Chimera repo at `chimera/atif/schema.json`.
- [ ] A Chimera trajectory passes `ATIFValidator` (schema + structural rules).
- [ ] `pier view <chimera-trajectory.json>` opens without errors on a real Pier install.
- [ ] `ATIFReader` parses a real Pier-generated DeepSWE trajectory (from a local checkout) without data loss.
- [ ] `peak_context_tokens`, `summarization_count`, `llm_call_count` are accurate, not approximated.
- [ ] Reasoning vs assistant-message separation enforced (a smoke test asserts no reasoning content leaks into `message`).

## Test Strategy

- **Unit:** emitter event-to-record mapping; validator structural rules; reader round-trip.
- **Live:** `pier` binary-gated test that opens a Chimera trajectory in `pier view`.
- **Cross-vendor:** parse a Pier-generated DeepSWE trajectory through `ATIFReader` and round-trip it back through `ATIFEmitter`, asserting byte-equivalence (or semantic equivalence with documented diffs).

## Open Questions

- Whether the schema permits per-tool-call latency fields or only per-step. Resolve during schema extraction.
- How to handle Chimera-specific events that have no ATIF analog (e.g. `Steering`, `Cancellation`). Initial choice: stash them in a `chimera_extensions` namespace inside the trajectory metadata, ignored by Pier.
- Backwards compatibility with future ATIF versions. Initial choice: version-pin to 1.7; introduce 1.8 as a separate spec when Pier publishes it.

## Out of Scope

- Forward-porting older ATIF versions (1.0–1.6).
- Building a Chimera-native trajectory viewer (Pier's `pier view` is the path forward).
- Emitting trajectories for non-Chimera agents (Pier already does that).

## References

- Mission: see `README.md` and `docs/philosophy.md`.
- Ecosystem: Datacurve ships DeepSWE (benchmark) + Pier (CLI-agent runner) + Harbor (task format); Chimera adopts these formats rather than forking them.
- Pier source: a local checkout of the Pier repository.
- Pier README on ATIF: `README.md` in the Pier source checkout.
