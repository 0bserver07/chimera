"""F1/W8 tests for ``chimera ferret serve --http`` HTTP wiring.

The HTTP transport is a thin wrapper around
:func:`chimera.otter.server.serve_http`: ferret reuses otter's proven
HTTP + SSE handler set and only swaps in a ferret-flavored
``agent_factory`` (provider via FF6, sandbox via FF2, approval via FF3).

These tests stub :func:`chimera.otter.server.serve_http` so the dispatch
contract is verified without binding a real port. We assert:

* ``--http`` reaches ``serve_http`` (rather than the ACP path).
* ``host`` / ``port`` / ``auth_token`` / ``tls_cert`` / ``tls_key`` flow
  through to the otter entry point.
* The ``agent_factory`` passed to ``serve_http`` is a callable that, when
  invoked with a fresh :class:`OtterSessionState`, returns an
  :class:`~chimera.core.agent.Agent`.
* Half-paired TLS (``--tls-cert`` without ``--tls-key`` or vice versa)
  exits 2 with a CLI-level error before any wiring fires.
* The default port falls back to ferret's 5174 (distinct from otter's
  5173) when ``--port`` is not set.
* Without ``--http``, dispatch goes through the ACP path (regression
  guard so adding the HTTP wiring doesn't capture the ACP default).
"""
from __future__ import annotations

import argparse
import os

from chimera.ferret import cli as ferret_cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera ferret")
    ferret_cli.add_arguments(parser)
    return parser


def _ns(**overrides: object) -> argparse.Namespace:
    """Build a Namespace seeded with parser defaults plus *overrides*."""
    parser = _build_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


# ---------------------------------------------------------------------------
# Dispatch wiring
# ---------------------------------------------------------------------------


def test_serve_http_dispatch_invokes_serve_http(monkeypatch) -> None:
    """``ferret serve --http`` calls :func:`chimera.otter.server.serve_http`."""
    captured: dict = {}

    def _fake(agent_factory, **kw):
        captured["agent_factory"] = agent_factory
        captured.update(kw)
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake, raising=True
    )
    rc = ferret_cli.run(_ns(subcommand="serve", http=True))
    assert rc == 0
    assert callable(captured.get("agent_factory"))


def test_serve_http_passes_host_port_token(monkeypatch) -> None:
    """``--host`` / ``--port`` / ``--auth-token`` flow through to serve_http."""
    captured: dict = {}

    def _fake(_factory, **kw):
        captured.update(kw)
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake, raising=True
    )
    rc = ferret_cli.run(
        _ns(
            subcommand="serve",
            http=True,
            host="0.0.0.0",
            port=9876,
            auth_token="s3cr3t",
        )
    )
    assert rc == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9876
    assert captured["auth_token"] == "s3cr3t"


def test_serve_http_default_port_is_5174(monkeypatch) -> None:
    """Unset ``--port`` falls back to ferret's 5174 (otter is 5173)."""
    captured: dict = {}

    def _fake(_factory, **kw):
        captured.update(kw)
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake, raising=True
    )
    rc = ferret_cli.run(_ns(subcommand="serve", http=True, port=None))
    assert rc == 0
    assert captured["port"] == 5174


def test_serve_http_passes_tls_pair(monkeypatch, tmp_path) -> None:
    """``--tls-cert`` + ``--tls-key`` propagate to ``serve_http``."""
    cert = tmp_path / "cert.pem"
    cert.write_text("dummy cert")
    key = tmp_path / "key.pem"
    key.write_text("dummy key")

    captured: dict = {}

    def _fake(_factory, **kw):
        captured.update(kw)
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake, raising=True
    )
    rc = ferret_cli.run(
        _ns(
            subcommand="serve",
            http=True,
            tls_cert=str(cert),
            tls_key=str(key),
        )
    )
    assert rc == 0
    assert captured["tls_cert"] == str(cert)
    assert captured["tls_key"] == str(key)


def test_serve_http_rejects_half_paired_tls_cert(capsys) -> None:
    """``--tls-cert`` without ``--tls-key`` exits 2 with a CLI error."""
    rc = ferret_cli.run(
        _ns(subcommand="serve", http=True, tls_cert="/tmp/cert.pem")
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "tls-cert" in captured.err and "tls-key" in captured.err


def test_serve_http_rejects_half_paired_tls_key(capsys) -> None:
    """``--tls-key`` without ``--tls-cert`` exits 2 with a CLI error."""
    rc = ferret_cli.run(
        _ns(subcommand="serve", http=True, tls_key="/tmp/key.pem")
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "tls-cert" in captured.err and "tls-key" in captured.err


def test_serve_default_path_does_not_invoke_serve_http(monkeypatch) -> None:
    """Without ``--http``, dispatch routes through ACP, not HTTP.

    Regression guard: adding the HTTP wiring must not accidentally
    capture the default (ACP) path.
    """
    serve_http_calls: list = []

    def _bad_serve(*_a, **_kw):
        serve_http_calls.append(_kw)
        return 0

    def _fake_acp(_args):
        return 7

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _bad_serve, raising=True
    )
    monkeypatch.setattr(
        "chimera.ferret.ide.maybe_serve_ide_acp", _fake_acp, raising=False
    )
    rc = ferret_cli.run(_ns(subcommand="serve"))
    assert rc == 7
    assert not serve_http_calls, "ACP path leaked into HTTP wiring"


# ---------------------------------------------------------------------------
# Agent factory shape
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Stand-in for the FF6 provider so factory tests don't hit the network."""

    model_name = "gpt-5"

    async def generate(self, *_a, **_kw):  # pragma: no cover - unused
        return None


def test_agent_factory_builds_agent(monkeypatch) -> None:
    """The ``agent_factory`` passed to ``serve_http`` returns a real Agent.

    We stub the FF6 provider builder so no real provider is constructed,
    capture the factory off ``serve_http``, then invoke it with a fresh
    :class:`OtterSessionState` and assert the result has the
    ``async_run`` attribute :class:`OtterServer` drives against.
    """
    from chimera.otter.server import OtterSessionState

    captured: dict = {}

    def _fake_serve(agent_factory, **kw):
        captured["agent_factory"] = agent_factory
        captured.update(kw)
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake_serve, raising=True
    )

    # WHY: ``_build_provider`` route is FF6 → generic factory. Stub the
    # internal helper so the factory call path doesn't try to reach a
    # real provider/network.
    monkeypatch.setattr(
        ferret_cli, "_build_provider", lambda model: _FakeProvider()
    )

    rc = ferret_cli.run(
        _ns(subcommand="serve", http=True, cwd=os.getcwd())
    )
    assert rc == 0

    factory = captured["agent_factory"]
    assert callable(factory)

    state = OtterSessionState(session_id="test-1", working_dir=os.getcwd())
    agent = factory(state)
    # Real chimera.core.agent.Agent exposes ``async_run`` and ``tools``.
    assert hasattr(agent, "async_run")
    assert hasattr(agent, "tools")


def test_agent_factory_honors_sandbox_and_approval(monkeypatch) -> None:
    """Factory invokes FF2 sandbox parser and FF3 approval preset.

    We can't easily introspect the wrapped env from the returned Agent,
    so we instead patch the late-bound modules and assert they were
    called with the user-supplied ``--sandbox`` / ``--approval`` values.
    """
    from chimera.otter.server import OtterSessionState

    captured_sandbox: list = []
    captured_approval: list = []

    captured: dict = {}

    def _fake_serve(agent_factory, **kw):
        captured["agent_factory"] = agent_factory
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake_serve, raising=True
    )
    monkeypatch.setattr(
        ferret_cli, "_build_provider", lambda model: _FakeProvider()
    )

    import chimera.ferret.sandbox as _sandbox_mod
    import chimera.ferret.approval as _approval_mod

    real_parse = _sandbox_mod.parse_sandbox_mode
    real_preset_from = _approval_mod.preset_from_string

    def _spy_parse(value):
        captured_sandbox.append(value)
        return real_parse(value)

    def _spy_preset(value):
        captured_approval.append(value)
        return real_preset_from(value)

    monkeypatch.setattr(_sandbox_mod, "parse_sandbox_mode", _spy_parse)
    monkeypatch.setattr(_approval_mod, "preset_from_string", _spy_preset)

    rc = ferret_cli.run(
        _ns(
            subcommand="serve",
            http=True,
            sandbox="workspace-write",
            approval="auto",
            cwd=os.getcwd(),
        )
    )
    assert rc == 0
    factory = captured["agent_factory"]
    state = OtterSessionState(session_id="test-2", working_dir=os.getcwd())
    factory(state)
    assert "workspace-write" in captured_sandbox
    assert "auto" in captured_approval
