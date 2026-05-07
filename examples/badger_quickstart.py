#!/usr/bin/env python3
"""Quickstart for the ``chimera badger`` strict / parity CLI.

Badger's load-bearing distinctions versus the other CLIs:

* ``--rerun-on-failure`` + ``--max-reruns`` — when the first attempt
  fails (test failures, syntax errors), reset and retry with a
  refined prompt up to N extra times.
* ``parity`` subcommand — diff the live agent's behavior against a
  declared schema (PARITY.md / PARITY.json) and exit non-zero when
  the surface drifts.
* Tighter ``--max-steps`` default (25 vs 50) — encourages rerun
  discipline over long single trajectories.

This script demonstrates two surfaces:

1. **One-shot with rerun**: ``chimera badger -p ... --rerun-on-failure
   --max-reruns 1`` — runs a tiny prompt and shows that rerun wiring
   is plumbed even when the first attempt succeeds.
2. **Parity check**: writes a minimal ``PARITY.json`` to a temp dir and
   runs ``chimera badger parity --against <file> --cwd <dir>`` to show
   the schema-diff path. No LLM is called for parity, so this surface
   always runs.

Skip-conditions:
    * ``chimera`` not on PATH.
    * No provider credential — only the ``-p`` demo is skipped.

Usage:
    python examples/badger_quickstart.py
    python examples/badger_quickstart.py --model claude-sonnet-4-6
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _has_credential() -> bool:
    """Return True when at least one supported credential is set."""
    for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "Z_AI_API_KEY"):
        if os.environ.get(var):
            return True
    return False


def demo_parity() -> int:
    """Write a tiny PARITY.json and run the parity check (no LLM)."""
    with tempfile.TemporaryDirectory(prefix="badger-parity-") as tmp:
        tmp_path = Path(tmp)
        schema = tmp_path / "PARITY.json"
        schema.write_text(json.dumps({"flags": ["--model", "--max-steps"]}))
        cmd = [
            "chimera",
            "badger",
            "parity",
            "--against",
            str(schema),
            "--cwd",
            str(tmp_path),
        ]
        print(f"$ {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        sys.stdout.write(proc.stdout)
        if proc.returncode not in (0, 1):
            sys.stderr.write(proc.stderr)
        return proc.returncode


def demo_rerun(model: str) -> int:
    """``-p`` with ``--rerun-on-failure`` wired (1 retry max)."""
    cmd = [
        "chimera",
        "badger",
        "-p",
        "Reply with the single word: ack",
        "--model",
        model,
        "--rerun-on-failure",
        "--max-reruns",
        "1",
        "--max-steps",
        "6",
        "--no-rich",
    ]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def main() -> int:
    """Entry point. Returns 0 on success or graceful skip."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="claude-sonnet-4-6")
    args = parser.parse_args()

    if shutil.which("chimera") is None:
        print("skipping: `chimera` CLI not on PATH (run `uv sync`).")
        return 0

    print("=== badger demo: parity check (no LLM) ===")
    rc1 = demo_parity()
    print(f"[parity] exit={rc1} (0 = match, 1 = drift, 2 = usage error)")

    if not _has_credential():
        print("\nskipping --rerun-on-failure demo: no provider credential found.")
        return 0

    print("\n=== badger demo: -p --rerun-on-failure ===")
    rc2 = demo_rerun(args.model)
    print(f"[rerun] exit={rc2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
