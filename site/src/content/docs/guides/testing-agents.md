---
title: "Testing Agents Hermetically"
description: "Script agent turns through the real loop with chimera.testing: deterministic, offline regression tests over the faux provider — while real-LLM validation stays the bar for done."
---

# Testing agents hermetically

`chimera.testing` runs scripted agent turns through the **real** loop — real
`AgentLoop`, real tools executing in a throwaway workspace, real cost
accounting — with only the model replaced by the deterministic faux provider
(`chimera/providers/faux.py`). No network, no API key, no mocks of the loop
itself.

## The contract (non-negotiable)

- **Hermetic tests are for regressions and unit-level loop behavior.** They
  are fast (the whole suite runs in well under a second), deterministic, and
  safe to run on every commit.
- **Real-LLM validation remains the bar for "done."** A scripted provider can
  prove the loop's plumbing; it cannot prove a feature works with an actual
  model. Nothing verified only against the harness may be reported as done —
  run it against a real LLM first. This is a standing repo rule, not a
  preference.

## Quick start

```python
from chimera.testing import create_harness

def test_agent_writes_the_file(tmp_path):
    harness = create_harness(
        turns=[
            {"text": "writing", "tool_calls": [
                {"name": "write_file",
                 "arguments": {"path": "hello.txt", "content": "hi"}},
            ]},
            {"text": "done"},
        ],
        workspace=tmp_path,
    )
    run = harness.run("create hello.txt")

    assert run.reason == "completed"
    assert run.files_created == ["hello.txt"]          # tool really executed
    assert (tmp_path / "hello.txt").read_text() == "hi"
    assert run.output_text == "done"
```

Each entry in `turns` is one provider completion, played in order. When the
script runs out, the provider returns an empty tool-less completion so the
loop terminates cleanly.

## Script grammar

| Step | Meaning |
| --- | --- |
| `{"text": "…"}` | Assistant text; no tool calls ends the loop. |
| `{"text": "…", "tool_calls": [{"name": "bash", "arguments": {…}}]}` | Tool-calling turn; the named tools execute **for real** in the workspace. |
| `{"thinking": "…"}` or `{"thinking": ["chunk1", "chunk2"]}` | Scripted reasoning; with `stream=True` each chunk arrives as a `thinking_chunk` event. |
| `{"usage": {"input_tokens": N, "output_tokens": M}}` | Pins exact token usage so cost/budget paths are testable (`model="glm-5.2"` gives priced, non-zero cost). |
| `{"error": "…"}` | The provider raises `FauxProviderError` — error-path injection. |

## What a run gives you

`harness.run(prompt)` returns a `HarnessRun`:

- `events` / `event_types` / `events_of(type)` — the ordered `LoopEvent`
  stream, exactly as a TUI would receive it.
- `reason` — terminal reason: `completed`, `max_turns`, `aborted_*`,
  `loop_detected`, or `error` when the loop raised (`run.error` holds the
  exception).
- `output_text`, `streamed_text`, `thinking_chunks`, `messages`.
- `tool_calls` / `tool_results` — what the model asked for and what actually
  happened (malformed arguments and unknown tools surface as error results,
  as in production).
- `files_created` / `files_modified` / `files_deleted` — workspace diff.
- `usage` / `cost_usd` / `turn_count` — the loop's own accounting.

Multi-turn: calling `run()` again continues the same conversation, like a
REPL turn (`harness.history` carries between calls).

## Steering, cancellation, and the assembled path

`on_event` fires after each event while the loop is suspended, so mid-stream
injection is deterministic:

```python
def steer_after_first_tool(ev):
    if ev.type == LoopEventType.tool_result:
        harness.steer("also update the changelog")   # or harness.abort()

run = harness.run("start", on_event=steer_after_first_tool)
```

`create_assembled_harness(...)` gives the same surface over the assembled
stack (`AgentDriver` → `CodingAgent` → `AgentLoop`), so preset wiring —
tool sets, prompt assembly, streaming posture, history persistence — is part
of what the test exercises.

## Regression-test convention

Shipped bugs become locks in `tests/regressions/`, one test per bug, named
for the commit (or issue) that fixed it, with the original failure mode in
the docstring:

```python
def test_eb87310_budget_records_cost_on_async_complete_path(tmp_path):
    """eb87310: max_cost never tripped for assembled agents (async path)…"""
```

Before trusting a new lock, verify it detects the bug: check out the pre-fix
version of the fixed module, watch the test fail, restore. A regression test
that never failed proves nothing.
