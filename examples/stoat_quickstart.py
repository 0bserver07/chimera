#!/usr/bin/env python3
"""Quickstart for the ``chimera stoat`` shell-first CLI.

Stoat's headline feature is **shell mode**: each REPL input runs as
``bash -c <input>`` until ``/shell`` (or Ctrl-X) flips back to agent
mode. The REPL is hard to drive non-interactively from a script, so
this example covers the two non-REPL surfaces instead:

1. **One-shot (``-p``)** — agent mode, single turn.
2. **JSON output (``-p --json``)** — machine-readable reply shape
   useful for piping into other tools.

For an interactive transcript walkthrough see the docs (``docs/stoat/``).
We document the limitation here so users aren't surprised when
``--shell-mode`` doesn't show up in this script: shell mode lives on
the REPL only and would require a pty harness to drive.

Skip-conditions:
    * ``chimera`` not on PATH.
    * No provider credential.

Usage:
    python examples/stoat_quickstart.py
    python examples/stoat_quickstart.py --model kimi-k2.6
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys


def _has_credential() -> bool:
    """Return True when at least one supported credential is set."""
    for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "Z_AI_API_KEY"):
        if os.environ.get(var):
            return True
    return False


def demo_one_shot(model: str) -> int:
    """``chimera stoat -p ... --model ...`` — plain text output."""
    cmd = [
        "chimera",
        "stoat",
        "-p",
        "Reply with the single word: pong",
        "--model",
        model,
        "--max-steps",
        "4",
        "--no-rich",
    ]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def demo_json_output(model: str) -> int:
    """``chimera stoat -p ... --json`` — single JSON object on stdout."""
    cmd = [
        "chimera",
        "stoat",
        "-p",
        "Reply with the single word: ack",
        "--model",
        model,
        "--max-steps",
        "4",
        "--json",
    ]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    payload = (proc.stdout or "").strip()
    try:
        parsed = json.loads(payload)
        print(f"[json] keys={sorted(parsed)} success={parsed.get('success')!r}")
    except json.JSONDecodeError:
        print(f"[json] raw stdout (first 200 chars): {payload[:200]!r}")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def main() -> int:
    """Entry point. Returns 0 on success or graceful skip.

    NB: The stoat REPL ``--shell-mode`` is not driven here because
    interactive readline input is hard to script reliably. Run
    ``chimera stoat --shell-mode`` in a real terminal to try it.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="kimi-k2.6")
    args = parser.parse_args()

    if shutil.which("chimera") is None:
        print("skipping: `chimera` CLI not on PATH (run `uv sync`).")
        return 0
    if not _has_credential():
        print("skipping: no provider credential found.")
        return 0

    print("=== stoat demo: one-shot -p ===")
    rc1 = demo_one_shot(args.model)
    print(f"[one-shot] exit={rc1}")

    print("\n=== stoat demo: -p --json ===")
    rc2 = demo_json_output(args.model)
    print(f"[json] exit={rc2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
