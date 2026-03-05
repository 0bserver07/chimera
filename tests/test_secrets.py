"""Tests for chimera.secrets — secret detection and redaction."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from chimera.events.base import Event, EventBus
from chimera.events.types import ToolResultEvent
from chimera.secrets.detector import SecretDetector
from chimera.secrets.redactor import RedactionMiddleware
from chimera.secrets.registry import SecretRegistry


# ---------------------------------------------------------------------------
# SecretRegistry
# ---------------------------------------------------------------------------

class TestSecretRegistry:
    def test_register_and_redact(self):
        reg = SecretRegistry()
        reg.register("api_key", "sk-secret-12345")
        assert reg.redact("My key is sk-secret-12345") == "My key is [REDACTED]"

    def test_empty_string_not_registered(self):
        reg = SecretRegistry()
        reg.register("empty", "")
        assert reg.secret_names == []

    def test_multiple_secrets(self):
        reg = SecretRegistry()
        reg.register("key1", "secret1")
        reg.register("key2", "secret2")
        result = reg.redact("secret1 and secret2")
        assert result == "[REDACTED] and [REDACTED]"

    def test_longer_secrets_replaced_first(self):
        reg = SecretRegistry()
        reg.register("short", "abc")
        reg.register("long", "abcdef")
        result = reg.redact("abcdef")
        # "abcdef" should be fully replaced, not partially
        assert result == "[REDACTED]"

    def test_contains_secret(self):
        reg = SecretRegistry()
        reg.register("key", "mysecret")
        assert reg.contains_secret("here is mysecret in text") is True
        assert reg.contains_secret("nothing here") is False

    def test_register_from_dict(self):
        reg = SecretRegistry()
        reg.register_from_dict({"k1": "v1", "k2": "v2"})
        assert set(reg.secret_names) == {"k1", "k2"}
        assert reg.redact("v1 v2") == "[REDACTED] [REDACTED]"

    def test_register_from_env(self):
        with patch.dict(os.environ, {"TEST_SECRET": "env_value"}):
            reg = SecretRegistry()
            reg.register_from_env("TEST_SECRET", "NONEXISTENT_VAR")
            assert reg.redact("env_value") == "[REDACTED]"
            assert "NONEXISTENT_VAR" not in reg.secret_names

    def test_secret_names(self):
        reg = SecretRegistry()
        reg.register("a", "val_a")
        reg.register("b", "val_b")
        assert sorted(reg.secret_names) == ["a", "b"]

    def test_no_secrets_passthrough(self):
        reg = SecretRegistry()
        text = "just normal text"
        assert reg.redact(text) == text

    def test_redact_placeholder(self):
        assert SecretRegistry.REDACTED_PLACEHOLDER == "[REDACTED]"


# ---------------------------------------------------------------------------
# SecretDetector
# ---------------------------------------------------------------------------

class TestSecretDetector:
    def setup_method(self):
        self.detector = SecretDetector()

    def test_detect_openai_key(self):
        text = "key is sk-abcdefghij1234567890abcdefghij"
        findings = self.detector.detect(text)
        assert len(findings) >= 1
        assert any("sk-" in f["match"] for f in findings)

    def test_detect_github_pat(self):
        text = "token=ghp_abcdefghij1234567890abcdefghijklmn"
        findings = self.detector.detect(text)
        assert any("ghp_" in f["match"] for f in findings)

    def test_detect_aws_access_key(self):
        text = "AWS key: AKIAIOSFODNN7EXAMPLE"
        findings = self.detector.detect(text)
        assert any("AKIA" in f["match"] for f in findings)

    def test_detect_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
        findings = self.detector.detect(text)
        assert any("Bearer" in f["match"] for f in findings)

    def test_detect_private_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB..."
        assert self.detector.has_secrets(text) is True

    def test_detect_password_assignment(self):
        text = "password=mysecretpassword123"
        assert self.detector.has_secrets(text) is True

    def test_detect_token_assignment(self):
        text = "token=abc123def456"
        assert self.detector.has_secrets(text) is True

    def test_no_false_positive_on_normal_code(self):
        text = "def calculate_sum(a, b):\n    return a + b"
        assert self.detector.has_secrets(text) is False

    def test_no_false_positive_on_normal_text(self):
        text = "The weather is nice today. Let's go for a walk."
        assert self.detector.has_secrets(text) is False

    def test_redact_detected(self):
        text = "key is sk-abcdefghij1234567890abcdefghij"
        redacted = self.detector.redact_detected(text)
        assert "[REDACTED]" in redacted
        assert "sk-" not in redacted

    def test_extra_patterns(self):
        detector = SecretDetector(extra_patterns=[r"custom_token_[a-z0-9]{10}"])
        text = "my token: custom_token_abc1234567"
        assert detector.has_secrets(text) is True

    def test_finding_structure(self):
        text = "sk-abcdefghij1234567890abcdefghij"
        findings = self.detector.detect(text)
        assert len(findings) >= 1
        f = findings[0]
        assert "pattern" in f
        assert "match" in f
        assert "start" in f
        assert "end" in f
        assert isinstance(f["start"], int)
        assert isinstance(f["end"], int)


# ---------------------------------------------------------------------------
# RedactionMiddleware
# ---------------------------------------------------------------------------

class TestRedactionMiddleware:
    def test_redacts_output_field(self):
        reg = SecretRegistry()
        reg.register("key", "supersecret")
        mw = RedactionMiddleware(registry=reg)

        event = ToolResultEvent(output="result contains supersecret data")
        received = []
        mw.process(event, lambda e: received.append(e))

        assert len(received) == 1
        assert "supersecret" not in received[0].output
        assert "[REDACTED]" in received[0].output

    def test_redacts_content_field(self):
        reg = SecretRegistry()
        reg.register("key", "topsecret")
        mw = RedactionMiddleware(registry=reg)

        from chimera.events.types import StepEvent
        event = StepEvent(content="contains topsecret")
        received = []
        mw.process(event, lambda e: received.append(e))

        assert "[REDACTED]" in received[0].content

    def test_redacts_metadata(self):
        reg = SecretRegistry()
        reg.register("key", "hidethis")
        mw = RedactionMiddleware(registry=reg)

        event = ToolResultEvent(output="ok", tool_metadata={})
        event.metadata = {"info": "hidethis is here"}
        received = []
        mw.process(event, lambda e: received.append(e))

        assert "[REDACTED]" in received[0].metadata["info"]

    def test_detect_unknown_secrets(self):
        reg = SecretRegistry()
        detector = SecretDetector()
        mw = RedactionMiddleware(
            registry=reg, detector=detector, detect_unknown=True,
        )

        event = ToolResultEvent(
            output="found key sk-abcdefghij1234567890abcdefghij"
        )
        received = []
        mw.process(event, lambda e: received.append(e))

        assert "sk-" not in received[0].output
        assert "[REDACTED]" in received[0].output

    def test_no_detection_when_disabled(self):
        reg = SecretRegistry()
        detector = SecretDetector()
        mw = RedactionMiddleware(
            registry=reg, detector=detector, detect_unknown=False,
        )

        event = ToolResultEvent(
            output="found key sk-abcdefghij1234567890abcdefghij"
        )
        received = []
        mw.process(event, lambda e: received.append(e))

        # Without detect_unknown, pattern-detected secrets are NOT redacted
        assert "sk-" in received[0].output

    def test_none_values_preserved(self):
        reg = SecretRegistry()
        reg.register("key", "secret")
        mw = RedactionMiddleware(registry=reg)

        # Event without output/content/text shouldn't cause errors
        event = Event(type="test")
        received = []
        mw.process(event, lambda e: received.append(e))
        assert len(received) == 1

    def test_event_bus_integration(self):
        reg = SecretRegistry()
        reg.register("key", "mysecretvalue")

        bus = EventBus()
        bus.use(RedactionMiddleware(registry=reg))

        received = []
        bus.subscribe("tool_result", lambda e: received.append(e))

        event = ToolResultEvent(output="key is mysecretvalue here")
        bus.publish(event)

        assert len(received) == 1
        assert "mysecretvalue" not in received[0].output
        assert "[REDACTED]" in received[0].output

    def test_opt_in_no_registry(self):
        """Agent works normally when no secret_registry is provided."""
        bus = EventBus()
        # No middleware added — secrets pass through
        received = []
        bus.subscribe("tool_result", lambda e: received.append(e))

        event = ToolResultEvent(output="key is sk-abc123abc123abc123abc123")
        bus.publish(event)

        assert "sk-abc123" in received[0].output
