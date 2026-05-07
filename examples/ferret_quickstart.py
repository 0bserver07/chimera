#!/usr/bin/env python3
"""Quickstart for the ``chimera ferret`` sandbox-first CLI.

Demonstrates the two ferret-only knobs that distinguish it from otter:

* ``--sandbox {read-only|workspace-write|workspace-write-network}``
* ``--approval {read-only|auto|full}``

Both default to the safest setting (``read-only``) — opting up requires
explicit selection. This script runs a one-shot read-only listing and
then a workspace-write demo that invokes a no-op ``echo`` command, so
the example is safe to run in any project tree without producing
side effects.

Skip-conditions:
    * No ``chimera`` on PATH.
    * No provider credential.

Usage:
    python examples/ferret_quickstart.py
    python examples/ferret_quickstart.py --model gpt-5
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def _has_credential() -> bool:
    """Return True when at least one supported credential is set."""
    for var in (
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "Z_AI_API_KEY",
    ):
        if os.environ.get(var):
            return True
    return False


def demo_read_only(model: str) -> int:
    """Sandbox-first: list files with the safest defaults."""
    cmd = [
        "chimera",
        "ferret",
        "-p",
        "List the top-level files in this directory.",
        "--sandbox",
        "read-only",
        "--approval",
        "read-only",
        "--model",
        model,
        "--max-steps",
        "6",
        "--no-rich",
    ]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def demo_workspace_write(model: str) -> int:
    """Opt up to workspace-write + auto approval (still no network)."""
    cmd = [
        "chimera",
        "ferret",
        "-p",
        "Run a no-op shell command (echo OK) and report its output.",
        "--sandbox",
        "workspace-write",
        "--approval",
        "auto",
        "--model",
        model,
        "--max-steps",
        "6",
        "--no-rich",
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
    parser.add_argument("--model", default="gpt-5")
    args = parser.parse_args()

    if shutil.which("chimera") is None:
        print("skipping: `chimera` CLI not on PATH (run `uv sync`).")
        return 0
    if not _has_credential():
        print("skipping: no provider credential found.")
        return 0

    print("=== ferret demo: sandbox=read-only, approval=read-only ===")
    rc1 = demo_read_only(args.model)
    print(f"[read-only] exit={rc1}")

    print("\n=== ferret demo: sandbox=workspace-write, approval=auto ===")
    rc2 = demo_workspace_write(args.model)
    print(f"[workspace-write] exit={rc2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
