"""Real-Session integration test for otter custom-command dispatch (W4 / F5).

W4's :mod:`chimera.otter.slash` test suite exercises
``build_custom_command_handler`` against light-weight session fakes
(``_QueueSession``, ``_SteerOnlySession``). That's good for unit-level
coverage but doesn't prove the end-to-end wiring through a *real*
:class:`chimera.sessions.session.Session` instance — i.e. that:

1. A synthetic ``.md`` file on disk parses into a real
   :class:`~chimera.otter.commands.CustomCommand`.
2. :func:`~chimera.otter.slash.build_custom_command_handler` returns a
   callable that, when invoked, calls ``session.queue(rendered)`` on a
   real ``Session``.
3. The real ``Session`` honors the call by enqueuing the rendered
   prompt onto its ``LoopConfig.message_queues`` follow-up channel —
   the same place a subsequent ``chat()`` would later pick it up.

This wave-3 test plugs that gap. It uses a real ``Session`` with a real
``Agent`` + real ``ReAct`` loop, but a synthetic mock provider that
records every message it sees so the test can assert the render landed
in the queue (rather than e.g. silently being dropped).

Stdlib + pytest only — no network, no third-party imports.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.message_queue import MessageQueues
from chimera.otter.commands import load_custom_commands, parse_command_file
from chimera.otter.slash import build_custom_command_handler
from chimera.providers.base import Provider, Response
from chimera.sessions.session import Session
from chimera.types import Message


# ---------------------------------------------------------------------------
# Mock provider: records every message list passed to ``complete()``.
# ---------------------------------------------------------------------------

class _RecordingProvider(Provider):
    """Provider double that records every message list it sees.

    The integration test never actually runs ``Session.chat()`` — it only
    needs the provider to satisfy the :class:`Agent` constructor — but we
    still fully implement the ABC so the agent / loop can be exercised
    later if desired. Each call to ``complete`` appends a deep snapshot
    of *messages* to :attr:`seen_messages` so test cases can confirm
    exactly which prompts ever reached the model boundary.
    """

    def __init__(self) -> None:
        self.seen_messages: list[list[Message]] = []

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        self.seen_messages.append(list(messages))
        return Response(
            content="ok",
            tool_calls=[],
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    @property
    def context_window(self) -> int:
        return 8192

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "recording-mock"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _captured() -> tuple[list[str], Any]:
    """Return (lines, callable) — captures every print from a slash handler."""
    lines: list[str] = []

    def _out(line: str = "") -> None:
        lines.append(line)

    return lines, _out


def _build_real_session(
    queues: MessageQueues | None = None,
) -> tuple[Session, _RecordingProvider, MessageQueues]:
    """Construct a real ``Session`` wired with a recording provider.

    The session has a real :class:`ReAct` loop with
    ``LoopConfig(message_queues=...)`` — that's the same wiring
    :class:`~chimera.cli.code` produces, so ``Session.queue`` will land
    rendered prompts on the same follow-up queue the live REPL drains.
    """
    q = queues or MessageQueues()
    config = LoopConfig(message_queues=q)
    loop = ReAct(max_steps=3, config=config)
    provider = _RecordingProvider()
    agent = Agent(provider=provider, tools=[], loop=loop)
    session = Session(agent=agent)
    return session, provider, q


def _write_command_md(
    project_root: Path,
    *,
    name: str = "review",
    description: str = "Review a file",
    body: str = "Please review $1 carefully ($ARGUMENTS).",
) -> Path:
    """Write a synthetic ``.opencode/command/<name>.md`` file under *project_root*.

    Returns the path to the written file so tests can assert on it.
    """
    cmd_dir = project_root / ".opencode" / "command"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    md_path = cmd_dir / f"{name}.md"
    md_path.write_text(
        "---\n"
        f"description: {description}\n"
        "args:\n"
        "  - name: target\n"
        "    description: file or dir\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return md_path


# ---------------------------------------------------------------------------
# End-to-end: parse .md -> CustomCommand -> dispatch -> real Session.queue
# ---------------------------------------------------------------------------

def test_md_file_to_real_session_queue_end_to_end(tmp_path: Path) -> None:
    """The full path: .md on disk -> handler -> real ``Session.queue``.

    Verifies every link of the chain:

    1. ``parse_command_file`` reads our synthetic ``.md`` into a
       :class:`CustomCommand` with the expected description / template.
    2. ``build_custom_command_handler`` wraps it as a slash handler.
    3. Invoking the handler against a real :class:`Session` lands the
       rendered prompt on the follow-up queue exposed via
       ``LoopConfig.message_queues``.
    4. The handler announces success via the *out* channel (so the REPL
       has something to print).
    """
    # 1. Synthetic .md file on disk.
    md_path = _write_command_md(tmp_path, name="review")
    cmd = parse_command_file(md_path)
    assert cmd is not None
    assert cmd.name == "review"
    assert cmd.description == "Review a file"
    assert cmd.body_template.startswith("Please review $1")

    # 2. Slash dispatch handler.
    handler = build_custom_command_handler(cmd)
    assert callable(handler)

    # 3. Real Session wired to a real MessageQueues.
    session, _provider, queues = _build_real_session()

    lines, out = _captured()
    handler(session, None, "src/main.py", out)

    # 4. The render landed on the follow-up queue.
    drained = queues.drain_follow_up()
    assert len(drained) == 1
    msg = drained[0]
    assert msg.role == "user"
    assert msg.content == "Please review src/main.py carefully (src/main.py)."

    # 5. The user-facing announcement is informative, not the body.
    assert any("queued" in line for line in lines)
    assert not any("Please review" in line for line in lines), (
        "Rendered prompt should go to the queue, not the REPL output"
    )

    # 6. The provider was *not* invoked — slash dispatch only queues; the
    #    next ``Session.chat()`` would drain the queue. Confirms we don't
    #    accidentally trigger an extra turn.
    assert _provider_call_count(session) == 0


def test_loader_then_dispatch_lands_on_real_session(tmp_path: Path) -> None:
    """``load_custom_commands`` + handler dispatch reach a real session.

    Mirrors the production flow: ``run_otter_repl`` calls
    :func:`load_custom_commands` to discover project-scope ``.md`` files,
    wraps each into a slash handler, and registers them onto the shared
    registry. This test exercises the same path against a real
    :class:`Session` so we know the seam survives a refactor of either
    side.
    """
    _write_command_md(
        tmp_path,
        name="summarize",
        description="Summarize a topic",
        body="Summarize the topic: $TOPIC ($ARGUMENTS)",
    )

    # Load via the production discovery path. Pass an empty user_dirs
    # tuple so the real ``~/.opencode/command`` is *not* consulted —
    # tests must never read the developer's home directory.
    loaded = load_custom_commands(project_root=tmp_path, user_dirs=())
    assert "summarize" in loaded
    cmd = loaded["summarize"]

    handler = build_custom_command_handler(cmd)
    session, _provider, queues = _build_real_session()

    lines, out = _captured()
    handler(session, None, 'extra topic="machine learning"', out)

    drained = queues.drain_follow_up()
    assert len(drained) == 1
    assert drained[0].content == (
        "Summarize the topic: machine learning (extra)"
    )
    assert any("queued" in line for line in lines)


def test_dispatch_without_message_queues_falls_back_silently(
    tmp_path: Path,
) -> None:
    """A real Session whose loop has no ``message_queues`` shouldn't crash.

    ``Session.queue`` is a *best-effort* method: it only enqueues when
    ``LoopConfig.message_queues`` is wired. The slash handler still
    calls ``queue()`` — it succeeds (no exception) but nothing lands.
    Confirms the integration tolerates a partially configured loop
    instead of erroring at the REPL boundary.
    """
    _write_command_md(tmp_path, name="ping", body="pong")
    cmd = parse_command_file(tmp_path / ".opencode" / "command" / "ping.md")
    assert cmd is not None
    handler = build_custom_command_handler(cmd)

    # Build a session whose loop has *no* LoopConfig (so no message_queues).
    bare_loop = ReAct(max_steps=1, config=LoopConfig())  # message_queues=None
    provider = _RecordingProvider()
    agent = Agent(provider=provider, tools=[], loop=bare_loop)
    session = Session(agent=agent)

    lines, out = _captured()
    # Must not raise even though queue() is a no-op.
    handler(session, None, "", out)

    # The handler thinks the queue succeeded (it returned without error),
    # so the user sees a "queued" confirmation. No crash, no leak. The
    # render is intentionally *not* echoed to ``out`` — that path is
    # reserved for the truly bare-session fallback (no queue + no steer).
    assert any("queued" in line for line in lines)


def test_dispatch_uses_steer_when_session_only_has_steer(
    tmp_path: Path,
) -> None:
    """Verify the ``queue`` -> ``steer`` fallback against a real Session-shaped object.

    A real :class:`Session` always exposes both methods, but a future
    custom session subclass may only override ``steer``. The handler
    should detect ``queue`` is missing and route through ``steer``
    instead. We model that with a thin subclass that hides ``queue``
    and instruments ``steer`` to forward into a real ``MessageQueues``.
    """
    _write_command_md(tmp_path, name="alert", body="Investigate $1!")
    cmd = parse_command_file(tmp_path / ".opencode" / "command" / "alert.md")
    assert cmd is not None
    handler = build_custom_command_handler(cmd)

    queues = MessageQueues()
    config = LoopConfig(message_queues=queues)
    loop = ReAct(max_steps=1, config=config)
    provider = _RecordingProvider()
    agent = Agent(provider=provider, tools=[], loop=loop)

    class _SteerOnlySession(Session):
        """Session subclass that hides ``queue`` to force the steer path."""

        # Setting to ``None`` makes ``getattr(session, "queue", None)``
        # return a non-callable, so the handler falls through to steer.
        queue = None  # type: ignore[assignment]

    session = _SteerOnlySession(agent=agent)

    lines, out = _captured()
    handler(session, None, "incident-42", out)

    # Steer landed on the steering channel, not follow-up.
    assert queues.drain_follow_up() == []
    steered = queues.drain_steering()
    assert len(steered) == 1
    assert steered[0].content == "Investigate incident-42!"
    assert any("steered" in line for line in lines)


# ---------------------------------------------------------------------------
# Helper used by the call-count assertion above.
# ---------------------------------------------------------------------------

def _provider_call_count(session: Session) -> int:
    """Return how many times the recording provider has been hit.

    Walks through ``Session._agent.provider`` to keep the assertion
    independent of any ``provider`` accessor we might or might not add.
    """
    provider = session._agent.provider  # noqa: SLF001 -- test introspection
    assert isinstance(provider, _RecordingProvider)
    return len(provider.seen_messages)
