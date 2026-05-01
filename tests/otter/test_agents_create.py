"""Regression tests for ``chimera otter agents create``.

The interactive scaffolder asks for the standard agent fields (name,
description, model, tools, system prompt) via stdlib :func:`input` and
writes a ``.opencode/agent/<name>.md`` file in either project- or
user-scope. Tests drive prompts by injecting an ``input_fn`` so we
never have to actually rebind ``builtins.input`` (the dispatcher path
test below also patches the builtin to prove the wave-9 wiring).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest


# ---------------------------------------------------------------------------
# Helper: a queue-backed fake ``input`` function
# ---------------------------------------------------------------------------


def _make_fake_input(answers: list[str]) -> "tuple[object, list[str]]":
    """Build a closure that returns scripted answers for sequential ``input()`` calls.

    Returns:
        Tuple of ``(callable, captured_prompts)`` so tests can inspect
        the exact prompt strings that were displayed.
    """
    queue: Iterator[str] = iter(answers)
    captured: list[str] = []

    def _fn(prompt: str = "") -> str:
        captured.append(prompt)
        try:
            return next(queue)
        except StopIteration:
            # Mirror EOF on stdin — useful for the multi-line block test.
            raise EOFError("scripted input exhausted")

    return _fn, captured


# ---------------------------------------------------------------------------
# 1. Happy path: project scope, all prompts answered
# ---------------------------------------------------------------------------


def test_create_writes_project_scope_md(tmp_path: Path) -> None:
    """``cmd_agents_create`` writes ``<cwd>/.opencode/agent/<name>.md``."""
    answers = [
        "myreviewer",          # name
        "Reviews diffs.",      # description
        "anthropic/claude-sonnet-4-6",  # model (Enter would also default)
        "bash, read_file",     # tools (comma-separated)
        "You are a careful reviewer.",  # system prompt line 1
        "Always cite line numbers.",     # system prompt line 2
        ".",                   # sentinel terminating the multi-line block
    ]
    fake, _ = _make_fake_input(answers)

    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        None, cwd=tmp_path, input_fn=fake, confirm=False,
    )
    assert rc == 0

    target = tmp_path / ".opencode" / "agent" / "myreviewer.md"
    assert target.exists(), f"{target} was not created"
    body = target.read_text()
    assert "name: myreviewer" in body
    assert 'description: "Reviews diffs."' in body
    assert "model: anthropic/claude-sonnet-4-6" in body
    assert "tools: [bash, read_file]" in body
    assert "You are a careful reviewer." in body
    assert "Always cite line numbers." in body


# ---------------------------------------------------------------------------
# 2. Round-trip: scaffold then re-read via AgentConfig.from_markdown
# ---------------------------------------------------------------------------


def test_create_roundtrips_through_agentconfig(tmp_path: Path) -> None:
    """A scaffolded file parses back as a valid :class:`AgentConfig`."""
    answers = [
        "rt",
        "Round trip.",
        "anthropic/claude-sonnet-4-6",
        "bash",
        "You are RT.",
        ".",
    ]
    fake, _ = _make_fake_input(answers)

    from chimera.agents.config import AgentConfig
    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        None, cwd=tmp_path, input_fn=fake, confirm=False,
    )
    assert rc == 0
    target = tmp_path / ".opencode" / "agent" / "rt.md"
    cfg = AgentConfig.from_markdown(str(target))
    assert cfg.name == "rt"
    assert cfg.description == "Round trip."
    assert cfg.model == "anthropic/claude-sonnet-4-6"
    assert cfg.tools == ["bash"]
    assert "You are RT." in cfg.system_prompt


# ---------------------------------------------------------------------------
# 3. CLI-supplied name is used as the prompt default
# ---------------------------------------------------------------------------


def test_create_uses_cli_name_as_default(tmp_path: Path) -> None:
    """An empty Enter at the name prompt accepts the CLI-provided default."""
    answers = [
        "",                      # name — Enter accepts default "preset"
        "",                      # description (Enter = empty)
        "",                      # model (Enter = chain default)
        "",                      # tools (Enter = inherit)
        "Body line.",
        ".",
    ]
    fake, _ = _make_fake_input(answers)

    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        "preset", cwd=tmp_path, input_fn=fake, confirm=False,
    )
    assert rc == 0
    target = tmp_path / ".opencode" / "agent" / "preset.md"
    assert target.exists()
    body = target.read_text()
    assert "name: preset" in body
    # Default model from the chain renders even when user accepts default.
    assert "model: anthropic/claude-sonnet-4-6" in body


# ---------------------------------------------------------------------------
# 4. ``--user`` flag (user=True) writes to ~/.opencode/agent/<name>.md
# ---------------------------------------------------------------------------


def test_create_user_scope_uses_home_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``user=True`` writes to ``~/.opencode/agent/`` instead of project."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]

    answers = [
        "global-agent",
        "User-scoped agent.",
        "anthropic/claude-sonnet-4-6",
        "",
        "Hello from home.",
        ".",
    ]
    fake, _ = _make_fake_input(answers)

    project = tmp_path / "proj"
    project.mkdir()

    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        None, user=True, cwd=project, input_fn=fake, confirm=False,
    )
    assert rc == 0
    user_target = fake_home / ".opencode" / "agent" / "global-agent.md"
    project_target = project / ".opencode" / "agent" / "global-agent.md"
    assert user_target.exists()
    assert not project_target.exists()


# ---------------------------------------------------------------------------
# 5. Refuses to overwrite an existing file
# ---------------------------------------------------------------------------


def test_create_refuses_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Existing target → exit 2 with an error pointing at the path."""
    target_dir = tmp_path / ".opencode" / "agent"
    target_dir.mkdir(parents=True)
    existing = target_dir / "dup.md"
    existing.write_text("---\nname: dup\n---\nbody")

    answers = ["dup"]  # only name needed; we abort before further prompts
    fake, _ = _make_fake_input(answers)

    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        None, cwd=tmp_path, input_fn=fake, confirm=False,
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "already exists" in err
    # File untouched.
    assert existing.read_text() == "---\nname: dup\n---\nbody"


# ---------------------------------------------------------------------------
# 6. Empty name prompts an error and exits 2
# ---------------------------------------------------------------------------


def test_create_empty_name_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty name (no CLI default) → exit 2 with a name-required error."""
    answers = [""]  # name prompt — empty with no default
    fake, _ = _make_fake_input(answers)

    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        None, cwd=tmp_path, input_fn=fake, confirm=False,
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "agent name is required" in err


# ---------------------------------------------------------------------------
# 7. Invalid characters in name are rejected
# ---------------------------------------------------------------------------


def test_create_rejects_path_separators(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Slashes / spaces in the name → exit 2 (no file written)."""
    answers = ["bad name/with-slash"]
    fake, _ = _make_fake_input(answers)

    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        None, cwd=tmp_path, input_fn=fake, confirm=False,
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "invalid characters" in err
    # No partial write.
    assert not (tmp_path / ".opencode").exists()


# ---------------------------------------------------------------------------
# 8. Confirmation gate: ``n`` aborts the write
# ---------------------------------------------------------------------------


def test_create_confirm_no_aborts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Answering ``n`` at the y/N gate exits 1 and writes nothing."""
    answers = [
        "abort-me",
        "Aborted scaffold.",
        "anthropic/claude-sonnet-4-6",
        "",
        "Body.",
        ".",
        "n",  # confirmation — no
    ]
    fake, _ = _make_fake_input(answers)

    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        None, cwd=tmp_path, input_fn=fake, confirm=True,
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "aborted" in err
    assert not (tmp_path / ".opencode" / "agent" / "abort-me.md").exists()


# ---------------------------------------------------------------------------
# 9. Tools prompt: unknown names are dropped, valid ones kept
# ---------------------------------------------------------------------------


def test_create_filters_unknown_tools(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """``Tools: bash, no_such_tool`` → only ``bash`` lands in frontmatter."""
    answers = [
        "filter",
        "Filters tools.",
        "anthropic/claude-sonnet-4-6",
        "bash, no_such_tool, read_file",
        "Body.",
        ".",
    ]
    fake, _ = _make_fake_input(answers)

    from chimera.otter.agents import cmd_agents_create

    rc = cmd_agents_create(
        None, cwd=tmp_path, input_fn=fake, confirm=False,
    )
    out = capsys.readouterr().out
    assert rc == 0
    target = tmp_path / ".opencode" / "agent" / "filter.md"
    body = target.read_text()
    assert "tools: [bash, read_file]" in body
    assert "no_such_tool" in out  # warning was printed


# ---------------------------------------------------------------------------
# 10. CLI dispatcher path: ``_dispatch_agents`` routes ``create`` correctly
# ---------------------------------------------------------------------------


def test_cli_dispatch_agents_create_writes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_dispatch_agents`` with action='create' invokes the scaffolder.

    Drives prompts via ``monkeypatch.setattr("builtins.input", ...)``
    per the W9 task brief — proves the wave-9 wiring (sub_action choice,
    --user dest) actually reaches :func:`cmd_agents_create`.
    """
    import argparse
    import builtins

    answers = iter([
        "wired",
        "Wired-up agent.",
        "anthropic/claude-sonnet-4-6",
        "bash",
        "Body.",
        ".",
        "y",  # confirm
    ])
    monkeypatch.setattr(builtins, "input", lambda *a, **kw: next(answers))

    from chimera.otter.cli import _dispatch_agents

    args = argparse.Namespace(
        sub_action="create",
        sub_target=None,
        no_color=True,
        no_rich=False,
        cwd=str(tmp_path),
        agents_user=False,
    )
    rc = _dispatch_agents(args)
    assert rc == 0
    target = tmp_path / ".opencode" / "agent" / "wired.md"
    assert target.exists()
    assert "name: wired" in target.read_text()
