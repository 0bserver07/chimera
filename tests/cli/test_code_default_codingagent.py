"""Default ``chimera code`` REPL builds a CodingAgent (G3, wave 10).

Bare ``chimera code`` (no ``--preset``, no ``--legacy-react``) used to
build a :class:`chimera.core.loop.ReAct` directly. Wave 10 G3 flips the
default to :class:`chimera.assembly.coding_agent.CodingAgent` with
``preset="coding_agent"`` because the new stack carries the full hook /
permission / compaction wiring that the legacy one lacks.

These tests pin three contracts:

1. The bare REPL (no preset, no ``legacy_react``) routes through the
   ``_run_new_stack`` async REPL — i.e. it instantiates a CodingAgent.
2. ``--legacy-react`` opts back into the old ReAct + Session stack.
3. ``--preset NAME`` keeps working (it always built a CodingAgent).
4. Per-CLI shims that pin ``legacy_react=True`` (mink/otter/ferret/
   badger/shrew/stoat) still hit the legacy path so the rich REPL
   features they layer on top (slash commands, snapshot hooks,
   /checkpoint, steering) keep working.

The tests stub out the async ``_run_new_stack`` so we don't need a real
provider; we only assert which branch ``run_code`` took.
"""
from __future__ import annotations

import argparse
from typing import Any

import pytest


def _make_args(**overrides: Any) -> argparse.Namespace:
    """Build a minimal Namespace for ``run_code``.

    Every attribute ``run_code`` reads via ``getattr`` is set explicitly
    so test failures point at the routing logic, not at attribute
    defaults that drifted.
    """
    base = dict(
        mode="interactive",
        model="test-model",
        workdir=".",
        max_steps=10,
        models="",
        preset=None,
        print_mode=None,
        legacy_react=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestBareReplDefaultsToCodingAgent:
    """Bare ``chimera code`` builds a CodingAgent via _run_new_stack."""

    def test_bare_repl_routes_through_new_stack(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    ) -> None:
        """No preset + no legacy_react → ``_run_new_stack`` is invoked."""
        from chimera.cli import code as _code

        captured: dict[str, Any] = {}

        async def _fake_new_stack(*, model: str, preset: str, cwd: str, agent_kwargs: Any = None) -> None:
            captured["model"] = model
            captured["preset"] = preset
            captured["cwd"] = cwd

        monkeypatch.setattr(_code, "_run_new_stack", _fake_new_stack)

        args = _make_args(workdir=str(tmp_path))
        rc = _code.run_code(args)
        assert rc == 0
        assert captured.get("preset") == "coding_agent", (
            "bare REPL must default to the canonical 'coding_agent' preset"
        )
        assert captured.get("model") == "test-model"
        # cwd is resolved to an absolute path
        assert captured.get("cwd") and captured["cwd"].endswith(str(tmp_path))

    def test_explicit_preset_routes_through_new_stack(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    ) -> None:
        """``--preset codex`` still routes through ``_run_new_stack``."""
        from chimera.cli import code as _code

        captured: dict[str, Any] = {}

        async def _fake_new_stack(*, model: str, preset: str, cwd: str, agent_kwargs: Any = None) -> None:
            captured["preset"] = preset

        monkeypatch.setattr(_code, "_run_new_stack", _fake_new_stack)

        args = _make_args(workdir=str(tmp_path), preset="codex")
        rc = _code.run_code(args)
        assert rc == 0
        assert captured.get("preset") == "codex"

    def test_print_mode_uses_coding_agent_for_bare_invocation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    ) -> None:
        """``-p TASK`` without --preset still builds a CodingAgent."""
        from chimera.cli import code as _code

        captured: dict[str, Any] = {}

        # Patch ``asyncio.run`` so we can intercept the coroutine without
        # actually starting an event loop / making provider calls.
        def _fake_asyncio_run(coro: Any) -> None:
            # Drain the coroutine to avoid "never awaited" warnings.
            try:
                coro.close()
            except Exception:
                pass
            captured["ran"] = True

        # ``run_code`` does ``import asyncio`` inside the function, so
        # patch the module-level reference the import will resolve to.
        import asyncio as _asyncio

        monkeypatch.setattr(_asyncio, "run", _fake_asyncio_run)

        args = _make_args(workdir=str(tmp_path), print_mode="say hi")
        rc = _code.run_code(args)
        assert rc == 0
        assert captured.get("ran") is True


class TestLegacyReactFlag:
    """``--legacy-react`` opts back into the legacy ReAct + Session path."""

    def test_parser_exposes_legacy_react_flag(self) -> None:
        """The flag is wired on the ``code`` subparser."""
        from chimera.cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["code", "--legacy-react"])
        assert args.legacy_react is True

    def test_parser_legacy_react_default_false(self) -> None:
        """Default value is ``False`` so the new stack wins."""
        from chimera.cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["code"])
        assert args.legacy_react is False

    def test_legacy_react_routes_through_react_stack(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    ) -> None:
        """``legacy_react=True`` skips _run_new_stack and uses ReAct."""
        from chimera.cli import code as _code

        new_stack_calls: list[Any] = []

        async def _fake_new_stack(*, model: str, preset: str, cwd: str, agent_kwargs: Any = None) -> None:
            new_stack_calls.append((model, preset, cwd))

        monkeypatch.setattr(_code, "_run_new_stack", _fake_new_stack)

        # Stub the provider + readline so we don't need an API key or TTY.
        mock_provider = type("P", (), {
            "complete": lambda *a, **kw: None,
            "stream": lambda *a, **kw: iter([]),
            "model_name": "test",
            "context_window": 4096,
            "supports_tool_use": True,
        })()
        monkeypatch.setattr(
            _code, "create_provider", lambda **kw: mock_provider,
        )
        monkeypatch.setattr(_code, "_setup_readline", lambda: None)

        # Exit immediately so the legacy REPL returns cleanly.
        monkeypatch.setattr("builtins.input", lambda prompt: "/exit")

        args = _make_args(workdir=str(tmp_path), legacy_react=True)
        rc = _code.run_code(args)
        assert rc == 0
        assert new_stack_calls == [], (
            "legacy_react=True must NOT route through _run_new_stack"
        )


class TestPostSessionInitMarkerKeepsLegacy:
    """Shims that wire ``_post_session_init`` stay on the legacy path.

    Otter and shrew install snapshot / extension hooks via this marker.
    Even without ``legacy_react=True`` they must keep getting the rich
    REPL because their hook expects a real Chimera ``Session`` object.
    """

    def test_post_session_init_forces_legacy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    ) -> None:
        from chimera.cli import code as _code

        new_stack_calls: list[Any] = []

        async def _fake_new_stack(*, model: str, preset: str, cwd: str, agent_kwargs: Any = None) -> None:
            new_stack_calls.append((model, preset, cwd))

        monkeypatch.setattr(_code, "_run_new_stack", _fake_new_stack)

        mock_provider = type("P", (), {
            "complete": lambda *a, **kw: None,
            "stream": lambda *a, **kw: iter([]),
            "model_name": "test",
            "context_window": 4096,
            "supports_tool_use": True,
        })()
        monkeypatch.setattr(
            _code, "create_provider", lambda **kw: mock_provider,
        )
        monkeypatch.setattr(_code, "_setup_readline", lambda: None)
        monkeypatch.setattr("builtins.input", lambda prompt: "/exit")

        # The hook marker forces the legacy path even when
        # ``legacy_react`` is False — preserves otter/shrew snapshot wiring.
        args = _make_args(workdir=str(tmp_path), legacy_react=False)
        args._post_session_init = lambda session, env: None  # type: ignore[attr-defined]
        rc = _code.run_code(args)
        assert rc == 0
        assert new_stack_calls == [], (
            "_post_session_init hook marker must keep us on the legacy "
            "rich REPL so otter/shrew snapshot wiring keeps working"
        )


class TestCliShimsPinLegacy:
    """Each per-CLI shim namespace pins ``legacy_react=True``.

    This is what keeps mink/otter/ferret/badger/shrew/stoat REPL test
    suites green: their shims build the namespace ``run_code`` reads,
    and they pin ``legacy_react=True`` so the rich REPL stays the
    transport even after the bare-REPL default flips to CodingAgent.
    """

    def test_mink_shim_pins_legacy_react(self) -> None:
        from chimera.mink.cli import _shim_code_args

        raw = argparse.Namespace(
            cwd=".", model=None, max_steps=50, agent=None,
        )
        shimmed = _shim_code_args(raw)
        assert getattr(shimmed, "legacy_react", False) is True

    def test_otter_shim_pins_legacy_react(self) -> None:
        from chimera.otter.repl import shim_otter_args

        raw = argparse.Namespace(
            cwd=".", model=None, max_steps=50, agent=None,
        )
        shimmed = shim_otter_args(raw)
        assert getattr(shimmed, "legacy_react", False) is True

    def test_ferret_shim_pins_legacy_react(self) -> None:
        from chimera.ferret.repl import shim_ferret_args

        raw = argparse.Namespace(
            cwd=".", model=None, max_steps=50, agent=None,
        )
        shimmed = shim_ferret_args(raw)
        assert getattr(shimmed, "legacy_react", False) is True

    def test_badger_shim_pins_legacy_react(self) -> None:
        from chimera.badger.repl import shim_badger_args

        raw = argparse.Namespace(
            cwd=".", model=None, max_steps=25, agent=None,
        )
        shimmed = shim_badger_args(raw)
        assert getattr(shimmed, "legacy_react", False) is True

    def test_shrew_shim_pins_legacy_react(self) -> None:
        from chimera.shrew.repl import _build_run_code_namespace

        raw = argparse.Namespace(cwd=".", model=None, max_steps=50)
        shimmed = _build_run_code_namespace(raw)
        assert getattr(shimmed, "legacy_react", False) is True
