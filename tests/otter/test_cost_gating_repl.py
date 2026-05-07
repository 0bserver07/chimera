"""W12-9 — REPL cost gating tests.

A8 (wave 11) wired ``--estimate-cost`` / ``--max-cost`` into the
one-shot ``-p`` path only. W12-9 extends ``--max-cost`` to the REPL:

* The shared per-turn gate ``chimera.cli.code._gate_turn_by_cost``
  reads the cap from ``session._max_cost``, estimates the prompt cost
  via :func:`chimera.cli.cost_estimator.estimate_cost`, and refuses
  turns whose estimate exceeds the cap.
* ``/max-cost`` shows / sets / clears the cap mid-session.
* ``/force-send`` bypasses the cap for exactly one turn (re-armed
  automatically afterwards).

The full readline REPL is integration-heavy (provider, env, MCP, hook
wiring) so these tests target the seams rather than the
``run_code`` body — that's the same approach
``tests/otter/test_cli.py`` and the wave-11 cost tests already use.

No live network calls. The single ``shim_otter_args`` test wraps the
otter shim with a fake namespace so we don't pull in the textual TUI
or actual provider construction.
"""

from __future__ import annotations

import argparse
import io
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from chimera.cli.code import (
    _gate_turn_by_cost,
    cmd_force_send,
    cmd_max_cost,
)
from chimera.cli.cost_estimator import CostEstimate, ModelNotPriced


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    *,
    model: str = "glm-5",
    max_cost: float | None = None,
    force_send_once: bool = False,
) -> SimpleNamespace:
    """Build a minimal session-shaped object the gate cares about."""
    return SimpleNamespace(
        provider=SimpleNamespace(model_name=model),
        _max_cost=max_cost,
        _force_send_once=force_send_once,
    )


def _capture_outs() -> tuple[list[str], Callable[[str], None]]:
    """Return (`buffer`, `print_fn`) for slash-command output capture."""
    buf: list[str] = []

    def out(line: str) -> None:
        buf.append(line)

    return buf, out


# ---------------------------------------------------------------------------
# _gate_turn_by_cost
# ---------------------------------------------------------------------------


class TestGateTurnByCost:
    """Per-turn refusal logic that lives between ``input()`` and the agent."""

    def test_no_cap_lets_turn_through(self) -> None:
        session = _make_session(max_cost=None)
        err = io.StringIO()
        assert _gate_turn_by_cost(session, "hello world", err=err) is True
        assert err.getvalue() == ""

    def test_cap_refuses_turn_over_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _make_session(max_cost=0.01)
        # Fake estimate well above the cap.
        fake_est = CostEstimate(
            model="glm-5",
            input_tokens=200,
            output_tokens=2048,
            input_cost_usd=0.0004,
            output_cost_usd=0.0180,
            total_usd=0.0184,
        )
        monkeypatch.setattr(
            "chimera.cli.cost_estimator.estimate_cost",
            lambda *args, **kwargs: fake_est,
        )
        err = io.StringIO()
        assert _gate_turn_by_cost(session, "x" * 1000, err=err) is False
        msg = err.getvalue()
        assert "Refusing turn" in msg
        assert "$0.0184" in msg
        assert "--max-cost $0.0100" in msg
        assert "/max-cost" in msg
        assert "/force-send" in msg

    def test_cap_lets_turn_under_budget_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _make_session(max_cost=1.0)
        fake_est = CostEstimate(
            model="glm-5",
            input_tokens=10,
            output_tokens=100,
            input_cost_usd=0.0001,
            output_cost_usd=0.0008,
            total_usd=0.0009,
        )
        monkeypatch.setattr(
            "chimera.cli.cost_estimator.estimate_cost",
            lambda *args, **kwargs: fake_est,
        )
        err = io.StringIO()
        assert _gate_turn_by_cost(session, "hi", err=err) is True
        assert err.getvalue() == ""

    def test_force_send_consumes_flag_and_skips_estimate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_force_send_once=True`` lets the turn through even when the
        estimate would exceed the cap, and re-arms gating afterwards."""
        session = _make_session(max_cost=0.001, force_send_once=True)

        def boom(*_args: object, **_kwargs: object) -> CostEstimate:
            raise AssertionError("estimate should be skipped on force-send")

        monkeypatch.setattr(
            "chimera.cli.cost_estimator.estimate_cost", boom,
        )
        err = io.StringIO()
        assert _gate_turn_by_cost(session, "anything", err=err) is True
        # Flag is consumed exactly once.
        assert session._force_send_once is False
        assert err.getvalue() == ""

    def test_unpriced_model_fails_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _make_session(model="custom-llm-7b", max_cost=0.50)

        def raise_unpriced(*_args: object, **_kwargs: object) -> CostEstimate:
            raise ModelNotPriced("custom-llm-7b")

        monkeypatch.setattr(
            "chimera.cli.cost_estimator.estimate_cost", raise_unpriced,
        )
        err = io.StringIO()
        assert _gate_turn_by_cost(session, "hello", err=err) is False
        msg = err.getvalue()
        assert "no PRICING entry" in msg
        assert "/max-cost off" in msg
        assert "/force-send" in msg

    def test_no_provider_fails_closed(self) -> None:
        """Cap set but no provider attached → refuse, point to `/max-cost off`."""
        session = SimpleNamespace(
            provider=None, _max_cost=0.10, _force_send_once=False,
        )
        err = io.StringIO()
        assert _gate_turn_by_cost(session, "hello", err=err) is False
        assert "Refusing turn" in err.getvalue()
        assert "/max-cost off" in err.getvalue()


# ---------------------------------------------------------------------------
# /max-cost slash command
# ---------------------------------------------------------------------------


class TestMaxCostCommand:
    """Mid-session cap manipulation via the ``/max-cost`` slash command."""

    def test_no_args_prints_unset(self) -> None:
        session = _make_session(max_cost=None)
        buf, out = _capture_outs()
        cmd_max_cost(session, env=None, args="", out=out)
        joined = "\n".join(buf)
        assert "unset" in joined
        assert "/max-cost <usd>" in joined

    def test_no_args_prints_current_cap(self) -> None:
        session = _make_session(max_cost=0.05)
        buf, out = _capture_outs()
        cmd_max_cost(session, env=None, args="", out=out)
        joined = "\n".join(buf)
        assert "$0.0500" in joined

    def test_set_numeric_cap(self) -> None:
        session = _make_session(max_cost=None)
        buf, out = _capture_outs()
        cmd_max_cost(session, env=None, args="0.25", out=out)
        assert session._max_cost == pytest.approx(0.25)
        assert any("$0.2500" in line for line in buf)

    def test_set_zero_cap(self) -> None:
        """``/max-cost 0`` is valid — it refuses every priced turn."""
        session = _make_session(max_cost=1.0)
        buf, out = _capture_outs()
        cmd_max_cost(session, env=None, args="0", out=out)
        assert session._max_cost == 0.0
        assert any("$0.0000" in line for line in buf)

    def test_clear_with_off(self) -> None:
        session = _make_session(max_cost=0.10)
        buf, out = _capture_outs()
        cmd_max_cost(session, env=None, args="off", out=out)
        assert session._max_cost is None
        assert any("cleared" in line.lower() for line in buf)

    def test_clear_with_none_keyword(self) -> None:
        session = _make_session(max_cost=0.10)
        buf, out = _capture_outs()
        cmd_max_cost(session, env=None, args="none", out=out)
        assert session._max_cost is None

    def test_invalid_value(self) -> None:
        session = _make_session(max_cost=0.10)
        buf, out = _capture_outs()
        cmd_max_cost(session, env=None, args="abc", out=out)
        # Cap unchanged.
        assert session._max_cost == 0.10
        assert any("Invalid" in line for line in buf)

    def test_negative_rejected(self) -> None:
        session = _make_session(max_cost=0.10)
        buf, out = _capture_outs()
        cmd_max_cost(session, env=None, args="-1", out=out)
        assert session._max_cost == 0.10  # unchanged
        assert any("Cap must be >= 0" in line for line in buf)


# ---------------------------------------------------------------------------
# /force-send slash command
# ---------------------------------------------------------------------------


class TestForceSendCommand:
    def test_arms_one_shot_bypass(self) -> None:
        session = _make_session(max_cost=0.001, force_send_once=False)
        buf, out = _capture_outs()
        cmd_force_send(session, env=None, args="", out=out)
        assert session._force_send_once is True
        assert any("Force-send armed" in line for line in buf)

    def test_force_send_then_gate_consumes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Integration: arm /force-send, then the next gate call passes
        through and the flag resets so the turn after is gated again."""
        session = _make_session(max_cost=0.001, force_send_once=False)
        buf, out = _capture_outs()
        cmd_force_send(session, env=None, args="", out=out)
        assert session._force_send_once is True

        # First turn: bypass.
        err = io.StringIO()
        assert _gate_turn_by_cost(session, "first", err=err) is True
        assert session._force_send_once is False

        # Second turn: gating is back on.
        fake_est = CostEstimate(
            model="glm-5",
            input_tokens=100,
            output_tokens=2048,
            input_cost_usd=0.0002,
            output_cost_usd=0.0164,
            total_usd=0.0166,
        )
        monkeypatch.setattr(
            "chimera.cli.cost_estimator.estimate_cost",
            lambda *args, **kwargs: fake_est,
        )
        err2 = io.StringIO()
        assert _gate_turn_by_cost(session, "second", err=err2) is False
        assert "Refusing turn" in err2.getvalue()


# ---------------------------------------------------------------------------
# Otter shim plumbing
# ---------------------------------------------------------------------------


class TestOtterShimPropagatesMaxCost:
    """``shim_otter_args`` must forward ``--max-cost`` to ``run_code``.

    The full ``shim_otter_args`` import chain pulls heavyweight modules,
    so we stub them in via ``sys.modules`` if necessary. The shim is a
    pure namespace transform; we just need to verify the new attribute
    is present.
    """

    def test_max_cost_forwarded(self) -> None:
        from chimera.otter.repl import shim_otter_args

        ns = argparse.Namespace(
            model="glm-5",
            cwd=None,
            max_steps=42,
            models="",
            agent=None,
            preset=None,
            max_cost=0.075,
        )
        shimmed = shim_otter_args(ns)
        assert getattr(shimmed, "max_cost", None) == pytest.approx(0.075)

    def test_max_cost_defaults_none_when_absent(self) -> None:
        from chimera.otter.repl import shim_otter_args

        ns = argparse.Namespace(
            model="glm-5",
            cwd=None,
            max_steps=42,
            models="",
            agent=None,
            preset=None,
        )
        shimmed = shim_otter_args(ns)
        # Attribute must exist (so run_code's getattr finds it) but be None.
        assert getattr(shimmed, "max_cost", "MISSING") is None


# ---------------------------------------------------------------------------
# Slash registry: make sure /max-cost and /force-send are dispatchable
# ---------------------------------------------------------------------------


class TestSlashRegistryHasNewCommands:
    def test_max_cost_registered(self) -> None:
        from chimera.cli import slash_commands

        names = {name for name, _help in slash_commands.list_commands()}
        assert "max-cost" in names
        assert "force-send" in names

    def test_dispatch_max_cost_through_shared_registry(self) -> None:
        """End-to-end: typing ``/max-cost 0.05`` mutates the session."""
        from chimera.cli.slash_commands import dispatch

        session = _make_session(max_cost=None)
        # Capture printed output via a sink so the registry's print=
        # parameter doesn't leak to stdout.
        captured: list[str] = []
        handled = dispatch(
            "/max-cost 0.05", session, env=None, out=captured.append,
        )
        assert handled is True
        assert session._max_cost == pytest.approx(0.05)
