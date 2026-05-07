#!/usr/bin/env python3
"""Quickstart for the ``chimera otter`` multi-session HTTP CLI.

Demonstrates two surfaces:

1. **One-shot**: ``chimera otter -p "<prompt>"`` runs a single turn,
   persists the run to ``~/.chimera/eventlog/``, and exits.
2. **HTTP server**: ``chimera otter serve --port 5173 --auth-token ...``
   spawns the multi-session HTTP server in a subprocess. The script
   then drives it with stdlib ``urllib.request``:
       POST /session                   -> returns ``{id, session_token}``
       POST /session/<id>/message      -> sends a user turn
   The per-session token (B8) is preferred when present; the master
   token is used as a fallback. The server is graceful-stopped via
   ``SIGTERM`` at teardown.

Skip-conditions:
    * No provider credential set: print and exit 0.
    * The ``chimera`` CLI is not on PATH: print and exit 0.

Usage:
    python examples/otter_quickstart.py
    python examples/otter_quickstart.py --port 5173
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _has_credential() -> bool:
    """Return True when at least one supported credential is set."""
    for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "Z_AI_API_KEY"):
        if os.environ.get(var):
            return True
    return False


def _http_post(url: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST JSON to *url* with bearer *token* and return the parsed reply."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — local
        parsed: dict[str, Any] = json.loads(resp.read().decode("utf-8") or "{}")
        return parsed


def demo_one_shot(model: str) -> int:
    """One-shot turn through the otter CLI."""
    cmd = ["chimera", "otter", "-p", "Say hi.", "--model", model, "--max-steps", "4"]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def demo_http_server(port: int, master_token: str, model: str) -> int:
    """Spawn ``chimera otter serve`` and drive it over HTTP."""
    cmd = [
        "chimera",
        "otter",
        "serve",
        "--port",
        str(port),
        "--auth-token",
        master_token,
        "--model",
        model,
    ]
    print(f"$ {' '.join(cmd)} &")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(2)
        # Create a session.
        create = _http_post(
            f"http://127.0.0.1:{port}/session",
            master_token,
            {},
        )
        session_id = create.get("id") or create.get("session_id")
        session_token = create.get("session_token") or master_token
        print(f"[create] id={session_id!r} per_session_token={'yes' if create.get('session_token') else 'no'}")

        # Send one user message.
        reply = _http_post(
            f"http://127.0.0.1:{port}/session/{session_id}/message",
            session_token,
            {"text": "ping"},
        )
        print(f"[message] keys={sorted(reply)[:8]}")
        return 0
    except urllib.error.URLError as exc:
        print(f"[http] skipped: {exc}")
        return 0
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()


def main() -> int:
    """Entry point. Returns 0 on success or graceful skip."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="glm-5")
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()

    if shutil.which("chimera") is None:
        print("skipping: `chimera` CLI not on PATH (run `uv sync`).")
        return 0
    if not _has_credential():
        print("skipping: no provider credential found.")
        return 0

    print("=== otter demo: one-shot -p ===")
    rc1 = demo_one_shot(args.model)
    print(f"[one-shot] exit={rc1}")

    print("\n=== otter demo: HTTP serve ===")
    rc2 = demo_http_server(args.port, "test-token", args.model)
    print(f"[http] exit={rc2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
