"""Custom-command ingest for the ``chimera otter`` subcommand.

Mirrors the upstream coding agent's ``.opencode/command/*.md`` convention so
projects can ship reusable prompt templates that show up as slash commands
in the otter REPL. The same convention is used by mink for
``.claude/commands/*.md``; this module is the otter-flavored parallel.

Layout::

    ~/.opencode/command/<name>.md          # user-level (lower precedence)
    <project>/.opencode/command/<name>.md  # project-level (overrides user)
    ~/.opencode/commands/<name>.md         # plural alias also accepted
    <project>/.opencode/commands/<name>.md

File schema (best-effort, stdlib-only YAML-ish frontmatter parser)::

    ---
    description: One-line summary shown in /help.
    args:
      - name: target
        description: file or directory to operate on
    ---
    Body of the prompt template. Supports ``$1``, ``$2`` for positional
    arguments (matching the upstream's ``$N`` substitution), and
    ``$ARG_NAME`` / ``$ARGUMENTS`` for named substitutions and the joined
    raw argument string.

Public API:

* :class:`CustomCommand` — dataclass holding ``name``, ``description``,
  ``args``, and ``body_template``.
* :func:`load_custom_commands` — read all user + project ``.md`` files and
  return a ``{name: CustomCommand}`` mapping where project entries override
  user entries on name conflict.
* :meth:`CustomCommand.render` — substitute ``$N`` / ``$ARG_NAME`` /
  ``$ARGUMENTS`` placeholders in the body template.

Per the otter SPEC, this module is stdlib-only — no PyYAML, no third-party
markdown parser. The frontmatter parser supports the small subset opencode
itself uses in shipped commands (scalar values, simple lists, nested
``args: [{name, description}]`` form).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CustomCommand",
    "CustomCommandArg",
    "load_custom_commands",
    "parse_command_file",
]


# Locations checked for command files. Both ``command`` and ``commands``
# (plural) are honored to match the upstream's ``{command,commands}/**/*.md``
# glob.
_USER_DIRS = (
    Path.home() / ".opencode" / "command",
    Path.home() / ".opencode" / "commands",
)


@dataclass
class CustomCommandArg:
    """A single named argument declared in a command's frontmatter.

    Attributes:
        name: Argument name. Used for ``$ARG_NAME`` substitution.
        description: One-line description for help / introspection.
    """

    name: str
    description: str = ""


@dataclass
class CustomCommand:
    """A user-defined command loaded from ``.opencode/command/<name>.md``.

    Attributes:
        name: Command name (filename without extension).
        description: One-line description shown in ``/help``.
        args: Declared positional / named arguments. Order is preserved so
            ``$1`` maps to ``args[0]``, ``$2`` to ``args[1]``, etc.
        body_template: Prompt template body (frontmatter stripped).
        source: Absolute path to the source ``.md`` file. Useful for
            ``/help`` style introspection and for telling the user which
            scope (user vs project) a given command came from.
    """

    name: str
    description: str = ""
    args: list[CustomCommandArg] = field(default_factory=list)
    body_template: str = ""
    source: str | None = None

    def render(self, *positional: str, **named: str) -> str:
        """Substitute ``$N`` / ``$ARG_NAME`` / ``$ARGUMENTS`` placeholders.

        Args:
            *positional: Positional arguments. ``positional[0]`` replaces
                ``$1`` in the template, ``positional[1]`` replaces ``$2``,
                and so on.
            **named: Named arguments. ``foo="bar"`` replaces every ``$FOO``
                or ``$foo`` occurrence in the template (case-insensitive
                lookup against the frontmatter-declared ``args`` list).

        Returns:
            The body template with all known placeholders substituted.
            Unknown ``$VAR`` occurrences are left intact so the model still
            sees them — matching the upstream's permissive behavior.
        """
        rendered = self.body_template

        # ``$ARGUMENTS`` — joined raw positional string.
        rendered = rendered.replace("$ARGUMENTS", " ".join(positional))

        # ``$1``, ``$2``, ... — positional. Replace longest first so ``$10``
        # is tried before ``$1`` (otherwise ``$10`` becomes ``<arg1>0``).
        for i, value in sorted(
            enumerate(positional, start=1), key=lambda kv: -kv[0],
        ):
            rendered = rendered.replace(f"${i}", value)

        # ``$ARG_NAME`` — named. Build a case-insensitive map first.
        if named:
            lower_named = {k.lower(): v for k, v in named.items()}
            # Substitute by walking the declared args (preferred) and any
            # extra kwargs the caller passed.
            keys_to_try: list[str] = []
            for arg in self.args:
                keys_to_try.append(arg.name)
            for k in named:
                if k not in keys_to_try:
                    keys_to_try.append(k)
            # Sort by length desc so ``$FOO_BAR`` substitutes before ``$FOO``.
            keys_to_try.sort(key=len, reverse=True)
            for key in keys_to_try:
                resolved = lower_named.get(key.lower())
                if resolved is None:
                    continue
                rendered = rendered.replace(f"${key}", resolved)
                rendered = rendered.replace(f"${key.upper()}", resolved)

        return rendered


# -- Frontmatter parser (stdlib-only, intentionally narrow) --

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<front>.*?\n)---\s*\n?(?P<body>.*)\Z",
    re.DOTALL,
)


def _parse_scalar(value: str) -> str:
    """Strip YAML-style quoting from a scalar value.

    The upstream ships only quoted strings, unquoted strings, and numbers
    in command frontmatter — we keep the parser intentionally narrow.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse a tiny YAML-ish frontmatter block.

    Supports:
      * ``key: value`` scalars (quoted or unquoted)
      * ``key:`` followed by indented ``- value`` list items
      * ``args:`` followed by indented ``- name: foo`` / ``description: ...``
        sub-items (the only nested form opencode commands actually use)

    Anything we can't parse silently maps to a plain string scalar so a
    malformed file still loads with a best-effort body — which is what the
    upstream does too (errors get logged, not raised).
    """
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue

        # Top-level entries are unindented ``key: ...``.
        if not raw.startswith(" ") and not raw.startswith("\t") and ":" in line:
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest:
                # Inline scalar.
                result[key] = _parse_scalar(rest)
                i += 1
                continue
            # Block value — peek at next lines.
            j = i + 1
            block: list[str] = []
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() == "":
                    block.append(nxt)
                    j += 1
                    continue
                if nxt.startswith(" ") or nxt.startswith("\t"):
                    block.append(nxt)
                    j += 1
                    continue
                break
            result[key] = _parse_block(block)
            i = j
            continue
        i += 1
    return result


def _parse_block(lines: list[str]) -> Any:
    """Parse an indented YAML block (list of scalars or list of dicts)."""
    items: list[Any] = []
    current: dict[str, str] | None = None
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            inner = stripped[2:].strip()
            if ":" in inner:
                # ``- key: value`` — start a new dict item.
                key, _, rest = inner.partition(":")
                current = {key.strip(): _parse_scalar(rest)}
                items.append(current)
            else:
                items.append(_parse_scalar(inner))
                current = None
        elif current is not None and ":" in stripped:
            # Continuation of the current dict item — ``  description: ...``.
            key, _, rest = stripped.partition(":")
            current[key.strip()] = _parse_scalar(rest)
    return items


def parse_command_file(path: Path) -> CustomCommand | None:
    """Parse a single ``.opencode/command/<name>.md`` file.

    Args:
        path: Path to the markdown file.

    Returns:
        A :class:`CustomCommand` on success, or ``None`` if the file is
        empty or unreadable. We never raise on parse errors — bad
        frontmatter degrades to "all body text, no description" so a
        single broken file can't tank the whole loader.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None

    name = path.stem
    description = ""
    args: list[CustomCommandArg] = []
    body = text

    match = _FRONTMATTER_RE.match(text)
    if match:
        front = _parse_frontmatter(match.group("front"))
        body = match.group("body")
        # ``description: ...`` scalar.
        raw_desc = front.get("description")
        if isinstance(raw_desc, str):
            description = raw_desc
        # ``name:`` override (rare but supported).
        raw_name = front.get("name")
        if isinstance(raw_name, str) and raw_name:
            name = raw_name
        # ``args:`` list of dicts or list of strings.
        raw_args = front.get("args")
        if isinstance(raw_args, list):
            for entry in raw_args:
                if isinstance(entry, str):
                    args.append(CustomCommandArg(name=entry))
                elif isinstance(entry, dict):
                    arg_name = entry.get("name") or ""
                    if not arg_name:
                        continue
                    args.append(
                        CustomCommandArg(
                            name=arg_name,
                            description=entry.get("description", "") or "",
                        ),
                    )

    return CustomCommand(
        name=name,
        description=description,
        args=args,
        body_template=body.strip("\n"),
        source=str(path),
    )


# -- Loader --

def _scan_dir(directory: Path) -> dict[str, CustomCommand]:
    """Collect every ``.md`` file in *directory* into a ``{name: cmd}`` map."""
    commands: dict[str, CustomCommand] = {}
    if not directory.is_dir():
        return commands
    for path in sorted(directory.glob("*.md")):
        cmd = parse_command_file(path)
        if cmd is None:
            continue
        commands[cmd.name] = cmd
    return commands


def load_custom_commands(
    project_root: Path | str | None = None,
    *,
    user_dirs: tuple[Path, ...] | None = None,
) -> dict[str, CustomCommand]:
    """Load custom commands from user and project scopes.

    Project-level commands override user-level commands on name conflict —
    matching the upstream's last-wins precedence ladder.

    Args:
        project_root: Project root path. ``.opencode/command/*.md`` and
            ``.opencode/commands/*.md`` under this path are read at
            project scope. ``None`` skips the project scope (user-only).
        user_dirs: Override the user-scope directories. Defaults to
            ``~/.opencode/command`` and ``~/.opencode/commands``. Exposed
            for tests so they don't pollute the real home dir.

    Returns:
        Mapping ``{name: CustomCommand}``. Empty if no files exist.
    """
    dirs = user_dirs if user_dirs is not None else _USER_DIRS

    merged: dict[str, CustomCommand] = {}
    # User scope first so project scope can clobber.
    for d in dirs:
        merged.update(_scan_dir(d))

    if project_root is not None:
        root = Path(project_root)
        for sub in ("command", "commands"):
            merged.update(_scan_dir(root / ".opencode" / sub))

    return merged
