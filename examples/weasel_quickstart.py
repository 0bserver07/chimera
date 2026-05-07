#!/usr/bin/env python3
"""Quickstart for the ``chimera weasel`` RPC + SDK CLI.

Demonstrates the four canonical modes weasel exposes via its
``--mode`` flag:

* ``interactive`` — REPL (covered by other docs; we don't drive it
  here because it requires a TTY).
* ``print`` — one-shot ``-p`` text output.
* ``rpc`` — JSON-RPC stdio server (paired with the much fuller
  ``examples/weasel_live_smoke.py``).
* ``sdk`` — prints the Python embedding pointer and exits (no LLM
  required, so this surface always runs).

For an in-process embedding tour see
:mod:`examples.weasel_sdk_quickstart`. For a full live smoke including
the RPC server see :mod:`examples.weasel_live_smoke`.

Skip-conditions:
    * The ``--print`` demo is skipped when no provider credential is
      set; the ``--mode sdk`` demo always runs.

Usage:
    python examples/weasel_quickstart.py
"""
from __future__ import annotations

import argparse
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


def demo_sdk_pointer() -> int:
    """``--mode sdk`` always runs: prints embedding pointer, exits 0."""
    cmd = ["chimera", "weasel", "--mode", "sdk"]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def demo_print(model: str) -> int:
    """``-p`` one-shot — requires a credential."""
    cmd = [
        "chimera",
        "weasel",
        "-p",
        "Reply with the single word: pong",
        "--model",
        model,
        "--max-steps",
        "4",
    ]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def main() -> int:
    """Entry point. Returns 0 on success or graceful skip."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="glm-5")
    args = parser.parse_args()

    if shutil.which("chimera") is None:
        print("skipping: `chimera` CLI not on PATH (run `uv sync`).")
        return 0

    print("=== weasel demo: --mode sdk (no LLM call) ===")
    rc1 = demo_sdk_pointer()
    print(f"[sdk] exit={rc1}")

    if not _has_credential():
        print("\nskipping --print demo: no provider credential found.")
        print("See examples/weasel_sdk_quickstart.py for in-process embedding.")
        print("See examples/weasel_live_smoke.py for the full RPC test.")
        return 0

    print("\n=== weasel demo: --print one-shot ===")
    rc2 = demo_print(args.model)
    print(f"[print] exit={rc2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
