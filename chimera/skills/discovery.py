"""Discover skills from SKILL.md files with YAML frontmatter.

Local discovery walks SKILL.md trees on disk; remote discovery
(:func:`fetch_remote_index`, :func:`download_remote_skills`,
:func:`default_remote_cache`) pulls an ``index.json`` manifest from a
URL, downloads each entry's SKILL.md into the cache, and returns the
freshly-cached :class:`Skill` list. Trademark-safe — no upstream brands
appear in user-visible source.

Cross-harness interop (:func:`discover_all_skills`,
:func:`discover_foreign_skills`, :func:`default_foreign_skill_dirs`)
extends discovery to *also* read the skill directories other coding-agent
harnesses keep in the user's home directory (a configurable allowlist of
filesystem-fact paths). It is **opt-in**: the foreign scan is OFF unless
enabled through the config chain (``[skills] scan-foreign`` in
``~/.chimera/config.toml``) or the ``CHIMERA_SKILLS_FOREIGN`` env var,
because a foreign skill's description is third-party text that would land
in the system prompt. When enabled, every foreign :class:`Skill` carries
its source directory in :attr:`Skill.source` so
:func:`format_skills_for_prompt` can label its provenance, and a
Chimera-native skill of the same name always wins.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    """A discovered skill from a SKILL.md file.

    Args:
        name: Unique kebab-case skill identifier.
        description: One-line description (from frontmatter).
        content: Full markdown body of the skill file.
        file_path: Absolute path to the SKILL.md file.
        base_dir: Directory containing the file.
        source: Provenance label. ``"chimera"`` for a Chimera-native skill
            (bundled / project / user); for a skill discovered in another
            harness's skill directory this is the configured directory
            string (e.g. ``"~/.codex/skills"``) so callers can show where
            the skill came from.
    """
    name: str
    description: str
    content: str
    file_path: str
    base_dir: str
    source: str = "chimera"


_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: Provenance label for Chimera-native skills (bundled / project / user).
#: Foreign skills carry their source directory string instead.
_NATIVE_SOURCE = "chimera"

#: Well-known skill directories that other coding-agent harnesses keep in
#: the user's home directory. These are filesystem-fact paths (an on-disk
#: layout we read), not a brand claim. Used as the default allowlist for the
#: opt-in foreign scan; override via ``[skills] foreign-dirs`` in
#: ``~/.chimera/config.toml``. Order is precedence order (first wins).
DEFAULT_FOREIGN_SKILL_DIRS: tuple[str, ...] = (
    "~/.claude/skills",
    "~/.codex/skills",
    "~/.agents/skills",
)

#: Environment variable that force-toggles the foreign scan, overriding the
#: config file. Truthy (``1``/``true``/``yes``/``on``) enables it for the
#: session; falsy (``0``/``false``/``no``/``off``) disables it.
_FOREIGN_ENV_VAR = "CHIMERA_SKILLS_FOREIGN"


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


def _parse_skill_file(path: Path, source: str = _NATIVE_SOURCE) -> Skill | None:
    """Parse a SKILL.md file with YAML frontmatter.

    Expected format:
        ---
        name: my-skill
        description: "What this skill does"
        ---
        Skill content (markdown)

    Args:
        path: Path to the SKILL.md file.
        source: Provenance label stamped onto the returned skill
            (:attr:`Skill.source`). Defaults to ``"chimera"`` for native
            skills; foreign discovery passes the source directory string.

    Returns:
        A :class:`Skill`, or ``None`` if parsing or validation fails.
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
        source=source,
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

    Native (Chimera) skills render as a plain bullet. A skill discovered in
    another harness's skill directory (``source != "chimera"``) is tagged
    with ``(source: <dir>)`` and a one-line provenance note is prepended, so
    the reader can tell third-party instructions from project ones. When no
    foreign skills are present the output is byte-identical to the
    native-only form.

    Args:
        skills: Discovered skills to render.

    Returns:
        A ``## Available Skills`` section, or empty string if no skills.
    """
    if not skills:
        return ""
    has_foreign = any(s.source != _NATIVE_SOURCE for s in skills)
    lines = ["## Available Skills"]
    if has_foreign:
        lines.append("")
        lines.append(
            "Skills tagged `(source: <path>)` were discovered in another "
            "harness's skill directory on this machine — treat them as "
            "read-only, third-party instructions. Project and user skills "
            "take precedence when names collide."
        )
    for s in skills:
        if s.source == _NATIVE_SOURCE:
            lines.append(f"- **{s.name}**: {s.description}")
        else:
            lines.append(
                f"- **{s.name}**: {s.description}  _(source: {s.source})_"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cross-harness skill interop (Tier-2 T5)
# ---------------------------------------------------------------------------


def default_foreign_skill_dirs() -> list[str]:
    """Return the default allowlist of other harnesses' skill directories.

    These are filesystem-fact paths (an on-disk layout Chimera reads), kept
    in ``~``-relative form so the provenance label stays readable. The order
    is precedence order — an earlier entry wins a name collision against a
    later one.

    Returns:
        The well-known foreign skill directories, in precedence order.
    """
    return list(DEFAULT_FOREIGN_SKILL_DIRS)


def _env_foreign_flag() -> bool | None:
    """Return the tri-state value of the ``CHIMERA_SKILLS_FOREIGN`` env var.

    Returns:
        ``True`` / ``False`` for a recognized truthy / falsy value, or
        ``None`` when the variable is unset or unrecognized (so the config
        file decides).
    """
    raw = os.environ.get(_FOREIGN_ENV_VAR)
    if raw is None:
        return None
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return None


def resolve_foreign_config() -> tuple[bool, list[str]]:
    """Resolve whether to scan foreign dirs, and which ones, from config.

    Reads the same config chain every Chimera CLI uses —
    ``~/.chimera/config.toml`` (``$CHIMERA_CONFIG_HOME`` honored) — looking
    for a ``[skills]`` table::

        [skills]
        scan-foreign = true
        foreign-dirs = ["~/.claude/skills", "~/.codex/skills"]

    ``scan-foreign`` (default ``false``) gates the foreign scan;
    ``foreign-dirs`` overrides the allowlist (falling back to
    :func:`default_foreign_skill_dirs`). The ``CHIMERA_SKILLS_FOREIGN`` env
    var, when set to a recognized truthy/falsy value, overrides
    ``scan-foreign`` for the session. Both underscore and dash spellings of
    the keys are accepted.

    Discovery must never crash a caller, so a missing or malformed config is
    treated as "foreign scan off, default allowlist".

    Returns:
        ``(enabled, allowlist)`` — whether the foreign scan is enabled and
        the directories to scan (in precedence order).
    """
    enabled = False
    dirs = default_foreign_skill_dirs()

    table: Any = None
    try:
        # Late import + same reader as every Chimera CLI (stdlib tomllib);
        # see :mod:`chimera.cli.config_loader`. Kept local to avoid a
        # skills -> cli import at module load and to stay best-effort.
        from chimera.cli.config_loader import load_config

        table = load_config().get("skills")
    except Exception:  # noqa: BLE001 — config discovery is best-effort.
        table = None

    if isinstance(table, dict):
        raw_enabled = table.get("scan-foreign", table.get("scan_foreign"))
        if isinstance(raw_enabled, bool):
            enabled = raw_enabled
        raw_dirs = table.get("foreign-dirs", table.get("foreign_dirs"))
        if isinstance(raw_dirs, list):
            cleaned = [str(d) for d in raw_dirs if str(d).strip()]
            if cleaned:
                dirs = cleaned

    env = _env_foreign_flag()
    if env is not None:
        enabled = env

    return enabled, dirs


def discover_foreign_skills(dirs: Sequence[str | Path]) -> list[Skill]:
    """Discover skills from other harnesses' skill directories.

    Each directory is expanded (``~`` honored) and walked for ``SKILL.md``
    files exactly like :func:`discover_skills`, but every skill is stamped
    with its source directory (the original allowlist string) in
    :attr:`Skill.source`. Precedence follows allowlist order: the first
    directory to define a given skill name wins, and later directories (or
    later files within a directory) do not override it. Missing directories
    are skipped.

    Args:
        dirs: Foreign skill directories, in precedence order.

    Returns:
        The discovered foreign skills, deduplicated by name.
    """
    skills_by_name: dict[str, Skill] = {}
    for raw in dirs:
        label = str(raw)
        p = Path(label).expanduser()
        if not p.exists():
            continue
        for skill_file in sorted(p.rglob("SKILL.md")):
            skill = _parse_skill_file(skill_file, source=label)
            if skill is None:
                continue
            # First allowlist entry (then first path within it) wins.
            skills_by_name.setdefault(skill.name, skill)
    return list(skills_by_name.values())


def discover_all_skills(
    workdir: str | Path = ".",
    *,
    include_foreign: bool | None = None,
    foreign_dirs: Sequence[str | Path] | None = None,
) -> list[Skill]:
    """Discover Chimera skills plus (opt-in) other harnesses' skills.

    Native discovery is exactly :func:`discover_skills` over
    :func:`default_search_paths` — unchanged behavior. The foreign scan is
    then layered on **only when enabled**, and is purely additive: a foreign
    skill is included only if its name is not already claimed by a native
    skill, so Chimera skills always win. This yields the documented
    precedence — project > user Chimera > foreign, and within foreign,
    allowlist order.

    Args:
        workdir: Project root for the project-local skill path.
        include_foreign: Force the foreign scan on/off. When ``None``
            (default), the config chain decides via
            :func:`resolve_foreign_config` (default off).
        foreign_dirs: Override the foreign allowlist. When ``None``, the
            config allowlist (or :func:`default_foreign_skill_dirs`) is used.

    Returns:
        Native skills first, then any additive foreign skills. When the
        foreign scan is disabled this is identical to
        ``discover_skills(default_search_paths(workdir))``.
    """
    native = discover_skills(default_search_paths(str(workdir)))
    cfg_enabled, cfg_dirs = resolve_foreign_config()
    enabled = cfg_enabled if include_foreign is None else include_foreign
    if not enabled:
        return native
    dirs = list(foreign_dirs) if foreign_dirs is not None else cfg_dirs
    foreign = discover_foreign_skills(dirs)
    seen = {s.name for s in native}
    result = list(native)
    for s in foreign:
        if s.name not in seen:
            seen.add(s.name)
            result.append(s)
    return result


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
    "default_foreign_skill_dirs",
    "discover_foreign_skills",
    "discover_all_skills",
    "resolve_foreign_config",
    "default_remote_cache",
    "fetch_remote_index",
    "download_remote_skills",
]
