from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

__all__ = ["AuthProvider", "Credential"]


@dataclass
class Credential:
    """Represents an authentication credential for an LLM provider."""

    provider: str  # "anthropic", "openai", etc.
    token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """Return True if the credential has a known expiry that has passed."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


class AuthProvider(ABC):
    """Base class for authentication providers."""

    @abstractmethod
    def authenticate(self) -> Credential:
        """Obtain a new credential."""

    @abstractmethod
    def refresh(self, credential: Credential) -> Credential:
        """Refresh an existing credential."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identifier of the provider this authenticator targets."""
