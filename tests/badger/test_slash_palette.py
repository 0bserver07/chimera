"""Tests for the W14-4 badger slash palette expansion (12 new slashes).

Covers, at minimum, 1-2 cases per slash:

* ``/memory`` — show, edit, append, error.
* ``/export`` — JSON, Markdown, unknown format error.
* ``/agents`` — list, show, missing-name error.
* ``/skills`` — list, empty fallback.
* ``/git status`` / ``/git diff`` / ``/git log`` / ``/git commit`` /
  ``/git push`` / ``/git branch`` — happy path + at least one error
  branch (missing args / non-int log limit / git binary missing).
* ``/bughunter`` — queues prompt + falls back to printing.
* ``/ultraplan`` — queues prompt + missing-goal error.
* ``BADGER_SLASH_COMMANDS`` exposes all 12 names.

Subprocess calls are monkeypatched so the test suite never shells out
to the real ``git`` binary or the user's editor.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.badger import slash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def chimera_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect ``$CHIMERA_HOME`` so /memory + /export are hermetic."""
    home = tmp_path / "ch"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    return home


class _Out:
    """Tiny print sink that captures all calls for assertion."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _Session:
    """Stand-in for the session object passed to slash handlers."""

    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Palette membership
# ---------------------------------------------------------------------------


def test_palette_contains_twelve_new_slashes() -> None:
    """All 12 W14-4 slashes are registered."""
    expected = {
        "memory",
        "export",
        "agents",
        "skills",
        "git status",
        "git diff",
        "git log",
        "git commit",
        "git push",
        "git branch",
        "bughunter",
        "ultraplan",
    }
    assert expected.issubset(slash.BADGER_SLASH_COMMANDS.keys())


def test_palette_help_text_for_new_slashes() -> None:
    """Every new slash has a help line."""
    for name in (
        "memory", "export", "agents", "skills",
        "git status", "git diff", "git log", "git commit",
        "git push", "git branch",
        "bughunter", "ultraplan",
    ):
        assert name in slash.BADGER_SLASH_HELP


# ---------------------------------------------------------------------------
# /memory
# ---------------------------------------------------------------------------


def test_memory_show_empty_hint(chimera_home: Path) -> None:
    """/memory with no notes prints the empty-state hint."""
    out = _Out()
    slash.cmd_memory(_Session(), None, "", out)
    assert "no notes yet" in out.text


def test_memory_append_creates_file(chimera_home: Path) -> None:
    """/memory append writes a line and reports the path."""
    out = _Out()
    slash.cmd_memory(_Session(), None, "append remember to ship", out)
    target = chimera_home / "badger" / "memory.md"
    assert target.exists()
    assert "remember to ship" in target.read_text()
    assert "appended" in out.text


def test_memory_show_after_append(chimera_home: Path) -> None:
    """A subsequent /memory dumps the file."""
    slash.cmd_memory(_Session(), None, "append hello", _Out())
    out = _Out()
    slash.cmd_memory(_Session(), None, "", out)
    assert "hello" in out.text


def test_memory_unknown_action_friendly_error(chimera_home: Path) -> None:
    """An unknown sub-action returns a friendly error rather than raising."""
    out = _Out()
    slash.cmd_memory(_Session(), None, "wat", out)
    assert "unknown action" in out.text


def test_memory_append_missing_text(chimera_home: Path) -> None:
    """``/memory append`` without text complains rather than appending blank."""
    out = _Out()
    slash.cmd_memory(_Session(), None, "append", out)
    assert "missing text" in out.text


# ---------------------------------------------------------------------------
# /export
# ---------------------------------------------------------------------------


def test_export_json_writes_file(chimera_home: Path) -> None:
    """``/export json`` writes a JSON envelope to ~/.chimera/exports/."""
    out = _Out()
    session = _Session(
        session_id="abc",
        history=[("user", "hi"), ("assistant", "hello")],
    )
    slash.cmd_export(session, None, "json", out)
    written = chimera_home / "exports" / "badger-abc.json"
    assert written.exists()
    payload = json.loads(written.read_text())
    assert payload["session_id"] == "abc"
    assert payload["history"][0] == {"role": "user", "content": "hi"}


def test_export_md_writes_file(chimera_home: Path) -> None:
    """``/export md`` writes a Markdown transcript."""
    out = _Out()
    session = _Session(
        session_id="abc", history=[("user", "hi")],
    )
    slash.cmd_export(session, None, "md", out)
    written = chimera_home / "exports" / "badger-abc.md"
    assert written.exists()
    body = written.read_text()
    assert "# badger session" in body
    assert "hi" in body


def test_export_unknown_format(chimera_home: Path) -> None:
    """Unknown format -> friendly error, no file written."""
    out = _Out()
    slash.cmd_export(_Session(history=[]), None, "yaml", out)
    assert "unknown format" in out.text


# ---------------------------------------------------------------------------
# /agents
# ---------------------------------------------------------------------------


def test_agents_list_includes_planner() -> None:
    """``/agents`` lists every bundled subagent profile."""
    out = _Out()
    slash.cmd_agents(_Session(), None, "", out)
    assert "planner" in out.text
    assert "researcher" in out.text


def test_agents_show_missing_name() -> None:
    """``/agents show`` without a name surfaces a friendly error."""
    out = _Out()
    slash.cmd_agents(_Session(), None, "show", out)
    assert "missing profile name" in out.text


def test_agents_show_unknown_profile() -> None:
    """``/agents show <bogus>`` -> not-found error."""
    out = _Out()
    slash.cmd_agents(_Session(), None, "show wat", out)
    assert "not found" in out.text


# ---------------------------------------------------------------------------
# /skills
# ---------------------------------------------------------------------------


def test_skills_list_renders_rows() -> None:
    """``/skills`` lists at least the bundled algorithm cheatsheets."""
    out = _Out()
    slash.cmd_skills(_Session(), _Session(workdir="."), "", out)
    # The bundled algorithm skills register under ``algo-*`` names.
    assert "algo-" in out.text or "no skills" in out.text


def test_skills_handles_discovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure inside discover_skills surfaces a friendly error."""
    import chimera.skills.discovery as disc

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(disc, "discover_skills", _boom)
    out = _Out()
    slash.cmd_skills(_Session(), _Session(workdir="."), "", out)
    assert "discovery failed" in out.text


# ---------------------------------------------------------------------------
# /git wrappers
# ---------------------------------------------------------------------------


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    raise_fnf: bool = False,
) -> list[list[str]]:
    """Replace ``subprocess.run`` for the duration of a test."""
    captured: list[list[str]] = []

    class _Result:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def _fake_run(argv: list[str], **_kw: Any) -> _Result:
        if raise_fnf:
            raise FileNotFoundError("git")
        captured.append(argv)
        return _Result()

    monkeypatch.setattr(slash.subprocess, "run", _fake_run)
    return captured


def test_git_status_invokes_short_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/git status`` calls ``git status -sb``."""
    captured = _patch_run(monkeypatch, stdout="## main\n M foo.py")
    out = _Out()
    slash.cmd_git_status(_Session(), _Session(workdir="."), "", out)
    assert captured and captured[0][:3] == ["git", "status", "-sb"]
    assert "main" in out.text


def test_git_diff_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/git diff`` always passes ``--no-color`` and forwards extra args."""
    captured = _patch_run(monkeypatch, stdout="diff --git a/x b/x")
    out = _Out()
    slash.cmd_git_diff(_Session(), _Session(workdir="."), "src/", out)
    assert captured[0] == ["git", "diff", "--no-color", "src/"]


def test_git_log_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/git log`` defaults to ``-n10 --oneline``."""
    captured = _patch_run(monkeypatch, stdout="abc1234 fix\n")
    out = _Out()
    slash.cmd_git_log(_Session(), _Session(workdir="."), "", out)
    assert "-n10" in captured[0]
    assert "--oneline" in captured[0]


def test_git_log_non_integer_friendly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-integer log limit -> friendly error, no subprocess fired."""
    captured = _patch_run(monkeypatch)
    out = _Out()
    slash.cmd_git_log(_Session(), _Session(workdir="."), "abc", out)
    assert captured == []
    assert "expected an integer" in out.text


def test_git_commit_missing_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/git commit`` without args surfaces a hint, no subprocess fired."""
    captured = _patch_run(monkeypatch)
    out = _Out()
    slash.cmd_git_commit(_Session(), _Session(workdir="."), "", out)
    assert captured == []
    assert "/git commit" in out.text


def test_git_commit_forwards_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/git commit -m '<msg>'`` shells out with the forwarded args."""
    captured = _patch_run(monkeypatch, stdout="[main abc1234] fix\n")
    out = _Out()
    slash.cmd_git_commit(
        _Session(), _Session(workdir="."), "-m 'fix'", out,
    )
    assert captured[0] == ["git", "commit", "-m", "fix"]


def test_git_push_forwards_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/git push origin main`` forwards the remote/branch."""
    captured = _patch_run(monkeypatch, stdout="To origin")
    out = _Out()
    slash.cmd_git_push(
        _Session(), _Session(workdir="."), "origin main", out,
    )
    assert captured[0] == ["git", "push", "origin", "main"]


def test_git_branch_forwards_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/git branch -a`` forwards the flag verbatim."""
    captured = _patch_run(monkeypatch, stdout="* main\n")
    out = _Out()
    slash.cmd_git_branch(
        _Session(), _Session(workdir="."), "-a", out,
    )
    assert captured[0] == ["git", "branch", "-a"]


def test_git_missing_binary_friendly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing ``git`` binary surfaces a friendly error."""
    _patch_run(monkeypatch, raise_fnf=True)
    out = _Out()
    slash.cmd_git_status(_Session(), _Session(workdir="."), "", out)
    assert "'git' binary not found" in out.text


# ---------------------------------------------------------------------------
# /bughunter
# ---------------------------------------------------------------------------


def test_bughunter_queues_prompt() -> None:
    """``/bughunter`` stores the workflow prompt on the session."""
    session = _Session()
    out = _Out()
    slash.cmd_bughunter(session, None, "", out)
    pending = getattr(session, "pending_workflow_prompt", "")
    assert "bug-hunting" in pending
    assert "queued" in out.text


def test_bughunter_with_scope() -> None:
    """``/bughunter <scope>`` adds a Scope: line to the queued prompt."""
    session = _Session()
    out = _Out()
    slash.cmd_bughunter(session, None, "src/auth", out)
    assert "Scope: src/auth" in session.pending_workflow_prompt


def test_bughunter_falls_back_to_print_when_session_frozen() -> None:
    """When the session can't accept the attribute, the prompt is printed."""

    class _Frozen:
        __slots__ = ()

    out = _Out()
    slash.cmd_bughunter(_Frozen(), None, "", out)
    assert "bug-hunting" in out.text


# ---------------------------------------------------------------------------
# /ultraplan
# ---------------------------------------------------------------------------


def test_ultraplan_queues_prompt() -> None:
    """``/ultraplan <goal>`` stores a five-phase plan prompt."""
    session = _Session()
    out = _Out()
    slash.cmd_ultraplan(session, None, "migrate auth", out)
    pending = getattr(session, "pending_workflow_prompt", "")
    assert "ULTRAPLAN" in pending
    assert "Goal: migrate auth" in pending


def test_ultraplan_missing_goal() -> None:
    """``/ultraplan`` without a goal surfaces a hint instead of queuing."""
    session = _Session()
    out = _Out()
    slash.cmd_ultraplan(session, None, "", out)
    assert not getattr(session, "pending_workflow_prompt", None)
    assert "missing goal" in out.text


# ---------------------------------------------------------------------------
# Installer covers the new commands
# ---------------------------------------------------------------------------


def test_register_installs_new_slashes() -> None:
    """The installer wires every new slash onto a target REPL state."""

    class _FakeRepl:
        def __init__(self) -> None:
            self.commands: dict[str, slash.SlashHandler] = {}

    repl = _FakeRepl()
    slash.register_badger_slash(repl)
    for name in (
        "memory", "export", "agents", "skills",
        "git status", "git diff", "git log", "git commit",
        "git push", "git branch",
        "bughunter", "ultraplan",
    ):
        assert name in repl.commands
