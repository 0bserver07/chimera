#!/usr/bin/env python3
"""Quickstart for the ``chimera shrew`` small-models CLI.

Shrew pins three small-model defaults relative to weasel:

* ``--model`` defaults to ``qwen3.6-35b-a3b`` (typically served via a
  local llama.cpp / Ollama daemon).
* ``--max-steps`` defaults to ``30`` (vs ``50`` elsewhere) — small
  models lose context on long horizons.
* ``--allowed-tools`` defaults to ``"Read,Write,Edit,Bash"`` — a
  restricted tool surface so the model picks the right tool more
  often.

Skills are auto-discovered from ``chimera/shrew/skills/`` and the user's
``~/.shrew/skills/`` directory at startup; no flag toggles them. This
script verifies the skill ingest path by running a tiny ``-p`` turn
and printing the ``shrew: skills=N mounted`` stderr line shrew emits.

Skip-conditions:
    * ``chimera`` not on PATH.
    * No Ollama daemon AND no remote credential set.

Usage:
    python examples/shrew_quickstart.py
    python examples/shrew_quickstart.py --model qwen3.6-35b-a3b
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


def _has_credential() -> bool:
    """Return True when at least one supported credential is set."""
    for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(var):
            return True
    return False


def _ollama_alive(host: str = "http://127.0.0.1:11434") -> bool:
    """Return True if a local Ollama daemon is responding."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as resp:  # noqa: S310
            status: int = resp.status
            return status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def demo_list_models() -> int:
    """``--list-models`` is a no-LLM surface — always safe to run."""
    cmd = ["chimera", "shrew", "--list-models"]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    # First few lines only to keep demo output bounded.
    head = "\n".join(proc.stdout.splitlines()[:10])
    print(head)
    return proc.returncode


def demo_print_with_skills(model: str) -> int:
    """One-shot ``-p`` with restricted tool set + auto-mounted skills."""
    cmd = [
        "chimera",
        "shrew",
        "-p",
        "Reply with the single word: pong",
        "--model",
        model,
        "--max-steps",
        "6",
    ]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    sys.stdout.write(proc.stdout)
    # Surface the "skills=N mounted" stderr line so the user sees skill ingest.
    for line in proc.stderr.splitlines():
        if "skills=" in line or "mounted" in line:
            print(f"[stderr] {line}")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def main() -> int:
    """Entry point. Returns 0 on success or graceful skip."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.6-35b-a3b")
    args = parser.parse_args()

    if shutil.which("chimera") is None:
        print("skipping: `chimera` CLI not on PATH (run `uv sync`).")
        return 0

    print("=== shrew demo: --list-models (no LLM call) ===")
    rc1 = demo_list_models()
    print(f"[list-models] exit={rc1}")

    if not _ollama_alive() and not _has_credential():
        print("\nskipping --print demo: no Ollama daemon at 127.0.0.1:11434")
        print("  AND no provider credential set.")
        print("  Start Ollama with `ollama serve` or set ANTHROPIC_AUTH_TOKEN.")
        return 0

    print("\n=== shrew demo: -p with auto-mounted skills ===")
    rc2 = demo_print_with_skills(args.model)
    print(f"[print] exit={rc2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
