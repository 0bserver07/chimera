"""Minimal ``.env`` loader — stdlib only (no python-dotenv dependency).

``chimera code`` calls this at startup so a project's ``.env`` (model, base
URL, API keys) is picked up automatically, without the user exporting vars in
every shell. The existing environment always wins — a shell export overrides
``.env`` — so this only ever fills in what's missing.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["parse_dotenv", "load_dotenv"]


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``.env`` text into an ordered ``{key: value}`` dict.

    Supports ``KEY=value`` and ``export KEY=value``, ``#`` comment lines and
    trailing inline comments on unquoted values, blank lines, and single- or
    double-quoted values (quotes are stripped). Malformed lines are skipped.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("export ", "export\t")):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        else:
            hash_idx = val.find(" #")
            if hash_idx != -1:
                val = val[:hash_idx].rstrip()
        out[key] = val
    return out


def load_dotenv(path: str | Path, *, override: bool = False) -> list[str]:
    """Load a ``.env`` file into ``os.environ``; return the keys applied.

    Args:
        path: Path to the ``.env`` file. A missing file is a silent no-op.
        override: When ``False`` (default), variables already set in the
            environment are left untouched (the shell wins). When ``True``,
            ``.env`` values replace them.

    Returns:
        The keys actually written into ``os.environ``.
    """
    p = Path(path)
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    applied: list[str] = []
    for key, val in parse_dotenv(text).items():
        if override or key not in os.environ:
            os.environ[key] = val
            applied.append(key)
    return applied
