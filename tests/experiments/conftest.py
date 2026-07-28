"""Isolation for the experiment-toolkit tests.

Every test here writes real files. They must land in ``tmp_path`` and nowhere
else — a test that resolved the developer's real ``~/.chimera`` would both
pollute it and pass for the wrong reason.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def experiment_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the storage root at ``tmp_path`` and neutralise every override.

    Both seams are patched for the same reason the M1 registry tests patch
    them: ``Path.home()`` for direct resolution and ``$HOME`` because
    ``Path.expanduser`` reads the environment. The cwd is moved too, so a stray
    ``./.chimera/config.toml`` in the checkout cannot supply a ``[storage]``
    root behind the tests' back.

    Returns:
        The storage root every test writes under.
    """
    home = tmp_path / "home"
    root = tmp_path / "chimera-home"
    workdir = tmp_path / "cwd"
    for path in (home, root, workdir):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CHIMERA_HOME", str(root))
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    monkeypatch.chdir(workdir)
    return root
