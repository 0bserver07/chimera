"""Pin the ``Optional`` signature of :func:`_open_run_log` (pyright A1).

The mink CLI reads ``run_id`` off the argparse ``Namespace`` via
``getattr(args, "run_id", None)``, which is ``str | None``. Forcing
callsites to narrow before invoking ``_open_run_log`` is needless
ceremony — the function itself accepts ``None`` and resolves to a
fresh id internally. This test pins both the signature and the
runtime behaviour so future refactors don't silently re-introduce
the type error pyright caught at ``chimera/mink/cli.py:745``.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest


# WHY: chimera.mink.cli imports rich (mink extra). Skip when not installed.
pytest.importorskip("rich")
from chimera.mink.cli import _make_run_id, _open_run_log


def test_open_run_log_accepts_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing ``None`` mints a fresh run id and creates the directory."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    log, run_dir = _open_run_log(None)
    try:
        assert run_dir.exists() and run_dir.is_dir()
        assert run_dir.name.startswith("mink-")
        assert run_dir.parent == tmp_path / ".chimera" / "eventlog"
    finally:
        # EventLog opens a file handle in some implementations; close if
        # the attribute exists, otherwise leave to gc.
        close = getattr(log, "close", None)
        if callable(close):
            close()


def test_open_run_log_accepts_explicit_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing an explicit id reuses it verbatim."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    explicit = "mink-test-fixed-id"
    log, run_dir = _open_run_log(explicit)
    try:
        assert run_dir.name == explicit
    finally:
        close = getattr(log, "close", None)
        if callable(close):
            close()


def test_open_run_log_signature_is_optional() -> None:
    """Pin the param annotation as Optional[str] so pyright stays green."""
    sig = inspect.signature(_open_run_log)
    run_id_param = sig.parameters["run_id"]
    annotation = run_id_param.annotation
    # Annotation is a string under ``from __future__ import annotations``;
    # accept either the string form or the resolved Union object.
    text = annotation if isinstance(annotation, str) else str(annotation)
    assert "None" in text, f"_open_run_log must accept None, got {text!r}"


def test_make_run_id_is_unique_enough() -> None:
    """Sanity: two consecutive calls produce different ids."""
    a = _make_run_id()
    b = _make_run_id()
    assert a != b
    assert a.startswith("mink-")
    assert b.startswith("mink-")
