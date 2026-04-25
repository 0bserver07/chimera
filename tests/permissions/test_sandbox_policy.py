"""Tests for chimera.security.sandbox."""
import os
import tempfile

from chimera.security.sandbox import (
    AccessLevel,
    NetworkRule,
    PathRule,
    SandboxPolicy,
)


def test_check_path_allowed():
    policy = SandboxPolicy(
        path_rules=[
            PathRule(path="/workspace", access=AccessLevel.WRITE),
        ]
    )
    assert policy.check_path("/workspace/file.py", AccessLevel.WRITE)
    assert policy.check_path("/workspace/file.py", AccessLevel.READ)


def test_check_path_denied():
    policy = SandboxPolicy(
        path_rules=[
            PathRule(path="/workspace", access=AccessLevel.READ),
        ]
    )
    assert not policy.check_path("/workspace/file.py", AccessLevel.WRITE)


def test_check_path_deny_rule():
    policy = SandboxPolicy(
        path_rules=[
            PathRule(path="/secrets", access=AccessLevel.DENY),
            PathRule(path="/", access=AccessLevel.READ),
        ]
    )
    assert not policy.check_path("/secrets/key.pem", AccessLevel.READ)
    assert policy.check_path("/other/file.txt", AccessLevel.READ)


def test_check_network_allowed():
    policy = SandboxPolicy(
        network_rules=[
            NetworkRule(host="api.example.com", allow=True),
        ]
    )
    assert policy.check_network("api.example.com")


def test_check_network_denied():
    policy = SandboxPolicy(
        network_rules=[
            NetworkRule(host="*", allow=False),
        ]
    )
    assert not policy.check_network("evil.com")


def test_check_command_allowed():
    policy = SandboxPolicy(denied_commands=["rm", "sudo"])
    assert policy.check_command("python script.py")
    assert not policy.check_command("rm -rf /")
    assert not policy.check_command("sudo apt install")


def test_check_command_allowlist():
    policy = SandboxPolicy(allowed_commands=["python", "pytest", "git"])
    assert policy.check_command("python test.py")
    assert not policy.check_command("curl http://evil.com")


def test_preset_permissive():
    policy = SandboxPolicy.permissive()
    assert policy.check_path("/anything", AccessLevel.EXECUTE)
    assert policy.check_network("anywhere.com")
    assert policy.check_command("anything")


def test_preset_workspace_only():
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy.workspace_only(d)
        assert policy.check_path(os.path.join(d, "file.py"), AccessLevel.WRITE)


def test_preset_strict():
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy.strict(d)
        assert not policy.check_network("evil.com")
        assert not policy.check_command("sudo apt")
        assert policy.check_command("python test.py")


def test_to_dict():
    policy = SandboxPolicy.strict("/workspace")
    d = policy.to_dict()
    assert d["name"] == "strict"
    assert len(d["path_rules"]) > 0
