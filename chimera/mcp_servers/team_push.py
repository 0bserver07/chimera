"""Real-time mailbox push for running teammates (issue #149).

Team messaging is pull-only by construction: a teammate sees its mail
when it next calls ``team_recv_messages``. With spawn-per-task runner
semantics that means "at the start of the next task" — mid-run "stop,
requirements changed" delivery is impossible.

This module adds the push half, without giving up anything the pull path
provides.

Why a filesystem watch rather than a notify socket
--------------------------------------------------
The team substrate is deliberately **daemonless and runtime-agnostic**:
coordination state is plain JSONL under ``~/.chimera/teams/<name>/``,
guarded by ``fcntl`` locks, so any MCP host — in any language, in any
process, started at any time — can join by reading and writing files. A
notify socket would add a second transport that only Chimera-side
processes can speak, plus a broker to keep alive. Watching the mailbox
file keeps exactly one source of truth, needs no new dependency (stdlib
``os.stat`` only), works on every platform Chimera supports, and is
inert when nothing is listening.

Delivery contract
-----------------
* The watcher **peeks** (never drains). It hands new messages to a
  :class:`TeammateSink` and only then acknowledges them with
  :meth:`~chimera.cli.agent_teams.TeamMailbox.consume`, which removes
  exactly those message ids under the mailbox lock.
* A sink signals failure by raising. Nothing is acked, so the message
  stays in the mailbox and the ordinary
  ``team_recv_messages`` pull still delivers it. **Push never loses
  mail; worst case it degrades to today's behavior.**
* Bursts are coalesced: after a change is seen the watcher waits
  ``debounce`` seconds and re-checks, so three messages sent together
  arrive as one steering message rather than three.

Reusing the steer seam
----------------------
:class:`TeammateSink` is exactly
:meth:`~chimera.assembly.driver.AgentDriver.steer`'s signature, so
``AgentDriver``, :class:`~chimera.sessions.session.Session`, and
:class:`~chimera.assembly.coding_agent.CodingAgent` are sinks *as-is* —
mid-run delivery rides the existing thread-safe steering queue and lands
at the next step boundary. Drivers that cannot steer (a subprocess-backed
external lane, which only notes that steering is unsupported) must not be
wrapped: leave the push path unconfigured and mail flows through the pull
path.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable, Protocol, Sequence

if TYPE_CHECKING:
    from chimera.cli.agent_teams import TeamMailbox

__all__ = [
    "DEFAULT_DEBOUNCE",
    "DEFAULT_WATCH_INTERVAL",
    "MailboxWatcher",
    "TeammateSink",
    "format_team_mail",
]

#: How often the watcher stats the mailbox file. Fast enough that an
#: idle teammate sees mail well inside a second, cheap enough that the
#: cost is one ``stat`` per tick.
DEFAULT_WATCH_INTERVAL = 0.25

#: Quiet period after a detected change before reading, so a burst of
#: sends is delivered as one steering message.
DEFAULT_DEBOUNCE = 0.1


class TeammateSink(Protocol):
    """Anything that can hand a message to a *live* teammate.

    The signature is deliberately identical to
    :meth:`chimera.assembly.driver.AgentDriver.steer`, so the existing
    steer seam is the push path rather than a parallel invention.

    Implementations must raise on failed delivery — the watcher treats a
    raise as "not delivered" and leaves the message queued for the pull
    path.
    """

    def steer(self, text: str) -> None:
        """Deliver *text* to the running teammate, or raise if it cannot."""
        ...


def format_team_mail(agent_id: str, messages: Sequence[dict[str, Any]]) -> str:
    """Render pending mail as one steering message.

    Args:
        agent_id: Recipient agent id (named so the model knows the mail
            is addressed to it, not to the team at large).
        messages: Mailbox records, oldest first.

    Returns:
        A single plain-text block. Empty string when *messages* is empty.
    """
    if not messages:
        return ""
    lines = [
        f"[team mail] {len(messages)} new message(s) for '{agent_id}' "
        f"— read them before continuing:"
    ]
    for rec in messages:
        sender = str(rec.get("from", "?"))
        content = str(rec.get("content", "")).strip()
        lines.append(f"- from {sender}: {content}")
    return "\n".join(lines)


class MailboxWatcher:
    """Watch one mailbox file and push new messages into a live teammate.

    The watcher owns no team state of its own: it peeks the mailbox,
    delivers, and acks. Start it when a teammate session is alive and
    stop it before that session goes away.

    Args:
        mailbox: The teammate's own mailbox.
        sink: Where to deliver — see :class:`TeammateSink`.
        interval: Seconds between mailbox stats.
        debounce: Quiet period after a change before reading, to
            coalesce bursts.
        formatter: Overrides :func:`format_team_mail`.
        on_error: Called with the exception when a delivery raises.
            Defaults to swallowing (the message stays queued for the
            pull path either way). Never let this raise.

    Attributes:
        delivered: Count of messages successfully pushed since start.
    """

    def __init__(
        self,
        mailbox: "TeamMailbox",
        sink: TeammateSink,
        *,
        interval: float = DEFAULT_WATCH_INTERVAL,
        debounce: float = DEFAULT_DEBOUNCE,
        formatter: Callable[[str, Sequence[dict[str, Any]]], str] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._mailbox = mailbox
        self._sink = sink
        self._interval = max(0.01, float(interval))
        self._debounce = max(0.0, float(debounce))
        self._formatter = formatter or format_team_mail
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pushed: set[str] = set()
        self._signature: tuple[int, int] | None = None
        self.delivered = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the watch thread. Idempotent."""
        if self._thread is not None:
            return
        # Baseline the signature so mail already sitting in the box when
        # the session starts is left to the agent's own first
        # ``team_recv_messages`` call — that is the pull path's job.
        self._signature = self._stat()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="team-mailbox-watch", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the watch thread and join it. Idempotent."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def __enter__(self) -> "MailboxWatcher":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # -- delivery -----------------------------------------------------------

    def poll_once(self) -> list[dict[str, Any]]:
        """Deliver any undelivered mail right now, synchronously.

        The watch thread calls this; tests (and callers that prefer to
        drive their own cadence) can call it directly.

        Returns:
            The records successfully pushed on this call — empty when
            there was nothing new or when delivery failed.
        """
        pending = [
            rec for rec in self._mailbox.recv(drain=False)
            if rec.get("id") and str(rec["id"]) not in self._pushed
        ]
        if not pending:
            return []

        text = self._formatter(self._mailbox.agent_id, pending)
        if not text:
            return []

        try:
            self._sink.steer(text)
        except Exception as exc:  # noqa: BLE001 - a failed push must not kill the watch
            if self._on_error is not None:
                try:
                    self._on_error(exc)
                except Exception:  # pragma: no cover - defensive
                    pass
            return []

        ids = [str(rec["id"]) for rec in pending]
        # Mark first, ack second: if the ack write fails we still refuse to
        # deliver the same message twice within this session.
        self._pushed.update(ids)
        self._mailbox.consume(ids)
        self.delivered += len(ids)
        # The ack rewrote the file, so re-baseline rather than treating our
        # own write as a fresh change.
        self._signature = self._stat()
        return pending

    # -- internals ----------------------------------------------------------

    def _stat(self) -> tuple[int, int] | None:
        """Return ``(mtime_ns, size)`` for the mailbox, or None if absent."""
        try:
            st = self._mailbox.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(self._interval):
                return
            signature = self._stat()
            if signature == self._signature:
                continue
            self._signature = signature
            if self._debounce and self._stop.wait(self._debounce):
                return
            # Re-stat after the quiet period so a burst still in flight is
            # picked up whole on the next tick rather than torn in half.
            self._signature = self._stat()
            self.poll_once()
