# chimera/permissions/patterns.py
from __future__ import annotations

import fnmatch

__all__ = ["matches_pattern"]


def matches_pattern(value: str, pattern: str) -> bool:
    """Glob-style matching using :func:`fnmatch.fnmatch`.

    >>> matches_pattern("bash", "bash")
    True
    >>> matches_pattern("write_file", "write_*")
    True
    >>> matches_pattern("read_file", "write_*")
    False
    >>> matches_pattern("anything", "*")
    True
    """
    return fnmatch.fnmatch(value, pattern)
