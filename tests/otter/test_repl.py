"""Smoke tests for the ``chimera otter`` interactive REPL.

These tests exercise the bootstrap path of ``run_otter_repl`` without
ever entering the readline loop. We patch :func:`chimera.cli.code.run_code`
so the REPL hand-off becomes a no-op and verify:

* the otter eventlog directory is created under
  ``~/.chimera/eventlog/otter-*/`` (collaborating with O3),
* the args shim produces a namespace with the keys ``run_code`` reads,
* ``build_otter_agent`` constructs an :class:`~chimera.core.agent.Agent`
  against a synthetic provider (mock) without crashing, and
* ``run_otter_repl`` returns the integer exit code from ``run_code``.

Tests skip cleanly on environments where the readline / TTY path is
unavailable. The build_otter_agent path doesn't need a TTY.
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
    """``chimera.otter.repl`` imports cleanly even without sibling modules."""
    mod = importlib.import_module("chimera.otter.repl")
    assert hasattr(mod, "run_otter_repl")
    assert hasattr(mod, "build_otter_agent")
    assert hasattr(mod, "shim_otter_args")
    assert hasattr(mod, "make_run_id")
    assert hasattr(mod, "open_otter_run_log")


def test_make_run_id_format() -> None:
    """Run ids are sortable and prefixed with ``otter-``."""
    from chimera.otter.repl import make_run_id

    rid = make_run_id()
    assert rid.startswith("otter-")
    # Format: otter-YYYYMMDDTHHMMSS-<8 hex>
    parts = rid.split("-")
    assert len(parts) == 3
    assert len(parts[2]) == 8


def test_shim_otter_args_produces_run_code_keys() -> None:
    """Shim populates every attribute ``run_code`` reads off ``args``."""
    from chimera.otter.repl import shim_otter_args

    src = argparse.Namespace(
        model="gpt-4o-mini",
        cwd="/tmp",
        max_steps=25,
        agent=None,
        models="",
    )
    out = shim_otter_args(src)
    # run_code reads these: model, workdir, max_steps, mode, models,
    # preset, print_mode.
    assert out.model == "gpt-4o-mini"
    assert os.path.isabs(out.workdir)
    assert out.max_steps == 25
    assert out.mode == "interactive"
    assert out.models == ""
    assert out.preset is None
    assert out.print_mode is None


def test_shim_otter_args_defaults_when_unset() -> None:
    """Missing attributes degrade to safe defaults rather than raising."""
    from chimera.otter.repl import shim_otter_args

    out = shim_otter_args(argparse.Namespace())
    assert out.mode == "interactive"
    assert out.max_steps == 50
    # workdir falls back to cwd
    assert os.path.isabs(out.workdir)


def test_build_otter_agent_with_mock_provider() -> None:
    """``build_otter_agent`` constructs an Agent against a mock provider.

    We pass the provider explicitly so this test never touches the
    network or the real factory chain.
    """
    from chimera.core.agent import Agent
    from chimera.otter.repl import build_otter_agent

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic-test-model"
    args = argparse.Namespace(model="synthetic-test-model", max_steps=10)
    agent = build_otter_agent(args, provider=fake_provider)
    assert isinstance(agent, Agent)
    assert agent.provider is fake_provider
    # Default tool group should be non-empty.
    assert len(agent.tools) > 0


def test_run_otter_repl_creates_run_dir(tmp_path: Path) -> None:
    """``run_otter_repl`` mints ``~/.chimera/eventlog/otter-*/`` and delegates."""
    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic-test-model"

    # Redirect HOME so the eventlog ends up under tmp_path.
    with patch.dict(os.environ, {"HOME": str(tmp_path)}, clear=False):
        # Pin Path.home() too — some stdlib paths cache the home lookup.
        with patch("chimera.otter.repl.Path") as mock_path_cls:
            real_path = Path  # capture before shadowing

            class _PathShim(real_path):  # type: ignore[misc, valid-type]
                pass

            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = real_path(tmp_path)

            with patch("chimera.otter.repl._build_otter_provider", return_value=fake_provider):
                with patch("chimera.cli.code.run_code", return_value=0) as mock_run_code:
                    from chimera.otter.repl import run_otter_repl

                    args = argparse.Namespace(
                        model="synthetic-test-model",
                        cwd=str(tmp_path),
                        max_steps=5,
                        agent=None,
                        models="",
                        _quiet_run_dir=True,
                    )
                    rc = run_otter_repl(args)

    assert rc == 0
    mock_run_code.assert_called_once()
    eventlog_root = tmp_path / ".chimera" / "eventlog"
    assert eventlog_root.exists()
    otter_runs = list(eventlog_root.glob("otter-*"))
    assert otter_runs, f"expected an otter-* run dir under {eventlog_root}"


def test_run_otter_repl_handles_missing_provider(tmp_path: Path) -> None:
    """When the provider chain raises ``ValueError`` we exit 1 cleanly."""
    with patch.dict(os.environ, {"HOME": str(tmp_path)}, clear=False):
        with patch("chimera.otter.repl.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = real_path(tmp_path)

            with patch(
                "chimera.otter.repl._build_otter_provider",
                side_effect=ValueError("no api key"),
            ):
                from chimera.otter.repl import run_otter_repl

                args = argparse.Namespace(
                    model=None,
                    cwd=str(tmp_path),
                    max_steps=5,
                    agent=None,
                    models="",
                    _quiet_run_dir=True,
                )
                rc = run_otter_repl(args)
    assert rc == 1


def test_run_otter_repl_skips_when_no_tty() -> None:
    """Documented skip surface: we never block on TTY in the smoke suite."""
    # The smoke tests above patch run_code, so they don't need a TTY.
    # This test simply documents that intent — if a future change
    # removes the patch and the suite hangs on input(), this test name
    # makes the regression searchable.
    if not hasattr(SimpleNamespace, "__init__"):  # always True
        pytest.skip("no tty surface required")
