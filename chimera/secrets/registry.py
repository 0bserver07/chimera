"""Secret registry for tracking and redacting known secrets."""
from __future__ import annotations

import os

__all__ = ["SecretRegistry"]


class SecretRegistry:
    """Tracks known secret values and provides redaction.

    Secrets are stored by name and redacted from text by replacing
    the literal value with ``[REDACTED]``. Longer secrets are replaced
    first to avoid partial-match issues.
    """

    REDACTED_PLACEHOLDER = "[REDACTED]"

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def register(self, name: str, value: str) -> None:
        """Register a secret value to be redacted.

        Args:
            name: Identifier for the secret.
            value: The secret value. Empty strings are ignored.
        """
        if value:
            self._secrets[name] = value

    def register_from_env(self, *env_vars: str) -> None:
        """Register secrets from environment variables.

        Args:
            env_vars: Names of environment variables to register.
        """
        for var in env_vars:
            val = os.environ.get(var)
            if val:
                self._secrets[var] = val

    def register_from_dict(self, secrets: dict[str, str]) -> None:
        """Register multiple secrets at once.

        Args:
            secrets: Mapping of name to secret value.
        """
        for name, value in secrets.items():
            self.register(name, value)

    def redact(self, text: str) -> str:
        """Replace all known secret values in text with [REDACTED].

        Longer secrets are replaced first to avoid partial matches.

        Args:
            text: Input text that may contain secrets.

        Returns:
            Text with all known secrets replaced.
        """
        result = text
        for _name, value in sorted(
            self._secrets.items(), key=lambda x: len(x[1]), reverse=True,
        ):
            if value in result:
                result = result.replace(value, self.REDACTED_PLACEHOLDER)
        return result

    def contains_secret(self, text: str) -> bool:
        """Check if text contains any known secret.

        Args:
            text: Text to check.

        Returns:
            True if any registered secret value appears in the text.
        """
        return any(value in text for value in self._secrets.values() if value)

    @property
    def secret_names(self) -> list[str]:
        """Return list of registered secret names."""
        return list(self._secrets.keys())
