"""Tests for chimera.context.agent_memory walk-up memory loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.context.agent_memory import (
    discover_memory_files,
    inject_memory,
    load_memory,
    parse_frontmatter,
    resolve_imports,
)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``Path.home()`` at an empty tmp dir so user-global memory is absent."""
    home = tmp_path / "_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_walk_up_collects_all_levels(tmp_path: Path, isolated_home: Path) -> None:
    """Nested CLAUDE.md files at root, mid, leaf are all collected in order."""
    root = tmp_path / "proj"
    mid = root / "pkg"
    leaf = mid / "sub"
    leaf.mkdir(parents=True)

    (root / "CLAUDE.md").write_text("ROOT")
    (mid / "CLAUDE.md").write_text("MID")
    (leaf / "CLAUDE.md").write_text("LEAF")

    files = discover_memory_files(leaf)
    names = [str(f.relative_to(tmp_path)) for f in files]

    # Root-most first; only those three should be collected (no user-global).
    assert names == [
        "proj/CLAUDE.md",
        "proj/pkg/CLAUDE.md",
        "proj/pkg/sub/CLAUDE.md",
    ]

    text = load_memory(leaf)
    # All three contents present, root before leaf.
    assert text.index("ROOT") < text.index("MID") < text.index("LEAF")


def test_at_import_resolution(tmp_path: Path, isolated_home: Path) -> None:
    """``@./snippets/style.md`` resolves to the imported file's content."""
    root = tmp_path / "proj"
    snip = root / "snippets"
    snip.mkdir(parents=True)
    (snip / "style.md").write_text("USE_TABS_NOT_SPACES")
    (root / "CLAUDE.md").write_text("Header\n@./snippets/style.md\nFooter")

    text = load_memory(root)
    assert "USE_TABS_NOT_SPACES" in text
    assert "@./snippets/style.md" not in text


def test_at_import_cycle_breaks(tmp_path: Path, isolated_home: Path) -> None:
    """A imports B imports A: terminates without infinite loop."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "A.md").write_text("A_BODY @./B.md A_TAIL")
    (root / "B.md").write_text("B_BODY @./A.md B_TAIL")
    (root / "CLAUDE.md").write_text("ENTRY @./A.md END")

    # Should not infinite loop.
    text = load_memory(root)
    assert "ENTRY" in text
    assert "A_BODY" in text
    assert "B_BODY" in text
    assert "END" in text


def test_at_import_max_hops(tmp_path: Path, isolated_home: Path) -> None:
    """Chain of 7 imports, with ``max_hops=5`` only 5 hops are followed."""
    root = tmp_path / "proj"
    root.mkdir()
    # f0 imports f1 imports f2 ... imports f6.
    for i in range(7):
        nxt = f"@./f{i + 1}.md" if i < 6 else ""
        (root / f"f{i}.md").write_text(f"L{i} {nxt}")
    (root / "CLAUDE.md").write_text("@./f0.md")

    text = load_memory(root, max_hops=5)
    # The entry point uses one hop budget, then 5 chained file expansions.
    # With max_hops=5 starting on CLAUDE.md content, recursion expands until
    # budget exhausted; final unexpanded `@./fN.md` token remains.
    assert "L0" in text
    # At least one of the deeper labels should NOT be expanded inline.
    assert "L6" not in text or text.count("@./f") >= 1


def test_paths_frontmatter_glob(tmp_path: Path, isolated_home: Path) -> None:
    """Rule with ``paths: ['**/*.ts']`` only injects when cwd contains TS files."""
    root = tmp_path / "proj"
    rules = root / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "typescript.md").write_text(
        "---\npaths:\n  - '**/*.ts'\n---\nTS_RULE_BODY\n"
    )

    # Case 1: no TS file -> rule excluded.
    files = discover_memory_files(root)
    assert all("typescript.md" not in str(f) for f in files)

    # Case 2: TS file present -> rule included.
    (root / "main.ts").write_text("export {};")
    files = discover_memory_files(root)
    assert any("typescript.md" in str(f) for f in files)


def test_user_global_appended(tmp_path: Path, isolated_home: Path) -> None:
    """``~/.claude/CLAUDE.md`` is included at the end of the discovery list."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "CLAUDE.md").write_text("PROJECT")

    user_dir = isolated_home / ".claude"
    user_dir.mkdir()
    user_md = user_dir / "CLAUDE.md"
    user_md.write_text("USER_GLOBAL")

    files = discover_memory_files(root)
    assert files[-1].resolve() == user_md.resolve()

    text = load_memory(root)
    assert text.index("PROJECT") < text.index("USER_GLOBAL")


def test_parse_frontmatter_inline_list() -> None:
    """Inline list form ``paths: [a, b]`` parses correctly."""
    fm, body = parse_frontmatter("---\npaths: ['**/*.ts', 'src/**']\n---\nbody\n")
    assert fm["paths"] == ["**/*.ts", "src/**"]
    assert body.strip() == "body"


def test_parse_frontmatter_block_list() -> None:
    """Block list form parses correctly."""
    fm, body = parse_frontmatter(
        "---\npaths:\n  - '**/*.ts'\n  - 'src/**'\n---\nbody\n"
    )
    assert fm["paths"] == ["**/*.ts", "src/**"]
    assert "body" in body


def test_parse_frontmatter_no_header() -> None:
    """Text without frontmatter returns empty dict and original text."""
    fm, body = parse_frontmatter("just body, no fm\n")
    assert fm == {}
    assert body == "just body, no fm\n"


def test_resolve_imports_max_hops_zero() -> None:
    """``hops=0`` short-circuits and returns content untouched."""
    out = resolve_imports("hello @./x.md", Path("/nonexistent"), hops=0)
    assert out == "hello @./x.md"


def test_inject_memory_after_system(tmp_path: Path, isolated_home: Path) -> None:
    """Memory is inserted after a system message and before the first user."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "CLAUDE.md").write_text("REMEMBER_THIS")

    msgs = [
        {"role": "system", "content": "you are an agent"},
        {"role": "user", "content": "hi"},
    ]
    out = inject_memory(msgs, root)
    assert len(out) == 3
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    assert "REMEMBER_THIS" in out[1]["content"]
    assert out[2]["role"] == "user"
    assert out[2]["content"] == "hi"


def test_inject_memory_no_system(tmp_path: Path, isolated_home: Path) -> None:
    """When there's no system message, memory goes at position 0."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "CLAUDE.md").write_text("HELLO")
    out = inject_memory([{"role": "user", "content": "q"}], root)
    assert out[0]["role"] == "user"
    assert "HELLO" in out[0]["content"]


def test_inject_memory_empty_returns_copy(
    tmp_path: Path, isolated_home: Path
) -> None:
    """When no memory files exist, original messages are returned (copy)."""
    root = tmp_path / "proj"
    root.mkdir()
    msgs = [{"role": "user", "content": "x"}]
    out = inject_memory(msgs, root)
    assert out == msgs
    assert out is not msgs  # shallow copy
