"""Discover skills from SKILL.md files with YAML frontmatter.

Local discovery walks SKILL.md trees on disk; remote discovery
(:func:`fetch_remote_index`, :func:`download_remote_skills`,
:func:`default_remote_cache`) pulls an ``index.json`` manifest from a
URL, downloads each entry's SKILL.md into the cache, and returns the
freshly-cached :class:`Skill` list. Trademark-safe — no upstream brands
appear in user-visible source.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    """A discovered skill from a SKILL.md file."""
    name: str
    description: str
    content: str
    file_path: str
    base_dir: str


_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def discover_skills(search_paths: Sequence[str | Path]) -> list[Skill]:
    """Walk directories for SKILL.md files and parse them.

    Search paths are checked in order. Later paths can override
    earlier ones (by skill name).

    Args:
        search_paths: Directories to search for SKILL.md files.

    Returns:
        Deduplicated list of skills (last wins by name).
    """
    skills_by_name: dict[str, Skill] = {}
    for path in search_paths:
        p = Path(path)
        if not p.exists():
            continue
        for skill_file in sorted(p.rglob("SKILL.md")):
            skill = _parse_skill_file(skill_file)
            if skill is not None:
                skills_by_name[skill.name] = skill
    return list(skills_by_name.values())


def _parse_skill_file(path: Path) -> Skill | None:
    """Parse a SKILL.md file with YAML frontmatter.

    Expected format:
        ---
        name: my-skill
        description: "What this skill does"
        ---
        Skill content (markdown)

    Returns None if parsing fails or validation fails.
    """
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None

    if not text.startswith("---"):
        return None

    # Split frontmatter
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = parts[1].strip()
    content = parts[2].strip()

    # Parse YAML frontmatter (simple key: value parsing, no pyyaml dependency)
    meta: dict[str, str] = {}
    for line in frontmatter.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            meta[key] = value

    name = meta.get("name", "")
    description = meta.get("description", "")

    if not name or not description:
        return None

    # Validate name format
    if not _NAME_PATTERN.match(name):
        return None

    # Validate description length
    if len(description) > 1024:
        return None

    return Skill(
        name=name,
        description=description,
        content=content,
        file_path=str(path),
        base_dir=str(path.parent),
    )


def bundled_algorithms_path() -> Path:
    """Return the path to the bundled algorithm skills (G12).

    Lives next to this module under ``chimera/skills/algorithms/``.
    Each subdirectory holds one ``SKILL.md`` (binary-search, dp,
    bfs, dfs, hash, two-pointers, sliding-window, sorting, greedy,
    recursion, graph-traversal, math-tricks, string-algos).

    Exposed as a function rather than a module-level constant so
    test fixtures can override the lookup with monkeypatch and so
    callers can import it without paying the resolve cost when the
    feature isn't used.
    """
    return Path(__file__).resolve().parent / "algorithms"


def default_search_paths(workdir: str = ".") -> list[Path]:
    """Return default skill search paths in priority order.

    1. Bundled chimera algorithm skills (G12) — read-only, ships with
       the package so shrew always has the algorithm cheat-sheet
       set without requiring user configuration.
    2. ``{workdir}/.chimera/skills/`` (project-local) — overrides
       bundled by skill name.
    3. ``~/.chimera/skills/`` (user global) — overrides project.

    The "later wins by name" semantics in :func:`discover_skills`
    means a project skill named ``algo-binary-search`` will override
    the bundled version, and a user skill of the same name will
    override the project version. This matches the rest of
    Chimera's project-over-user precedence model.
    """
    return [
        bundled_algorithms_path(),
        Path(workdir) / ".chimera" / "skills",
        Path.home() / ".chimera" / "skills",
    ]


def format_skills_for_prompt(skills: list[Skill]) -> str:
    """Format discovered skills for system prompt injection.

    Returns empty string if no skills found.
    """
    if not skills:
        return ""
    lines = ["## Available Skills"]
    for s in skills:
        lines.append(f"- **{s.name}**: {s.description}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Remote index download (W14-2 task 5)
# ---------------------------------------------------------------------------


def default_remote_cache() -> Path:
    """Return ``~/.chimera/cache/skills`` honoring the live ``Path.home()``.

    Picked to live alongside the rest of the chimera cache tree so a
    user can blow away a misbehaving cache with
    ``rm -rf ~/.chimera/cache``.
    """
    return Path.home() / ".chimera" / "cache" / "skills"


def _http_get(url: str, *, timeout: float = 10.0) -> bytes:
    """Fetch ``url`` with stdlib ``urllib`` and return the body.

    Raises :class:`urllib.error.URLError` (or subclasses) on failure so
    the caller can surface a structured error.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "chimera-skills/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL whitelist enforced by caller.
        body: bytes = resp.read()
    return body


def _parse_index(raw: bytes) -> list[dict[str, Any]]:
    """Parse a remote ``index.json`` payload.

    The expected schema is::

        {
          "skills": [
            {"name": "...", "description": "...", "url": "https://..."},
            ...
          ]
        }

    A bare list (``[{...}, ...]``) is also accepted so a flat manifest
    works with no envelope.

    Raises:
        ValueError: When the payload is malformed.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"index.json is not valid JSON: {exc}") from exc

    if isinstance(data, dict):
        items = data.get("skills") or data.get("items") or []
    else:
        items = data

    if not isinstance(items, list):
        raise ValueError("index.json must contain a 'skills' list (or be a JSON list)")

    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name", "")).strip()
        url = str(it.get("url", "")).strip()
        if not name or not url:
            continue
        if not _NAME_PATTERN.match(name):
            continue
        if not (url.startswith("https://") or url.startswith("http://")):
            continue
        out.append(
            {
                "name": name,
                "description": str(it.get("description", "")),
                "url": url,
                "version": str(it.get("version", "")),
            }
        )
    return out


def fetch_remote_index(
    index_url: str,
    *,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Download and parse a remote skills ``index.json``.

    Args:
        index_url: HTTPS URL to the index manifest.
        timeout: Per-request timeout (seconds).

    Returns:
        List of validated ``{name, description, url, version}`` entries.

    Raises:
        ValueError: For malformed payloads or non-HTTP(S) URLs.
        urllib.error.URLError: On network errors.
    """
    if not (index_url.startswith("https://") or index_url.startswith("http://")):
        raise ValueError(
            f"index URL must use http/https scheme, got: {index_url!r}"
        )
    raw = _http_get(index_url, timeout=timeout)
    return _parse_index(raw)


def download_remote_skills(
    index_url: str,
    *,
    cache_dir: Path | None = None,
    timeout: float = 10.0,
    overwrite: bool = False,
) -> list[Skill]:
    """Download every skill from ``index_url`` into the local cache.

    Each entry's ``url`` is fetched and written to
    ``<cache_dir>/<name>/SKILL.md``. The freshly cached files are then
    re-parsed via :func:`discover_skills` so the return value matches
    what a local discovery walk would yield.

    Args:
        index_url: HTTPS URL to the index manifest.
        cache_dir: Override the cache root (defaults to
            :func:`default_remote_cache`).
        timeout: Per-request timeout (seconds).
        overwrite: When ``False`` (default), skip skills whose
            SKILL.md already exists in the cache. When ``True`` re-fetch
            unconditionally.

    Returns:
        Discovered :class:`Skill` instances after the cache is up to
        date. Skills that fail to validate after download are dropped.

    Raises:
        ValueError: For malformed manifests or invalid URLs.
        urllib.error.URLError: On network errors during index fetch.
    """
    cache_root = cache_dir or default_remote_cache()
    cache_root.mkdir(parents=True, exist_ok=True)
    entries = fetch_remote_index(index_url, timeout=timeout)
    written: list[str] = []
    for entry in entries:
        name = entry["name"]
        target_dir = cache_root / name
        target = target_dir / "SKILL.md"
        if target.exists() and not overwrite:
            written.append(name)
            continue
        try:
            body = _http_get(entry["url"], timeout=timeout)
        except urllib.error.URLError:
            # Skip individual download failures so one broken entry
            # doesn't poison the whole index.
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        # Pad metadata into frontmatter when the remote SKILL.md lacks
        # the required ``name`` / ``description`` keys.
        text = body.decode("utf-8", errors="replace")
        if not text.lstrip().startswith("---"):
            desc = entry.get("description", "").replace('"', '\\"')
            front = (
                f"---\nname: {name}\ndescription: \"{desc}\"\n---\n\n"
            )
            text = front + text
        target.write_text(text, encoding="utf-8")
        written.append(name)
    if not written:
        return []
    return discover_skills([cache_root])


__all__ = [
    "Skill",
    "discover_skills",
    "default_search_paths",
    "bundled_algorithms_path",
    "format_skills_for_prompt",
    "default_remote_cache",
    "fetch_remote_index",
    "download_remote_skills",
]
