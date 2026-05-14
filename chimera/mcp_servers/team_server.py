#!/usr/bin/env python3
"""MCP server exposing Chimera's experimental agent-team coordinator.

Wraps the :class:`chimera.cli.agent_teams.Team` and
:class:`chimera.cli.agent_teams.TeamMailbox` primitives as MCP tools so any
compliant MCP host can drive shared task queues and per-agent mailboxes
over JSON-RPC.

Exposed tools:

- ``team_init`` -- create / initialise a team directory
- ``team_join`` -- add an agent id to the team's member list
- ``team_list_members`` -- list current members
- ``team_add_task`` -- append a new task (lead only when role-gated)
- ``team_list_tasks`` -- list tasks filtered by status
- ``team_claim_task`` -- claim a specific task, or auto-claim the next unblocked one
- ``team_release_task`` -- release a previously-claimed task back to ``open``
- ``team_complete_task`` -- mark a claimed task as completed
- ``team_send_message`` -- deliver a message to another agent's mailbox
- ``team_recv_messages`` -- drain the current agent's mailbox

Role gating:

The server can be started with ``--role lead`` or ``--role teammate``
(default: ``teammate``). ``CHIMERA_ROLE`` is honoured as a fallback. When
running as a teammate, ``team_add_task`` returns an ``isError`` response
and refuses to create new tasks; every other tool is unchanged.

Usage::

    python -m chimera.mcp_servers.team_server --role lead --team alpha
    # or
    python chimera/mcp_servers/team_server.py --role teammate

Configure in ``.mcp.json`` for any compatible MCP host::

    {
      "mcpServers": {
        "chimera-team-mcp": {
          "command": "python3",
          "args": [
            "chimera/mcp_servers/team_server.py",
            "--role", "teammate",
            "--team", "alpha"
          ]
        }
      }
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from chimera.cli.agent_teams import Team, TeamMailbox, create_team, join_team

__all__ = ["TeamMCPServer", "main"]


# -- Server metadata -------------------------------------------------------

SERVER_NAME = "chimera-team-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

VALID_ROLES = {"lead", "teammate"}
DEFAULT_ROLE = "teammate"


# -- Tool definitions ------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "team_init",
        "description": (
            "Initialise (or open) a team directory. Idempotent: re-running "
            "with the same name is safe. Returns the team name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
                "default_model": {
                    "type": "string",
                    "description": "Default model id stored in the team config.",
                    "default": "kimi-k2.6",
                },
            },
            "required": [],
        },
    },
    {
        "name": "team_join",
        "description": (
            "Add an agent id to the team's member list and seed an empty "
            "mailbox for it. Idempotent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier joining the team.",
                },
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "team_list_members",
        "description": "List all member agent ids for the team.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "team_add_task",
        "description": (
            "Append a new task to the team's shared queue. Returns the new "
            "task id. Lead-only when the server is started with "
            "--role teammate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Free-form description of the work item.",
                },
                "created_by": {
                    "type": "string",
                    "description": "Agent id of the creator. Defaults to 'lead'.",
                    "default": "lead",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of task ids that must reach status "
                        "'completed' before this task is claimable."
                    ),
                    "default": [],
                },
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "team_list_tasks",
        "description": (
            "List tasks. 'open' returns only unblocked open tasks. "
            "'open_all' returns every open task including those blocked by "
            "incomplete deps. 'blocked' returns only open-but-blocked tasks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": ["all", "open", "open_all", "blocked", "claimed", "completed"],
                    "description": "Status filter. Defaults to 'all'.",
                    "default": "all",
                },
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "team_claim_task",
        "description": (
            "Claim a task. When task_id is omitted the first unblocked open "
            "task is auto-claimed. When task_id is given but the task has "
            "unresolved deps, the claim is refused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent id performing the claim.",
                },
                "task_id": {
                    "type": "string",
                    "description": (
                        "Specific task id to claim. Omit to auto-claim the "
                        "next unblocked open task."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "team_release_task",
        "description": "Release a previously-claimed task back to status 'open'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent id holding the claim."},
                "task_id": {"type": "string", "description": "Task id to release."},
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": ["agent_id", "task_id"],
        },
    },
    {
        "name": "team_complete_task",
        "description": "Mark a claimed task as completed and record a result string.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent id holding the claim."},
                "task_id": {"type": "string", "description": "Task id to complete."},
                "result": {
                    "type": "string",
                    "description": "Completion notes / result summary.",
                    "default": "",
                },
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": ["agent_id", "task_id"],
        },
    },
    {
        "name": "team_send_message",
        "description": "Append a direct message to the recipient's mailbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "Sender agent id."},
                "to": {"type": "string", "description": "Recipient agent id."},
                "content": {"type": "string", "description": "Message body."},
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": ["sender", "to", "content"],
        },
    },
    {
        "name": "team_recv_messages",
        "description": (
            "Drain (or peek) the mailbox of the given agent id. By default "
            "messages are removed after reading; pass drain=false to peek."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent id whose inbox to read."},
                "drain": {
                    "type": "boolean",
                    "description": "If true (default) clear the inbox after reading.",
                    "default": True,
                },
                "name": {
                    "type": "string",
                    "description": "Team name. Defaults to the server's --team flag.",
                },
            },
            "required": ["agent_id"],
        },
    },
]


# -- MCP server ------------------------------------------------------------


class TeamMCPServer:
    """MCP server that exposes Chimera team coordination tools.

    Reads JSON-RPC messages from stdin (newline-delimited) and writes
    responses to stdout.

    Args:
        role: Either ``"lead"`` (full access) or ``"teammate"`` (cannot
            create new tasks). Falls back to the ``CHIMERA_ROLE`` env var
            and finally to ``"teammate"``.
        team_name: Default team name applied when a tool call omits
            ``name``. May be ``None`` to require an explicit name on every
            call.
    """

    def __init__(self, role: str | None = None, team_name: str | None = None) -> None:
        resolved = role or os.environ.get("CHIMERA_ROLE") or DEFAULT_ROLE
        if resolved not in VALID_ROLES:
            resolved = DEFAULT_ROLE
        self._role = resolved
        self._team_name = team_name
        self._initialized = False

    @property
    def role(self) -> str:
        return self._role

    # -- JSON-RPC dispatch ---------------------------------------------------

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC message.

        Args:
            message: Parsed JSON-RPC request or notification.

        Returns:
            JSON-RPC response dict, or None for notifications.
        """
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        if msg_id is None:
            return None

        handler = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }.get(method)

        if handler is None:
            return self._error_response(msg_id, -32601, f"Method not found: {method}")

        try:
            result = handler(params)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:
            return self._error_response(msg_id, -32603, str(e))

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        self._initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": TOOL_DEFINITIONS}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        dispatch = {
            "team_init": self._call_init,
            "team_join": self._call_join,
            "team_list_members": self._call_list_members,
            "team_add_task": self._call_add_task,
            "team_list_tasks": self._call_list_tasks,
            "team_claim_task": self._call_claim_task,
            "team_release_task": self._call_release_task,
            "team_complete_task": self._call_complete_task,
            "team_send_message": self._call_send_message,
            "team_recv_messages": self._call_recv_messages,
        }
        fn = dispatch.get(tool_name)
        if fn is None:
            return _text_error(f"Unknown tool: {tool_name}")
        return fn(arguments)

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    # -- Tool implementations ----------------------------------------------

    def _resolve_team_name(self, arguments: dict[str, Any]) -> str | None:
        name = arguments.get("name") or self._team_name
        if not name:
            return None
        return str(name)

    def _require_team(self, arguments: dict[str, Any]) -> tuple[Team | None, dict[str, Any] | None]:
        """Resolve and return a Team handle, or an error response."""
        name = self._resolve_team_name(arguments)
        if not name:
            return None, _text_error("Error: 'name' is required (or set --team).")
        team = Team(name)
        if not team.exists():
            return None, _text_error(f"Error: team '{name}' does not exist. Call team_init first.")
        return team, None

    def _call_init(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = self._resolve_team_name(arguments)
        if not name:
            return _text_error("Error: 'name' is required (or set --team).")
        default_model = arguments.get("default_model") or "kimi-k2.6"
        create_team(name, default_model=str(default_model))
        return _text_ok(f"team '{name}' ready")

    def _call_join(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = self._resolve_team_name(arguments)
        if not name:
            return _text_error("Error: 'name' is required (or set --team).")
        agent_id = arguments.get("agent_id")
        if not agent_id:
            return _text_error("Error: 'agent_id' is required.")
        join_team(name, str(agent_id))
        return _text_ok(f"{agent_id} joined '{name}'")

    def _call_list_members(self, arguments: dict[str, Any]) -> dict[str, Any]:
        team, err = self._require_team(arguments)
        if err is not None or team is None:
            return err or _text_error("Error: team unavailable.")
        cfg = team.load_config()
        members = list(cfg.get("members", []))
        return _json_ok({"members": members})

    def _call_add_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._role != "lead":
            return _text_error(
                "only lead can add tasks (set --role lead or CHIMERA_ROLE=lead)"
            )
        team, err = self._require_team(arguments)
        if err is not None or team is None:
            return err or _text_error("Error: team unavailable.")
        description = arguments.get("description")
        if not description:
            return _text_error("Error: 'description' is required.")
        created_by = str(arguments.get("created_by") or "lead")
        depends_on_raw = arguments.get("depends_on") or []
        if not isinstance(depends_on_raw, list):
            return _text_error("Error: 'depends_on' must be an array of task ids.")
        depends_on = [str(d) for d in depends_on_raw]
        task_id = team.add_task(str(description), created_by=created_by, depends_on=depends_on)
        return _json_ok({"task_id": task_id})

    def _call_list_tasks(self, arguments: dict[str, Any]) -> dict[str, Any]:
        team, err = self._require_team(arguments)
        if err is not None or team is None:
            return err or _text_error("Error: team unavailable.")
        status_filter = str(arguments.get("filter") or "all")
        tasks = team.list_tasks(status_filter=status_filter)
        return _json_ok({"tasks": tasks, "filter": status_filter})

    def _call_claim_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        team, err = self._require_team(arguments)
        if err is not None or team is None:
            return err or _text_error("Error: team unavailable.")
        agent_id = arguments.get("agent_id")
        if not agent_id:
            return _text_error("Error: 'agent_id' is required.")
        task_id = arguments.get("task_id")
        if not task_id:
            claimed = team.auto_claim_task(str(agent_id))
            if claimed is None:
                return _json_ok({"claimed": False, "reason": "no unblocked open tasks"})
            return _json_ok({"claimed": True, "task_id": claimed})
        # Specific task: refuse early if blocked so the caller sees a clear reason.
        if team.is_blocked(str(task_id)):
            return _json_ok({"claimed": False, "reason": "blocked by deps"})
        won = team.claim_task(str(task_id), str(agent_id))
        if not won:
            return _json_ok({"claimed": False, "reason": "already claimed or not open"})
        return _json_ok({"claimed": True, "task_id": str(task_id)})

    def _call_release_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        team, err = self._require_team(arguments)
        if err is not None or team is None:
            return err or _text_error("Error: team unavailable.")
        agent_id = arguments.get("agent_id")
        task_id = arguments.get("task_id")
        if not agent_id or not task_id:
            return _text_error("Error: 'agent_id' and 'task_id' are required.")
        released = team.release_task(str(task_id), str(agent_id))
        return _json_ok({"released": released})

    def _call_complete_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        team, err = self._require_team(arguments)
        if err is not None or team is None:
            return err or _text_error("Error: team unavailable.")
        agent_id = arguments.get("agent_id")
        task_id = arguments.get("task_id")
        if not agent_id or not task_id:
            return _text_error("Error: 'agent_id' and 'task_id' are required.")
        result = str(arguments.get("result") or "")
        completed = team.complete_task(str(task_id), str(agent_id), result=result)
        return _json_ok({"completed": completed})

    def _call_send_message(self, arguments: dict[str, Any]) -> dict[str, Any]:
        team, err = self._require_team(arguments)
        if err is not None or team is None:
            return err or _text_error("Error: team unavailable.")
        sender = arguments.get("sender")
        to = arguments.get("to")
        content = arguments.get("content")
        if not sender or not to or content is None:
            return _text_error("Error: 'sender', 'to', and 'content' are required.")
        mailbox = TeamMailbox(team, str(to))
        mailbox.send(str(sender), str(content))
        return _text_ok(f"delivered to {to}")

    def _call_recv_messages(self, arguments: dict[str, Any]) -> dict[str, Any]:
        team, err = self._require_team(arguments)
        if err is not None or team is None:
            return err or _text_error("Error: team unavailable.")
        agent_id = arguments.get("agent_id")
        if not agent_id:
            return _text_error("Error: 'agent_id' is required.")
        drain = arguments.get("drain", True)
        mailbox = TeamMailbox(team, str(agent_id))
        messages = mailbox.recv(drain=bool(drain))
        return _json_ok({"messages": messages, "count": len(messages)})

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _error_response(msg_id: int | str, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    # -- Stdio loop --------------------------------------------------------

    def run(self) -> None:
        """Run the MCP server, reading from stdin and writing to stdout."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                error = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                sys.stdout.write(json.dumps(error) + "\n")
                sys.stdout.flush()
                continue

            response = self.handle_message(message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


# -- Response helpers ------------------------------------------------------


def _text_ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _text_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _json_ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


# -- Entry point -----------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="chimera-team-mcp",
        description="MCP server for Chimera agent-team coordination.",
    )
    parser.add_argument(
        "--role",
        choices=sorted(VALID_ROLES),
        default=None,
        help="Role granted to this MCP host (lead can add tasks). "
             "Falls back to CHIMERA_ROLE env var, then 'teammate'.",
    )
    parser.add_argument(
        "--team",
        default=None,
        help="Default team name applied when a tool call omits 'name'. "
             "Falls back to CHIMERA_TEAM env var.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the MCP team server.

    Both ``--team`` and ``--role`` accept env-var fallbacks
    (``CHIMERA_TEAM`` / ``CHIMERA_ROLE``) so the teammate runner can inject
    identity without per-invocation ``mcp.json`` edits.
    """
    ns = _parse_args(argv)
    team_name = ns.team or os.environ.get("CHIMERA_TEAM")
    role = ns.role or os.environ.get("CHIMERA_ROLE")
    server = TeamMCPServer(role=role, team_name=team_name)
    server.run()


if __name__ == "__main__":
    main()
