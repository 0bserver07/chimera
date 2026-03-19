"""Tests for OS-native sandboxing."""
from __future__ import annotations

import platform

import pytest

from chimera.env.native_sandbox import (
    NativeSandbox,
    SandboxCapabilities,
    detect_capabilities,
    generate_seatbelt_profile,
)
from chimera.security.sandbox import (
    AccessLevel,
    NetworkRule,
    PathRule,
    SandboxPolicy,
)


class TestSeatbeltProfileGeneration:

    def test_generates_valid_sb_profile(self):
        policy = SandboxPolicy(
            path_rules=[
                PathRule(path="/workspace", access=AccessLevel.WRITE, recursive=True),
                PathRule(path="/etc", access=AccessLevel.READ, recursive=True),
            ],
            network_rules=[NetworkRule(host="*", allow=False)],
        )
        profile = generate_seatbelt_profile(policy)
        assert "(version 1)" in profile
        assert "(deny default)" in profile
        assert '(subpath "/workspace")' in profile
        assert "file-write*" in profile
        assert '(subpath "/etc")' in profile
        assert "Network denied" in profile

    def test_network_allowed(self):
        policy = SandboxPolicy(
            network_rules=[NetworkRule(host="*", allow=True)],
        )
        profile = generate_seatbelt_profile(policy)
        assert "(allow network-outbound)" in profile

    def test_network_denied(self):
        policy = SandboxPolicy(
            network_rules=[NetworkRule(host="*", allow=False)],
        )
        profile = generate_seatbelt_profile(policy)
        assert "Network denied" in profile

    def test_read_only_path(self):
        policy = SandboxPolicy(
            path_rules=[PathRule(path="/data", access=AccessLevel.READ)],
        )
        profile = generate_seatbelt_profile(policy)
        assert "file-read*" in profile
        assert "file-write*" not in profile.split("System access")[0]

    def test_literal_path(self):
        policy = SandboxPolicy(
            path_rules=[PathRule(path="/single/file.txt", access=AccessLevel.READ, recursive=False)],
        )
        profile = generate_seatbelt_profile(policy)
        assert '(literal "/single/file.txt")' in profile

    def test_system_paths_always_readable(self):
        policy = SandboxPolicy()
        profile = generate_seatbelt_profile(policy)
        assert '"/usr"' in profile
        assert '"/System"' in profile

    def test_strict_policy(self):
        policy = SandboxPolicy.strict("/workspace")
        profile = generate_seatbelt_profile(policy)
        assert "(deny default)" in profile
        assert '"/workspace"' in profile


class TestDetectCapabilities:

    def test_detects_platform(self):
        caps = detect_capabilities()
        assert caps.platform == platform.system()
        if platform.system() == "Darwin":
            assert isinstance(caps.seatbelt, bool)

    def test_capabilities_dataclass(self):
        caps = SandboxCapabilities(seatbelt=True, landlock=False, platform="Darwin")
        assert caps.has_native_sandbox is True
        caps2 = SandboxCapabilities(seatbelt=False, landlock=False, platform="Windows")
        assert caps2.has_native_sandbox is False


class TestNativeSandbox:

    def test_run_echo_unsandboxed(self):
        """Fallback unsandboxed execution works."""
        policy = SandboxPolicy()
        sandbox = NativeSandbox(policy)
        result = sandbox._run_unsandboxed("echo hello", cwd=None, timeout=5)
        assert "hello" in result.stdout
        assert result.exit_code == 0

    def test_timeout(self):
        policy = SandboxPolicy()
        sandbox = NativeSandbox(policy)
        result = sandbox.run("sleep 10", timeout=1)
        assert result.exit_code != 0

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_seatbelt_profile_is_valid(self):
        """On macOS, verify we can generate and write a valid .sb profile."""
        policy = SandboxPolicy(
            path_rules=[
                PathRule(path="/tmp", access=AccessLevel.WRITE, recursive=True),
            ],
        )
        profile = generate_seatbelt_profile(policy)
        # Should be valid SBPL syntax
        assert "(version 1)" in profile
        assert "(deny default)" in profile
        assert '"/tmp"' in profile

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_seatbelt_blocks_network(self):
        """On macOS, verify network is blocked when denied."""
        policy = SandboxPolicy(
            path_rules=[PathRule(path="/tmp", access=AccessLevel.WRITE, recursive=True)],
            network_rules=[NetworkRule(host="*", allow=False)],
        )
        sandbox = NativeSandbox(policy)
        if sandbox.capabilities.seatbelt:
            # curl should fail because network is denied
            result = sandbox._run_seatbelt(
                "curl -s --connect-timeout 2 https://example.com",
                cwd="/tmp", timeout=5,
            )
            # Either exit code != 0 or stderr has error
            assert result.exit_code != 0 or "denied" in result.stderr.lower() or result.stdout == ""
