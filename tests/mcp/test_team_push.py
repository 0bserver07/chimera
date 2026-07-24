"""Tests for real-time mailbox push to running teammates (issue #149).

Two layers are covered:

* The mailbox primitives — per-message ids and
  :meth:`~chimera.cli.agent_teams.TeamMailbox.consume`, the ack half of
  the push path. The invariant under test is that push never loses
  mail: anything not acked is still returned by the pull path.
* :class:`~chimera.mcp_servers.team_push.MailboxWatcher`, both driven
  synchronously (deterministic) and running on its own thread (latency).

The end-to-end seam test drives a real
:class:`~chimera.assembly.driver.AgentDriver` through the hermetic
harness, proving that a pushed message reaches the model mid-run via the
existing steering queue rather than a bespoke channel.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from chimera.cli.agent_teams import Team, TeamMailbox
from chimera.mcp_servers.team_push import (
    MailboxWatcher,
    format_team_mail,
)


class _RecordingSink:
    """Sink that records every delivered message."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def steer(self, text: str) -> None:
        self.messages.append(text)


class _BrokenSink:
    """Sink that always fails, the way a dead session would."""

    def __init__(self) -> None:
        self.attempts = 0

    def steer(self, text: str) -> None:
        self.attempts += 1
        raise RuntimeError("no live session to push into")


def _mailbox(tmp_path: Path, agent_id: str = "worker-1") -> TeamMailbox:
    team = Team("push-team", root=tmp_path)
    team.init()
    team.add_member(agent_id)
    return TeamMailbox(team, agent_id)


class TestMailboxIds:
    def test_send_returns_a_stable_id_stamped_on_the_record(
        self, tmp_path: Path,
    ) -> None:
        mb = _mailbox(tmp_path)
        mid = mb.send("lead", "hello")

        assert mid
        messages = mb.recv(drain=False)
        assert [m["id"] for m in messages] == [mid]

    def test_ids_are_unique_per_message(self, tmp_path: Path) -> None:
        mb = _mailbox(tmp_path)
        ids = [mb.send("lead", f"m{i}") for i in range(5)]

        assert len(set(ids)) == 5

    def test_consume_removes_only_the_named_ids(self, tmp_path: Path) -> None:
        mb = _mailbox(tmp_path)
        first = mb.send("lead", "delivered by push")
        second = mb.send("lead", "still pending")

        assert mb.consume([first]) == 1

        remaining = mb.recv(drain=False)
        assert [m["id"] for m in remaining] == [second]

    def test_consume_keeps_messages_that_arrived_after_the_read(
        self, tmp_path: Path,
    ) -> None:
        # The acceptance criterion from the issue: a push ack must not
        # swallow mail that landed between the read and the ack.
        mb = _mailbox(tmp_path)
        pushed = mb.send("lead", "pushed")
        pending = mb.recv(drain=False)
        assert len(pending) == 1

        raced = mb.send("lead", "arrived mid-ack")
        mb.consume([pushed])

        left = mb.recv(drain=True)
        assert [m["id"] for m in left] == [raced]

    def test_consume_is_a_no_op_for_unknown_ids(self, tmp_path: Path) -> None:
        mb = _mailbox(tmp_path)
        mb.send("lead", "keep me")

        assert mb.consume(["not-a-real-id"]) == 0
        assert len(mb.recv(drain=False)) == 1

    def test_consume_on_an_empty_mailbox_is_safe(self, tmp_path: Path) -> None:
        mb = _mailbox(tmp_path)

        assert mb.consume(["anything"]) == 0

    def test_recv_still_drains_everything(self, tmp_path: Path) -> None:
        # Back-compat: the pull path is untouched by the id addition.
        mb = _mailbox(tmp_path)
        mb.send("lead", "one")
        mb.send("lead", "two")

        assert len(mb.recv(drain=True)) == 2
        assert mb.recv(drain=False) == []


class TestFormatting:
    def test_format_names_the_recipient_and_every_sender(self) -> None:
        text = format_team_mail(
            "worker-1",
            [
                {"from": "lead", "content": "stop, requirements changed"},
                {"from": "worker-2", "content": "I took the parser task"},
            ],
        )

        assert "worker-1" in text
        assert "2 new message(s)" in text
        assert "stop, requirements changed" in text
        assert "worker-2" in text

    def test_format_of_nothing_is_empty(self) -> None:
        assert format_team_mail("worker-1", []) == ""


class TestWatcherDelivery:
    def test_poll_once_delivers_and_acks(self, tmp_path: Path) -> None:
        mb = _mailbox(tmp_path)
        sink = _RecordingSink()
        watcher = MailboxWatcher(mb, sink)

        mb.send("lead", "requirements changed")
        pushed = watcher.poll_once()

        assert len(pushed) == 1
        assert len(sink.messages) == 1
        assert "requirements changed" in sink.messages[0]
        # Acked: the pull path must not re-deliver it.
        assert mb.recv(drain=False) == []
        assert watcher.delivered == 1

    def test_poll_once_coalesces_a_burst_into_one_message(
        self, tmp_path: Path,
    ) -> None:
        mb = _mailbox(tmp_path)
        sink = _RecordingSink()
        watcher = MailboxWatcher(mb, sink)

        mb.send("lead", "first")
        mb.send("lead", "second")
        mb.send("lead", "third")
        watcher.poll_once()

        assert len(sink.messages) == 1
        assert "first" in sink.messages[0]
        assert "third" in sink.messages[0]

    def test_failed_delivery_leaves_mail_for_the_pull_path(
        self, tmp_path: Path,
    ) -> None:
        # The degrade-to-polling guarantee, at per-message granularity.
        mb = _mailbox(tmp_path)
        sink = _BrokenSink()
        errors: list[Exception] = []
        watcher = MailboxWatcher(mb, sink, on_error=errors.append)

        mb.send("lead", "must not be lost")
        assert watcher.poll_once() == []

        assert sink.attempts == 1
        assert len(errors) == 1
        pulled = mb.recv(drain=True)
        assert len(pulled) == 1
        assert pulled[0]["content"] == "must not be lost"
        assert watcher.delivered == 0

    def test_a_message_is_never_pushed_twice(self, tmp_path: Path) -> None:
        mb = _mailbox(tmp_path)
        sink = _RecordingSink()
        watcher = MailboxWatcher(mb, sink)

        mb.send("lead", "once")
        watcher.poll_once()
        watcher.poll_once()

        assert len(sink.messages) == 1

    def test_nothing_to_deliver_is_a_no_op(self, tmp_path: Path) -> None:
        mb = _mailbox(tmp_path)
        sink = _RecordingSink()

        assert MailboxWatcher(mb, sink).poll_once() == []
        assert sink.messages == []

    def test_records_without_an_id_are_left_to_the_pull_path(
        self, tmp_path: Path,
    ) -> None:
        # A message written by an older process has no id, so it cannot be
        # acked — the watcher must leave it alone rather than deliver it
        # and lose track of it.
        mb = _mailbox(tmp_path)
        mb.path.parent.mkdir(parents=True, exist_ok=True)
        mb.path.write_text('{"from": "lead", "to": "worker-1", "content": "legacy"}\n')
        sink = _RecordingSink()

        assert MailboxWatcher(mb, sink).poll_once() == []
        assert len(mb.recv(drain=False)) == 1


class TestWatcherThread:
    def test_mail_reaches_a_running_teammate_within_a_second(
        self, tmp_path: Path,
    ) -> None:
        # The issue's acceptance criterion: ~1s from send to delivery.
        mb = _mailbox(tmp_path)
        sink = _RecordingSink()
        watcher = MailboxWatcher(mb, sink, interval=0.05, debounce=0.02)

        with watcher:
            sent_at = time.monotonic()
            mb.send("lead", "urgent")
            deadline = sent_at + 1.0
            while not sink.messages and time.monotonic() < deadline:
                time.sleep(0.02)
            elapsed = time.monotonic() - sent_at

        assert sink.messages, "message was not pushed within 1s"
        assert elapsed < 1.0
        assert "urgent" in sink.messages[0]

    def test_mail_waiting_before_start_is_left_to_the_pull_path(
        self, tmp_path: Path,
    ) -> None:
        # Backlog belongs to the agent's own first team_recv_messages call;
        # the watcher only owns what arrives while the session is live.
        mb = _mailbox(tmp_path)
        mb.send("lead", "backlog")
        sink = _RecordingSink()
        watcher = MailboxWatcher(mb, sink, interval=0.05, debounce=0.02)

        with watcher:
            time.sleep(0.3)

        assert sink.messages == []
        assert len(mb.recv(drain=False)) == 1

    def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        watcher = MailboxWatcher(_mailbox(tmp_path), _RecordingSink())
        watcher.start()
        watcher.start()  # idempotent
        watcher.stop()
        watcher.stop()


class TestDriverSeam:
    def test_pushed_mail_reaches_the_model_through_the_steer_seam(
        self, tmp_path: Path,
    ) -> None:
        # End-to-end proof that the push path IS the existing steer seam:
        # mailbox -> MailboxWatcher -> AgentDriver.steer -> conversation.
        from chimera.testing import create_assembled_harness

        mb = _mailbox(tmp_path)
        harness = create_assembled_harness(
            [
                {"text": "thinking", "tool_calls": [{"name": "list_files",
                                                     "arguments": {"path": "."}}]},
                {"text": "done"},
            ],
            workspace=tmp_path / "ws",
        )
        # AgentDriver satisfies TeammateSink structurally — no adapter.
        watcher = MailboxWatcher(mb, harness.driver)

        pushed: list[Any] = []

        def _on_event(event: Any) -> None:
            if not pushed:
                mb.send("lead", "scope changed: only touch auth.py")
                pushed.extend(watcher.poll_once())

        run = harness.run("do the task", on_event=_on_event)

        assert pushed, "watcher did not push the message"
        contents = [str(getattr(m, "content", "")) for m in run.messages]
        assert any("scope changed: only touch auth.py" in c for c in contents), (
            "steered team mail never reached the conversation"
        )
