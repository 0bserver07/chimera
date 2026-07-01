"""Chimera TUI — a Textual frontend over :class:`~chimera.assembly.driver.AgentDriver`.

Phase 1 ships a single-agent coding TUI: streaming transcript with tool-call
rendering, a status line (model / cost / context), an input box, and slash
commands. The multiplexer (N agents/models racing in panes) builds on this.

Requires the ``tui`` extra (``pip install 'chimera-run[tui]'`` / ``textual``).
"""
from __future__ import annotations

__all__ = ["ChimeraTUI", "run_tui"]


def __getattr__(name: str) -> object:
    # Lazy import so importing chimera.tui without textual installed doesn't
    # explode until something is actually used.
    if name in __all__:
        from chimera.tui.app import ChimeraTUI, run_tui

        return {"ChimeraTUI": ChimeraTUI, "run_tui": run_tui}[name]
    raise AttributeError(name)
