"""Regression: the anthropic client carries an explicit timeout.

Without it, the SDK's ``_calculate_nonstreaming_timeout`` guard raises
``ValueError: Streaming is required ...`` for any non-streaming ``complete()``
whose ``max_tokens`` exceeds ~21k — which is exactly the GLM/Kimi default (32k).
An explicit client timeout makes the SDK honor it instead of raising, so the
eval Harness / bench-matrix path works for large-output models.
"""

from __future__ import annotations

import pytest

pytest.importorskip("anthropic")

from chimera.providers.anthropic import AnthropicProvider


def test_client_has_explicit_nondefault_timeout() -> None:
    provider = AnthropicProvider(model="glm-5.2", api_key="sk-test-not-real")
    timeout = provider._client.timeout
    # An explicit, non-default timeout is what makes the SDK skip its
    # non-streaming ">10 min" guard (messages.py: `self._client.timeout ==
    # DEFAULT_TIMEOUT`). A short connect keeps genuine connection failures fast.
    assert getattr(timeout, "read", None) == 900.0
    assert getattr(timeout, "connect", None) == 10.0
