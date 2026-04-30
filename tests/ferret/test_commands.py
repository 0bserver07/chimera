"""Tests for ``chimera.ferret.commands`` — custom command ingest.

Covers:

* Project-only scan finds ``.codex/command/*.md`` files.
* User-only scan finds ``~/.codex/command/*.md`` files.
* Project entries override user entries on name conflict.
* Frontmatter parsing extracts ``description`` and ``args`` correctly.
* Body template renders with ``$1``, ``$2``, ``$ARGUMENTS``, ``$ARG_NAME``.
* Files without frontmatter still load (body becomes the template).
* Malformed / empty files are silently skipped instead of raising.
* The ``commands`` (plural) directory alias is honored.
* ``$10`` substitutes correctly even when ``$1`` is also present.

Tests are stdlib-only and use ``tmp_path`` so they never touch the real
``~/.codex/`` directory.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.ferret.commands import (
    CustomCommand,
    CustomCommandArg,
    load_custom_commands,
    parse_command_file,
)


# -- Helpers --

def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# -- parse_command_file --

def test_parse_basic_frontmatter(tmp_path: Path) -> None:
    """A simple file with description + body parses cleanly."""
    f = _write(
        tmp_path / "review.md",
        """---
description: Review a diff
---
Please review:
$1
""",
    )
    cmd = parse_command_file(f)
    assert cmd is not None
    assert cmd.name == "review"
    assert cmd.description == "Review a diff"
    assert cmd.body_template == "Please review:\n$1"
    assert cmd.args == []
    assert cmd.source == str(f)


def test_parse_args_list_of_dicts(tmp_path: Path) -> None:
    """``args:`` as a list of ``{name, description}`` dicts is parsed."""
    f = _write(
        tmp_path / "explain.md",
        """---
description: Explain a file
args:
  - name: target
    description: file path
  - name: focus
    description: optional aspect
---
Explain $TARGET focusing on $FOCUS.
""",
    )
    cmd = parse_command_file(f)
    assert cmd is not None
    assert len(cmd.args) == 2
    assert cmd.args[0] == CustomCommandArg(name="target", description="file path")
    assert cmd.args[1] == CustomCommandArg(name="focus", description="optional aspect")


def test_parse_args_list_of_strings(tmp_path: Path) -> None:
    """``args:`` as a list of bare names also works."""
    f = _write(
        tmp_path / "deploy.md",
        """---
description: Deploy
args:
  - env
  - tag
---
Deploy to $env tag $tag
""",
    )
    cmd = parse_command_file(f)
    assert cmd is not None
    assert [a.name for a in cmd.args] == ["env", "tag"]


def test_parse_no_frontmatter(tmp_path: Path) -> None:
    """A file with no frontmatter still loads — body becomes the template."""
    f = _write(tmp_path / "raw.md", "Just a raw prompt with $1 placeholder.\n")
    cmd = parse_command_file(f)
    assert cmd is not None
    assert cmd.name == "raw"
    assert cmd.description == ""
    assert cmd.body_template == "Just a raw prompt with $1 placeholder."


def test_parse_empty_file(tmp_path: Path) -> None:
    """Empty files return None instead of raising."""
    f = _write(tmp_path / "empty.md", "")
    assert parse_command_file(f) is None


def test_parse_quoted_description(tmp_path: Path) -> None:
    """Quoted scalar values get unquoted."""
    f = _write(
        tmp_path / "q.md",
        """---
description: "review: with colon"
---
body
""",
    )
    cmd = parse_command_file(f)
    assert cmd is not None
    assert cmd.description == "review: with colon"


# -- render --

def test_render_positional() -> None:
    """``$1`` / ``$2`` substitution works in declaration order."""
    cmd = CustomCommand(name="t", body_template="A=$1 B=$2")
    assert cmd.render("alpha", "beta") == "A=alpha B=beta"


def test_render_named() -> None:
    """``$ARG_NAME`` substitution honors the declared arg list."""
    cmd = CustomCommand(
        name="t",
        args=[CustomCommandArg(name="target")],
        body_template="See $target file",
    )
    assert cmd.render(target="README.md") == "See README.md file"


def test_render_arguments_keyword() -> None:
    """``$ARGUMENTS`` joins all positional arguments with spaces."""
    cmd = CustomCommand(name="t", body_template="Args: $ARGUMENTS")
    assert cmd.render("foo", "bar", "baz") == "Args: foo bar baz"


def test_render_dollar_ten_before_dollar_one() -> None:
    """``$10`` should substitute correctly even when ``$1`` is present."""
    args = [str(i) for i in range(1, 11)]
    cmd = CustomCommand(name="t", body_template="first=$1 last=$10")
    out = cmd.render(*args)
    assert out == "first=1 last=10"


def test_render_unknown_placeholders_left_intact() -> None:
    """Unsubstituted ``$VAR`` placeholders are left as-is."""
    cmd = CustomCommand(name="t", body_template="Got $1 and $UNKNOWN")
    assert cmd.render("hello") == "Got hello and $UNKNOWN"


def test_render_named_case_insensitive() -> None:
    """``$FOO`` and ``$foo`` both substitute when ``foo`` is declared."""
    cmd = CustomCommand(
        name="t",
        args=[CustomCommandArg(name="target")],
        body_template="lower=$target upper=$TARGET",
    )
    out = cmd.render(target="X")
    assert out == "lower=X upper=X"


# -- load_custom_commands --

def test_load_project_only(tmp_path: Path) -> None:
    """Project-level ``.codex/command/*.md`` files load."""
    _write(
        tmp_path / ".codex" / "command" / "ship.md",
        "---\ndescription: Ship it\n---\nShip the diff.\n",
    )
    _write(
        tmp_path / ".codex" / "command" / "review.md",
        "Review the changes.\n",
    )
    cmds = load_custom_commands(tmp_path, user_dirs=())
    assert set(cmds) == {"ship", "review"}
    assert cmds["ship"].description == "Ship it"
    assert cmds["review"].body_template == "Review the changes."


def test_load_user_only(tmp_path: Path) -> None:
    """User-level dirs picked up via the ``user_dirs`` override."""
    user_dir = tmp_path / "home" / ".codex" / "command"
    _write(user_dir / "tidy.md", "---\ndescription: Tidy up\n---\nTidy.\n")
    cmds = load_custom_commands(project_root=None, user_dirs=(user_dir,))
    assert set(cmds) == {"tidy"}
    assert cmds["tidy"].description == "Tidy up"


def test_project_overrides_user(tmp_path: Path) -> None:
    """Project entries clobber user entries with the same name."""
    user_dir = tmp_path / "home" / ".codex" / "command"
    _write(user_dir / "ship.md", "---\ndescription: USER ship\n---\nUSER body\n")

    project = tmp_path / "proj"
    _write(
        project / ".codex" / "command" / "ship.md",
        "---\ndescription: PROJECT ship\n---\nPROJECT body\n",
    )

    cmds = load_custom_commands(project, user_dirs=(user_dir,))
    assert cmds["ship"].description == "PROJECT ship"
    assert cmds["ship"].body_template == "PROJECT body"


def test_plural_alias_honored(tmp_path: Path) -> None:
    """Both ``command/`` and ``commands/`` directories are scanned."""
    project = tmp_path
    _write(
        project / ".codex" / "command" / "a.md",
        "from singular dir",
    )
    _write(
        project / ".codex" / "commands" / "b.md",
        "from plural dir",
    )
    cmds = load_custom_commands(project, user_dirs=())
    assert {"a", "b"} <= set(cmds)


def test_load_handles_missing_dirs(tmp_path: Path) -> None:
    """No ``.codex/`` dir at all returns an empty mapping (no crash)."""
    cmds = load_custom_commands(tmp_path, user_dirs=())
    assert cmds == {}


def test_load_skips_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``OSError`` while reading one file does not abort the whole scan."""
    project = tmp_path
    _write(project / ".codex" / "command" / "ok.md", "good")
    bad = _write(project / ".codex" / "command" / "bad.md", "bad")

    real_read_text = Path.read_text

    def fake_read_text(self: Path, *a: object, **kw: object) -> str:
        if self == bad:
            raise OSError("simulated unreadable")
        return real_read_text(self, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    cmds = load_custom_commands(project, user_dirs=())
    assert "ok" in cmds
    assert "bad" not in cmds
    assert cmds["ok"].body_template == "good"
