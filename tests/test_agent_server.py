"""Tests for chimera.server.webhook — AgentServer and WebhookEvent."""

from __future__ import annotations

import json
import time
import urllib.request
from unittest.mock import MagicMock

from chimera.server.webhook import AgentServer, WebhookEvent
from chimera.types import AgentResult


def _mock_agent():
    agent = MagicMock()
    agent.run.return_value = AgentResult(
        output="done", steps=1, tool_calls_total=0, cost=0.01, success=True
    )
    return agent


def test_make_thread_id_deterministic():
    id1 = AgentServer.make_thread_id("github", "org/repo:42")
    id2 = AgentServer.make_thread_id("github", "org/repo:42")
    assert id1 == id2


def test_make_thread_id_different_sources():
    id1 = AgentServer.make_thread_id("github", "42")
    id2 = AgentServer.make_thread_id("slack", "42")
    assert id1 != id2


def test_parse_github_webhook():
    payload = {
        "action": "created",
        "comment": {"body": "Fix this bug", "id": 123},
        "issue": {"number": 42, "title": "Bug in parser"},
        "repository": {"full_name": "org/repo"},
    }
    event = AgentServer.parse_github_webhook(payload)
    assert event is not None
    assert event.source == "github"
    assert "Fix this bug" in event.task
    assert event.metadata["issue_number"] == 42


def test_parse_github_ignores_non_created():
    payload = {"action": "deleted"}
    assert AgentServer.parse_github_webhook(payload) is None


def test_parse_slack_event():
    payload = {
        "event": {
            "text": "Hey bot, fix the tests",
            "channel": "C123",
            "thread_ts": "1234567890.123456",
        }
    }
    event = AgentServer.parse_slack_event(payload)
    assert event is not None
    assert event.source == "slack"
    assert "fix the tests" in event.task


def test_handle_event():
    server = AgentServer(agent_factory=_mock_agent)
    event = WebhookEvent(source="test", thread_id="t1", task="do it")
    result = server.handle_event(event)
    assert result.success
    assert result.output == "done"


def test_thread_persistence():
    server = AgentServer(agent_factory=_mock_agent)
    event1 = WebhookEvent(source="test", thread_id="t1", task="first")
    event2 = WebhookEvent(source="test", thread_id="t1", task="second")
    server.handle_event(event1)
    server.handle_event(event2)
    # Same thread_id should reuse the same agent
    assert len(server.active_threads) == 1


def test_different_threads():
    server = AgentServer(agent_factory=_mock_agent)
    server.handle_event(WebhookEvent(source="test", thread_id="t1", task="a"))
    server.handle_event(WebhookEvent(source="test", thread_id="t2", task="b"))
    assert len(server.active_threads) == 2


def test_get_result():
    server = AgentServer(agent_factory=_mock_agent)
    event = WebhookEvent(source="test", thread_id="t1", task="do it")
    server.handle_event(event)
    result = server.get_result("t1")
    assert result is not None
    assert result.success


def test_http_server_starts_and_stops():
    server = AgentServer(agent_factory=_mock_agent, port=0)  # port 0 = random
    httpd = server.start(blocking=False)
    assert httpd is not None
    port = httpd.server_address[1]

    # Health check
    resp = urllib.request.urlopen(f"http://localhost:{port}/health")
    data = json.loads(resp.read())
    assert data["status"] == "ok"

    httpd.shutdown()


def test_http_run_endpoint():
    server = AgentServer(agent_factory=_mock_agent, port=0)
    httpd = server.start(blocking=False)
    port = httpd.server_address[1]

    # POST /run
    req = urllib.request.Request(
        f"http://localhost:{port}/run",
        data=json.dumps({"task": "hello", "thread_id": "test-123"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    assert data["status"] == "accepted"
    assert data["thread_id"] == "test-123"

    # Wait for async processing
    time.sleep(0.5)
    result = server.get_result("test-123")
    assert result is not None

    httpd.shutdown()
