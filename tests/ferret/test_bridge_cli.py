"""Tests for the ``chimera ferret bridge`` CLI subcommand wiring (agent F2).

Covers:

* The ``bridge`` token is registered in the ``_VALID_SUBCOMMANDS`` tuple
  and its dispatcher is bound in ``_SUBCOMMAND_DISPATCH``.
* ``add_arguments`` exposes ``--remote-url`` and ``--bridge-token`` with
  the default ``None`` so :func:`chimera.ferret.cloud_bridge.run_bridge`
  receives the env-fallback contract.
* ``ferret bridge`` routes through :func:`chimera.ferret.cli.run` and
  forwards the parsed namespace to
  :func:`chimera.ferret.cloud_bridge.run_bridge` (mocked here — we never
  actually open a network connection).

Tests stay lightweight: stdlib only, no live HTTP, no live provider.
"""
from __future__ import annotations

import argparse
from typing import Any

from chimera.ferret import cli as ferret_cli


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera ferret")
    ferret_cli.add_arguments(parser)
    return parser


# ---------------------------------------------------------------------------
# Subcommand registration
# ---------------------------------------------------------------------------


def test_bridge_is_in_valid_subcommands() -> None:
    """``bridge`` is registered in the documented subcommand enum."""
    assert "bridge" in ferret_cli._VALID_SUBCOMMANDS  # noqa: SLF001


def test_bridge_dispatcher_is_bound() -> None:
    """``_SUBCOMMAND_DISPATCH['bridge']`` resolves to ``_dispatch_bridge``."""
    handler = ferret_cli._SUBCOMMAND_DISPATCH.get("bridge")  # noqa: SLF001
    assert handler is ferret_cli._dispatch_bridge  # noqa: SLF001


# ---------------------------------------------------------------------------
# Flag surface
# ---------------------------------------------------------------------------


def test_add_arguments_registers_remote_url_and_bridge_token() -> None:
    """``--remote-url`` and ``--bridge-token`` are wired with default ``None``."""
    parser = _build_parser()
    options: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 — argparse internals are stable.
        options.update(action.option_strings)
    assert "--remote-url" in options
    assert "--bridge-token" in options

    args = parser.parse_args([])
    assert args.remote_url is None
    assert args.bridge_token is None


def test_bridge_flags_round_trip_through_argparse() -> None:
    """``ferret bridge --remote-url ... --bridge-token ...`` parses cleanly."""
    parser = _build_parser()
    args = parser.parse_args(
        [
            "--remote-url",
            "https://bridge.example.com",
            "--bridge-token",
            "deadbeef",
            "bridge",
        ]
    )
    assert args.subcommand == "bridge"
    assert args.remote_url == "https://bridge.example.com"
    assert args.bridge_token == "deadbeef"


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


def _ns(**overrides: object) -> argparse.Namespace:
    """Build a Namespace seeded with parser defaults plus *overrides*."""
    parser = _build_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_run_dispatches_bridge_subcommand_to_run_bridge(monkeypatch) -> None:
    """``ferret bridge`` forwards parsed args to ``cloud_bridge.run_bridge``.

    Patches the cloud-bridge entry point so the test asserts the wiring
    without opening a network connection. The mock captures the
    namespace + handler and returns a sentinel rc.
    """
    captured: dict[str, Any] = {}

    def _fake_run_bridge(args: Any, handler: Any) -> int:
        captured["args"] = args
        captured["handler"] = handler
        return 0

    monkeypatch.setattr(
        "chimera.ferret.cloud_bridge.run_bridge", _fake_run_bridge,
    )

    rc = ferret_cli.run(
        _ns(
            subcommand="bridge",
            remote_url="https://bridge.example.com",
            bridge_token="abc123",
        )
    )
    assert rc == 0
    assert "args" in captured, "cloud_bridge.run_bridge was not called"
    fwd = captured["args"]
    assert getattr(fwd, "remote_url", None) == "https://bridge.example.com"
    assert getattr(fwd, "bridge_token", None) == "abc123"
    # The default inbound handler must be a callable so the bridge can
    # dispatch messages through it without crashing.
    assert callable(captured["handler"])


def test_run_dispatches_bridge_subcommand_propagates_rc(monkeypatch) -> None:
    """The dispatcher returns whatever ``run_bridge`` returns, unchanged."""
    monkeypatch.setattr(
        "chimera.ferret.cloud_bridge.run_bridge",
        lambda args, handler: 2,
    )
    rc = ferret_cli.run(_ns(subcommand="bridge"))
    assert rc == 2


def test_default_bridge_inbound_handler_logs_to_stderr(capsys) -> None:
    """The default no-op handler writes a ``[ferret bridge]`` line to stderr.

    Operators rely on this line to verify the round-trip during the
    wave-8 scaffold (before the live REPL attachment lands in wave-9).
    """

    class _Msg:
        message_id = "m-1"
        text = "hello"

    ferret_cli._default_bridge_inbound_handler(_Msg())  # noqa: SLF001
    captured = capsys.readouterr()
    assert "[ferret bridge]" in captured.err
    assert "m-1" in captured.err
    assert "hello" in captured.err
