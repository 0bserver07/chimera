"""Tests for the deprecated ``chimera cc`` subcommand alias.

The ``cc`` alias was renamed to ``mink`` in v0.5.0 and is slated for
removal in v0.7.0. Until then it must:

* Still parse and dispatch to ``chimera mink``.
* Print a stderr deprecation banner that names both the v0.5.0 rename
  and the v0.7.0 removal target.
* Emit a real ``DeprecationWarning`` for callers that route warnings
  through ``warnings.filterwarnings`` (CI, test suites, etc.).
* Honor ``CHIMERA_SUPPRESS_CC_WARNING=1`` as an opt-out for users with
  vendored tooling that can't be updated immediately.

These tests touch only the dispatcher branch in ``chimera.cli.main``;
they do not boot the full mink runtime. We monkey-patch
``chimera.mink.cli.run`` to a no-op so the test stays fast and
hermetic.
"""
from __future__ import annotations

import argparse
import warnings

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_mink_run(monkeypatch: pytest.MonkeyPatch) -> list[argparse.Namespace]:
    """Replace ``chimera.mink.cli.run`` with a recorder.

    Returns the list of namespaces the dispatcher forwarded; tests can
    assert that ``cc`` actually routed to mink without booting the
    real runtime.
    """
    captured: list[argparse.Namespace] = []

    def _run(args: argparse.Namespace) -> int:
        captured.append(args)
        return 0

    import chimera.mink.cli as _mink_cli

    monkeypatch.setattr(_mink_cli, "run", _run)
    return captured


@pytest.fixture
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop CHIMERA_SUPPRESS_CC_WARNING so the host env can't leak in."""
    monkeypatch.delenv("CHIMERA_SUPPRESS_CC_WARNING", raising=False)


# ---------------------------------------------------------------------------
# Argparse surface
# ---------------------------------------------------------------------------


def test_cc_subparser_help_mentions_v0_7_0() -> None:
    """The cc subparser's help text names mink and the v0.7.0 removal."""
    from chimera.cli.main import build_parser

    parser = build_parser()
    # argparse's _SubParsersAction lives in the parser's actions; we
    # find it by attribute rather than by index so this isn't fragile
    # to subparser-ordering changes.
    sub_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    cc_parser = sub_action.choices["cc"]
    fmt = cc_parser.format_help()
    # The help text on the *parser itself* doesn't have to repeat the
    # banner — but the registration metadata (which feeds the parent
    # parser's "available subcommands" list) must call out v0.7.0 so
    # users running ``chimera --help`` see the removal note inline.
    parent_help = parser.format_help()
    assert "cc" in parent_help
    assert "v0.7.0" in parent_help
    assert "DEPRECATED" in parent_help.upper()
    # The subparser itself still parses cleanly — invoking its help
    # shouldn't crash.
    assert "usage" in fmt.lower()


# ---------------------------------------------------------------------------
# Dispatcher behaviour
# ---------------------------------------------------------------------------


def test_cc_dispatches_to_mink(
    capsys: pytest.CaptureFixture[str],
    fake_mink_run: list[argparse.Namespace],
    hermetic_env: None,
) -> None:
    """Invoking ``chimera cc --help`` style still dispatches to mink."""
    from chimera.cli.main import main

    # We don't pass --help here because that would short-circuit at
    # argparse. Use --version, which mink defines and which doesn't
    # require external state.
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        rc = main(["cc"])
        capsys.readouterr()  # drain banner so it doesn't pollute stdout/err
    assert rc == 0
    assert len(fake_mink_run) == 1, (
        "cc should dispatch to mink exactly once"
    )


def test_cc_emits_deprecation_warning(
    capsys: pytest.CaptureFixture[str],
    fake_mink_run: list[argparse.Namespace],
    hermetic_env: None,
) -> None:
    """A real DeprecationWarning fires alongside the stderr banner."""
    from chimera.cli.main import main

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        main(["cc"])

    # Banner on stderr.
    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    assert "v0.5.0" in err
    assert "v0.7.0" in err
    assert "chimera mink" in err

    # And a real DeprecationWarning routed through the warnings module.
    matching = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "v0.7.0" in str(w.message)
    ]
    assert matching, (
        "expected a DeprecationWarning naming v0.7.0; "
        f"got {[(w.category.__name__, str(w.message)) for w in caught]}"
    )


def test_cc_warning_suppressed_by_env(
    capsys: pytest.CaptureFixture[str],
    fake_mink_run: list[argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CHIMERA_SUPPRESS_CC_WARNING=1`` silences both surfaces."""
    monkeypatch.setenv("CHIMERA_SUPPRESS_CC_WARNING", "1")

    from chimera.cli.main import main

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        main(["cc"])

    err = capsys.readouterr().err
    assert "deprecated" not in err.lower(), (
        "stderr banner should be suppressed when CHIMERA_SUPPRESS_CC_WARNING=1"
    )
    matching = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert not matching, (
        "DeprecationWarning should be suppressed when the env var is set; "
        f"got {[(w.category.__name__, str(w.message)) for w in matching]}"
    )
    assert len(fake_mink_run) == 1, (
        "suppression must not block the dispatch — the alias still works"
    )


def test_cc_warning_suppressed_only_for_truthy_env(
    capsys: pytest.CaptureFixture[str],
    fake_mink_run: list[argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty env var should NOT suppress (no accidental silencing)."""
    monkeypatch.setenv("CHIMERA_SUPPRESS_CC_WARNING", "")

    from chimera.cli.main import main

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        main(["cc"])

    err = capsys.readouterr().err
    assert "deprecated" in err.lower(), (
        "an empty CHIMERA_SUPPRESS_CC_WARNING should not suppress the banner"
    )
    matching = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert matching, (
        "an empty env var should not suppress the DeprecationWarning either"
    )


# ---------------------------------------------------------------------------
# Documentation contract
# ---------------------------------------------------------------------------


def test_cc_dispatcher_warning_contains_v_targets() -> None:
    """The dispatcher's warning string itself names both versions.

    Static check: even without invoking the CLI, the source contains the
    canonical version markers, so future refactors that drop ``v0.7.0``
    or ``v0.5.0`` from the message break this test rather than silently
    weakening the deprecation contract.
    """
    import inspect

    from chimera.cli.main import main

    src = inspect.getsource(main)
    # Both versions must appear in the dispatcher branch.
    assert "v0.5.0" in src
    assert "v0.7.0" in src
    assert "CHIMERA_SUPPRESS_CC_WARNING" in src
