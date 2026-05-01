"""Tests for the top-level ``chimera resume`` dispatcher.

Covers:
* prefix-based codename detection across the seven known CLIs
* "newest across all" resolution from synthetic eventlog dirs
* subprocess dispatch with pass-through args (mocked)
* error paths: unknown prefix, empty eventlog root

The fixture builds a synthetic ``~/.chimera/eventlog/`` populated with
runs from three different CLIs (otter / mink / weasel) so the
"newest-across-all" resolver can be exercised without provider imports.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.cli import resume_cmd
from chimera.cli.main import build_parser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_run(
    root: Path,
    codename: str,
    timestamp: str,
    uuid8: str,
    *,
    cwd: str | None = None,
) -> str:
    """Materialise a synthetic eventlog run directory.

    Mirrors the on-disk layout the per-CLI sessions create — a
    ``summary.json`` in a ``<codename>-<timestamp>-<uuid8>`` directory —
    so :func:`find_latest_run` and the cwd filter can both find it.
    """
    run_id = f"{codename}-{timestamp}-{uuid8}"
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    summary: dict[str, Any] = {"cwd": cwd or "/tmp/test"}
    (run_dir / "summary.json").write_text(json.dumps(summary))
    return run_id


@pytest.fixture()
def eventlog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthetic eventlog root with runs from three CLIs.

    The fixture also redirects ``Path.home()`` so that
    :func:`default_eventlog_root` resolves into ``tmp_path/.chimera``,
    keeping every test fully sandboxed.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)

    root = home / ".chimera" / "eventlog"
    root.mkdir(parents=True)

    # Older otter run
    _make_run(root, "otter", "20260101T120000", "aaaaaaaa")
    # Newer mink run (wins lexically because timestamp > otter's)
    _make_run(root, "mink", "20260301T090000", "bbbbbbbb")
    # Mid weasel run
    _make_run(root, "weasel", "20260201T080000", "cccccccc")

    return root


# ---------------------------------------------------------------------------
# detect_codename
# ---------------------------------------------------------------------------


class TestDetectCodename:
    @pytest.mark.parametrize(
        "run_id,expected",
        [
            ("otter-20260430T101501-71032a5e", "otter"),
            ("mink-20260101T120000-aaaaaaaa", "mink"),
            ("ferret-20260101T120000-aaaaaaaa", "ferret"),
            ("weasel-20260101T120000-aaaaaaaa", "weasel"),
            ("shrew-20260101T120000-aaaaaaaa", "shrew"),
            ("stoat-20260101T120000-aaaaaaaa", "stoat"),
            ("badger-20260101T120000-aaaaaaaa", "badger"),
        ],
    )
    def test_known_codename(self, run_id: str, expected: str) -> None:
        assert resume_cmd.detect_codename(run_id) == expected

    @pytest.mark.parametrize(
        "run_id",
        [
            "ferocious-20260101T120000-aaaaaaaa",  # not in KNOWN_CODENAMES
            "20260101T120000-no-prefix",
            "",
        ],
    )
    def test_unknown_returns_none(self, run_id: str) -> None:
        assert resume_cmd.detect_codename(run_id) is None


# ---------------------------------------------------------------------------
# find_latest_across_all
# ---------------------------------------------------------------------------


class TestFindLatestAcrossAll:
    def test_picks_newest_lexically(self, eventlog: Path) -> None:
        latest = resume_cmd.find_latest_across_all(eventlog)
        # mink-20260301... > weasel-20260201... > otter-20260101...
        assert latest is not None
        assert latest.startswith("mink-")

    def test_empty_root_returns_none(self, tmp_path: Path) -> None:
        assert resume_cmd.find_latest_across_all(tmp_path / "missing") is None


# ---------------------------------------------------------------------------
# run() dispatch
# ---------------------------------------------------------------------------


class TestRun:
    def test_explicit_id_dispatches_to_correct_cli(
        self, eventlog: Path
    ) -> None:
        run_id = "otter-20260101T120000-aaaaaaaa"
        args = argparse.Namespace(run_id=run_id, extra=[])

        fake = MagicMock()
        fake.returncode = 0
        with patch.object(subprocess, "run", return_value=fake) as mock_run:
            rc = resume_cmd.run(args)

        assert rc == 0
        mock_run.assert_called_once()
        argv = mock_run.call_args[0][0]
        # The dispatched argv should contain `otter --resume <id>`
        assert "otter" in argv
        assert "--resume" in argv
        assert run_id in argv

    def test_passthrough_extra_args(self, eventlog: Path) -> None:
        run_id = "weasel-20260201T080000-cccccccc"
        args = argparse.Namespace(
            run_id=run_id, extra=["-p", "next prompt"]
        )

        fake = MagicMock()
        fake.returncode = 7
        with patch.object(subprocess, "run", return_value=fake) as mock_run:
            rc = resume_cmd.run(args)

        assert rc == 7  # mirrors the delegate's exit code
        argv = mock_run.call_args[0][0]
        assert argv[-3:] == ["--resume", run_id, "-p"] or argv[-2:] == [
            "-p",
            "next prompt",
        ]
        assert "next prompt" in argv

    def test_passthrough_strips_leading_double_dash(
        self, eventlog: Path
    ) -> None:
        run_id = "mink-20260301T090000-bbbbbbbb"
        args = argparse.Namespace(
            run_id=run_id, extra=["--", "-p", "hi"]
        )

        fake = MagicMock()
        fake.returncode = 0
        with patch.object(subprocess, "run", return_value=fake) as mock_run:
            resume_cmd.run(args)

        argv = mock_run.call_args[0][0]
        assert "--" not in argv

    def test_no_id_picks_newest_across_all(
        self, eventlog: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(run_id=None, extra=[])

        fake = MagicMock()
        fake.returncode = 0
        with patch.object(subprocess, "run", return_value=fake) as mock_run:
            rc = resume_cmd.run(args)

        assert rc == 0
        argv = mock_run.call_args[0][0]
        # Newest is the mink-... run
        assert "mink" in argv
        # Stderr advertises the auto-pick
        captured = capsys.readouterr()
        assert "most recent" in captured.err

    def test_no_id_no_runs_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "empty-home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        args = argparse.Namespace(run_id=None, extra=[])
        rc = resume_cmd.run(args)
        assert rc == 1

    def test_unknown_prefix_errors(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(
            run_id="ferocious-20260101T120000-deadbeef", extra=[]
        )
        rc = resume_cmd.run(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "cannot detect" in captured.err

    def test_subprocess_oserror_returns_2(self, eventlog: Path) -> None:
        run_id = "otter-20260101T120000-aaaaaaaa"
        args = argparse.Namespace(run_id=run_id, extra=[])

        with patch.object(
            subprocess, "run", side_effect=OSError("boom")
        ):
            rc = resume_cmd.run(args)
        assert rc == 2


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


class TestParserRegistration:
    def test_resume_subcommand_registered(self) -> None:
        parser = build_parser()
        for action in parser._subparsers._actions:  # type: ignore[union-attr]
            if isinstance(action, argparse._SubParsersAction):
                assert "resume" in action.choices
                return
        pytest.fail("subparsers action not found")

    def test_resume_with_id_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["resume", "otter-20260101T120000-aaaaaaaa"]
        )
        assert args.command == "resume"
        assert args.run_id == "otter-20260101T120000-aaaaaaaa"

    def test_resume_no_id_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["resume"])
        assert args.command == "resume"
        assert args.run_id is None

    def test_resume_with_passthrough_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "resume",
                "otter-20260101T120000-aaaaaaaa",
                "-p",
                "next",
            ]
        )
        assert args.command == "resume"
        # Pass-through args land in args.extra via REMAINDER
        assert "next" in args.extra
