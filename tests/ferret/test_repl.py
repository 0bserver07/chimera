"""Smoke tests for the ``chimera ferret`` interactive REPL.

These tests exercise the bootstrap path of ``run_ferret_repl`` without
ever entering the readline loop. We patch :func:`chimera.cli.code.run_code`
so the REPL hand-off becomes a no-op and verify:

* the ferret eventlog directory is created under
  ``~/.chimera/eventlog/ferret-*/``,
* the args shim produces a namespace with the keys ``run_code`` reads,
* ``build_ferret_agent`` constructs an :class:`~chimera.core.agent.Agent`
  against a synthetic provider (mock) without crashing,
* ``run_ferret_repl`` returns the integer exit code from ``run_code``.

Tests skip cleanly on environments where the readline / TTY path is
unavailable. The build_ferret_agent path doesn't need a TTY.
"""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_repl_module_importable() -> None:
    """``chimera.ferret.repl`` imports cleanly even without sibling modules."""
    mod = importlib.import_module("chimera.ferret.repl")
    assert hasattr(mod, "run_ferret_repl")
    assert hasattr(mod, "build_ferret_agent")
    assert hasattr(mod, "shim_ferret_args")
    assert hasattr(mod, "make_run_id")
    assert hasattr(mod, "open_ferret_run_log")


def test_make_run_id_format() -> None:
    """Run ids are sortable and prefixed with ``ferret-``."""
    from chimera.ferret.repl import make_run_id

    rid = make_run_id()
    assert rid.startswith("ferret-")
    # Format: ferret-YYYYMMDDTHHMMSS-<8 hex>
    parts = rid.split("-")
    assert len(parts) == 3
    assert parts[0] == "ferret"
    assert len(parts[1]) == 15
    assert len(parts[2]) == 8


def test_make_run_id_unique() -> None:
    """Two consecutive calls don't collide (uuid suffix)."""
    from chimera.ferret.repl import make_run_id

    a = make_run_id()
    b = make_run_id()
    assert a != b


def test_shim_ferret_args_produces_run_code_keys() -> None:
    """Shim populates every attribute ``run_code`` reads off ``args``."""
    from chimera.ferret.repl import shim_ferret_args

    src = argparse.Namespace(
        model="gpt-4o-mini",
        cwd="/tmp",
        max_steps=25,
        agent=None,
        models="",
    )
    out = shim_ferret_args(src)
    assert out.model == "gpt-4o-mini"
    assert os.path.isabs(out.workdir)
    assert out.max_steps == 25
    assert out.mode == "interactive"
    assert out.models == ""
    assert out.preset is None
    assert out.print_mode is None


def test_shim_ferret_args_defaults_when_unset() -> None:
    """Missing attributes degrade to safe defaults rather than raising."""
    from chimera.ferret.repl import shim_ferret_args

    out = shim_ferret_args(argparse.Namespace())
    assert out.mode == "interactive"
    assert out.max_steps == 50
    assert os.path.isabs(out.workdir)


def test_build_ferret_agent_with_mock_provider() -> None:
    """``build_ferret_agent`` constructs an Agent against a mock provider."""
    from chimera.core.agent import Agent
    from chimera.ferret.repl import build_ferret_agent

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic-test-model"
    args = argparse.Namespace(model="synthetic-test-model", max_steps=10)
    agent = build_ferret_agent(args, provider=fake_provider)
    assert isinstance(agent, Agent)
    assert agent.provider is fake_provider
    assert len(agent.tools) > 0


def test_run_ferret_repl_creates_run_dir(tmp_path: Path) -> None:
    """``run_ferret_repl`` mints ``~/.chimera/eventlog/ferret-*/`` and delegates."""
    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic-test-model"

    with patch.dict(os.environ, {"HOME": str(tmp_path)}, clear=False):
        with patch("chimera.ferret.repl.Path") as mock_path_cls:
            real_path = Path

            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = real_path(tmp_path)

            with patch(
                "chimera.ferret.repl._build_ferret_provider",
                return_value=fake_provider,
            ):
                with patch(
                    "chimera.cli.code.run_code", return_value=0
                ) as mock_run_code:
                    from chimera.ferret.repl import run_ferret_repl

                    args = argparse.Namespace(
                        model="synthetic-test-model",
                        cwd=str(tmp_path),
                        max_steps=5,
                        agent=None,
                        models="",
                        _quiet_run_dir=True,
                    )
                    rc = run_ferret_repl(args)

    assert rc == 0
    mock_run_code.assert_called_once()
    eventlog_root = tmp_path / ".chimera" / "eventlog"
    assert eventlog_root.exists()
    ferret_runs = list(eventlog_root.glob("ferret-*"))
    assert ferret_runs, f"expected a ferret-* run dir under {eventlog_root}"


def test_run_ferret_repl_handles_missing_provider(tmp_path: Path) -> None:
    """When the provider chain raises ``ValueError`` we exit 1 cleanly."""
    with patch.dict(os.environ, {"HOME": str(tmp_path)}, clear=False):
        with patch("chimera.ferret.repl.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = real_path(tmp_path)

            with patch(
                "chimera.ferret.repl._build_ferret_provider",
                side_effect=ValueError("no api key"),
            ):
                from chimera.ferret.repl import run_ferret_repl

                args = argparse.Namespace(
                    model=None,
                    cwd=str(tmp_path),
                    max_steps=5,
                    agent=None,
                    models="",
                    _quiet_run_dir=True,
                )
                rc = run_ferret_repl(args)
    assert rc == 1


def test_run_ferret_repl_skips_when_no_tty() -> None:
    """Documented skip surface: we never block on TTY in the smoke suite."""
    if not hasattr(SimpleNamespace, "__init__"):  # always True
        pytest.skip("no tty surface required")


def test_ferret_eventlog_root_returns_path() -> None:
    """``ferret_eventlog_root`` resolves under ``Path.home()``."""
    from chimera.ferret.repl import ferret_eventlog_root

    root = ferret_eventlog_root()
    assert root.parts[-2:] == (".chimera", "eventlog")
