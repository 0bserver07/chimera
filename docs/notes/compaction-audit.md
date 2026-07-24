# Compaction & session-log audit (Tier-2, verify-first)

A verify-first audit of three durable-log properties: **audit → probe → act
only on proven gaps.** Each property below was demonstrated by a runnable probe
before any code was written or any test was pinned. Two properties already
held (now regression-locked); one had a proven gap and a named absence (now
enriched, minimally and additively).

Scope note: Chimera has two durable session journals. The **`SessionTree`**
(`chimera/sessions/tree.py`) is the branchable JSONL tree used by
`chimera code` / `chimera mink`; the **`EventLog`**
(`chimera/sessions/eventlog/log.py`) is the append-only per-event journal
behind `EventSourcedSession`. Both are append-only. The in-memory `Context`
compaction on `Session` is a separate, ephemeral view (see P1). This audit
centers on `SessionTree` (where reversibility, iterative summary, and typed
entries all live) and notes the `EventLog` and in-memory paths where relevant.

## Verdict table

| # | Property | Verdict | Receipt (file:line) | Pinned by |
|---|----------|---------|---------------------|-----------|
| P1 | Reversible compaction — compaction *appends* a boundary; pre-compaction history stays reachable (fork/switch) | **HOLDS** | `tree.py:143` (`add_compaction` appends, `parent_id=active_leaf`); `tree.py:324` (append-mode write); `tree.py:276`/`:295` (`fork`/`switch_branch`) | `tests/sessions/test_compaction_audit.py::TestReversibleCompaction` (3) |
| P2 | Iterative summary merge — re-compacting an already-compacted session feeds the prior summary to the summarizer | **HOLDS** | `tree.py:214` (`get_messages` renders a prior `CompactionEntry` as a message); `summary.py:41`+`:87` (prior summary falls into `middle`, reaches the prompt) | `tests/sessions/test_compaction_audit.py::TestIterativeSummaryMerge` (2) |
| P3 | Typed non-message log entries — a true event store, not message-centric | **ENRICHED** | `tree.py:17-118` (typed `SessionHeader`/`Message`/`Compaction`/`Label`/generic entries); gap: generic `data` dropped on serialize + no model/thinking entry | `tests/sessions/test_compaction_audit.py::TestTypedNonMessageEntries` (4) |

Net: **2 HOLDS + 1 ENRICHED.** Source change is additive and confined to
`chimera/sessions/tree.py`; 9 hermetic tests added.

---

## P1 — Reversible compaction · HOLDS

**Claim.** After a compaction, the pre-compaction history is still on disk and
navigable: a branch can fork/switch back to the pre-compaction node and recover
the full raw history, with the summary absent.

**Receipts.**

- `SessionTree.add_compaction` (`tree.py:143`) builds a `CompactionEntry` with
  `parent_id=self._active_leaf` and calls `append` — it **adds a child**; it
  never removes, rewrites, or truncates prior entries.
- `_append_to_file` (`tree.py:324`) opens the JSONL in **append mode** (`"a"`)
  and writes one line — the existing file bytes are never rewritten.
- `fork` (`tree.py:276`) and `switch_branch` (`tree.py:295`) reposition
  `_active_leaf` to **any** entry id, including a pre-compaction message entry.
  `get_branch`/`get_messages` (`tree.py:189`/`:207`) then reconstruct that
  leaf's ancestor chain. Because the compaction entry is a *child* of the
  pre-compaction leaf (not an ancestor), reverting to that leaf yields the raw
  history with **no** summary.

**Probe evidence** (pre-change):

```
on-disk lines before compaction: 3
on-disk lines after compaction : 4   (append-only: grew, none removed)
messages after switch back to pre-compaction leaf: ['q1', 'a1', 'q2']
summary present in the reverted branch? False
reloaded entry_count: 4
compaction entry still reachable from its leaf: True
```

**Related, unchanged.** The `EventLog` journal is likewise strictly
append-only — `append` writes a new immutable file per event and never deletes
(`eventlog/log.py:87-113`), and `EventSourcedSession.resume_from(up_to_index)`
reconstructs any earlier prefix by index. The one place that *rewrites* is the
in-memory `Session._maybe_compact`, which reassigns `self._context._messages`
(`session.py:276`) — but that is the **ephemeral** in-process view, not a
durable journal, and the tree still holds every message entry appended by
`chat` (`session.py:131-137`). See deferred follow-up (1).

**Pinned by** `TestReversibleCompaction`:
`test_add_compaction_is_append_only_on_disk` (the whole prior file is an
untouched prefix; line count grows by exactly one),
`test_fork_back_recovers_full_history_without_summary`, and
`test_pre_compaction_history_survives_reload`.

---

## P2 — Iterative summary merge · HOLDS

**Claim.** Compacting a session that was already compacted feeds the prior
summary back into the summarizer, rather than dropping it or stacking
independent summaries.

**Receipts (two paths).**

- `SessionTree.summarize_branch` (`tree.py:219`) collects the branch via
  `get_messages` (`tree.py:207`), which renders a prior `CompactionEntry` as a
  reconstructed message — `Message.user("[Session compacted. Summary: …]")`
  (`tree.py:214-216`). So a second `summarize_branch` hands the prior summary
  (plus the intervening turns) to the injected summarizer.
- `SummaryCompaction.compact` (`summary.py:32`) keeps `keep_first` head +
  `keep_last` tail and summarizes the `middle` (`summary.py:41-45`). The summary
  from the first pass is a `system` message that sits at index `keep_first`
  (`summary.py:48-51`), so on the second pass it falls **inside** `middle` and
  reaches the summarization prompt (`summary.py:84-87`).

**Probe evidence** (pre-change):

```
after 1st compaction, middle element (summary) role/prefix: system '[Compacted 18 messages]\n…'
provider called 2 times
2nd summarization prompt contains prior summary marker '[Compacted'? True
2nd summarize_branch input: ['hello', 'hi', '[Session compacted. Summary: S1]', 'more']
prior summary S1 marker present in 2nd input? True
```

**Caveat (see deferred follow-up 2).** This is "feed", not a bespoke
incremental merge: `SummaryCompaction._summarize_with_provider` truncates each
`middle` message — including the prior summary — to 200 chars (`summary.py:87`),
which can erode a long running summary across repeated compactions. A dedicated
"update this running summary with these new turns" prompt would be
higher-fidelity but is a summarizer redesign, out of scope for [S].

**Pinned by** `TestIterativeSummaryMerge`:
`test_summary_compaction_feeds_prior_summary_on_recompaction` (spy provider
captures the 2nd prompt; asserts the prior `[Compacted` marker is present) and
`test_summarize_branch_feeds_prior_summary_on_recompaction` (capturing
summarizer; asserts the first summary appears in the 2nd input).

---

## P3 — Typed non-message log entries · ENRICHED

**Claim to test.** Is the log a true typed event store, or message-centric?

**Finding.** It is a **typed event store**, not message-centric. `tree.py`
already defines typed entries beyond messages: `SessionHeader` (version / cwd /
system prompt), `CompactionEntry` (the compaction boundary), `LabelEntry`
(labels / bookmarks), and a generic `SessionEntry` (arbitrary `type` +
`data`) — union'd as `AnyEntry`. So the base property **holds**. But the probe
found a **proven gap** and a **named absence**:

1. **Proven bug — generic extension payloads were dropped on serialize.**
   `_serialize` (`tree.py:329`) had branches only for Message / Compaction /
   Header / Label; a bare `SessionEntry` fell through with only its base fields,
   so `entry.data` was never written. On reload the payload came back empty:

   ```
   reloaded custom entry data: {'type': 'model_change', 'id': 'custom1', 'parent_id': '…', 'timestamp': 1.0}
   custom payload survived reload? False
   ```

   So "extension/custom state" was not actually a faithful round-trip.

2. **Named absence — no first-class model-change / thinking-change entry.**
   Those scalar control-plane changes had no typed entry; they could only ride
   in the (broken) generic entry.

**Enrichment (additive, `chimera/sessions/tree.py` only).**

- **`StateChangeEntry`** (`tree.py:70`) + **`SessionTree.add_state_change(kind,
  value)`** (`tree.py:166`): records model / thinking-level swaps as a
  first-class typed non-message entry (`kind="model"`, `value="glm-5"`, etc.),
  wired through `_serialize`/`_deserialize` and the `AnyEntry` union. It is
  skipped by `get_messages` (like `LabelEntry`), so it never pollutes the
  reconstructed conversation, yet it persists and navigates like any entry.
- **Generic round-trip fix** (`_serialize`/`_deserialize`): a bare
  `SessionEntry` now serializes its payload under a nested `data` key, and the
  loader reads it back — falling back to the whole record for older/foreign
  logs, so existing files still load. Extension/custom-state is now faithful.

**Probe evidence** (post-change):

```
StateChangeEntry type/kind/value: StateChangeEntry model glm-5
custom entry type/data: custom_x {'k': 'v', 'n': 3}
custom payload survived reload? True
messages (should be just hello): ['hello']
```

**Why not more.** The log was never message-centric, so the task's
"add-if-message-centric" trigger was not the operative one; the operative gaps
were the serialization bug (fixed) and the named absence (added). Adding
`StateChangeEntry` mirrors the module's existing pattern of shipping entry
primitives ahead of their callers (`LabelEntry`/`add_label` is likewise only
exercised by tests today), so it is consistent rather than speculative.

**Pinned by** `TestTypedNonMessageEntries`: `test_state_change_entry_round_trips`,
`test_state_change_entry_excluded_from_messages`,
`test_generic_custom_entry_payload_round_trips` (the bug fix), and
`test_all_entry_kinds_coexist_and_round_trip` (header + message + compaction +
label + state-change + generic on one branch, each keeping its type through a
reload — the "true typed event store" proof).

---

## Deferred follow-ups (out of scope for this [S] task)

1. **Journal the in-memory `Session` compaction as a tree boundary.**
   `Session._maybe_compact` (`session.py:261-276`) rewrites the ephemeral
   `Context` but does not append a `CompactionEntry` to an attached
   `SessionTree` at the compaction point. No history is lost (the tree already
   holds every `add_message`), but there is no explicit boundary marker for that
   auto-compaction, and the rewrite-vs-append policy for the live context is a
   behavioral decision. **Rationale to defer:** touches the live chat loop and
   its compaction policy — a behavioral change, not an additive [S] pin.

2. **True incremental summary merge for `SummaryCompaction`.** Replace
   "re-summarize an excerpt that happens to include the prior summary (truncated
   to 200 chars, `summary.py:87`)" with a dedicated "update this running summary
   with these new turns" prompt, so a long running summary is not eroded across
   repeated compactions. **Rationale to defer:** a summarizer-prompt redesign
   with model-quality implications, beyond a structural [S] pin.

3. **Wire `add_state_change` into the REPL / driver.** The typed entry now
   exists and round-trips, but no `/model` command or driver thinking-level
   change emits it yet (same wiring status as `add_label`). **Rationale to
   defer:** needs the `chimera/cli/code.py` + `AgentDriver` seam and is a
   natural next step once a UI surface consumes the entries.
