"""Shared fixtures for the TUI tests.

The multiplexer now reads the user config chain (``~/.chimera/config.toml``
via ``$CHIMERA_CONFIG_HOME``) at construction for ``tui.keybinds`` overrides.
Tests must never see the developer's real config, so every test in this
package gets an isolated (empty) config home by default; tests that exercise
the config read path write their own ``config.toml`` under the same variable.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_chimera_config(tmp_path, monkeypatch):
    """Point the Chimera config chain at an empty per-test directory."""
    config_home = tmp_path / "chimera-config-home"
    config_home.mkdir()
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(config_home))
    return config_home
