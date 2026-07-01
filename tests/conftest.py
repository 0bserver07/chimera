"""Shared pytest fixtures for the Chimera test suite.

The overall test corpus constructs bare ``Agent()`` / ``ReAct()`` /
``LoopConfig()`` instances that expect tool calls to run without
interruption — they use mock providers, mock tools with names that
aren't in the production risk taxonomy, and simply need deterministic
behaviour.

Post-safety-overhaul, ``LoopConfig.__post_init__`` installs an
:class:`~chimera.permissions.presets.Interactive` permission policy by
default, which returns ``ASK`` for any tool that isn't in its read-list
— the autoused behaviour in ``drain_steps`` denies those asks, so every
test that called a tool named ``echo`` / ``mock`` / etc. suddenly
produced ``tool_calls_total=0``.

Rather than sprinkle ``yolo_mode=True`` across hundreds of test sites
— which would dilute the signal for readers — we flip the
``CHIMERA_UNSAFE`` escape hatch at the test-suite level.  The one
module that must exercise the *real* defaults (``test_loop_config_safety``)
overrides this with its own autouse fixture that deletes the env var
before each test runs.
"""
from __future__ import annotations

import pytest

from chimera.core.loop_config import UNSAFE_ENV_VAR


@pytest.fixture(autouse=True)
def _default_unsafe_mode_for_tests(monkeypatch, request):
    """Enable ``CHIMERA_UNSAFE`` for every test unless the file opts out.

    Opt-out is by filename: any test living in a module whose name
    contains ``loop_config_safety`` runs with the env var *unset*, so
    the real safety defaults are observable.  Individual safety tests
    can still flip the variable via their own ``monkeypatch`` calls.
    """
    mod_name = getattr(request.module, "__name__", "")
    if "loop_config_safety" in mod_name:
        # Safety-posture tests: don't interfere.  The module's own
        # autouse fixture clears the env var explicitly.
        return
    monkeypatch.setenv(UNSAFE_ENV_VAR, "1")


@pytest.fixture(autouse=True)
def _no_dotenv_autoload(monkeypatch):
    """Stop ``chimera code``'s startup .env auto-load from leaking real creds
    (e.g. ``~/.config/chimera/env``) into ``os.environ`` across tests.

    ``run_code`` imports ``load_dotenv`` lazily, so patching the module
    attribute neutralises it there; ``test_dotenv`` imports the function at
    module load time and keeps its own real reference, so it is unaffected.
    """
    monkeypatch.setattr(
        "chimera.config.dotenv.load_dotenv", lambda *a, **k: [], raising=False,
    )
