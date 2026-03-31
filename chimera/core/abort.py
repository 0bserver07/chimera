"""AbortSignal: one-shot abort primitive with child propagation."""
from __future__ import annotations

from typing import Callable


class AbortSignal:
    """Signals that an operation should be aborted.

    Properties:
        aborted: True once abort() has been called.
        reason:  The reason string passed to abort(), or None.

    Methods:
        abort(reason)     — Mark as aborted; fires listeners once only.
        on_abort(cb)      — Register a callback; called immediately if already aborted.
        linked_child()    — Return a child signal that aborts when this one does.
    """

    def __init__(self) -> None:
        self._aborted: bool = False
        self._reason: str | None = None
        self._listeners: list[Callable[[str], None]] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> str | None:
        return self._reason

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def abort(self, reason: str = "aborted") -> None:
        """Mark this signal as aborted. Idempotent: subsequent calls are no-ops."""
        if self._aborted:
            return
        self._aborted = True
        self._reason = reason
        for listener in self._listeners:
            listener(reason)

    def on_abort(self, callback: Callable[[str], None]) -> None:
        """Register *callback* to be called when this signal is aborted.

        If the signal is already aborted, the callback is invoked immediately.
        """
        if self._aborted:
            # reason is guaranteed non-None here, but satisfy the type checker
            callback(self._reason or "aborted")
        else:
            self._listeners.append(callback)

    def linked_child(self) -> AbortSignal:
        """Create a child signal that aborts when this parent aborts.

        Aborting the child has no effect on the parent.
        """
        child = AbortSignal()
        self.on_abort(child.abort)
        return child
