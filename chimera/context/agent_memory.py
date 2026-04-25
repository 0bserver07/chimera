"""Agent memory walk-up loader.

Walks the directory tree from cwd to filesystem root, collecting memory files
(``CLAUDE.md``, ``CLAUDE.local.md``, ``.claude/CLAUDE.md`` — the on-disk names
remain for ecosystem compatibility) in injection order, then appends
user-global memory and any path-scoped rules. Resolves ``@path`` import
directives recursively (max 5 hops, cycle-detected).

The composed memory is injected as a single user message immediately after
the system prompt, NOT folded into the system prompt itself.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

# Per-level filenames in fixed precedence order. Within a single directory,
# CLAUDE.md is loaded first, then CLAUDE.local.md, then .claude/CLAUDE.md.
_PER_LEVEL_NAMES: tuple[str, ...] = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")

# HTML-comment block stripper (CC strips block-level HTML comments before injection).
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# @import directive: matches `@<path>` at start of line or after whitespace.
# Allows ~, ./, ../, absolute paths, and bare names. Stops at whitespace.
_IMPORT_RE = re.compile(r"(?:^|(?<=\s))@([~./A-Za-z0-9_\-][^\s]*)")


def discover_memory_files(cwd: Path | None = None) -> list[Path]:
    """Walk cwd -> filesystem root collecting memory files in injection order.

    Per-level order: ``CLAUDE.md``, ``CLAUDE.local.md``, ``.claude/CLAUDE.md``.
    Root-most level first, leaf-most last, then ``~/.claude/CLAUDE.md``, then
    any matching ``.claude/rules/*.md``.

    Args:
        cwd: Working directory anchor (default ``Path.cwd()``).

    Returns:
        De-duplicated list of existing memory file paths in injection order.
    """
    cwd = (cwd or Path.cwd()).resolve()

    # Walk leaf -> root, collect at each level, then reverse so root is first.
    levels: list[Path] = []
    cur = cwd
    while True:
        levels.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    levels.reverse()  # root-most first

    seen: set[Path] = set()
    out: list[Path] = []
    for level in levels:
        for name in _PER_LEVEL_NAMES:
            p = (level / name).resolve()
            if p.exists() and p.is_file() and p not in seen:
                seen.add(p)
                out.append(p)

    # User-global memory.
    user_global = (Path.home() / ".claude" / "CLAUDE.md").resolve()
    if user_global.exists() and user_global.is_file() and user_global not in seen:
        seen.add(user_global)
        out.append(user_global)

    # Path-scoped rules: .claude/rules/*.md from cwd (closest project root).
    # Search the cwd-most .claude/rules dir we can find walking up.
    for level in reversed(levels):  # leaf-most first
        rules_dir = level / ".claude" / "rules"
        if rules_dir.is_dir():
            for rule_path in sorted(rules_dir.glob("*.md")):
                rule_path = rule_path.resolve()
                if rule_path in seen:
                    continue
                if _rule_matches_cwd(rule_path, cwd):
                    seen.add(rule_path)
                    out.append(rule_path)
            break  # only the closest rules dir is consulted

    return out


def _rule_matches_cwd(rule_path: Path, cwd: Path) -> bool:
    """Return True if rule's ``paths:`` frontmatter glob matches cwd or any file under it.

    Rules with no ``paths:`` always match.
    """
    try:
        text = rule_path.read_text(encoding="utf-8")
    except OSError:
        return False
    fm, _ = parse_frontmatter(text)
    globs = fm.get("paths")
    if not globs:
        return True
    if isinstance(globs, str):
        globs = [globs]
    for pattern in globs:
        # Match against cwd path itself (fnmatch does NOT recurse on **).
        if fnmatch.fnmatch(str(cwd), pattern):
            return True
        # Use Path.glob (supports recursive **) for any matching file under cwd.
        try:
            for _ in cwd.glob(pattern):
                return True
        except (OSError, ValueError):
            pass
        # Fallback: scan with fnmatch on relative paths (bounded).
        try:
            for i, candidate in enumerate(cwd.rglob("*")):
                if i > 5000:
                    break
                rel = candidate.relative_to(cwd).as_posix()
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(
                    candidate.name, pattern
                ):
                    return True
        except OSError:
            continue
    return False


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse minimal YAML-ish frontmatter (scalars, inline + block lists).

    No PyYAML dep. Returns ``({}, text)`` if no ``---`` header.

    Args:
        text: Full file contents.

    Returns:
        ``(frontmatter_dict, remaining_body)``.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if len(lines) < 2:
        return {}, text
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return {}, text
    header = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :])

    fm: dict[str, Any] = {}
    i = 0
    while i < len(header):
        line = header[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = [_strip_quotes(s.strip()) for s in inner.split(",") if s.strip()]
            fm[key] = items
        elif val == "":
            # Possible block list following.
            block: list[str] = []
            j = i + 1
            while j < len(header):
                nxt = header[j]
                stripped = nxt.lstrip()
                if stripped.startswith("- "):
                    block.append(_strip_quotes(stripped[2:].strip()))
                    j += 1
                elif nxt.strip() == "":
                    j += 1
                else:
                    break
            if block:
                fm[key] = block
                i = j
                continue
            fm[key] = ""
        else:
            fm[key] = _strip_quotes(val)
        i += 1
    return fm, body


def _strip_quotes(s: str) -> str:
    """Remove surrounding single or double quotes from a YAML-ish scalar."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def resolve_imports(
    content: str,
    base: Path,
    hops: int = 5,
    _seen: set[Path] | None = None,
) -> str:
    """Recursively expand ``@path`` imports relative to ``base``.

    ``@~/foo`` resolves via ``Path.expanduser``. Cycles drop the second visit;
    depth cap defaults to 5 (ecosystem parity). Unreadable targets leave the
    ``@path`` token in place.

    Args:
        content: Markdown possibly containing ``@path`` tokens.
        base: Directory used to resolve relative imports.
        hops: Remaining recursion budget.
        _seen: Internal cycle-detection set.

    Returns:
        Content with imports expanded inline.
    """
    if _seen is None:
        _seen = set()
    if hops <= 0:
        return content

    def replace(match: re.Match[str]) -> str:
        raw = match.group(1)
        # Trim trailing punctuation that isn't part of a path.
        raw = raw.rstrip(".,;:)]}")
        if raw.startswith("~"):
            target = Path(raw).expanduser()
        else:
            target = (base / raw).resolve()
        try:
            target = target.resolve()
        except OSError:
            return match.group(0)
        if target in _seen:
            return ""  # cycle: drop, do not recurse
        if not target.is_file():
            return match.group(0)
        try:
            inner = target.read_text(encoding="utf-8")
        except OSError:
            return match.group(0)
        _seen.add(target)
        return resolve_imports(inner, target.parent, hops - 1, _seen)

    return _IMPORT_RE.sub(replace, content)


def load_memory(cwd: Path | None = None, *, max_hops: int = 5) -> str:
    """Walk, read, expand imports, concatenate all memory files.

    Each file gets a ``<!-- source: <path> -->`` header. Block-level HTML
    comments in source files are stripped (ecosystem parity).

    Args:
        cwd: Working directory anchor (default ``Path.cwd()``).
        max_hops: Max ``@import`` recursion depth.

    Returns:
        Concatenated markdown string. Empty if nothing found.
    """
    cwd = (cwd or Path.cwd()).resolve()
    files = discover_memory_files(cwd)
    if not files:
        return ""
    parts: list[str] = []
    for f in files:
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        body = _HTML_COMMENT_RE.sub("", raw)
        expanded = resolve_imports(body, f.parent, hops=max_hops)
        parts.append(f"<!-- source: {f} -->\n{expanded.rstrip()}\n")
    return "\n".join(parts)


def inject_memory(messages: list[dict[str, Any]], cwd: Path) -> list[dict[str, Any]]:
    """Insert memory as a user message after any leading system message(s).

    Empty memory returns a shallow copy of ``messages``. contract: memory
    is a user message, NOT folded into the system prompt.

    Args:
        messages: OpenAI-style ``{role, content}`` dicts.
        cwd: Working directory anchor for discovery.

    Returns:
        New list with memory injected at the correct position.
    """
    memory = load_memory(cwd)
    if not memory:
        return list(messages)
    insert_at = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            insert_at = i + 1
        else:
            break
    out = list(messages)
    out.insert(
        insert_at,
        {
            "role": "user",
            "content": (
                "<memory source=\"CLAUDE.md\">\n"
                f"{memory}"
                "</memory>"
            ),
        },
    )
    return out


__all__ = [
    "discover_memory_files",
    "inject_memory",
    "load_memory",
    "parse_frontmatter",
    "resolve_imports",
]
