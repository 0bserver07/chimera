"""Project + user rules ingest for the ``chimera otter`` subcommand.

Loads textual coding-style + behavioral rules from the conventional sources
that the open-source coding-agent ecosystem has settled on, and returns a
single concatenated markdown string suitable for injection into an agent's
system prompt. Mirrors the way :mod:`chimera.context.agent_memory` ingests
``CLAUDE.md`` for mink.

Sources (loaded in this order, lowest precedence first)::

    ~/.opencode/AGENTS.md                      # user level
    ~/.config/opencode/AGENTS.md               # XDG user level
    <project_root>/AGENTS.md                   # project level
    <project_root>/.cursor/rules/*.mdc         # cursor rules
    <project_root>/.opencode/rules.md          # project rules

Design contract:

* **Stdlib only.** No PyYAML, no markdown parser.
* **Frontmatter strip.** ``---``-delimited YAML-ish frontmatter at the head
  of any file is dropped before the body is concatenated. The frontmatter
  body is *not* parsed — only the surrounding markers are stripped.
* **Length cap.** The composed string is capped at
  :data:`DEFAULT_MAX_CHARS` (10,000) characters. Truncation logs a warning
  via :mod:`logging` and appends a single ``[...truncated]`` marker so the
  model can see that more was available.
* **Missing files are silent.** Unreadable / nonexistent paths are skipped
  without raising.
* **Override:** later sources appear *after* earlier ones in the output, so
  a project-level rule restating a user-level rule wins by virtue of
  recency in the prompt.

Public API:

* :func:`load_otter_rules` — concatenate user + project rules into one string.
* :func:`discover_rule_files` — return the ordered list of paths that
  :func:`load_otter_rules` would read (useful for tests / introspection).
* :data:`DEFAULT_MAX_CHARS` — soft cap on the composed output.
"""
from __future__ import annotations

import logging
from pathlib import Path

__all__ = [
    "DEFAULT_MAX_CHARS",
    "TRUNCATION_MARKER",
    "discover_rule_files",
    "load_otter_rules",
    "strip_frontmatter",
]


_logger = logging.getLogger(__name__)


# 10K chars is roughly 2-3K tokens — generous for a rules block while
# leaving room for the rest of the prompt budget.
DEFAULT_MAX_CHARS: int = 10_000

# Appended to the composed string when the cap is exceeded so the model
# knows content was elided.
TRUNCATION_MARKER: str = "\n\n[...truncated]\n"


def discover_rule_files(project_root: Path, *, home: Path | None = None) -> list[Path]:
    """Return existing rule files in load order (user-level first).

    Args:
        project_root: Project root directory (where ``AGENTS.md`` /
            ``.opencode/rules.md`` / ``.cursor/rules/`` would live).
        home: Override for the user's home directory. Defaults to
            ``Path.home()``. Useful in tests so the real ``~/.opencode``
            isn't read.

    Returns:
        Ordered, de-duplicated list of file paths that exist on disk.
        Order: user-level ``AGENTS.md`` files, then project ``AGENTS.md``,
        then ``.cursor/rules/*.mdc`` (sorted), then
        ``.opencode/rules.md``.
    """
    home = (home or Path.home()).resolve()
    project_root = project_root.resolve()

    candidates: list[Path] = [
        home / ".opencode" / "AGENTS.md",
        home / ".config" / "opencode" / "AGENTS.md",
        project_root / "AGENTS.md",
    ]

    cursor_dir = project_root / ".cursor" / "rules"
    if cursor_dir.is_dir():
        candidates.extend(sorted(cursor_dir.glob("*.mdc")))

    candidates.append(project_root / ".opencode" / "rules.md")

    seen: set[Path] = set()
    out: list[Path] = []
    for p in candidates:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        if not resolved.is_file():
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def strip_frontmatter(text: str) -> str:
    """Remove a leading ``---`` ... ``---`` YAML-ish frontmatter block.

    If ``text`` does not start with ``---`` (optionally preceded by a UTF-8
    BOM or blank lines), returns ``text`` unchanged. Otherwise returns the
    body following the closing ``---`` line, with one leading newline
    stripped.

    Args:
        text: Full file contents.

    Returns:
        Body with frontmatter removed; original ``text`` if no frontmatter.
    """
    # Normalize a leading BOM so a frontmatter-bearing file saved by an
    # editor still parses.
    body = text.lstrip("﻿")
    if not body.startswith("---"):
        return text
    lines = body.splitlines()
    # First line is ``---``; find the closing ``---``.
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            rest = "\n".join(lines[i + 1 :])
            # Drop a single leading newline left behind by the closer.
            if rest.startswith("\n"):
                rest = rest[1:]
            return rest
    # No closing marker: leave content alone rather than swallow the file.
    return text


def _read_rule_file(path: Path) -> str:
    """Read ``path`` as UTF-8, strip frontmatter, return body.

    Returns ``""`` (and logs a debug message) if the file cannot be read.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.debug("otter.rules: skipping unreadable %s (%s)", path, exc)
        return ""
    return strip_frontmatter(raw).strip()


def load_otter_rules(
    project_root: Path,
    *,
    home: Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Concatenate user + project rules into one markdown string.

    Args:
        project_root: Project root directory used to locate ``AGENTS.md``,
            ``.cursor/rules/*.mdc``, and ``.opencode/rules.md``.
        home: Override for the user's home directory (defaults to
            ``Path.home()``). Useful in tests.
        max_chars: Soft cap on the composed output. When exceeded, the
            string is truncated and :data:`TRUNCATION_MARKER` is appended;
            a warning is emitted via :mod:`logging`.

    Returns:
        Concatenated markdown ready for system-prompt injection. Each
        source contributes a ``<!-- source: <path> -->`` header so the
        agent can see provenance. Returns ``""`` when no rule files exist.
    """
    files = discover_rule_files(project_root, home=home)
    if not files:
        return ""

    parts: list[str] = []
    for f in files:
        body = _read_rule_file(f)
        if not body:
            continue
        parts.append(f"<!-- source: {f} -->\n{body}\n")

    if not parts:
        return ""

    combined = "\n".join(parts).rstrip() + "\n"

    if len(combined) > max_chars:
        _logger.warning(
            "otter.rules: combined rules length %d exceeds cap %d; truncating",
            len(combined),
            max_chars,
        )
        # Reserve room for the truncation marker so the final string still
        # fits inside ``max_chars``.
        keep = max(0, max_chars - len(TRUNCATION_MARKER))
        combined = combined[:keep] + TRUNCATION_MARKER

    _emit_instructions_loaded([str(f) for f in files], len(combined))

    return combined


def _emit_instructions_loaded(files: list[str], char_count: int) -> None:
    """Fire :data:`HookEvent.INSTRUCTIONS_LOADED` via the global emitter.

    Best-effort: missing emitter or hook errors are swallowed so rule
    loading is never gated on the hook system being healthy.
    """
    try:
        from chimera.hooks.emitter import get_global_emitter
        from chimera.hooks.events import HookEvent
        emitter = get_global_emitter()
        if emitter.active:
            emitter.emit_sync(
                HookEvent.INSTRUCTIONS_LOADED,
                tool_name="otter.rules",
                tool_input={"files": files, "char_count": char_count},
            )
    except Exception:
        pass
