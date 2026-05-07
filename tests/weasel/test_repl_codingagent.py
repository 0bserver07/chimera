"""Tests for the wave-11 B1 CodingAgent dispatch in :class:`MinimalRepl`.

Wave-10 G3 flipped the bare ``chimera code`` REPL to default-route through
:class:`chimera.assembly.coding_agent.CodingAgent` instead of the legacy
bare :class:`chimera.core.loop.ReAct` stack. The weasel REPL was missed
in that wave; B1 (wave 11) closes the gap.

Behaviour under test:

* When ``legacy_react`` is ``False`` (the default), free-text turns route
  through ``CodingAgent`` with ``preset="coding_agent"`` and the resolved
  model + ``project_dir=cwd``.
* When ``legacy_react`` is ``True``, free-text turns continue to use the
  pre-B1 :class:`ReAct` path (the legacy ``_run_turn_react`` codepath).
* When the assembly module is not importable (a defensive fallback), the
  default routing transparently degrades to the legacy ReAct path so a
  trimmed install never crashes the REPL.

These tests stub out the actual provider / event-stream so they don't
need network credentials; they assert on which codepath was taken and
which constructor args were passed.
"""

from __future__ import annotations

import argparse
import builtins
import io
from typing import Any

import pytest

from chimera.weasel import repl as weasel_repl
from chimera.weasel.repl import MinimalRepl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repl(
    *,
    legacy_react: bool = False,
    model: str | None = "claude-sonnet-4-6",
    workdir: str = ".",
    max_steps: int = 50,
) -> tuple[MinimalRepl, io.StringIO]:
    """Build a :class:`MinimalRepl` with no input script (tests drive it directly)."""
    out = io.StringIO()

    def fake_input(_prompt: str) -> str:
        raise EOFError()

    repl = MinimalRepl(
        model=model,
        workdir=workdir,
        max_steps=max_steps,
        out=out,
        input_fn=fake_input,
        legacy_react=legacy_react,
    )
    return repl, out


# ---------------------------------------------------------------------------
# Default path: CodingAgent
# ---------------------------------------------------------------------------


def test_default_uses_coding_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """``legacy_react`` defaults to ``False`` and routes turns through CodingAgent."""
    captured: dict[str, Any] = {}

    def fake_coding_path(self: MinimalRepl, prompt: str) -> str:
        captured["path"] = "coding_agent"
        captured["prompt"] = prompt
        captured["model"] = self.model
        captured["workdir"] = self.workdir
        return "coded:" + prompt

    def fake_react_path(self: MinimalRepl, prompt: str) -> str:
        captured["path"] = "react"
        return "react:" + prompt

    monkeypatch.setattr(MinimalRepl, "_run_turn_coding_agent", fake_coding_path)
    monkeypatch.setattr(MinimalRepl, "_run_turn_react", fake_react_path)

    repl, _ = _make_repl(legacy_react=False)
    assert repl.legacy_react is False

    text = repl.run_turn("hello")
    assert text == "coded:hello"
    assert captured["path"] == "coding_agent"
    assert captured["prompt"] == "hello"


def test_default_routes_through_assembly_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_turn_coding_agent`` constructs CodingAgent with the resolved args."""
    import chimera.assembly.coding_agent as ca_mod

    captured: dict[str, Any] = {}

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def reset_abort(self) -> None:
            captured["reset_abort"] = True

        def abort(self) -> None:  # pragma: no cover — only on Ctrl-C
            captured["abort"] = True

        async def run(self, task: str) -> Any:
            captured["task"] = task
            # Yield a single fake assistant event whose ``content`` is a
            # short string. We import LoopEventType lazily so the event
            # type matches what _run_turn_coding_agent dispatches on.
            from chimera.core.loop_events import LoopEvent, LoopEventType

            class _Data:
                content = "OK"

            yield LoopEvent(type=LoopEventType.assistant, data=_Data(), turn=1)

    monkeypatch.setattr(ca_mod, "CodingAgent", _FakeAgent)

    repl, _ = _make_repl(legacy_react=False, model="glm-5", workdir=".")
    text = repl.run_turn("ping")

    assert text == "OK"
    init = captured["init"]
    assert init["model"] == "glm-5"
    assert init["preset"] == "coding_agent"
    # workdir is normalised to an absolute path by ``MinimalRepl.__init__``
    assert init["project_dir"] == repl.workdir
    assert captured["task"] == "ping"
    assert captured["reset_abort"] is True
    # History tracking continues to fire so /clear has something to drop.
    assert ("user", "ping") in repl.history
    assert ("assistant", "OK") in repl.history


# ---------------------------------------------------------------------------
# Legacy path: ReAct
# ---------------------------------------------------------------------------


def test_legacy_react_flag_uses_old_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """``legacy_react=True`` skips the CodingAgent path entirely."""
    captured: dict[str, Any] = {}

    def fake_coding_path(self: MinimalRepl, prompt: str) -> str:
        captured["path"] = "coding_agent"
        return "coded:" + prompt

    def fake_react_path(self: MinimalRepl, prompt: str) -> str:
        captured["path"] = "react"
        captured["prompt"] = prompt
        return "react:" + prompt

    monkeypatch.setattr(MinimalRepl, "_run_turn_coding_agent", fake_coding_path)
    monkeypatch.setattr(MinimalRepl, "_run_turn_react", fake_react_path)

    repl, _ = _make_repl(legacy_react=True)
    assert repl.legacy_react is True

    text = repl.run_turn("hello")
    assert text == "react:hello"
    assert captured["path"] == "react"
    assert captured["prompt"] == "hello"


# ---------------------------------------------------------------------------
# Defensive fallback: missing assembly module
# ---------------------------------------------------------------------------


def test_coding_agent_unavailable_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``chimera.assembly.coding_agent`` cannot be imported, fall back to ReAct.

    Simulates a trimmed install / import drift: monkeypatches the builtin
    ``__import__`` so any attempt to load ``chimera.assembly.coding_agent``
    raises :class:`ImportError`. The default-routed turn must then degrade
    to ``_run_turn_react`` rather than crashing the REPL.
    """
    captured: dict[str, Any] = {}

    def fake_react_path(self: MinimalRepl, prompt: str) -> str:
        captured["path"] = "react"
        return "react:" + prompt

    monkeypatch.setattr(MinimalRepl, "_run_turn_react", fake_react_path)

    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        # Block both ``import chimera.assembly.coding_agent`` and
        # ``from chimera.assembly.coding_agent import CodingAgent``.
        if name == "chimera.assembly.coding_agent" or (
            name == "chimera.assembly" and "coding_agent" in (fromlist or ())
        ):
            raise ImportError("simulated: coding_agent unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    repl, _ = _make_repl(legacy_react=False)
    text = repl.run_turn("hello")

    assert text == "react:hello"
    assert captured["path"] == "react"


# ---------------------------------------------------------------------------
# Entry point: args.legacy_react flows into MinimalRepl
# ---------------------------------------------------------------------------


def test_run_entry_point_propagates_legacy_react(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``run(args)`` entry point reads ``args.legacy_react`` and forwards it."""
    captured: dict[str, Any] = {}

    class _FakeRepl:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def run(self) -> int:
            return 0

    monkeypatch.setattr(weasel_repl, "MinimalRepl", _FakeRepl)
    args = argparse.Namespace(
        model="gpt-4o",
        cwd="/tmp",
        max_steps=12,
        legacy_react=True,
    )
    rc = weasel_repl.run(args)
    assert rc == 0
    init = captured["init"]
    assert init["legacy_react"] is True


def test_run_entry_point_default_legacy_react_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``legacy_react`` isn't on the namespace at all, default to ``False``."""
    captured: dict[str, Any] = {}

    class _FakeRepl:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def run(self) -> int:
            return 0

    monkeypatch.setattr(weasel_repl, "MinimalRepl", _FakeRepl)
    args = argparse.Namespace(model=None)
    rc = weasel_repl.run(args)
    assert rc == 0
    init = captured["init"]
    assert init["legacy_react"] is False


# ---------------------------------------------------------------------------
# Argparse wiring: --legacy-react flag is exposed
# ---------------------------------------------------------------------------


def test_cli_exposes_legacy_react_flag() -> None:
    """``add_arguments`` registers ``--legacy-react`` with sane defaults."""
    from chimera.weasel.cli import add_arguments

    parser = argparse.ArgumentParser()
    add_arguments(parser)

    # Default: flag absent → legacy_react=False
    ns = parser.parse_args([])
    assert ns.legacy_react is False

    # Flag present → legacy_react=True
    ns = parser.parse_args(["--legacy-react"])
    assert ns.legacy_react is True
