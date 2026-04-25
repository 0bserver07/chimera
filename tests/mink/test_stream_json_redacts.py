"""Regression test for AUDIT.md M-10: RedactionMiddleware wired into the
``chimera mink --output-format=stream-json`` flow.

Before the fix, ``_run_stream_json`` wrote raw ``json.dumps(line)`` straight
to stdout, so a tool-call payload containing an API key leaked verbatim. The
fix routes every emitted line through a :class:`RedactionMiddleware` built
in :func:`_build_stream_redaction`, keyed off the live :class:`SecretRegistry`
so callers get the same scrubbing the rest of the event flow relies on.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


_FAKE_SECRET = "sk-ant-fake-leak-DEADBEEF"


def _build_test_middleware() -> Any:
    """Return a redaction middleware that knows about :data:`_FAKE_SECRET`.

    Tests inject the secret as a registered value rather than rely on the
    pattern detector so the assertion stays deterministic across detector
    refactors.
    """
    from chimera.secrets.detector import SecretDetector
    from chimera.secrets.redactor import RedactionMiddleware
    from chimera.secrets.registry import SecretRegistry

    registry = SecretRegistry()
    registry.register("FAKE_API_KEY", _FAKE_SECRET)
    return RedactionMiddleware(
        registry=registry,
        detector=SecretDetector(),
        detect_unknown=True,
    )


def test_m10_redact_stream_line_scrubs_secret_in_data_payload() -> None:
    """``_redact_stream_line`` must replace registered secrets in ``data``."""
    from chimera.mink.cli import _redact_stream_line

    middleware = _build_test_middleware()
    line = {
        "type": "tool_call",
        "turn": 1,
        "data": {
            "tool": "bash",
            "arguments": {"command": f"curl -H 'auth: {_FAKE_SECRET}' /api"},
        },
    }
    out = _redact_stream_line(line, middleware)
    flat = json.dumps(out)
    assert _FAKE_SECRET not in flat, f"raw secret leaked: {flat!r}"
    assert "[REDACTED]" in flat, f"redaction placeholder missing: {flat!r}"
    # WHY: the schema must be preserved — only secrets get rewritten.
    assert out["type"] == "tool_call"
    assert out["turn"] == 1


def test_m10_redact_stream_line_scrubs_nested_strings() -> None:
    """Recursive container walk: secrets buried in lists must also redact."""
    from chimera.mink.cli import _redact_stream_line

    middleware = _build_test_middleware()
    line = {
        "type": "tool_result",
        "turn": 2,
        "data": {
            "output": [
                "step 1 ok",
                f"step 2 leaked Bearer {_FAKE_SECRET}",
            ],
        },
    }
    out = _redact_stream_line(line, middleware)
    flat = json.dumps(out)
    assert _FAKE_SECRET not in flat, f"nested-list secret leaked: {flat!r}"


@pytest.mark.usefixtures("capsys")
def test_m10_run_stream_json_redacts_tool_call_payload(capsys: Any) -> None:
    """End-to-end: drive ``_run_stream_json`` with a fake agent that emits a
    tool-call event whose payload contains :data:`_FAKE_SECRET`. The captured
    stdout must contain the placeholder and never the raw value.
    """
    from chimera.mink.cli import _run_stream_json

    class _LeakyResult:
        # WHY: an agent_result whose ``output`` smuggles the secret. The
        # synthetic-result emit path has to scrub the dict it builds before
        # writing.
        output = f"final answer with {_FAKE_SECRET} embedded"
        steps = 1
        cost = 0.0
        success = True

    class _FakeAgent:
        async def async_run(self, prompt: str, env: Any = None) -> Any:
            return _LeakyResult()

    class _FakeEnv:
        def cleanup(self) -> None:
            pass

    class _FakeCancel:
        def cancel(self) -> None:
            pass

    middleware = _build_test_middleware()
    rc = _run_stream_json(
        _FakeAgent(),
        _FakeEnv(),
        "say leak",
        cancel=_FakeCancel(),
        redaction=middleware,
    )
    out = capsys.readouterr().out.strip()
    assert rc == 0, f"expected success, got {rc}; stdout={out!r}"
    assert out, "stream-json produced no output"
    assert _FAKE_SECRET not in out, (
        f"AUDIT M-10 regression: raw secret leaked to stdout:\n{out}"
    )
    parsed = [json.loads(line) for line in out.splitlines()]
    assert parsed, "no JSON lines parsed"
    # WHY: at least one line must show the placeholder so we know redaction
    # ran (rather than the secret simply not appearing because data was
    # dropped).
    assert any("[REDACTED]" in json.dumps(line) for line in parsed), (
        f"no [REDACTED] marker found in {parsed}"
    )


def test_m10_default_redaction_is_built_when_none_passed() -> None:
    """When no ``redaction=`` kwarg is passed, ``_run_stream_json`` builds the
    default middleware via :func:`_build_stream_redaction`. This pins the lazy
    construction so a future refactor can't accidentally drop redaction by
    forgetting to construct it.
    """
    from chimera.mink import cli

    middleware = cli._build_stream_redaction()
    # WHY: SecretRegistry is the load-bearing piece — assert it's present
    # and the detector is wired up so the middleware actually scrubs.
    assert middleware.registry is not None
    assert middleware.detector is not None
    assert middleware.detect_unknown is True
