"""Fallback model switching on rate limits or overload.

Provides :class:`ModelFallbackManager` which transparently switches to a
fallback model when the primary model returns HTTP 429 (rate limited) or
529 (overloaded), then reverts on :meth:`reset`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelFallbackConfig:
    """Configuration for model fallback behaviour.

    Attributes:
        primary_model: Model identifier used by default.
        fallback_model: Model identifier to switch to on error, or ``None``
            to disable fallback entirely.
        fallback_on_429: Whether to fall back on HTTP 429 (rate-limited).
        fallback_on_529: Whether to fall back on HTTP 529 (overloaded).
        max_fallback_attempts: Maximum number of times fallback may be
            activated before giving up.
    """

    primary_model: str
    fallback_model: str | None = None
    fallback_on_429: bool = True
    fallback_on_529: bool = True
    max_fallback_attempts: int = 3


class ModelFallbackManager:
    """Switch to fallback model on rate limits or overload.

    Args:
        config: A :class:`ModelFallbackConfig` defining which models to use
            and under which conditions fallback is allowed.
    """

    def __init__(self, config: ModelFallbackConfig) -> None:
        self.config = config
        self._fallback_count = 0
        self._using_fallback = False

    def should_fallback(self, error_code: int) -> bool:
        """Return ``True`` if fallback should be attempted for *error_code*.

        Returns ``False`` if no fallback model is configured, the relevant
        error code is disabled, or max attempts have been reached.
        """
        if self._fallback_count >= self.config.max_fallback_attempts:
            return False
        if self.config.fallback_model is None:
            return False
        if error_code == 429 and self.config.fallback_on_429:
            return True
        if error_code == 529 and self.config.fallback_on_529:
            return True
        return False

    def activate_fallback(self) -> str:
        """Activate fallback and return the fallback model name.

        Each call increments the internal attempt counter.
        """
        self._fallback_count += 1
        self._using_fallback = True
        return self.config.fallback_model  # type: ignore[return-value]

    @property
    def current_model(self) -> str:
        """The model identifier currently in use."""
        if self._using_fallback and self.config.fallback_model:
            return self.config.fallback_model
        return self.config.primary_model

    def reset(self) -> None:
        """Revert to the primary model."""
        self._using_fallback = False
