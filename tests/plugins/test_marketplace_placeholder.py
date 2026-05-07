"""Tests for the "no built-in default index" path of the marketplace.

The default :data:`chimera.plugins.marketplace.DEFAULT_INDEX_URL` is
intentionally ``None``: we don't host a public registry. These tests
verify the contract:

- When no env / no flag / no config is set, ``chimera plugins search``
  exits ``rc=2`` and writes a friendly multi-line help message to
  ``stderr`` (not ``stdout``, so JSON output mode stays parseable).
- ``$CHIMERA_PLUGIN_INDEX`` (env var) and ``--index`` (flag) both
  override the missing default and put the marketplace into its
  normal happy path. ``--index`` beats env.
- ``examples/plugin-index.json`` parses through the marketplace's own
  loader, ships >= 2 plugin entries, and keeps the ``_note`` warning
  intact so future maintainers don't accidentally dress up the sample
  as a real index.

Every test path is hermetic: we redirect ``$CHIMERA_CONFIG_HOME`` at
``tmp_path`` so any host-side ``~/.chimera/config.toml`` cannot leak
``[global] plugin_index`` into the resolution chain.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from chimera.cli.main import main as cli_main
from chimera.plugins.marketplace import (
    NO_INDEX_HELP,
    PluginInfo,
    fetch_index,
)

# ``examples/plugin-index.json`` sits at the repo root, three parents
# above this file: tests/plugins/test_x.py -> tests/plugins -> tests
# -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_INDEX = _REPO_ROOT / "examples" / "plugin-index.json"


@pytest.fixture(autouse=True)
def _hermetic_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure the test never sees the developer's real config or env."""
    monkeypatch.delenv("CHIMERA_PLUGIN_INDEX", raising=False)
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path / "_config_home"))


# ---------------------------------------------------------------------------
# rc=2 friendly-error path
# ---------------------------------------------------------------------------


def test_search_no_index_friendly_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No env / no flag / no config -> rc=2 + multi-line help on stderr.

    Going to stderr (not stdout) is load-bearing: ``--mode json`` users
    parse stdout, so a friendly help banner there would corrupt the
    JSON they're trying to read.
    """
    rc = cli_main(["plugins", "search"])
    assert rc == 2

    captured = capsys.readouterr()
    # The friendly help lands on stderr.
    assert "No plugin index configured" in captured.err
    assert "CHIMERA_PLUGIN_INDEX" in captured.err
    assert "--index" in captured.err
    assert "chimera config set plugin_index" in captured.err
    assert "docs/plugins-index.md" in captured.err
    assert "examples/plugin-index.json" in captured.err

    # Stdout stays clean — important for `--mode json` consumers.
    assert captured.out == ""

    # The constant matches the rendered output verbatim (no string
    # drift).
    assert NO_INDEX_HELP in captured.err


# ---------------------------------------------------------------------------
# Happy path — env var
# ---------------------------------------------------------------------------


def test_search_with_env_var(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Setting ``$CHIMERA_PLUGIN_INDEX`` lets search reach the registry.

    We point env at a local file so no network is involved — that's
    what users do for offline mirrors and what we use to keep the
    test fully hermetic.
    """
    index = tmp_path / "env-index.json"
    index.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "via-env",
                        "version": "1.0.0",
                        "description": "Env-resolved plugin",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHIMERA_PLUGIN_INDEX", str(index))

    rc = cli_main(["plugins", "search"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "via-env" in out
    assert "Env-resolved plugin" in out


def test_search_with_env_var_remote_stub(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``http(s)://`` env var routes through httpx — stubbed here."""
    payload = json.dumps(
        {"plugins": [{"name": "remote-via-env", "version": "9.0.0"}]}
    )
    calls: list[str] = []

    class _StubResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def _stub_get(url: str, timeout: float = 0.0) -> _StubResponse:
        calls.append(url)
        return _StubResponse(payload)

    stub_module = types.ModuleType("httpx")
    stub_module.get = _stub_get  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "httpx", stub_module)
    monkeypatch.setenv(
        "CHIMERA_PLUGIN_INDEX", "https://example.com/index.json"
    )

    rc = cli_main(["plugins", "search", "remote"])
    assert rc == 0
    assert calls == ["https://example.com/index.json"]
    assert "remote-via-env" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# --index flag wins over env
# ---------------------------------------------------------------------------


def test_search_with_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """``--index`` overrides ``$CHIMERA_PLUGIN_INDEX``.

    Env points at an *invalid* file; if the override didn't actually
    win, fetch_index would explode trying to parse that — so a clean
    rc=0 here is proof the precedence is correct.
    """
    bogus = tmp_path / "bogus.json"
    bogus.write_text("not json", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_PLUGIN_INDEX", str(bogus))

    real = tmp_path / "real.json"
    real.write_text(
        json.dumps(
            {"plugins": [{"name": "from-flag", "version": "1.0.0"}]}
        ),
        encoding="utf-8",
    )

    rc = cli_main(
        ["plugins", "search", "from", "--index", str(real)]
    )
    assert rc == 0
    assert "from-flag" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Sample index parses + retains its EXAMPLE warning
# ---------------------------------------------------------------------------


def test_example_index_parses() -> None:
    """``examples/plugin-index.json`` loads via the marketplace parser.

    Round-tripping through ``fetch_index`` (rather than ``json.load``
    directly) is the point: it proves the sample stays compatible
    with whatever schema the parser expects, even after future
    refactors of ``PluginInfo.from_dict``.
    """
    assert _EXAMPLE_INDEX.is_file(), (
        f"sample index missing at {_EXAMPLE_INDEX}"
    )
    registry = fetch_index(str(_EXAMPLE_INDEX))
    plugins = registry.list_all()
    assert len(plugins) >= 2, (
        "sample index should ship >=2 plugin entries to demonstrate "
        f"a non-trivial registry (got {len(plugins)})"
    )
    # Each entry must round-trip through PluginInfo without
    # losing required fields.
    for info in plugins:
        assert isinstance(info, PluginInfo)
        assert info.name
        assert info.version


def test_example_index_warning_present() -> None:
    """The sample carries an EXAMPLE marker so it can never be mistaken
    for a real registry.

    This is a tripwire: if a future maintainer dresses the sample up
    as a "real" plugin index — adding live download URLs, removing
    the warning — this test catches it before the file leaks into
    user-facing search results.
    """
    raw = json.loads(_EXAMPLE_INDEX.read_text(encoding="utf-8"))
    note = raw.get("_note", "")
    assert "EXAMPLE" in note.upper(), (
        f"sample index should keep an EXAMPLE warning in '_note'; "
        f"got {note!r}"
    )
    # Each entry's description should also flag itself as an
    # example so search output makes the sample status obvious to
    # anyone running `chimera plugins search` against the file.
    for entry in raw.get("plugins", []):
        desc = entry.get("description", "")
        assert "EXAMPLE" in desc.upper(), (
            f"plugin entry {entry.get('name')!r} should flag itself "
            f"as an example in 'description'; got {desc!r}"
        )
