"""Chimera TUI — Textual frontends over :class:`~chimera.assembly.driver.AgentDriver`.

One codebase serves every lane count (issue #172): the multiplexer races N
agents side by side, and the single-agent daily driver is the same app with
one ``inplace`` lane and single-lane chrome (:func:`run_single_agent`, behind
bare ``chimera code --tui``). The legacy single-agent :class:`ChimeraTUI` is
deprecated and kept importable for one release.

Requires the ``tui`` extra (``pip install 'chimera-run[tui]'`` / ``textual``).
"""
from __future__ import annotations

__all__ = [
    "ChimeraTUI",
    "run_tui",
    "MultiplexApp",
    "run_multiplexer",
    "run_single_agent",
]


def __getattr__(name: str) -> object:
    # Lazy import so importing chimera.tui without textual installed doesn't
    # explode until something is actually used.
    if name in ("ChimeraTUI", "run_tui"):
        from chimera.tui.app import ChimeraTUI, run_tui

        return {"ChimeraTUI": ChimeraTUI, "run_tui": run_tui}[name]
    if name in ("MultiplexApp", "run_multiplexer", "run_single_agent"):
        from chimera.tui.multiplex import (
            MultiplexApp,
            run_multiplexer,
            run_single_agent,
        )

        return {
            "MultiplexApp": MultiplexApp,
            "run_multiplexer": run_multiplexer,
            "run_single_agent": run_single_agent,
        }[name]
    raise AttributeError(name)
