"""Tests for wave-11 A4: ``chimera which`` heuristic CLI recommender.

Covers:
* ``recommend()`` returns the right top codename for a representative
  task across all 7 codenames.
* Tie-breaking is deterministic (canonical codename order).
* Empty task returns ``[]``.
* No-match task returns ``top_k`` zero-score entries (documented shape).
* JSON output mode parses round-trip to the documented schema.
* ``--top-k=1`` clips the list correctly.
* End-to-end argparse: ``add_subparser`` + ``run`` produce the documented
  text and JSON.
"""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout

import pytest

from chimera.cli import which_cmd
from chimera.cli.which_cmd import (
    KEYWORD_MAP,
    Recommendation,
    add_subparser,
    recommend,
    run,
    tokenize,
)


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------


def test_tokenize_lowercases_and_splits_on_non_alpha() -> None:
    assert tokenize("Spin up a TUI/IDE panel") == [
        "spin", "up", "a", "tui", "ide", "panel",
    ]


def test_tokenize_drops_digits_and_punctuation() -> None:
    assert tokenize("low-resource llama.cpp 7B!") == [
        "low", "resource", "llama", "cpp", "b",
    ]


def test_tokenize_empty_returns_empty_list() -> None:
    assert tokenize("") == []
    assert tokenize("   ") == []
    assert tokenize("123 !!! ...") == []


# ---------------------------------------------------------------------------
# Catalogue shape
# ---------------------------------------------------------------------------


def test_keyword_map_has_all_seven_codenames() -> None:
    expected = {"mink", "otter", "ferret", "weasel", "shrew", "stoat", "badger"}
    assert set(KEYWORD_MAP.keys()) == expected


def test_keyword_map_canonical_order() -> None:
    """Insertion order = canonical order so tie-breaking is documented."""
    assert list(KEYWORD_MAP.keys()) == [
        "mink", "shrew", "stoat", "ferret", "badger", "weasel", "otter",
    ]


# ---------------------------------------------------------------------------
# recommend() -- table-driven across all 7 codenames
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task, expected_top",
    [
        # mink: TUI / panel / IDE / GUI / textual
        ("I want a TUI panel for coding", "mink"),
        ("Open an IDE-style GUI for the agent", "mink"),
        # shrew: small / local / ollama / mini / tiny / llama / qwen
        ("Run a small local llama model on my laptop", "shrew"),
        ("Use ollama to host a tiny qwen", "shrew"),
        # stoat: shell / bash / repl / terminal / command
        ("Open a shell REPL in the terminal", "stoat"),
        ("Drive bash commands from the agent", "stoat"),
        # ferret: sandbox / docker / isolate / jail / security / untrusted
        ("Sandbox untrusted code in docker", "ferret"),
        ("Isolate the jail for a security review", "ferret"),
        # badger: strict / parity / validate / golden / deterministic
        ("I need strict deterministic parity validation", "badger"),
        ("Validate against the golden output", "badger"),
        # weasel: rpc / json-stdio / headless / programmatic / api
        ("Drive the agent over rpc as a headless api", "weasel"),
        ("Programmatic json stdio harness", "weasel"),
        # otter: server / http / multi-session / multi-user / port / daemon
        ("Run an http server with multi-user multi-session support", "otter"),
        ("Spin up a daemon on a port", "otter"),
    ],
)
def test_recommend_top_codename(task: str, expected_top: str) -> None:
    """The highest-score codename for each task matches expectation."""
    recs = recommend(task, top_k=3)
    assert recs, f"no recommendations for task={task!r}"
    assert recs[0].name == expected_top, (
        f"task={task!r} -> top={recs[0].name!r} "
        f"(expected {expected_top!r}); full={recs}"
    )
    assert recs[0].score >= 1


def test_recommend_returns_top_k_count() -> None:
    recs = recommend("I want a TUI panel for coding", top_k=3)
    assert len(recs) == 3
    recs_one = recommend("I want a TUI panel for coding", top_k=1)
    assert len(recs_one) == 1
    assert recs_one[0].name == "mink"


def test_recommend_top_k_zero_returns_empty() -> None:
    assert recommend("anything", top_k=0) == []


def test_recommend_top_k_clamps_to_catalogue_size() -> None:
    recs = recommend("tui panel", top_k=99)
    assert len(recs) == len(KEYWORD_MAP)


def test_recommend_empty_task_returns_empty_list() -> None:
    """Empty / whitespace-only / digit-only tasks yield no recommendations."""
    assert recommend("", top_k=3) == []
    assert recommend("   ", top_k=3) == []
    assert recommend("123!!!", top_k=3) == []


def test_recommend_no_match_returns_zero_score_entries() -> None:
    """Documented shape: top_k zero-score entries in canonical order."""
    recs = recommend("xyzzy plover frobnitz", top_k=3)
    assert len(recs) == 3
    # All zero-score, all empty rationales.
    for r in recs:
        assert r.score == 0
        assert r.rationale == []
    # Canonical order tie-break: mink, shrew, stoat (insertion order).
    canonical = list(KEYWORD_MAP.keys())
    assert [r.name for r in recs] == canonical[:3]


def test_recommend_rationale_lists_matched_keywords() -> None:
    recs = recommend("I want a TUI panel with a GUI", top_k=1)
    assert recs[0].name == "mink"
    # tui, panel, gui all match
    assert set(recs[0].rationale) >= {"tui", "panel", "gui"}


def test_recommend_multi_token_keyword_requires_all_tokens() -> None:
    """``json-stdio`` requires both 'json' and 'stdio' to appear."""
    only_json = recommend("I want json output", top_k=7)
    weasel = next(r for r in only_json if r.name == "weasel")
    assert "json-stdio" not in weasel.rationale

    both = recommend("a json stdio harness", top_k=7)
    weasel_both = next(r for r in both if r.name == "weasel")
    assert "json-stdio" in weasel_both.rationale


def test_recommend_deterministic_tie_break() -> None:
    """When two codenames score the same, canonical order wins."""
    # 'mini' is a shrew keyword; 'panel' is a mink keyword.
    # Both score 1, tie-breaks to canonical order: mink before shrew.
    recs = recommend("a panel and a mini setup", top_k=2)
    names = [r.name for r in recs]
    assert names == ["mink", "shrew"], recs


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


def test_format_text_contains_numbered_recs() -> None:
    args = argparse.Namespace(
        task="I want a TUI panel", output="text", top_k=2,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    out = buf.getvalue()
    assert rc == 0
    assert "1. mink" in out
    assert "matched:" in out
    assert "tui" in out


def test_format_json_round_trips() -> None:
    args = argparse.Namespace(
        task="run a small llama locally", output="json", top_k=3,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    payload = json.loads(buf.getvalue())
    assert rc == 0
    assert payload["task"] == "run a small llama locally"
    assert isinstance(payload["recommendations"], list)
    assert len(payload["recommendations"]) == 3
    top = payload["recommendations"][0]
    assert top["name"] == "shrew"
    assert top["score"] >= 1
    assert isinstance(top["rationale"], list)
    assert "small" in top["rationale"] or "llama" in top["rationale"]


def test_format_json_empty_task_yields_empty_recs() -> None:
    args = argparse.Namespace(task="", output="json", top_k=3)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    payload = json.loads(buf.getvalue())
    assert rc == 0
    assert payload["task"] == ""
    assert payload["recommendations"] == []


def test_format_text_top_k_one() -> None:
    args = argparse.Namespace(
        task="sandbox untrusted code in docker",
        output="text",
        top_k=1,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    out = buf.getvalue()
    assert rc == 0
    assert "1. ferret" in out
    # No second entry.
    assert "2. " not in out


# ---------------------------------------------------------------------------
# argparse wiring (add_subparser)
# ---------------------------------------------------------------------------


def test_add_subparser_registers_required_task_flag() -> None:
    parser = argparse.ArgumentParser(prog="chimera")
    sub = parser.add_subparsers(dest="command")
    which_parser = add_subparser(sub)
    assert which_parser is not None
    # --task is required: parsing without it must error.
    with pytest.raises(SystemExit):
        parser.parse_args(["which"])
    # Happy path.
    args = parser.parse_args(
        ["which", "--task", "tui panel", "--output", "json", "--top-k", "1"]
    )
    assert args.command == "which"
    assert args.task == "tui panel"
    assert args.output == "json"
    assert args.top_k == 1


def test_add_subparser_defaults() -> None:
    parser = argparse.ArgumentParser(prog="chimera")
    sub = parser.add_subparsers(dest="command")
    add_subparser(sub)
    args = parser.parse_args(["which", "--task", "anything"])
    assert args.output == "text"
    assert args.top_k == 3


# ---------------------------------------------------------------------------
# Recommendation dataclass
# ---------------------------------------------------------------------------


def test_recommendation_to_dict_shape() -> None:
    r = Recommendation(name="mink", score=2, rationale=["tui", "panel"])
    d = r.to_dict()
    assert d == {"name": "mink", "score": 2, "rationale": ["tui", "panel"]}
    # Mutating returned rationale doesn't mutate the dataclass.
    d["rationale"].append("extra")
    assert r.rationale == ["tui", "panel"]


def test_module_exports() -> None:
    """Public API surface stays stable."""
    expected = {
        "KEYWORD_MAP", "Recommendation", "add_subparser", "recommend",
        "run", "tokenize",
    }
    assert expected <= set(which_cmd.__all__)
