# Output Formats

`chimera mink` supports three output formats so the same harness can drive a
human in a terminal, a structured-event consumer in an editor, or a
batch-mode analyzer that wants the whole session as one document.

Selected via `--output-format=text|stream-json|json` (default `text`).
Implementation lives in `chimera/cli/output_format.py`.

| Format        | Shape                                                | Use when |
| ------------- | ---------------------------------------------------- | -------- |
| `text`        | Human-readable TUI (the default REPL output).        | interactive sessions |
| `stream-json` | One JSON object per line (NDJSON), one per event.    | editor/IDE plug-ins, line consumers |
| `json`        | Single aggregated document at session end.           | scripting, batch eval |

`select_handler(format, out=...)` returns the matching `StreamHandler`.

## `stream-json` — NDJSON Event Stream

`StreamJsonHandler` writes one JSON object per `LoopEvent`:

```jsonl
{"type": "agent_start", "ts": 1730000000.001}
{"type": "step", "ts": 1730000000.020, "step_number": 1}
{"type": "text_delta", "ts": 1730000000.041, "content": "Reading "}
{"type": "tool_call", "ts": 1730000000.060, "tool_name": "read_file", "call_id": "call_1"}
{"type": "tool_result", "ts": 1730000000.083, "call_id": "call_1", "output": "..."}
{"type": "agent_end", "ts": 1730000000.991, "success": true, "steps": 1, "total_cost": 0.0021}
```

Each line is independently parseable. Fields after `type` and `ts` mirror
the underlying dataclass (`AgentStartEvent`, `StepEvent`, `TextDeltaEvent`,
`ToolCallEvent`, `ToolResultEvent`, `AgentEndEvent`, …).

Recommended consumer pattern:

```python
import json
import subprocess

proc = subprocess.Popen(
    ["chimera", "mink", "-p", "explain this repo", "--output-format=stream-json"],
    stdout=subprocess.PIPE, text=True,
)
for line in proc.stdout:
    event = json.loads(line)
    if event["type"] == "tool_call":
        print(f"agent called {event['tool_name']}")
```

## `json` — Aggregated Document

`JsonHandler` buffers every event and emits one document at session end:

```json
{
  "events": [{"type": "agent_start", "ts": ...}, ...],
  "result": {"success": true, "steps": 4},
  "cost":   {"total_cost": 0.0083}
}
```

Use this format for batch-mode evaluation where you want a single artifact
per run that you can diff, archive, or feed into a grader.

## Secret Redaction (Both JSON Formats)

Both JSON handlers run every event through
`chimera.secrets.redactor.RedactionMiddleware` before serialization. By
default the registry auto-loads `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and
`GEMINI_API_KEY` from the environment, plus the ten built-in
`SecretDetector` patterns (Bearer tokens, AWS keys, private-key blocks,
etc.). Matched substrings are replaced with `<REDACTED>` in-place.

Pass a custom middleware to redact additional values:

```python
from chimera.cli.output_format import StreamJsonHandler
from chimera.secrets.detector import SecretDetector
from chimera.secrets.redactor import RedactionMiddleware
from chimera.secrets.registry import SecretRegistry

reg = SecretRegistry()
reg.register("internal_token", os.environ["INTERNAL_TOKEN"])
handler = StreamJsonHandler(redaction=RedactionMiddleware(
    registry=reg, detector=SecretDetector(), detect_unknown=True,
))
```

## Programmatic Selection

```python
from chimera.cli.output_format import select_handler

handler = select_handler("stream-json")     # NDJSON to stdout
handler = select_handler("json", out=fh)    # aggregated to file handle
handler = select_handler("text")            # ConsoleStreamHandler
```

Unknown values raise `ValueError`.

## Divergences from the reference implementation

- The reference `--output-format=json` historically nested `result.text`.
  Chimera's `result` block currently exposes only `success` and `steps`;
  the model's final assistant message can be reconstructed by concatenating
  the `text_delta` events. We may revisit if downstream tools demand parity.
- The reference implementation emits a one-line summary on `text` output at
  session end ("…tokens used, $X cost"); Chimera emits the equivalent via
  the `agent_end` event, which the `text` handler already prints.

## Tests

- `tests/cli/test_stream_json_output.py` — five tests covering NDJSON line
  shape, secret redaction, JSON aggregation, and `select_handler`'s
  format dispatch.
