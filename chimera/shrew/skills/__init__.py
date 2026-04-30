"""Curated skill markdown set for the ``chimera shrew`` subcommand.

Shrew ships a small, opinionated set of skill snippets specifically tuned
for small local coding models (9B–35B parameter range). The skills are
grouped under three subdirectories::

    knowledge/   # background concepts the model needs to do good work
    protocols/   # decision flows for common situations
    tools/       # how to use the agent's tools effectively

Each file is a markdown document with a short YAML-ish frontmatter block
listing ``name``, ``description``, and ``triggers`` (a list of phrases the
agent might see in a user turn that suggest this skill is relevant).

Public surface:

* :data:`SKILLS_ROOT` — directory containing the bundled skills.
* :data:`CATEGORIES` — tuple of subdirectory names in canonical order.
* :class:`ShrewSkill` — parsed-skill record.
* :func:`discover_shrew_skills` — walk :data:`SKILLS_ROOT` (and any extra
  search paths) and return the parsed skill list.
* :func:`format_shrew_skills_for_prompt` — render the parsed skills as a
  single markdown block suitable for system-prompt injection.

The discovery is deliberately stdlib-only and does not depend on PyYAML;
it parses the small subset of frontmatter syntax shrew skills use.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "CATEGORIES",
    "SKILLS_ROOT",
    "ShrewSkill",
    "discover_shrew_skills",
    "format_shrew_skills_for_prompt",
]


SKILLS_ROOT: Path = Path(__file__).resolve().parent
"""Root directory containing the bundled shrew skills."""

CATEGORIES: tuple[str, ...] = ("knowledge", "protocols", "tools")
"""Canonical ordering of skill subdirectories."""


_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class ShrewSkill:
    """A parsed shrew skill markdown file.

    Attributes:
        name: Slug-style identifier (lowercase, hyphenated).
        description: One-line summary.
        triggers: Phrases that suggest this skill is relevant. May be empty.
        category: One of :data:`CATEGORIES`.
        body: Markdown content following the frontmatter, stripped.
        path: Absolute path to the source file, as a string.
    """

    name: str
    description: str
    category: str
    body: str
    path: str
    triggers: tuple[str, ...] = field(default_factory=tuple)


def discover_shrew_skills(
    extra_search_paths: Sequence[str | Path] | None = None,
) -> list[ShrewSkill]:
    """Discover the bundled shrew skills, plus any from extra search paths.

    The bundled skills under :data:`SKILLS_ROOT` are loaded first, then any
    ``extra_search_paths`` (typically ``~/.shrew/skills/``) are layered on
    top. A later skill with the same ``name`` overrides an earlier one,
    matching the ``rules.py``-style precedence used elsewhere in chimera.

    Args:
        extra_search_paths: Optional additional directories to scan for
            ``*.md`` skill files. Each path is walked recursively; only
            files inside a :data:`CATEGORIES` subdirectory are considered.

    Returns:
        Skills sorted by ``(category, name)``, with later sources winning.
    """
    by_name: dict[str, ShrewSkill] = {}

    for skill in _scan_root(SKILLS_ROOT):
        by_name[skill.name] = skill

    if extra_search_paths:
        for raw in extra_search_paths:
            root = Path(raw)
            if not root.exists() or not root.is_dir():
                continue
            for skill in _scan_root(root):
                by_name[skill.name] = skill

    cat_order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(
        by_name.values(),
        key=lambda s: (cat_order.get(s.category, len(CATEGORIES)), s.name),
    )


def format_shrew_skills_for_prompt(skills: Iterable[ShrewSkill]) -> str:
    """Render skills as a markdown block suitable for system-prompt injection.

    The output groups skills by category and lists each by name with its
    one-line description. Empty input yields an empty string.

    Args:
        skills: Skills to render (typically the result of
            :func:`discover_shrew_skills`).

    Returns:
        A markdown string, or ``""`` when ``skills`` is empty.
    """
    grouped: dict[str, list[ShrewSkill]] = {c: [] for c in CATEGORIES}
    extras: list[ShrewSkill] = []
    for s in skills:
        if s.category in grouped:
            grouped[s.category].append(s)
        else:
            extras.append(s)

    if not any(grouped.values()) and not extras:
        return ""

    lines: list[str] = ["## Shrew skills"]
    for cat in CATEGORIES:
        items = grouped.get(cat, [])
        if not items:
            continue
        lines.append(f"\n### {cat}")
        for s in sorted(items, key=lambda x: x.name):
            lines.append(f"- **{s.name}** — {s.description}")
    if extras:
        lines.append("\n### other")
        for s in sorted(extras, key=lambda x: x.name):
            lines.append(f"- **{s.name}** — {s.description}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scan_root(root: Path) -> list[ShrewSkill]:
    """Walk ``root`` for ``CATEGORIES``/*.md files and parse them."""
    out: list[ShrewSkill] = []
    for cat in CATEGORIES:
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            skill = _parse_skill(md_file, cat)
            if skill is not None:
                out.append(skill)
    return out


def _parse_skill(path: Path, category: str) -> ShrewSkill | None:
    """Parse a single skill markdown file. Returns ``None`` on any failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = parts[1].strip()
    body = parts[2].strip()

    meta = _parse_frontmatter(frontmatter)
    name = meta.get("name", "")
    description = meta.get("description", "")
    triggers_raw = meta.get("triggers", "")

    if not name or not description:
        return None
    if not _NAME_PATTERN.match(name):
        return None
    if len(description) > 1024:
        return None

    triggers = _parse_triggers(triggers_raw)

    return ShrewSkill(
        name=name,
        description=description,
        category=category,
        body=body,
        path=str(path),
        triggers=triggers,
    )


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse the small ``key: value`` subset shrew skills use."""
    meta: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        # Strip a single layer of matched quotes if present.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        meta[key] = value
    return meta


def _parse_triggers(raw: str) -> tuple[str, ...]:
    """Parse a ``triggers`` value as either a JSON-ish list or a CSV.

    Accepts ``["a", "b"]``, ``[a, b]``, ``a, b``, or empty.
    """
    if not raw:
        return ()
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    items: list[str] = []
    for token in s.split(","):
        t = token.strip().strip('"').strip("'").strip()
        if t:
            items.append(t)
    return tuple(items)
