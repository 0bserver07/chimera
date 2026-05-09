"""Tests for the W15-2 P2 otter additions.

Covers:

* OPENCODE G16 — ``chimera otter generate <PROMPT>`` is a thin shim over
  the print-mode path with ``no_save=True``. We monkeypatch
  ``_run_print_mode`` so the test is hermetic.
* OPENCODE G18 — ``chimera otter models`` lists every catalog entry,
  with text and JSON output formats.
"""
from __future__ import annotations

import argparse
import json

from chimera.otter import cli as otter_cli


def _make_args(**overrides: object) -> argparse.Namespace:
    ns = argparse.Namespace(
        sub_action=None,
        sessions_format=None,
        print_mode=None,
        no_save=False,
        subcommand=None,
        model=None,
        cwd=None,
        output_format="text",
        max_steps=50,
        no_color=True,
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


# ---------------------------------------------------------------------------
# generate (OPENCODE G16)
# ---------------------------------------------------------------------------


def test_generate_missing_prompt_returns_2(capsys) -> None:
    rc = otter_cli._dispatch_generate(_make_args())
    assert rc == 2
    err = capsys.readouterr().err
    assert "missing PROMPT" in err


def test_generate_routes_through_print_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: argparse.Namespace) -> int:
        captured["print_mode"] = args.print_mode
        captured["no_save"] = args.no_save
        return 0

    monkeypatch.setattr(otter_cli, "_run_print_mode", fake_run)
    rc = otter_cli._dispatch_generate(_make_args(sub_action="hello"))
    assert rc == 0
    assert captured["print_mode"] == "hello"
    assert captured["no_save"] is True


def test_generate_subcommand_recognized() -> None:
    assert "generate" in otter_cli._SUBCOMMAND_DISPATCH
    assert "generate" in otter_cli._VALID_SUBCOMMANDS


# ---------------------------------------------------------------------------
# models (OPENCODE G18)
# ---------------------------------------------------------------------------


def test_models_text_lists_catalog_entries(capsys) -> None:
    rc = otter_cli._dispatch_models(_make_args())
    assert rc == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln]
    # Catalog defaults include at least a few well-known names.
    assert len(lines) >= 1
    # Sorted in ascending order
    assert lines == sorted(lines)


def test_models_json_format_emits_array(capsys) -> None:
    rc = otter_cli._dispatch_models(_make_args(sessions_format="json"))
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert all(isinstance(name, str) for name in payload)


def test_models_subcommand_registered() -> None:
    assert "models" in otter_cli._SUBCOMMAND_DISPATCH
    assert "models" in otter_cli._VALID_SUBCOMMANDS
