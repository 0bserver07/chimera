"""Tests for PEP 562 lazy loading in ``chimera/__init__.py``.

The package exposes ~330 public names but must NOT eager-import every
subsystem when a caller only needs one layer. These tests pin that behavior:

1. Importing a leaf module (``chimera.providers.base``) loads only a handful
   of ``chimera.*`` submodules -- far fewer than 50.
2. Every name in ``__all__`` is resolvable (contract with downstream users).
3. ``dir(chimera)`` returns exactly ``__all__``.
"""

from __future__ import annotations

import subprocess
import sys

import chimera


def test_leaf_import_does_not_pull_full_stack() -> None:
    """Importing chimera.providers.base should load < 50 chimera.* modules.

    Run in a fresh subprocess so measurement is independent of whatever the
    pytest process has already loaded.
    """
    code = (
        "import sys, chimera.providers.base;"
        "print(sum(1 for m in sys.modules if m.startswith('chimera')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = int(out.stdout.strip())
    assert loaded < 50, (
        f"expected < 50 chimera.* modules loaded for a leaf import, got {loaded}. "
        f"This suggests chimera/__init__.py has regressed to eager loading."
    )


def test_all_public_names_resolve() -> None:
    """Every name in chimera.__all__ must be accessible via getattr."""
    missing: list[tuple[str, str]] = []
    for name in chimera.__all__:
        try:
            obj = getattr(chimera, name)
        except AttributeError as exc:
            missing.append((name, str(exc)))
            continue
        assert obj is not None, f"{name!r} resolved to None"
    assert not missing, f"unresolved public names: {missing}"


def test_dir_returns_all() -> None:
    """``dir(chimera)`` should return exactly ``__all__`` (order-insensitive)."""
    assert sorted(dir(chimera)) == sorted(chimera.__all__)


def test_getattr_caches_results() -> None:
    """After first access, the name should be present in chimera's globals
    so repeated lookups skip __getattr__ entirely."""
    # Pick a name that's unlikely to have been touched elsewhere in the test
    # suite. Use something from an isolated submodule.
    name = "FlowError"
    # Pop any pre-cached value from earlier tests.
    chimera.__dict__.pop(name, None)
    assert name not in chimera.__dict__
    _ = getattr(chimera, name)
    assert name in chimera.__dict__, (
        f"expected {name!r} to be cached in chimera.__dict__ after first access"
    )


def test_unknown_attribute_raises_attribute_error() -> None:
    """Lookups for names NOT in _LAZY_ATTRS must raise AttributeError."""
    try:
        chimera.ThisNameDoesNotExistAnywhere  # type: ignore[attr-defined]
    except AttributeError:
        pass
    else:
        raise AssertionError("expected AttributeError for unknown attribute")
