"""Tests for the W14-5 weasel print-mode features.

Covers:

* ``--thinking [LEVEL]`` parsing + provider mutation.
* ``@file`` expansion in prompts.
* Piped stdin sourcing of the prompt.
* Multi-message ``-p`` accumulation.
* ``--stream-json`` emitting one line per loop event.

Tests stay parser- and helper-level: no live provider, no real agent
runs. The streaming path uses a tiny stub agent that yields a fixed
LoopEvent sequence so the JSON envelope is asserted deterministically.
"""

from __future__ import annotations

import argparse
import io
import json
from typing import Any

import pytest

from chimera.weasel import cli as weasel_cli
from chimera.weasel import print_mode as pm


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera weasel")
    weasel_cli.add_arguments(parser)
    return parser


def _ns(**overrides: Any) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


# ---------------------------------------------------------------------------
# Parser surface
# ---------------------------------------------------------------------------


class TestParserSurface:
    def test_thinking_flag_registered(self) -> None:
        parser = _build_parser()
        opts: set[str] = set()
        for action in parser._actions:  # noqa: SLF001
            opts.update(action.option_strings)
        assert "--thinking" in opts
        assert "--stream-json" in opts

    def test_thinking_default_none(self) -> None:
        args = _build_parser().parse_args([])
        assert args.thinking is None

    def test_thinking_bare_flag_returns_const(self) -> None:
        args = _build_parser().parse_args(["--thinking"])
        assert args.thinking == ""

    def test_thinking_with_level(self) -> None:
        args = _build_parser().parse_args(["--thinking", "high"])
        assert args.thinking == "high"

    def test_print_repeatable(self) -> None:
        args = _build_parser().parse_args(
            ["-p", "first", "-p", "second"]
        )
        assert args.print_mode == ["first", "second"]

    def test_print_single_still_list(self) -> None:
        # action="append" still produces a list when used once. The
        # downstream normalize_prompts helper handles both shapes.
        args = _build_parser().parse_args(["-p", "only"])
        assert args.print_mode == ["only"]

    def test_stream_json_default_false(self) -> None:
        args = _build_parser().parse_args([])
        assert args.stream_json is False


# ---------------------------------------------------------------------------
# parse_thinking_arg
# ---------------------------------------------------------------------------


class TestParseThinking:
    def test_none_returns_disabled(self) -> None:
        spec = pm.parse_thinking_arg(None)
        assert spec.enabled is False
        assert spec.budget == 0

    def test_empty_string_returns_medium(self) -> None:
        spec = pm.parse_thinking_arg("")
        assert spec.enabled is True
        assert spec.level == "medium"
        assert spec.budget > 0

    def test_named_level(self) -> None:
        spec = pm.parse_thinking_arg("high")
        assert spec.enabled is True
        assert spec.level == "high"

    def test_off_disables(self) -> None:
        spec = pm.parse_thinking_arg("off")
        assert spec.enabled is False
        assert spec.budget == 0

    def test_numeric_budget(self) -> None:
        spec = pm.parse_thinking_arg("4096")
        assert spec.enabled is True
        assert spec.budget == 4096

    def test_unknown_level_raises(self) -> None:
        with pytest.raises(ValueError):
            pm.parse_thinking_arg("ultra-high")

    def test_apply_thinking_to_provider_sets_attrs(self) -> None:
        class _Provider:
            _enable_thinking = False
            _thinking_budget = 0

        provider = _Provider()
        spec = pm.parse_thinking_arg("medium")
        pm.apply_thinking_to_provider(provider, spec)
        assert provider._enable_thinking is True
        assert provider._thinking_budget > 0

    def test_apply_thinking_noop_on_unsupported(self) -> None:
        class _OpenAILike:
            pass  # No _enable_thinking attr.

        provider = _OpenAILike()
        spec = pm.parse_thinking_arg("high")
        # Must not raise even though the provider lacks the attrs.
        pm.apply_thinking_to_provider(provider, spec)
        assert not hasattr(provider, "_enable_thinking")


# ---------------------------------------------------------------------------
# expand_at_files
# ---------------------------------------------------------------------------


class TestAtFileExpansion:
    def test_no_at_returns_unchanged(self, tmp_path) -> None:
        out = pm.expand_at_files("plain prompt", base_dir=str(tmp_path))
        assert out == "plain prompt"

    def test_email_address_not_expanded(self, tmp_path) -> None:
        # ``user@example.com`` looks like an at-mention but isn't a path
        # because there's no leading slash. Must be left intact.
        out = pm.expand_at_files(
            "ping user@example.com please", base_dir=str(tmp_path)
        )
        assert out == "ping user@example.com please"

    def test_absolute_path_inlined(self, tmp_path) -> None:
        f = tmp_path / "data.txt"
        f.write_text("hello world\n", encoding="utf-8")
        out = pm.expand_at_files(
            f"summarize @{f}", base_dir=str(tmp_path)
        )
        assert "[" + str(f) + "]" in out
        assert "hello world" in out
        assert "[/file end]" in out

    def test_relative_path_inlined(self, tmp_path) -> None:
        f = tmp_path / "rel.txt"
        f.write_text("relative body\n", encoding="utf-8")
        out = pm.expand_at_files(
            "fix @./rel.txt", base_dir=str(tmp_path)
        )
        assert "relative body" in out

    def test_missing_file_emits_stderr_and_keeps_token(
        self, tmp_path, capsys
    ) -> None:
        out = pm.expand_at_files(
            "@/does/not/exist.txt please look", base_dir=str(tmp_path)
        )
        assert "@/does/not/exist.txt" in out
        captured = capsys.readouterr()
        assert "@file: not found" in captured.err

    def test_truncates_large_file(self, tmp_path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("x" * 200_000, encoding="utf-8")
        out = pm.expand_at_files(
            f"@{f}", base_dir=str(tmp_path), max_bytes=128
        )
        assert "truncated" in out
        # Body cap is enforced; the result is bounded.
        assert len(out) < 1024


# ---------------------------------------------------------------------------
# stdin / normalize_prompts
# ---------------------------------------------------------------------------


class TestStdinAndNormalize:
    def test_read_stdin_skips_tty(self) -> None:
        class _TtyStdin:
            def isatty(self) -> bool:
                return True

            def read(self) -> str:  # pragma: no cover - never called
                raise AssertionError("stdin must not be read in tty mode")

        assert pm.read_stdin_prompt(_TtyStdin()) is None

    def test_read_stdin_returns_body(self) -> None:
        class _PipedStdin:
            def isatty(self) -> bool:
                return False

            def read(self) -> str:
                return "  piped prompt  \n"

        assert pm.read_stdin_prompt(_PipedStdin()) == "piped prompt"

    def test_normalize_prefers_explicit_p(self, tmp_path) -> None:
        args = _ns(print_mode=["one", "two"])

        class _Stdin:
            def isatty(self) -> bool:
                return False

            def read(self) -> str:  # pragma: no cover - never read
                raise AssertionError("must not consume stdin when -p set")

        out = pm.normalize_prompts(args, stdin=_Stdin(), base_dir=str(tmp_path))
        assert out == ["one", "two"]

    def test_normalize_falls_through_to_stdin(self, tmp_path) -> None:
        args = _ns(print_mode=None)

        class _Stdin:
            def isatty(self) -> bool:
                return False

            def read(self) -> str:
                return "from stdin"

        out = pm.normalize_prompts(args, stdin=_Stdin(), base_dir=str(tmp_path))
        assert out == ["from stdin"]

    def test_normalize_returns_empty_when_nothing(self, tmp_path) -> None:
        args = _ns(print_mode=None)

        class _Stdin:
            def isatty(self) -> bool:
                return True

        out = pm.normalize_prompts(args, stdin=_Stdin(), base_dir=str(tmp_path))
        assert out == []

    def test_normalize_expands_at_files(self, tmp_path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("# README\n", encoding="utf-8")
        args = _ns(print_mode=[f"summarize @{f}"])

        class _Stdin:
            def isatty(self) -> bool:
                return True

        out = pm.normalize_prompts(
            args, stdin=_Stdin(), base_dir=str(tmp_path)
        )
        assert len(out) == 1
        assert "# README" in out[0]


# ---------------------------------------------------------------------------
# stream-json runner
# ---------------------------------------------------------------------------


class _FakeEventType:
    """Helper that mimics LoopEvent.type with a ``.value`` attribute."""

    def __init__(self, value: str) -> None:
        self.value = value


class _FakeEvent:
    def __init__(self, type_: str, turn: int, data: Any) -> None:
        self.type = _FakeEventType(type_)
        self.turn = turn
        self.data = data


class _StreamingAgent:
    """Stub agent producing a deterministic LoopEvent sequence."""

    def __init__(self, success: bool = True) -> None:
        self.success = success

    async def async_run_events(self, prompt: str, env: Any = None):
        yield _FakeEvent("step", 0, {"text": "thinking"})
        yield _FakeEvent("step", 1, {"text": "calling tool"})
        yield _FakeEvent(
            "result",
            2,
            type("R", (), {"output": "done", "reason": "" if self.success else "error"})(),
        )


class TestStreamingJson:
    def test_emits_one_line_per_event(self) -> None:
        agent = _StreamingAgent(success=True)
        out = io.StringIO()
        rc = pm.run_streaming_json_turn(
            agent, "anything", env=None, out=out
        )
        assert rc == 0
        lines = [ln for ln in out.getvalue().splitlines() if ln]
        assert len(lines) == 3
        first = json.loads(lines[0])
        assert first["type"] == "step"
        assert "data" in first
        last = json.loads(lines[-1])
        assert last["type"] == "result"

    def test_failure_event_returns_rc1(self) -> None:
        agent = _StreamingAgent(success=False)
        out = io.StringIO()
        rc = pm.run_streaming_json_turn(
            agent, "anything", env=None, out=out
        )
        assert rc == 1

    def test_legacy_agent_falls_back_to_synthetic_result(self) -> None:
        class _LegacyAgent:
            async def async_run(self, prompt: str, env: Any = None) -> Any:
                return type(
                    "R",
                    (),
                    {"output": "legacy ok", "cost": 0.0, "success": True, "steps": 1},
                )()

        out = io.StringIO()
        rc = pm.run_streaming_json_turn(_LegacyAgent(), "p", env=None, out=out)
        assert rc == 0
        lines = [ln for ln in out.getvalue().splitlines() if ln]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["type"] == "result"
        assert rec["data"]["output"] == "legacy ok"


# ---------------------------------------------------------------------------
# select_prompt_strategy
# ---------------------------------------------------------------------------


class TestSelectStrategy:
    def test_text_default(self) -> None:
        args = argparse.Namespace(json_output=False, stream_json=False)
        assert pm.select_prompt_strategy(args) == "text"

    def test_json_when_only_json(self) -> None:
        args = argparse.Namespace(json_output=True, stream_json=False)
        assert pm.select_prompt_strategy(args) == "json"

    def test_stream_json_wins(self) -> None:
        args = argparse.Namespace(json_output=True, stream_json=True)
        assert pm.select_prompt_strategy(args) == "stream-json"


# ---------------------------------------------------------------------------
# End-to-end print path (with provider/agent stubs)
# ---------------------------------------------------------------------------


class _FakeProvider:
    model_name = "fake-model"
    _enable_thinking = False
    _thinking_budget = 0


class _FakeAgent:
    """Stub Agent that records every prompt it received."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def async_run(self, prompt: str, env: Any = None) -> Any:
        self.prompts.append(prompt)
        return type(
            "R",
            (),
            {"output": f"[reply to {prompt!r}]", "success": True, "cost": 0.0, "steps": 1},
        )()


class TestPrintPathIntegration:
    def test_multi_p_runs_each_turn(self, tmp_path, monkeypatch, capsys) -> None:
        fake_agent = _FakeAgent()

        # Stub provider construction.
        monkeypatch.setattr(
            "chimera.weasel.providers.build_provider",
            lambda args: _FakeProvider(),
        )
        # Stub Agent construction so our fake captures the prompts.
        monkeypatch.setattr(
            "chimera.core.agent.Agent",
            lambda **kwargs: fake_agent,
        )
        # Force resume path to no-op.
        monkeypatch.setattr(
            weasel_cli, "_apply_weasel_resume_prefix",
            lambda args, default_prompt: default_prompt,
        )

        args = _ns(
            print_mode=["alpha", "beta"],
            cwd=str(tmp_path),
            json_output=True,
            stream_json=False,
        )
        rc = weasel_cli._run_print_mode(args)  # noqa: SLF001
        assert rc == 0
        # Two prompts were sent.
        assert len(fake_agent.prompts) == 2
        # Two JSON envelopes were printed (one per turn).
        out_lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert len(out_lines) == 2
        envelopes = [json.loads(ln) for ln in out_lines]
        assert envelopes[0]["turn"] == 0
        assert envelopes[1]["turn"] == 1

    def test_stdin_substitutes_when_no_p(self, tmp_path, monkeypatch, capsys) -> None:
        fake_agent = _FakeAgent()
        monkeypatch.setattr(
            "chimera.weasel.providers.build_provider",
            lambda args: _FakeProvider(),
        )
        monkeypatch.setattr(
            "chimera.core.agent.Agent",
            lambda **kwargs: fake_agent,
        )
        monkeypatch.setattr(
            weasel_cli, "_apply_weasel_resume_prefix",
            lambda args, default_prompt: default_prompt,
        )

        # Replace sys.stdin with a piped stand-in.
        class _Piped(io.StringIO):
            def isatty(self) -> bool:
                return False

        monkeypatch.setattr("sys.stdin", _Piped("from stdin pipe"))

        args = _ns(
            print_mode=None, cwd=str(tmp_path),
            json_output=False, stream_json=False,
        )
        rc = weasel_cli._run_print_mode(args)  # noqa: SLF001
        assert rc == 0
        assert fake_agent.prompts == ["from stdin pipe"]

    def test_missing_prompt_returns_2(self, tmp_path, monkeypatch, capsys) -> None:
        # No -p, no stdin → usage error.
        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr("sys.stdin", _Tty(""))
        args = _ns(
            print_mode=None, cwd=str(tmp_path),
            json_output=False, stream_json=False,
        )
        rc = weasel_cli._run_print_mode(args)  # noqa: SLF001
        assert rc == 2
        assert "PROMPT" in capsys.readouterr().err

    def test_at_file_expansion_through_print_path(
        self, tmp_path, monkeypatch
    ) -> None:
        fake_agent = _FakeAgent()
        f = tmp_path / "snippet.py"
        f.write_text("def foo(): pass\n", encoding="utf-8")

        monkeypatch.setattr(
            "chimera.weasel.providers.build_provider",
            lambda args: _FakeProvider(),
        )
        monkeypatch.setattr(
            "chimera.core.agent.Agent",
            lambda **kwargs: fake_agent,
        )
        monkeypatch.setattr(
            weasel_cli, "_apply_weasel_resume_prefix",
            lambda args, default_prompt: default_prompt,
        )

        args = _ns(
            print_mode=[f"please review @{f}"],
            cwd=str(tmp_path),
            json_output=False,
            stream_json=False,
        )
        rc = weasel_cli._run_print_mode(args)  # noqa: SLF001
        assert rc == 0
        # The prompt the agent received contains the inlined file body.
        assert any("def foo(): pass" in p for p in fake_agent.prompts), (
            f"agent never saw the @file body. prompts={fake_agent.prompts!r}"
        )
