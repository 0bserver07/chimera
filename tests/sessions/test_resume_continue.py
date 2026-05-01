"""Tests for the C1 (wave 9) ``--resume`` / ``--continue`` resume helpers.

The helpers under test live in
:mod:`chimera.sessions.eventlog.resume_helpers` and back the new
``--resume <id>`` + ``-c`` / ``--continue`` flag pair on every CLI in
the Chimera coding-agent set (mink / otter / ferret / weasel / shrew).
The contract being verified here is intentionally narrow:

* ``find_latest_run`` returns the newest run id matching the requested
  ``<prefix>``, optionally filtering by ``cwd``.
* ``resolve_resume_id`` combines explicit ``--resume`` and the boolean
  ``-c`` toggle into a single id, with the explicit id always winning.
* ``resume_run`` round-trips a JSONL eventlog through
  :meth:`EventSourcedSession.resume` and surfaces the replayed
  conversation via the session's ``messages`` property.
* ``build_resume_prefix`` renders replayed messages into a
  ``<prior_conversation>`` block suitable for prepending onto a fresh
  ``-p`` prompt.

The fixture stages three synthetic ``otter-*`` runs on disk plus a
``weasel-*`` decoy so the prefix filter is exercised; tests then drive
each helper against that corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.events.base import Event
from chimera.sessions.eventlog import (
    ResumeAgentShim,
    build_resume_prefix,
    find_latest_run,
    resolve_resume_id,
    resume_run,
)
from chimera.sessions.eventlog.log import EventLog


def _seed_run(
    root: Path,
    run_id: str,
    *,
    user: str,
    assistant: str,
    cwd: str | None = None,
) -> Path:
    """Stage one persisted run on disk: events + summary.

    Args:
        root: The eventlog root (typically the per-test tmp_path).
        run_id: Directory name for this run (e.g. ``"otter-..."``).
        user: User-message content to seed.
        assistant: Agent-result output to seed.
        cwd: Persisted ``cwd`` field for ``summary.json``. ``None``
            means "skip cwd field" so we can verify the cwd filter
            handles missing summaries.

    Returns:
        The run directory path.
    """
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    log = EventLog(run_dir)
    log.append(Event(type="user_message", metadata={"content": user}))
    log.append(
        Event(
            type="agent_result",
            metadata={
                "output": assistant,
                "steps": 1,
                "tool_calls_total": 0,
                "cost": 0.0,
                "success": True,
                "error": None,
            },
        ),
    )

    summary: dict[str, object] = {
        "run_id": run_id,
        "session_id": run_id,
        "model": "test-model",
        "prompt": user,
        "agent": run_id.split("-")[0],
        "success": True,
    }
    if cwd is not None:
        summary["cwd"] = cwd
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run_dir


@pytest.fixture()
def staged_runs(tmp_path: Path) -> dict[str, Path]:
    """Stage three otter runs (oldest → newest) plus a weasel decoy.

    The three otter runs share a single fake cwd; the decoy lives under
    a different cwd so cwd filters can be verified without picking it
    up. Run ids embed UTC timestamps in lexical-sort-order so
    ``find_latest_run`` can pick the newest by string comparison alone.
    """
    cwd = str(tmp_path / "project")
    other_cwd = str(tmp_path / "elsewhere")

    paths: dict[str, Path] = {}
    paths["older"] = _seed_run(
        tmp_path,
        "otter-20260101T010101-aaaaaaaa",
        user="first turn",
        assistant="first reply",
        cwd=cwd,
    )
    paths["middle"] = _seed_run(
        tmp_path,
        "otter-20260201T020202-bbbbbbbb",
        user="second turn",
        assistant="second reply",
        cwd=cwd,
    )
    paths["newest"] = _seed_run(
        tmp_path,
        "otter-20260301T030303-cccccccc",
        user="third turn",
        assistant="third reply",
        cwd=cwd,
    )
    paths["decoy"] = _seed_run(
        tmp_path,
        "weasel-20260301T030303-dddddddd",
        user="decoy",
        assistant="decoy reply",
        cwd=other_cwd,
    )
    paths["root"] = tmp_path
    paths["cwd"] = Path(cwd)
    paths["other_cwd"] = Path(other_cwd)
    return paths


def test_find_latest_run_picks_newest_by_prefix(staged_runs: dict[str, Path]) -> None:
    """``find_latest_run`` returns the lexically-newest matching id."""
    root = staged_runs["root"]
    latest = find_latest_run("otter-", root)
    assert latest == "otter-20260301T030303-cccccccc"


def test_find_latest_run_respects_cwd_filter(staged_runs: dict[str, Path]) -> None:
    """``cwd=`` filters out runs whose summary cwd doesn't match."""
    root = staged_runs["root"]
    cwd = str(staged_runs["cwd"])
    latest = find_latest_run("otter-", root, cwd=cwd)
    assert latest == "otter-20260301T030303-cccccccc"

    # The decoy is the only weasel-* run and its cwd doesn't match the
    # otter cwd; querying with the otter cwd should drop it from the
    # weasel candidate set.
    weasel_in_otter_cwd = find_latest_run("weasel-", root, cwd=cwd)
    assert weasel_in_otter_cwd is None


def test_find_latest_run_skips_unmatched_prefix(staged_runs: dict[str, Path]) -> None:
    """A prefix with no matches returns ``None``."""
    root = staged_runs["root"]
    assert find_latest_run("ferret-", root) is None


def test_find_latest_run_handles_missing_root(tmp_path: Path) -> None:
    """A non-existent eventlog root yields ``None`` (no crash)."""
    missing = tmp_path / "does-not-exist"
    assert find_latest_run("otter-", missing) is None


def test_resolve_resume_id_explicit_wins(staged_runs: dict[str, Path]) -> None:
    """``--resume <id>`` always overrides ``-c`` when both are set."""
    root = staged_runs["root"]
    resolved = resolve_resume_id(
        explicit_id="otter-20260101T010101-aaaaaaaa",
        continue_latest=True,
        prefix="otter-",
        eventlog_root=root,
    )
    assert resolved == "otter-20260101T010101-aaaaaaaa"


def test_resolve_resume_id_continue_picks_newest(staged_runs: dict[str, Path]) -> None:
    """``-c`` resolves to ``find_latest_run`` when ``--resume`` is unset."""
    root = staged_runs["root"]
    resolved = resolve_resume_id(
        explicit_id=None,
        continue_latest=True,
        prefix="otter-",
        eventlog_root=root,
        cwd=str(staged_runs["cwd"]),
    )
    assert resolved == "otter-20260301T030303-cccccccc"


def test_resolve_resume_id_neither_returns_none(staged_runs: dict[str, Path]) -> None:
    """When neither flag is set the resolver returns ``None``."""
    root = staged_runs["root"]
    assert (
        resolve_resume_id(
            explicit_id=None,
            continue_latest=False,
            prefix="otter-",
            eventlog_root=root,
        )
        is None
    )


def test_resume_run_hydrates_messages(staged_runs: dict[str, Path]) -> None:
    """``resume_run`` replays the JSONL log and surfaces messages."""
    root = staged_runs["root"]
    session = resume_run(
        "otter-20260201T020202-bbbbbbbb",
        agent=ResumeAgentShim(),
        eventlog_root=root,
    )
    contents = [m.content for m in session.messages]
    assert "second turn" in contents
    assert "second reply" in contents


def test_resume_run_default_agent_shim(staged_runs: dict[str, Path]) -> None:
    """``resume_run`` defaults to a built-in shim when no agent is passed."""
    root = staged_runs["root"]
    session = resume_run(
        "otter-20260101T010101-aaaaaaaa",
        eventlog_root=root,
    )
    assert any(m.content == "first turn" for m in session.messages)


def test_resume_run_unknown_id_raises(staged_runs: dict[str, Path]) -> None:
    """Resuming an id with no log directory raises ``ValueError``."""
    root = staged_runs["root"]
    with pytest.raises(ValueError):
        resume_run("otter-does-not-exist", eventlog_root=root)


def test_build_resume_prefix_renders_block(staged_runs: dict[str, Path]) -> None:
    """``build_resume_prefix`` wraps replayed messages in XML tags."""
    root = staged_runs["root"]
    session = resume_run(
        "otter-20260301T030303-cccccccc", eventlog_root=root,
    )
    rendered = build_resume_prefix(list(session.messages))
    assert rendered.startswith("<prior_conversation>")
    assert rendered.endswith("</prior_conversation>\n\n")
    assert "third turn" in rendered
    assert "third reply" in rendered


def test_build_resume_prefix_empty_messages_returns_empty() -> None:
    """An empty messages list yields ``""`` (callers can unconditionally prepend)."""
    assert build_resume_prefix([]) == ""


def test_build_resume_prefix_truncates_when_oversized() -> None:
    """When the transcript exceeds ``max_chars`` we drop oldest turns."""
    from chimera.types import Message

    big = "x" * 500
    msgs = [Message.user(big), Message.assistant(big), Message.user("keep me")]
    rendered = build_resume_prefix(msgs, max_chars=100)
    assert "keep me" in rendered
    assert "[truncated" in rendered
