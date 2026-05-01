"""Implementation of the ``chimera config`` subcommand.

Manages persistent user defaults stored in ``~/.chimera/config.toml`` (or
``$CHIMERA_CONFIG_HOME/config.toml`` when overridden). The command surface
is intentionally minimal — five verbs, no nested grammar::

    chimera config get <key>
    chimera config set <key> <value>
    chimera config unset <key>
    chimera config list [--cli mink|otter|...]
    chimera config edit

Keys are dot-namespaced: the prefix before the first ``.`` selects a TOML
table (e.g. ``otter.model`` writes ``[otter] model = "..."``), and the rest
becomes the key inside that table. A bare key without a dot (e.g. ``model``)
is treated as ``global.model``.

Values passed to ``set`` are parsed heuristically: ``true``/``false`` become
booleans, integers become ints, and everything else stays a string.

Stdlib only. We read via :mod:`tomllib` and write via a hand-rolled TOML
emitter — the schema is shallow enough that we never need full TOML round-
tripping (no arrays, no nested tables, no datetimes).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from chimera.cli.config_loader import config_path, load_config

#: CLI namespaces recognised by the ``--cli`` filter on ``list``. Mirrors
#: ``CODING_AGENT_CLIS`` in :mod:`chimera.cli.completion` plus ``global``.
KNOWN_CLIS: tuple[str, ...] = (
    "global",
    "mink",
    "otter",
    "ferret",
    "weasel",
    "shrew",
    "stoat",
    "badger",
)


# ---------------------------------------------------------------------------
# Key parsing & value coercion
# ---------------------------------------------------------------------------


def _split_key(key: str) -> tuple[str, str]:
    """Split a dotted key into ``(table, leaf)``.

    A bare key (no dot) is bucketed under ``global`` so users who just want
    "set this default everywhere" don't have to type a prefix. Keys with
    multiple dots keep everything after the first dot as the leaf — TOML
    keys may contain dots when quoted, so ``mink.tools.bash`` becomes
    ``[mink] "tools.bash" = ...``.

    Raises:
        ValueError: if ``key`` is empty or starts/ends with a dot.
    """
    if not key or key.startswith(".") or key.endswith("."):
        raise ValueError(f"invalid key: {key!r}")
    if "." not in key:
        return "global", key
    table, _, leaf = key.partition(".")
    if not table or not leaf:
        raise ValueError(f"invalid key: {key!r}")
    return table, leaf


def _coerce_value(raw: str) -> Any:
    """Heuristically convert a CLI-supplied string to int/bool/str.

    The order matters: ``"true"`` and ``"false"`` (case-insensitive) become
    booleans first; then we attempt an int parse; otherwise the raw string
    is preserved verbatim. Floats are intentionally **not** auto-coerced —
    most user-facing config values are discrete (model names, permission
    modes, integer budgets) and silently turning ``"1.0"`` into a float
    would surprise users who expected to round-trip the literal string.
    """
    lowered = raw.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


# ---------------------------------------------------------------------------
# TOML emitter (write side)
# ---------------------------------------------------------------------------


def _emit_scalar(value: Any) -> str:
    """Render a Python scalar as a TOML literal.

    Booleans become ``true``/``false``; ints stay numeric; everything else is
    coerced to ``str()`` and emitted as a basic double-quoted string with
    backslash and quote escaped. We deliberately avoid multi-line strings or
    fancy escape rules — the values we store are short identifiers, paths,
    and model names.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    s = str(value)
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _quote_key(key: str) -> str:
    """Return a TOML-safe rendering of a leaf key.

    Bare keys are restricted to ``[A-Za-z0-9_-]``. Anything else gets the
    quoted-key treatment so a leaf like ``tools.bash`` round-trips correctly.
    """
    safe = all(ch.isalnum() or ch in ("_", "-") for ch in key)
    if safe and key:
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_toml(data: dict[str, Any]) -> str:
    """Serialise a flat ``{table: {key: scalar}}`` dict to TOML text.

    Tables are emitted in sorted order for deterministic file contents (so
    `git diff` stays readable). Empty tables are skipped — they convey no
    information and would clutter the file.
    """
    lines: list[str] = []
    for table in sorted(data.keys()):
        body = data[table]
        if not isinstance(body, dict) or not body:
            continue
        if lines:
            lines.append("")
        lines.append(f"[{table}]")
        for key in sorted(body.keys()):
            lines.append(f"{_quote_key(key)} = {_emit_scalar(body[key])}")
    if lines:
        lines.append("")
    return "\n".join(lines)


def _save_config(data: dict[str, Any]) -> Path:
    """Write ``data`` to disk, creating the parent directory as needed.

    Returns the path that was written so callers can echo it to the user.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_toml(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_get(args: argparse.Namespace) -> int:
    """Print the value at ``args.key`` or an empty line when unset.

    Exit code is always ``0`` — "missing key" is not an error condition for a
    config-get; downstream shell scripts can detect "unset" by checking for
    an empty stdout line.
    """
    try:
        table, leaf = _split_key(args.key)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    data = load_config()
    body = data.get(table)
    if isinstance(body, dict) and leaf in body:
        value = body[leaf]
        if isinstance(value, bool):
            print("true" if value else "false")
        else:
            print(value)
    else:
        print("")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    """Persist ``args.key = args.value`` to ``config.toml``."""
    try:
        table, leaf = _split_key(args.key)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    data = load_config()
    body = data.get(table)
    if not isinstance(body, dict):
        body = {}
        data[table] = body
    body[leaf] = _coerce_value(args.value)

    path = _save_config(data)
    print(f"set {table}.{leaf} = {body[leaf]!r} -> {path}")
    return 0


def cmd_unset(args: argparse.Namespace) -> int:
    """Remove ``args.key`` from ``config.toml`` if present."""
    try:
        table, leaf = _split_key(args.key)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    data = load_config()
    body = data.get(table)
    if not isinstance(body, dict) or leaf not in body:
        print(f"unset {table}.{leaf}: not set")
        return 0
    del body[leaf]
    if not body:
        del data[table]

    path = _save_config(data)
    print(f"unset {table}.{leaf} -> {path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Print all configured keys, optionally filtered to a single CLI."""
    data = load_config()
    if not data:
        print("(no defaults configured)")
        return 0

    cli_filter: str | None = getattr(args, "cli", None)
    tables = sorted(data.keys())
    if cli_filter:
        tables = [t for t in tables if t == cli_filter]
        if not tables:
            print(f"(no defaults configured for {cli_filter})")
            return 0

    for table in tables:
        body = data[table]
        if not isinstance(body, dict) or not body:
            continue
        for leaf in sorted(body.keys()):
            value = body[leaf]
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            else:
                rendered = repr(value) if isinstance(value, str) else str(value)
            print(f"{table}.{leaf} = {rendered}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Open ``config.toml`` in ``$EDITOR``.

    If ``$EDITOR`` is unset, refuses with a non-zero exit code rather than
    guessing at ``vi``/``nano`` — explicit configuration beats an editor
    surprise on a remote box.
    """
    editor = os.environ.get("EDITOR")
    if not editor:
        print(
            "Error: $EDITOR is not set. "
            "Set EDITOR=vim (or your editor of choice) and retry.",
            file=sys.stderr,
        )
        return 2

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Touch the file so the editor opens an empty buffer with the
        # right name rather than complaining about a missing path.
        path.write_text("", encoding="utf-8")

    # WHY shutil.which: $EDITOR is sometimes a bare command name. Resolving
    # it once gives a clearer error than letting subprocess fail with a
    # platform-specific message.
    binary = shutil.which(editor.split()[0])
    if binary is None:
        print(
            f"Error: editor not found on PATH: {editor!r}",
            file=sys.stderr,
        )
        return 2

    cmd = editor.split() + [str(path)]
    try:
        proc = subprocess.run(cmd, check=False)
    except OSError as exc:
        print(f"Error: failed to launch editor: {exc}", file=sys.stderr)
        return 1
    return proc.returncode


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Attach the ``config`` subcommand and its verbs to ``subparsers``."""
    cfg = subparsers.add_parser(
        "config",
        help="Get/set persistent CLI defaults in ~/.chimera/config.toml",
    )
    cfg_sub = cfg.add_subparsers(dest="config_cmd", required=True)

    p_get = cfg_sub.add_parser("get", help="print the value of a config key")
    p_get.add_argument("key", help="dot-namespaced key, e.g. otter.model")
    p_get.set_defaults(func=cmd_get)

    p_set = cfg_sub.add_parser("set", help="persist a config key")
    p_set.add_argument("key", help="dot-namespaced key, e.g. otter.model")
    p_set.add_argument(
        "value",
        help=(
            "value to store; parsed as bool ('true'/'false'), int, or "
            "string (in that order)"
        ),
    )
    p_set.set_defaults(func=cmd_set)

    p_unset = cfg_sub.add_parser("unset", help="remove a config key")
    p_unset.add_argument("key", help="dot-namespaced key, e.g. otter.model")
    p_unset.set_defaults(func=cmd_unset)

    p_list = cfg_sub.add_parser("list", help="list all configured keys")
    p_list.add_argument(
        "--cli",
        choices=list(KNOWN_CLIS),
        default=None,
        help="restrict output to a single CLI namespace",
    )
    p_list.set_defaults(func=cmd_list)

    p_edit = cfg_sub.add_parser(
        "edit",
        help="open config.toml in $EDITOR",
    )
    p_edit.set_defaults(func=cmd_edit)


def run(args: argparse.Namespace) -> int:
    """Dispatch to the handler chosen by argparse via ``set_defaults(func=...)``.

    Used by :func:`chimera.cli.main.main` when ``args.command == 'config'``.
    """
    func = getattr(args, "func", None)
    if func is None:
        print("Error: no config subcommand given", file=sys.stderr)
        return 2
    rc = func(args)
    return int(rc)


__all__ = [
    "KNOWN_CLIS",
    "cmd_edit",
    "cmd_get",
    "cmd_list",
    "cmd_set",
    "cmd_unset",
    "register",
    "run",
]
