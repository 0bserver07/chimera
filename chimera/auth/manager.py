from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class StoredCredential:
    provider: str
    key: str
    source: str  # "env", "keyring", "config"

class AuthManager:
    """Centralized API key management for multiple providers."""

    ENV_VARS = {
        "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
        "openai": ["OPENAI_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "ollama": [],  # No key needed
    }

    def __init__(self, config_dir: Path | None = None):
        self._config_dir = config_dir or Path.home() / ".chimera"
        self._credentials: dict[str, StoredCredential] = {}
        self._load_from_env()

    def _load_from_env(self) -> None:
        """Load credentials from environment variables."""
        for provider, vars in self.ENV_VARS.items():
            for var in vars:
                val = os.environ.get(var)
                if val:
                    self._credentials[provider] = StoredCredential(provider=provider, key=val, source="env")
                    break

    def get_token(self, provider: str) -> str | None:
        """Get API token for a provider."""
        cred = self._credentials.get(provider)
        if cred:
            return cred.key
        # Try config file
        config_path = self._config_dir / "auth.json"
        if config_path.exists():
            data = json.loads(config_path.read_text())
            key = data.get(provider, {}).get("api_key")
            if key:
                self._credentials[provider] = StoredCredential(provider=provider, key=key, source="config")
                return key
        return None

    def set_token(self, provider: str, key: str) -> None:
        """Store API token for a provider."""
        self._credentials[provider] = StoredCredential(provider=provider, key=key, source="config")
        # Persist to config
        config_path = self._config_dir / "auth.json"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        if config_path.exists():
            data = json.loads(config_path.read_text())
        data[provider] = {"api_key": key}
        config_path.write_text(json.dumps(data, indent=2))

    def remove_token(self, provider: str) -> bool:
        """Remove stored token."""
        if provider in self._credentials:
            del self._credentials[provider]
        config_path = self._config_dir / "auth.json"
        if config_path.exists():
            data = json.loads(config_path.read_text())
            if provider in data:
                del data[provider]
                config_path.write_text(json.dumps(data, indent=2))
                return True
        return False

    def list_providers(self) -> list[dict[str, str]]:
        """List configured providers with their source."""
        result = []
        for provider, cred in self._credentials.items():
            result.append({"provider": provider, "source": cred.source, "key_preview": cred.key[:8] + "..."})
        return result

    def status(self) -> str:
        """Human-readable auth status."""
        providers = self.list_providers()
        if not providers:
            return "No API keys configured. Set ANTHROPIC_API_KEY or use 'chimera auth login'."
        lines = ["Configured providers:"]
        for p in providers:
            lines.append(f"  {p['provider']}: {p['key_preview']} (from {p['source']})")
        return "\n".join(lines)
