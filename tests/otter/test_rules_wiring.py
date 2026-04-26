"""Tests for ``--no-rules`` + ``_compose_prompt`` wiring (W3).

Covers:

* ``_compose_prompt`` appends a ``## Project Rules`` section header
  followed by the loaded rules text when rules are available.
* ``_compose_prompt`` returns the base prompt unchanged when
  :func:`chimera.otter.rules.load_otter_rules` returns ``""``.
* ``_compose_prompt`` returns the base prompt unchanged when
  ``no_rules=True``, even if rule files exist.
* The CLI registers ``--no-rules`` on the otter parser (default off).
* ``_run_print_mode`` composes the prompt with the loaded rules text
  by default.
* ``_dispatch_serve_http`` factory composes the prompt with rules.
* ``_dispatch_serve_acp`` factory composes the prompt with rules.
* ``build_otter_agent`` (REPL bootstrap) composes the prompt with rules.

The tests stub :func:`chimera.otter.rules.load_otter_rules` with a
canned string, then assert the composed prompt that reaches
``Prompt.from_string`` includes both the ``## Project Rules`` section
header and the canned text. The provider stack is mocked so no SDK
imports or network calls are made.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.otter import cli as otter_cli


CANNED_RULES = "Project rule line A.\nProject rule line B.\n"


# ---------------------------------------------------------------------------
# _compose_prompt
# ---------------------------------------------------------------------------


def test_compose_prompt_appends_rules_section(tmp_path: Path) -> None:
    """Loaded rules are appended under a ``## Project Rules`` header."""
    with patch(
        "chimera.otter.rules.load_otter_rules",
        return_value=CANNED_RULES,
    ):
        out = otter_cli._compose_prompt(
            "BASE PROMPT", project_root=tmp_path, no_rules=False,
        )
    assert "BASE PROMPT" in out
    assert "## Project Rules" in out
    assert CANNED_RULES.strip() in out
    # Section header must appear *after* the base prompt.
    assert out.index("BASE PROMPT") < out.index("## Project Rules")


def test_compose_prompt_no_rules_flag_skips(tmp_path: Path) -> None:
    """``no_rules=True`` returns the base unchanged even when rules exist."""
    with patch(
        "chimera.otter.rules.load_otter_rules",
        return_value=CANNED_RULES,
    ) as mock_load:
        out = otter_cli._compose_prompt(
            "BASE", project_root=tmp_path, no_rules=True,
        )
    assert out == "BASE"
    assert "## Project Rules" not in out
    # Loader is never called when the flag is set.
    mock_load.assert_not_called()


def test_compose_prompt_empty_rules_returns_base(tmp_path: Path) -> None:
    """No rule files = base prompt unchanged, no section header injected."""
    with patch(
        "chimera.otter.rules.load_otter_rules",
        return_value="",
    ):
        out = otter_cli._compose_prompt(
            "BASE", project_root=tmp_path, no_rules=False,
        )
    assert out == "BASE"
    assert "## Project Rules" not in out


def test_compose_prompt_swallows_loader_exception(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A raising loader logs to stderr and falls back to the base prompt."""
    with patch(
        "chimera.otter.rules.load_otter_rules",
        side_effect=RuntimeError("boom"),
    ):
        out = otter_cli._compose_prompt(
            "BASE", project_root=tmp_path, no_rules=False,
        )
    assert out == "BASE"
    err = capsys.readouterr().err
    assert "rules ingest failed" in err
    assert "boom" in err


# ---------------------------------------------------------------------------
# Parser: --no-rules flag is registered, default off
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera otter")
    otter_cli.add_arguments(parser)
    return parser


def test_no_rules_flag_registered_default_off() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert hasattr(args, "no_rules")
    assert args.no_rules is False


def test_no_rules_flag_sets_true() -> None:
    parser = _build_parser()
    args = parser.parse_args(["--no-rules"])
    assert args.no_rules is True


# ---------------------------------------------------------------------------
# _run_print_mode wires _compose_prompt
# ---------------------------------------------------------------------------


def _ns(**overrides: Any) -> argparse.Namespace:
    """Build a fully-populated otter Namespace via the parser."""
    parser = _build_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _capture_prompt() -> tuple[MagicMock, list[str]]:
    """Patch ``Prompt.from_string`` and capture every template it sees."""
    captured: list[str] = []
    prompt_factory = MagicMock(side_effect=lambda template: captured.append(template) or MagicMock())
    return prompt_factory, captured


def test_run_print_mode_includes_rules_in_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-p`` path injects the canned rules into the system prompt."""
    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic"

    fake_result = MagicMock()
    fake_result.success = True
    fake_result.output = "ok"
    fake_result.steps = 1
    fake_result.cost = 0.0

    async def _fake_async_run(self: Any, prompt: str, env: Any = None) -> Any:  # noqa: ARG001
        return fake_result

    captured: list[str] = []

    def _capture_prompt_from_string(template: str) -> Any:
        captured.append(template)
        # Return an instance whose ``.render`` produces *something* an Agent
        # can later use; the agent is mocked above so the value doesn't
        # propagate further.
        m = MagicMock()
        m.render.return_value = template
        return m

    args = _ns(
        print_mode="hi",
        cwd=str(tmp_path),
        no_save=True,
        output_format="text",
    )

    with patch(
        "chimera.otter.rules.load_otter_rules", return_value=CANNED_RULES,
    ):
        with patch.object(
            otter_cli, "_build_provider", return_value=fake_provider,
        ):
            with patch(
                "chimera.core.prompt.Prompt.from_string",
                side_effect=_capture_prompt_from_string,
            ):
                with patch(
                    "chimera.core.agent.Agent.async_run",
                    new=_fake_async_run,
                ):
                    rc = otter_cli._run_print_mode(args)

    assert rc == 0
    assert captured, "Prompt.from_string was never called"
    composed = captured[0]
    assert "You are Otter" in composed
    assert "## Project Rules" in composed
    assert CANNED_RULES.strip() in composed


def test_run_print_mode_no_rules_skips_rules_section(
    tmp_path: Path,
) -> None:
    """``--no-rules`` keeps the base prompt unchanged in the ``-p`` path."""
    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic"

    fake_result = MagicMock()
    fake_result.success = True
    fake_result.output = "ok"
    fake_result.steps = 1
    fake_result.cost = 0.0

    async def _fake_async_run(self: Any, prompt: str, env: Any = None) -> Any:  # noqa: ARG001
        return fake_result

    captured: list[str] = []

    def _capture_prompt_from_string(template: str) -> Any:
        captured.append(template)
        m = MagicMock()
        m.render.return_value = template
        return m

    args = _ns(
        print_mode="hi",
        cwd=str(tmp_path),
        no_save=True,
        no_rules=True,
        output_format="text",
    )

    # Even with rules present, ``--no-rules`` must skip ingestion. We assert
    # that the loader is never called by setting it to raise.
    with patch(
        "chimera.otter.rules.load_otter_rules",
        side_effect=AssertionError("loader should not be called when --no-rules"),
    ):
        with patch.object(
            otter_cli, "_build_provider", return_value=fake_provider,
        ):
            with patch(
                "chimera.core.prompt.Prompt.from_string",
                side_effect=_capture_prompt_from_string,
            ):
                with patch(
                    "chimera.core.agent.Agent.async_run",
                    new=_fake_async_run,
                ):
                    rc = otter_cli._run_print_mode(args)

    assert rc == 0
    assert captured
    composed = captured[0]
    assert "You are Otter" in composed
    assert "## Project Rules" not in composed
    assert CANNED_RULES.strip() not in composed


# ---------------------------------------------------------------------------
# _dispatch_serve_http factory wires _compose_prompt
# ---------------------------------------------------------------------------


def test_dispatch_serve_http_factory_includes_rules(tmp_path: Path) -> None:
    """The HTTP factory composes the prompt with rules by default."""
    captured: list[str] = []

    def _capture_prompt_from_string(template: str) -> Any:
        captured.append(template)
        return MagicMock()

    captured_factory: dict[str, Any] = {}

    def _fake_serve_http(factory: Any, **kwargs: Any) -> int:
        captured_factory["fn"] = factory
        return 0

    args = _ns(
        subcommand="serve",
        cwd=str(tmp_path),
        host="127.0.0.1",
        port=0,
        auth_token=None,
    )

    with patch("chimera.otter.server.serve_http", side_effect=_fake_serve_http):
        with patch.object(otter_cli, "_build_provider", return_value=MagicMock()):
            rc = otter_cli._dispatch_serve_http(args)
    assert rc == 0

    factory = captured_factory["fn"]
    state = MagicMock()
    state.working_dir = str(tmp_path)

    with patch(
        "chimera.otter.rules.load_otter_rules", return_value=CANNED_RULES,
    ):
        with patch(
            "chimera.core.prompt.Prompt.from_string",
            side_effect=_capture_prompt_from_string,
        ):
            with patch.object(otter_cli, "_build_provider", return_value=MagicMock()):
                factory(state)

    assert captured
    composed = captured[-1]
    assert "driven over HTTP" in composed
    assert "## Project Rules" in composed
    assert CANNED_RULES.strip() in composed


def test_dispatch_serve_http_factory_no_rules_skips(tmp_path: Path) -> None:
    """``--no-rules`` propagates to the HTTP factory closure."""
    captured: list[str] = []

    def _capture_prompt_from_string(template: str) -> Any:
        captured.append(template)
        return MagicMock()

    captured_factory: dict[str, Any] = {}

    def _fake_serve_http(factory: Any, **kwargs: Any) -> int:
        captured_factory["fn"] = factory
        return 0

    args = _ns(
        subcommand="serve",
        cwd=str(tmp_path),
        host="127.0.0.1",
        port=0,
        auth_token=None,
        no_rules=True,
    )

    with patch("chimera.otter.server.serve_http", side_effect=_fake_serve_http):
        with patch.object(otter_cli, "_build_provider", return_value=MagicMock()):
            otter_cli._dispatch_serve_http(args)

    factory = captured_factory["fn"]
    state = MagicMock()
    state.working_dir = str(tmp_path)

    with patch(
        "chimera.otter.rules.load_otter_rules",
        side_effect=AssertionError("loader should not be called"),
    ):
        with patch(
            "chimera.core.prompt.Prompt.from_string",
            side_effect=_capture_prompt_from_string,
        ):
            with patch.object(otter_cli, "_build_provider", return_value=MagicMock()):
                factory(state)

    assert captured
    composed = captured[-1]
    assert "driven over HTTP" in composed
    assert "## Project Rules" not in composed


# ---------------------------------------------------------------------------
# _dispatch_serve_acp factory wires _compose_prompt
# ---------------------------------------------------------------------------


def test_dispatch_serve_acp_factory_includes_rules(tmp_path: Path) -> None:
    """The ACP factory composes the prompt with rules by default."""
    captured: list[str] = []

    def _capture_prompt_from_string(template: str) -> Any:
        captured.append(template)
        return MagicMock()

    captured_factory: dict[str, Any] = {}

    def _fake_serve_stdio(factory: Any) -> int:
        captured_factory["fn"] = factory
        return 0

    args = _ns(
        subcommand="serve",
        cwd=str(tmp_path),
        acp=True,
    )

    with patch("chimera.otter.acp.serve_stdio", side_effect=_fake_serve_stdio):
        with patch.object(otter_cli, "_build_provider", return_value=MagicMock()):
            rc = otter_cli._dispatch_serve_acp(args)
    assert rc == 0

    factory = captured_factory["fn"]
    state = MagicMock()
    state.working_dir = str(tmp_path)

    with patch(
        "chimera.otter.rules.load_otter_rules", return_value=CANNED_RULES,
    ):
        with patch(
            "chimera.core.prompt.Prompt.from_string",
            side_effect=_capture_prompt_from_string,
        ):
            with patch.object(otter_cli, "_build_provider", return_value=MagicMock()):
                factory(state)

    assert captured
    composed = captured[-1]
    assert "driven over ACP" in composed
    assert "## Project Rules" in composed
    assert CANNED_RULES.strip() in composed


# ---------------------------------------------------------------------------
# build_otter_agent (REPL bootstrap) wires _compose_prompt
# ---------------------------------------------------------------------------


def test_build_otter_agent_includes_rules(tmp_path: Path) -> None:
    """``build_otter_agent`` (REPL bootstrap) composes the prompt with rules."""
    from chimera.otter.repl import build_otter_agent

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic"

    captured: list[str] = []

    def _capture_prompt_from_string(template: str) -> Any:
        captured.append(template)
        return MagicMock()

    args = argparse.Namespace(
        model="synthetic",
        max_steps=10,
        cwd=str(tmp_path),
        no_lsp=True,  # skip LSP path so test stays hermetic
        no_rules=False,
    )

    with patch(
        "chimera.otter.rules.load_otter_rules", return_value=CANNED_RULES,
    ):
        with patch(
            "chimera.core.prompt.Prompt.from_string",
            side_effect=_capture_prompt_from_string,
        ):
            build_otter_agent(args, provider=fake_provider)

    assert captured
    composed = captured[0]
    assert "interactive coding assistant" in composed
    assert "## Project Rules" in composed
    assert CANNED_RULES.strip() in composed


def test_build_otter_agent_no_rules_skips(tmp_path: Path) -> None:
    """``--no-rules`` (on REPL args) skips rule ingestion in the bootstrap."""
    from chimera.otter.repl import build_otter_agent

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic"

    captured: list[str] = []

    def _capture_prompt_from_string(template: str) -> Any:
        captured.append(template)
        return MagicMock()

    args = argparse.Namespace(
        model="synthetic",
        max_steps=10,
        cwd=str(tmp_path),
        no_lsp=True,
        no_rules=True,
    )

    with patch(
        "chimera.otter.rules.load_otter_rules",
        side_effect=AssertionError("loader should not be called"),
    ):
        with patch(
            "chimera.core.prompt.Prompt.from_string",
            side_effect=_capture_prompt_from_string,
        ):
            build_otter_agent(args, provider=fake_provider)

    assert captured
    composed = captured[0]
    assert "interactive coding assistant" in composed
    assert "## Project Rules" not in composed
