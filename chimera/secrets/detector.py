"""Pattern-based secret detection."""
from __future__ import annotations

import re

__all__ = ["SecretDetector"]


class SecretDetector:
    """Detects potential secrets in text using regex patterns.

    Args:
        extra_patterns: Additional regex patterns to match.
    """

    PATTERNS = [
        # API keys
        r"sk-[a-zA-Z0-9]{20,}",
        r"AIza[0-9A-Za-z\-_]{35}",
        r"ghp_[a-zA-Z0-9]{36}",
        r"glpat-[a-zA-Z0-9\-_]{20,}",
        # AWS
        r"AKIA[0-9A-Z]{16}",
        r"(?:aws_secret_access_key|AWS_SECRET)\s*[=:]\s*\S+",
        # Generic patterns
        r"(?:password|passwd|pwd)\s*[=:]\s*\S+",
        r"(?:secret|token|api_key|apikey)\s*[=:]\s*\S+",
        r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*",
        # Private keys
        r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----",
    ]

    def __init__(self, extra_patterns: list[str] | None = None) -> None:
        patterns = self.PATTERNS + (extra_patterns or [])
        self._compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

    def detect(self, text: str) -> list[dict]:
        """Find potential secrets in text.

        Args:
            text: Text to scan for secrets.

        Returns:
            List of dicts with keys: pattern, match, start, end.
        """
        findings: list[dict] = []
        for pattern in self._compiled:
            for match in pattern.finditer(text):
                findings.append({
                    "pattern": pattern.pattern,
                    "match": match.group(),
                    "start": match.start(),
                    "end": match.end(),
                })
        return findings

    def has_secrets(self, text: str) -> bool:
        """Check if text contains any pattern-matched secrets.

        Args:
            text: Text to scan.

        Returns:
            True if any secret pattern matches.
        """
        return any(p.search(text) for p in self._compiled)

    def redact_detected(self, text: str) -> str:
        """Redact all detected secrets (even if not in a registry).

        Args:
            text: Text to redact.

        Returns:
            Text with all detected secrets replaced with [REDACTED].
        """
        result = text
        findings = sorted(self.detect(text), key=lambda f: f["start"], reverse=True)
        for finding in findings:
            result = result[: finding["start"]] + "[REDACTED]" + result[finding["end"] :]
        return result
