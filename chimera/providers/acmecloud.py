"""ACME Cloud (fictional) provider — the 20-line bar for the capability matrix.

Demonstrates adding a brand-new OpenAI-compatible backend as pure **data**: a
base URL, a capability row keyed by the shared ``openai-compat`` wire
protocol, and a registry lambda. No new :class:`~chimera.providers.base.Provider`
subclass — divergence lives in the matrix, not in code. ACME Cloud is not a
real service; it exists so a test can prove the ~20-line pattern end to end.
"""
from __future__ import annotations

from chimera.providers.capabilities import WireProtocol, register_capabilities
from chimera.providers.compatible import OpenAICompatibleProvider
from chimera.providers.registry import register_provider

ACMECLOUD_BASE_URL = "https://api.acmecloud.example/v1"

# Divergence-as-data: ACME Cloud wants strict function tools and stamps a house
# reasoning knob into every request. Both are matrix values, not code.
register_capabilities(
    WireProtocol.OPENAI_COMPAT,
    provider="acmecloud",
    supports_strict_tools=True,
    extra_payload={"acmecloud_reasoning": "auto"},
)

register_provider(
    "acmecloud",
    lambda model="", api_key=None, base_url=None, **kw: OpenAICompatibleProvider(
        model=model, base_url=base_url or ACMECLOUD_BASE_URL, api_key=api_key,
        provider="acmecloud", **kw,
    ),
)
