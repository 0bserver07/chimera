"""Shared TUI-test isolation.

The multiplexer reads the user config chain at construction — keybinding
overrides (``tui.keybinds`` via ``$CHIMERA_CONFIG_HOME``) and the status
line's config scopes (``~/.config/chimera``, ``~/.chimera``,
``<project>/.chimera``). Tests must render the *defaults* regardless of what
the developer's machine has configured, so both read paths are isolated for
every test in this directory:

- ``$CHIMERA_CONFIG_HOME`` points at an empty per-test directory; tests that
  exercise the keybinds read path write their own ``config.toml`` under it.
- Status-line config discovery is stubbed to ``{}``; tests that exercise the
  loader itself use the ``real_load_tui_config`` fixture, and tests needing a
  specific config pass ``StatusLine(config=...)`` explicitly.
"""
from __future__ import annotations

import pytest

try:  # the tui extra (rich/textual) may be absent — never break collection
    import chimera.tui.statusline as _statusline
except ImportError:  # pragma: no cover
    _statusline = None  # type: ignore[assignment]

_REAL_LOAD_TUI_CONFIG = _statusline.load_tui_config if _statusline is not None else None


@pytest.fixture(autouse=True)
def _isolated_chimera_config(tmp_path, monkeypatch):
    """Point the Chimera config chain at an empty per-test directory."""
    config_home = tmp_path / "chimera-config-home"
    config_home.mkdir()
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(config_home))
    return config_home


@pytest.fixture(autouse=True)
def _no_machine_tui_config(monkeypatch: pytest.MonkeyPatch):
    """Keep this machine's chimera config out of TUI test behavior."""
    if _statusline is not None:
        monkeypatch.setattr(_statusline, "load_tui_config", lambda *a, **k: {})
    yield


@pytest.fixture
def real_load_tui_config():
    """The unstubbed loader, for tests that exercise config discovery."""
    if _REAL_LOAD_TUI_CONFIG is None:  # pragma: no cover
        pytest.skip("tui extra not installed")
    return _REAL_LOAD_TUI_CONFIG
