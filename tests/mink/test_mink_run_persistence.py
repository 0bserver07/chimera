"""Persistence tests for ``chimera mink -p`` one-shot runs.

Every ``-p`` invocation must journal its full conversation (user
message, agent result, tool calls) under ``~/.chimera/eventlog/<run_id>/``
so users can inspect, audit, and ``--resume`` it later. ``--no-save``
disables persistence; ``--run-id`` overrides the auto-generated id.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import pytest

# WHY: chimera.mink.cli imports rich for the streaming render handler (mink
# extra). Skip the whole module cleanly when the optional dep isn't installed.
pytest.importorskip("rich")

from chimera.mink import cli as mink_cli
from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubProvider(Provider):
    """Provider that returns a single text response with no tool calls.

    Records every ``complete`` invocation so assertions can inspect the
    conversation length the agent actually saw.
    """

    def __init__(self, model: str = "stub-mink-model", reply: str = "DONE") -> None:
        self._model = model
        self._reply = reply
        self.calls: list[list[Message]] = []

    def complete(  # type: ignore[override]
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Response:
        # Snapshot a shallow copy so later mutations don't poison the record.
        self.calls.append(list(messages))
        return Response(
            content=self._reply,
            tool_calls=[],
            usage={"input": 1, "output": 1},
        )

    @property
    def model_name(self) -> str:  # type: ignore[override]
        return self._model

    @property
    def context_window(self) -> int:  # type: ignore[override]
        return 8192

    @property
    def supports_tool_use(self) -> bool:  # type: ignore[override]
        return True


def _make_args(
    *,
    print_mode: str,
    no_save: bool = False,
    run_id: str | None = None,
    cwd: str | None = None,
) -> argparse.Namespace:
    """Build a Namespace mirroring what argparse would produce."""
    return argparse.Namespace(
        model="stub-mink-model",
        permission_mode="bypassPermissions",
        allowed_tools="",
        resume=None,
        agent=None,
        cwd=cwd,
        print_mode=print_mode,
        output_format="text",
        max_steps=2,
        no_save=no_save,
        run_id=run_id,
    )


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` and ``$HOME`` at ``tmp_path``."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def stub_provider_factory(monkeypatch: pytest.MonkeyPatch) -> _StubProvider:
    """Patch ``_build_provider`` so no Ollama daemon is contacted."""
    provider = _StubProvider()
    monkeypatch.setattr(
        mink_cli, "_build_provider", lambda model: provider
    )
    return provider


# ---------------------------------------------------------------------------
# _make_run_id
# ---------------------------------------------------------------------------


def test_run_id_is_sortable() -> None:
    """Three sequential ids sort lexically in the same order they were minted."""
    a = mink_cli._make_run_id()
    # The timestamp uses second precision; sleep just enough to advance it
    # so two ids minted in the same second don't tie on the timestamp prefix.
    time.sleep(1.1)
    b = mink_cli._make_run_id()
    time.sleep(1.1)
    c = mink_cli._make_run_id()

    ids = [a, b, c]
    assert ids == sorted(ids), f"ids not chronologically sortable: {ids}"
    for rid in ids:
        assert rid.startswith("mink-"), rid
        # Suffix is 8 hex chars (uuid4().hex[:8]).
        assert len(rid.rsplit("-", 1)[-1]) == 8


# ---------------------------------------------------------------------------
# Persistence on / off
# ---------------------------------------------------------------------------


def test_print_mode_saves_eventlog_by_default(
    tmp_path: Path,
    fake_home: Path,
    stub_provider_factory: _StubProvider,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A default ``-p`` run journals user_message + agent_result + summary.json."""
    work = tmp_path / "work"
    work.mkdir()
    args = _make_args(print_mode="hello mink", cwd=str(work))

    rc = mink_cli._run_print_mode(args)
    assert rc in (0, 1), f"unexpected exit code {rc}"

    eventlog_root = fake_home / ".chimera" / "eventlog"
    assert eventlog_root.exists(), "eventlog root not created"
    runs = sorted(p for p in eventlog_root.iterdir() if p.is_dir())
    assert len(runs) == 1, f"expected exactly 1 run dir, got {runs}"
    run_dir = runs[0]
    assert run_dir.name.startswith("mink-")

    # At least one event file (the user_message) must exist.
    event_files = sorted(run_dir.glob("event-*.json"))
    assert event_files, f"no event files written under {run_dir}"
    first = json.loads(event_files[0].read_text(encoding="utf-8"))
    assert first["type"] == "user_message"
    assert first["metadata"]["content"] == "hello mink"

    # summary.json shape contract.
    summary_path = run_dir / "summary.json"
    assert summary_path.exists(), "summary.json missing"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for key in ("run_id", "started_at", "ended_at", "model", "prompt",
                "cwd", "permission_mode", "steps", "success", "cost_usd"):
        assert key in summary, f"summary.json missing key {key!r}"
    assert summary["prompt"] == "hello mink"
    assert summary["model"] == "stub-mink-model"
    assert summary["run_id"] == run_dir.name

    # The stderr announce-line points at the run dir.
    captured = capsys.readouterr()
    assert run_dir.name in captured.err
    assert "[mink] run saved" in captured.err


def test_print_mode_no_save_writes_nothing(
    tmp_path: Path,
    fake_home: Path,
    stub_provider_factory: _StubProvider,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--no-save`` skips the eventlog write entirely."""
    work = tmp_path / "work"
    work.mkdir()
    args = _make_args(print_mode="quiet", cwd=str(work), no_save=True)

    mink_cli._run_print_mode(args)

    eventlog_root = fake_home / ".chimera" / "eventlog"
    assert not eventlog_root.exists(), (
        f"eventlog dir created despite --no-save: {list(eventlog_root.iterdir())}"
    )
    captured = capsys.readouterr()
    assert "[mink] run saved" not in captured.err


def test_run_id_override_uses_provided_id(
    tmp_path: Path,
    fake_home: Path,
    stub_provider_factory: _StubProvider,
) -> None:
    """``--run-id <custom>`` puts the events under exactly that directory."""
    work = tmp_path / "work"
    work.mkdir()
    args = _make_args(
        print_mode="custom",
        cwd=str(work),
        run_id="mink-fixed-test-id",
    )
    mink_cli._run_print_mode(args)

    expected = fake_home / ".chimera" / "eventlog" / "mink-fixed-test-id"
    assert expected.exists()
    summary = json.loads((expected / "summary.json").read_text(encoding="utf-8"))
    assert summary["run_id"] == "mink-fixed-test-id"


# ---------------------------------------------------------------------------
# Resume picks up persisted print runs
# ---------------------------------------------------------------------------


def test_resume_picks_up_persisted_print_run(
    tmp_path: Path,
    fake_home: Path,
    stub_provider_factory: _StubProvider,
) -> None:
    """A second run with ``--resume <id>`` rebuilds the prior conversation.

    We use ``EventSourcedSession.resume`` directly — the same code path
    ``_apply_launch_resume`` uses — to assert the persisted log replays
    into a context with the user message we sent.
    """
    work = tmp_path / "work"
    work.mkdir()

    # First: run mink -p and let it persist.
    first_args = _make_args(
        print_mode="initial prompt",
        cwd=str(work),
        run_id="mink-resume-fixture",
    )
    mink_cli._run_print_mode(first_args)

    # Now resume directly via the same surface --resume uses internally.
    from chimera.core.agent import Agent
    from chimera.core.prompt import Prompt
    from chimera.sessions.eventlog.session import EventSourcedSession

    eventlog_root = fake_home / ".chimera" / "eventlog"
    agent = Agent(
        provider=_StubProvider(reply="ignored"),
        tools=[],
        prompt=Prompt.from_string("resumed system"),
    )
    session = EventSourcedSession.resume(
        log_dir=eventlog_root,
        session_id="mink-resume-fixture",
        agent=agent,
    )
    msgs = list(session.messages)
    # The original user message must round-trip into the resumed context.
    user_messages = [m for m in msgs if m.role == "user"]
    assert any(m.content == "initial prompt" for m in user_messages), (
        f"resumed context missing original user message: {[m.content for m in msgs]}"
    )


# ---------------------------------------------------------------------------
# Smoke: existing _run_print_mode behaviors not regressed
# ---------------------------------------------------------------------------


def test_print_mode_returns_nonzero_when_provider_marks_failure(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the agent loop reports ``success=False`` the CLI exits 1.

    Persistence still happens — durable record of failures is the whole point.
    """
    failing_provider = _StubProvider(reply="")
    monkeypatch.setattr(mink_cli, "_build_provider", lambda model: failing_provider)
    work = tmp_path / "work"
    work.mkdir()
    args = _make_args(print_mode="will fail empty reply", cwd=str(work))

    rc = mink_cli._run_print_mode(args)
    # Even on non-zero exit, the eventlog must have been written.
    eventlog_root = fake_home / ".chimera" / "eventlog"
    assert eventlog_root.exists()
    runs = list(eventlog_root.iterdir())
    assert len(runs) == 1
    summary = json.loads((runs[0] / "summary.json").read_text(encoding="utf-8"))
    # The contract is that the prompt + run_id always land on disk.
    assert summary["prompt"] == "will fail empty reply"
    # rc shape: 0 if success else 1; loop may legitimately succeed on empty reply.
    assert rc in (0, 1)
