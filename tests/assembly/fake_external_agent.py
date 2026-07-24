"""A scripted stand-in for an external coding-agent CLI (tests only).

Invoked as ``python fake_external_agent.py <mode> [task]`` by the
ExternalAgentDriver tests. Each mode emits a fixed, known output so tests can
assert the exact event mapping without touching a real agent:

- ``stream``       — happy-path stream-json: init, thinking, text, tool_use,
                     tool_result, final text, result (cost/usage/steps);
                     writes ``external.txt`` into the CWD so workspace
                     isolation and diffs are provable.
- ``stream-error`` — a stream-json ``result`` with ``is_error`` and an error
                     subtype, exit code 1.
- ``badjson``      — one non-JSON banner line, then a valid result line.
- ``text``         — plain stdout lines (no JSON), writes ``external.txt``,
                     exit 0.
- ``fail``         — writes to stderr and exits 3 with no result line.
- ``hang``         — prints an init line, then sleeps ~60s (the default
                     SIGTERM disposition ends it; used by cancel/timeout
                     tests).
- ``env``          — stream-json whose text reports ``FAKE_ALLOWED`` /
                     ``FAKE_SECRET`` env values (allowlist tests).
"""
from __future__ import annotations

import json
import os
import sys
import time


def _emit(row: dict) -> None:
    sys.stdout.write(json.dumps(row) + "\n")
    sys.stdout.flush()


def _result_row(**overrides) -> dict:
    row = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1234,
        "num_turns": 2,
        "total_cost_usd": 0.0042,
        "result": "Done — external.txt written.",
        "usage": {"input_tokens": 17, "output_tokens": 22},
    }
    row.update(overrides)
    return row


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "stream"
    task = sys.argv[2] if len(sys.argv) > 2 else ""

    if mode == "stream":
        with open("external.txt", "w", encoding="utf-8") as fh:
            fh.write(f"done: {task}\n")
        _emit({
            "type": "system", "subtype": "init",
            "model": "fake-external-1", "cwd": os.getcwd(), "session_id": "s-1",
        })
        _emit({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 5})
        _emit({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "planning the write"}],
                "usage": {"input_tokens": 7, "output_tokens": 1},
            },
        })
        _emit({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Creating the file now."},
                    {
                        "type": "tool_use", "id": "t1", "name": "Write",
                        "input": {"file_path": "external.txt"},
                    },
                ],
                "usage": {
                    "input_tokens": 7, "cache_read_input_tokens": 100,
                    "output_tokens": 9,
                },
            },
        })
        _emit({"type": "rate_limit_event"})
        _emit({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result", "tool_use_id": "t1",
                    "content": "File created successfully",
                }],
            },
        })
        _emit({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done — external.txt written."}],
                "usage": {"input_tokens": 3, "output_tokens": 12},
            },
        })
        _emit(_result_row())
        return 0

    if mode == "stream-error":
        _emit({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "something broke"}],
                "usage": {"input_tokens": 2, "output_tokens": 2},
            },
        })
        _emit(_result_row(
            subtype="error_during_execution", is_error=True,
            num_turns=1, total_cost_usd=0.001, result="",
        ))
        return 1

    if mode == "badjson":
        sys.stdout.write("warning: fake banner line\n")
        sys.stdout.flush()
        _emit(_result_row(num_turns=1))
        return 0

    if mode == "text":
        with open("external.txt", "w", encoding="utf-8") as fh:
            fh.write(f"done: {task}\n")
        sys.stdout.write("step one\n")
        sys.stdout.flush()
        sys.stdout.write("step two\n")
        sys.stdout.flush()
        return 0

    if mode == "fail":
        sys.stderr.write("boom: credentials missing\n")
        sys.stderr.flush()
        return 3

    if mode == "hang":
        _emit({"type": "system", "subtype": "init", "model": "fake-hang"})
        time.sleep(60)  # SIGTERM (default disposition) ends this early
        return 0

    if mode == "env":
        _emit({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": (
                        f"allowed={os.environ.get('FAKE_ALLOWED', 'unset')} "
                        f"secret={os.environ.get('FAKE_SECRET', 'unset')}"
                    ),
                }],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        })
        _emit(_result_row(num_turns=1, result=""))
        return 0

    sys.stderr.write(f"unknown mode: {mode}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
