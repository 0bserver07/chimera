"""Weasel prompt-template registry — markdown-with-frontmatter prompts.

Prompt templates are a core extension surface for weasel: each template
bundles a system prompt, an optional user-message prefix, and arbitrary
metadata under one named identifier the CLI can swap in via
``--prompt-template <name>``. The registry mirrors the npm-style
discovery model already used by :mod:`chimera.weasel.extensions` and
:mod:`chimera.weasel.themes` — templates can ship as markdown files
under ``<project_root>/.weasel/prompts/`` (project scope) or
``~/.weasel/prompts/`` (user scope), with project-scope winning on name
conflict. A built-in ``default`` template is always present so a stock
weasel invocation always has a sensible fallback.

File shape:

```markdown
---
name: review
description: Strict code-review system prompt.
user_prefix: "Review the following diff: "
tags: [review, lint]
---
You are a meticulous reviewer. Focus on correctness and clarity.
Flag bugs, surface unclear naming, and suggest concrete fixes.
```

Frontmatter is parsed with a tiny stdlib YAML-subset reader — only
``key: value`` (strings, ints, floats, booleans, single-line lists)
is recognised. The block following the closing ``---`` is the system
prompt body (stripped of leading/trailing whitespace).

The loader is **stdlib-only** and never raises on a malformed file:
the offending entry is skipped so a single bad markdown cannot break
the whole weasel invocation.

Trademark hygiene: never names the upstream brand. ``.weasel/prompts/``
is a filesystem fact, not a product claim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# PromptTemplate dataclass
# ---------------------------------------------------------------------------


@dataclass
class PromptTemplate:
    """A named system-prompt + user-prefix + metadata bundle.

    Attributes:
        name: Template identifier; lookup key for
            :func:`get_prompt_template`. Defaults to the source file's
            stem when frontmatter omits an explicit ``name`` field.
        system_prompt: The body of the markdown file (everything after
            the closing ``---``). Stripped of surrounding whitespace.
        user_prefix: Optional string spliced in front of every user
            turn. Empty string (the default) means no prefix.
        metadata: Free-form bag of frontmatter keys not consumed by
            the dataclass slots above. Preserved verbatim so embedders
            can stash routing tags, owner emails, etc.
    """

    name: str
    system_prompt: str = ""
    user_prefix: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Built-in template
# ---------------------------------------------------------------------------


_DEFAULT_SYSTEM_PROMPT = (
    "You are Weasel, a minimal Chimera coding agent. "
    "Use tools to inspect and modify the user's repo. Be concise."
)
"""The default weasel system prompt.

Mirrors the literal used by :func:`chimera.weasel.cli._run_print_mode` so
``get_prompt_template(None)`` returns the same instructions the bare CLI
ships when no ``--prompt-template`` flag is set.
"""


def _builtin_default() -> PromptTemplate:
    """Return the stock ``default`` prompt template.

    Mirrors the literal system prompt used by the rest of the weasel
    surface so flagless invocations and ``--prompt-template default``
    produce identical agent behaviour.
    """
    return PromptTemplate(
        name="default",
        system_prompt=_DEFAULT_SYSTEM_PROMPT,
        user_prefix="",
        metadata={"description": "Stock weasel system prompt."},
    )


def _builtin_templates() -> dict[str, PromptTemplate]:
    """Return a freshly-built dict containing the built-in template."""
    return {"default": _builtin_default()}


# Module-level cache used by :func:`get_prompt_template` when no explicit
# registry is provided. Built lazily so importing this module stays cheap.
_DEFAULT_REGISTRY: dict[str, PromptTemplate] | None = None


def _get_default_registry() -> dict[str, PromptTemplate]:
    """Return (and cache) the module-level built-in registry."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = _builtin_templates()
    return _DEFAULT_REGISTRY


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def _coerce_scalar(raw: str) -> Any:
    """Coerce a YAML-subset scalar string into a Python value.

    Supports the bare-minimum set we need from frontmatter: bools,
    integers, floats, single-line bracketed lists, and quoted /
    unquoted strings. Anything that fails to parse falls back to the
    stripped string so authors are never surprised by silent drops.
    """
    s = raw.strip()
    if not s:
        return ""
    # Strip matching surrounding quotes.
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    # Inline list: ``[a, b, c]`` -> list of coerced scalars.
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_coerce_scalar(item) for item in _split_inline_list(inner)]
    lower = s.lower()
    if lower in ("true", "yes"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "none", "~"):
        return None
    # Numeric coercions.
    try:
        if "." not in s and "e" not in lower:
            return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_inline_list(inner: str) -> list[str]:
    """Split a YAML inline-list body on commas, respecting quotes.

    Naive ``split(",")`` would break ``["a, b", c]``; this helper
    walks character-by-character and skips commas inside matching
    single- or double-quoted segments.
    """
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in inner:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [item for item in out if item]


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter dict, body string).

    A frontmatter block is recognised when the file starts with a line
    of exactly three dashes (``---``) and a closing line of exactly
    three dashes follows somewhere later. Files without frontmatter
    return an empty dict plus the full text as the body.

    Args:
        text: Full file contents.

    Returns:
        Tuple ``(metadata, body)`` — ``metadata`` is the parsed
        frontmatter mapping (possibly empty), ``body`` is the post-
        frontmatter text stripped of surrounding whitespace.
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text.strip()
    # Split on the first two ``---`` fences.
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    frontmatter_block = parts[1]
    body = parts[2]
    metadata: dict[str, Any] = {}
    for line in frontmatter_block.splitlines():
        # Skip blank lines and comments.
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        if ":" not in stripped_line:
            continue
        key, _, raw_value = stripped_line.partition(":")
        key = key.strip()
        if not key:
            continue
        metadata[key] = _coerce_scalar(raw_value)
    return metadata, body.strip()


def _template_from_text(
    text: str, *, fallback_name: str
) -> PromptTemplate | None:
    """Materialize a :class:`PromptTemplate` from raw markdown text.

    Returns ``None`` when the body would be empty *and* no metadata
    is present — that pair indicates a wholly blank file the loader
    should skip rather than emit as a no-op template.

    Args:
        text: Full markdown file contents.
        fallback_name: Used as the template name when the frontmatter
            omits an explicit ``name`` field.

    Returns:
        A :class:`PromptTemplate`, or ``None`` for empty files.
    """
    metadata, body = _parse_frontmatter(text)
    if not body and not metadata:
        return None
    raw_name = metadata.pop("name", None) if metadata else None
    name = (
        str(raw_name).strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else fallback_name
    )
    raw_prefix = metadata.pop("user_prefix", "") if metadata else ""
    user_prefix = str(raw_prefix) if raw_prefix is not None else ""
    return PromptTemplate(
        name=name,
        system_prompt=body,
        user_prefix=user_prefix,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _user_root() -> Path:
    """Return the user-level weasel prompt-template root."""
    return Path.home() / ".weasel" / "prompts"


def _project_root_dir(project_root: Path) -> Path:
    """Return the project-level weasel prompt-template root."""
    return project_root / ".weasel" / "prompts"


def _scan_dir(root: Path) -> list[PromptTemplate]:
    """Return parsed prompt templates under a single root.

    Hidden files (``.foo.md``) are skipped. Read or parse failures
    yield ``None`` from :func:`_template_from_text` and are filtered
    silently — a single malformed file does not break the load.
    """
    if not root.is_dir():
        return []
    out: list[PromptTemplate] = []
    for child in sorted(root.iterdir()):
        if not child.is_file():
            continue
        if child.suffix.lower() != ".md":
            continue
        if child.name.startswith("."):
            continue
        try:
            text = child.read_text(encoding="utf-8")
        except OSError:
            continue
        template = _template_from_text(text, fallback_name=child.stem)
        if template is not None:
            out.append(template)
    return out


def load_prompt_templates(
    project_root: Path,
    *,
    user_root: Path | None = None,
) -> dict[str, PromptTemplate]:
    """Load the full prompt-template registry.

    Discovery order, with later entries overriding earlier ones on
    name collision:

    1. Built-in templates (``default``).
    2. User-scope markdown files under ``user_root`` (defaults to
       ``~/.weasel/prompts/``).
    3. Project-scope markdown files under
       ``<project_root>/.weasel/prompts/``.

    Args:
        project_root: Project directory; ``<project_root>/.weasel/prompts/``
            is scanned for project-scope templates.
        user_root: Override for the user-level prompt root. Defaults
            to ``~/.weasel/prompts/``. Primarily used by tests.

    Returns:
        Dict keyed by template name. The returned dict is a fresh
        copy so callers can mutate it without poisoning the module
        cache.
    """
    user_dir = user_root if user_root is not None else _user_root()
    project_dir = _project_root_dir(project_root)

    registry: dict[str, PromptTemplate] = _builtin_templates()
    for template in _scan_dir(user_dir):
        registry[template.name] = template
    for template in _scan_dir(project_dir):
        registry[template.name] = template
    return registry


def get_prompt_template(
    name: str | None,
    *,
    registry: dict[str, PromptTemplate] | None = None,
) -> PromptTemplate:
    """Return the named template, falling back to ``default`` when missing.

    Args:
        name: Template identifier; ``None`` or unknown names yield
            the built-in ``default`` template. Whitespace is stripped.
        registry: Optional pre-built registry (typically the result of
            :func:`load_prompt_templates`). When omitted, the
            module-level built-in cache is used so the lookup stays
            stdlib-only.

    Returns:
        The matched :class:`PromptTemplate`. Always returns a template
        — never raises — so callers can inline the lookup in agent
        construction code.
    """
    bag = registry if registry is not None else _get_default_registry()
    if isinstance(name, str):
        cleaned = name.strip()
        if cleaned and cleaned in bag:
            return bag[cleaned]
    if "default" in bag:
        return bag["default"]
    return _builtin_default()


__all__ = [
    "PromptTemplate",
    "get_prompt_template",
    "load_prompt_templates",
]
