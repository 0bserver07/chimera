"""Faithful protocol-mock external agent for the chimera-team MCP server.

Spawns chimera-team-mcp as a stdio subprocess, performs the MCP handshake,
drains its mailbox, auto-claims one open task, "does the work" (just echoes
the task description), and completes the task. Used by verify_integration.py
to stand in for Codex / OpenCode / a real MCP host.

The teammate runner sets CHIMERA_TEAM, CHIMERA_AGENT, CHIMERA_TEAMS_HOME,
and CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1 in our process env before invoking
us, so we just forward our environment to the MCP server child.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REQUEST_ID = 0


def _send(proc: subprocess.Popen, method: str, params: dict | None = None) -> dict:
    """Send a JSON-RPC request, return the parsed response dict."""
    global REQUEST_ID
    REQUEST_ID += 1
    msg = {"jsonrpc": "2.0", "id": REQUEST_ID, "method": method, "params": params or {}}
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError(f"chimera-team-mcp closed stdout before responding to {method}")
    return json.loads(line)


def _tool(proc: subprocess.Popen, name: str, args: dict | None = None) -> str:
    """Call a tool by name; return the text content of the first result block."""
    resp = _send(proc, "tools/call", {"name": name, "arguments": args or {}})
    result = resp.get("result", {})
    content = result.get("content", [])
    if content and isinstance(content[0], dict):
        return str(content[0].get("text", ""))
    return ""


def main() -> int:
    server_cmd = [sys.executable, "-m", "chimera.mcp_servers.team_server"]
    proc = subprocess.Popen(
        server_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        env=os.environ.copy(),
        text=True,
        bufsize=1,
    )
    try:
        _send(proc, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        _tool(proc, "team_recv_messages", {"drain": True})
        claim = _tool(proc, "team_claim_task", {})
        try:
            payload = json.loads(claim)
        except json.JSONDecodeError:
            return 0
        if not payload.get("claimed"):
            return 0
        task_id = payload["task_id"]
        description = payload.get("description", "")
        _tool(proc, "team_complete_task", {"task_id": task_id, "result": f"echo: {description}"})
        return 0
    finally:
        try:
            assert proc.stdin is not None
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=3)


if __name__ == "__main__":
    sys.exit(main())
