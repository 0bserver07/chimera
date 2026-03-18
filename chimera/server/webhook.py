"""Lightweight webhook agent server using only stdlib.

Runs Chimera agents in response to webhook triggers from GitHub, Slack,
or raw HTTP POST requests. Each conversation thread gets a persistent
agent session keyed by a deterministic thread ID.

This is a Layer 8 (CLI / deployment) addition.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.types import AgentResult


@dataclass
class WebhookEvent:
    """A parsed webhook event.

    Attributes:
        source: Origin of the event (``"github"``, ``"slack"``, ``"http"``).
        thread_id: Deterministic ID that routes this event to a persistent
            agent session.
        task: The task or message text to send to the agent.
        metadata: Arbitrary key/value pairs from the source payload.
    """

    source: str
    thread_id: str
    task: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentServer:
    """Lightweight HTTP server that runs Chimera agents on webhook triggers.

    Supports GitHub issue comments, Slack mentions, and raw HTTP POST.
    Each thread gets a persistent agent session with deterministic IDs.

    Usage::

        server = AgentServer(
            agent_factory=lambda: Agent(provider=provider, tools=tools),
            env_factory=lambda: LocalEnvironment(workdir=tempfile.mkdtemp()),
        )
        server.start(port=8080)

    Endpoints:
        - ``POST /webhook/github`` -- GitHub issue/PR comment webhooks
        - ``POST /webhook/slack``  -- Slack event subscriptions
        - ``POST /run``            -- Raw HTTP task submission
        - ``GET  /health``         -- Health check
    """

    def __init__(
        self,
        agent_factory: Callable[[], Agent],
        env_factory: Callable[[], Environment] | None = None,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self._agent_factory = agent_factory
        self._env_factory = env_factory
        self._host = host
        self._port = port
        self._threads: dict[str, dict[str, Any]] = {}  # thread_id -> {agent, env, runs}
        self._results: dict[str, AgentResult] = {}
        self._lock = threading.Lock()

    def _get_or_create_thread(self, thread_id: str) -> dict[str, Any]:
        """Get existing thread or create a new one.

        Args:
            thread_id: The deterministic thread identifier.

        Returns:
            Thread state dict containing ``agent``, ``env``, and ``runs``.
        """
        with self._lock:
            if thread_id not in self._threads:
                agent = self._agent_factory()
                env = self._env_factory() if self._env_factory else None
                if env:
                    env.setup()
                self._threads[thread_id] = {
                    "agent": agent,
                    "env": env,
                    "runs": 0,
                }
            return self._threads[thread_id]

    def handle_event(self, event: WebhookEvent) -> AgentResult:
        """Process a webhook event synchronously.

        Args:
            event: The parsed webhook event.

        Returns:
            The agent's result after processing the task.
        """
        thread = self._get_or_create_thread(event.thread_id)
        agent = thread["agent"]
        env = thread["env"]
        thread["runs"] += 1

        result = agent.run(event.task, env=env)
        self._results[event.thread_id] = result
        return result

    def handle_event_async(self, event: WebhookEvent) -> str:
        """Process a webhook event in a background thread.

        Args:
            event: The parsed webhook event.

        Returns:
            The thread_id that can be used to retrieve the result later
            via :meth:`get_result`.
        """
        t = threading.Thread(target=self.handle_event, args=(event,), daemon=True)
        t.start()
        return event.thread_id

    @property
    def active_threads(self) -> list[str]:
        """List active thread IDs."""
        with self._lock:
            return list(self._threads.keys())

    def get_result(self, thread_id: str) -> AgentResult | None:
        """Get the latest result for a thread.

        Args:
            thread_id: The thread identifier.

        Returns:
            The most recent :class:`AgentResult`, or ``None`` if no result
            is available yet.
        """
        return self._results.get(thread_id)

    @staticmethod
    def make_thread_id(source: str, identifier: str) -> str:
        """Generate a deterministic thread ID from source + identifier.

        Same issue/thread always routes to the same agent session.

        Args:
            source: Event source (e.g. ``"github"``, ``"slack"``).
            identifier: Source-specific unique key (e.g. ``"org/repo:42"``).

        Returns:
            A UUID-formatted deterministic hash string.
        """
        raw = f"{source}:{identifier}"
        h = hashlib.sha256(raw.encode()).hexdigest()[:32]
        # Format as UUID-like for readability
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    @staticmethod
    def parse_github_webhook(payload: dict[str, Any]) -> WebhookEvent | None:
        """Parse a GitHub issue/PR comment webhook.

        Only handles ``action: "created"`` events. Other actions are
        silently ignored.

        Args:
            payload: The JSON payload from GitHub's webhook POST.

        Returns:
            A :class:`WebhookEvent` if the payload is a new comment,
            ``None`` otherwise.
        """
        action = payload.get("action")
        if action != "created":
            return None

        comment = payload.get("comment", {})
        body = comment.get("body", "")

        issue = payload.get("issue", {})
        issue_number = issue.get("number", 0)
        repo = payload.get("repository", {}).get("full_name", "unknown")

        thread_id = AgentServer.make_thread_id("github", f"{repo}:{issue_number}")

        # Build task from issue context + comment
        issue_title = issue.get("title", "")
        task = f"GitHub issue #{issue_number}: {issue_title}\n\nComment: {body}"

        return WebhookEvent(
            source="github",
            thread_id=thread_id,
            task=task,
            metadata={
                "repo": repo,
                "issue_number": issue_number,
                "comment_id": comment.get("id"),
            },
        )

    @staticmethod
    def parse_slack_event(payload: dict[str, Any]) -> WebhookEvent | None:
        """Parse a Slack event (message with bot mention).

        Args:
            payload: The JSON payload from Slack's Events API.

        Returns:
            A :class:`WebhookEvent` built from the Slack message.
        """
        event = payload.get("event", {})
        text = event.get("text", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", event.get("ts", ""))

        thread_id = AgentServer.make_thread_id("slack", f"{channel}:{thread_ts}")

        return WebhookEvent(
            source="slack",
            thread_id=thread_id,
            task=text,
            metadata={"channel": channel, "thread_ts": thread_ts},
        )

    def start(self, blocking: bool = True) -> HTTPServer | None:
        """Start the HTTP server.

        Args:
            blocking: If ``True``, blocks forever serving requests.
                If ``False``, starts in a background daemon thread and
                returns the :class:`HTTPServer` instance.

        Returns:
            The :class:`HTTPServer` when ``blocking=False``, ``None``
            when ``blocking=True`` (never returns).
        """
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            """HTTP request handler for the agent webhook server."""

            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)

                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "invalid json"}')
                    return

                # Route by path
                if self.path == "/webhook/github":
                    event = server_ref.parse_github_webhook(payload)
                elif self.path == "/webhook/slack":
                    event = server_ref.parse_slack_event(payload)
                elif self.path == "/run":
                    # Raw HTTP: {"task": "...", "thread_id": "..."}
                    task = payload.get("task", "")
                    tid = payload.get("thread_id", str(uuid.uuid4()))
                    event = WebhookEvent(source="http", thread_id=tid, task=task)
                else:
                    self.send_response(404)
                    self.end_headers()
                    return

                if event is None:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ignored"}')
                    return

                # Run async
                thread_id = server_ref.handle_event_async(event)

                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"thread_id": thread_id, "status": "accepted"}).encode()
                )

            def do_GET(self) -> None:
                if self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {
                                "status": "ok",
                                "active_threads": len(server_ref.active_threads),
                            }
                        ).encode()
                    )
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass  # suppress default logging

        httpd = HTTPServer((self._host, self._port), Handler)

        if blocking:
            httpd.serve_forever()
            return None
        else:
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            return httpd
