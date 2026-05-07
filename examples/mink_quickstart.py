#!/usr/bin/env python3
"""Quickstart for the ``chimera mink`` TUI-first CLI.

Drives ``chimera mink`` end-to-end through ``subprocess.run`` so the
example mirrors what a shell user would actually type. Covers two
canonical surfaces:

1. **One-shot**: ``chimera mink -p "<prompt>" --model glm-5`` runs a
   single turn, prints the answer, and persists the run under
   ``~/.chimera/eventlog/`` (default behavior).
2. **Runs introspection**: ``chimera mink runs list`` enumerates every
   persisted mink run so users can resume / share / inspect them.

Skip-conditions:
    * No GLM-5 credential (``ANTHROPIC_AUTH_TOKEN`` or ``Z_AI_API_KEY``)
      AND no ``CHIMERA_MINK_MODEL`` override pointing at Ollama-cloud.
      We print a friendly message and exit 0.

Usage:
    python examples/mink_quickstart.py
    python examples/mink_quickstart.py --model kimi-k2.6:cloud
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


def demo_one_shot(model: str) -> int:
    """Run ``chimera mink -p ... --model ...`` and stream stdout."""
    cmd = [
        "chimera",
        "mink",
        "-p",
        "Add a one-line docstring to a hypothetical fibonacci function.",
        "--model",
        model,
        "--max-steps",
        "8",
        "--no-rich",
    ]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def demo_runs_list() -> int:
    """List persisted mink runs (no LLM call required)."""
    cmd = ["chimera", "mink", "runs", "list", "--limit", "5"]
    print(f"\n$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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

    if not _has_credential():
        print("skipping: no provider credential found.")
        print("  Set ANTHROPIC_AUTH_TOKEN, Z_AI_API_KEY, or ANTHROPIC_API_KEY.")
        return 0

    print("=== mink demo: one-shot -p ===")
    rc1 = demo_one_shot(args.model)
    print(f"[one-shot] exit={rc1}")

    print("\n=== mink demo: runs list ===")
    rc2 = demo_runs_list()
    print(f"[runs list] exit={rc2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
