"""Function-synthesis-specific exceptions."""
from __future__ import annotations


class CacheMissError(LookupError):
    """Raised when a required cache entry is missing and refresh is disallowed."""

    def __init__(self, *, kind: str, key: str) -> None:
        super().__init__(f"cache miss: {kind}={key!r}")
        self.kind = kind
        self.key = key


class OfflineError(RuntimeError):
    """Raised when an online operation is attempted while offline mode is active."""

    def __init__(self, *, operation: str) -> None:
        super().__init__(f"offline mode active; refusing to {operation}")
        self.operation = operation
