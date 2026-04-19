"""Tests for :mod:`chimera.function_synthesis.credentials`."""
from __future__ import annotations

import json
import logging
import os
import stat

import pytest

from chimera.function_synthesis.credentials import CredentialStore


# ---------------------------------------------------------------------------
# Basic set / get / delete / list
# ---------------------------------------------------------------------------


def test_set_then_get_round_trip(tmp_path):
    store = CredentialStore(path=tmp_path / "credentials.json")
    store.set("huggingface", "hf_secret_token")
    assert store.get("huggingface") == "hf_secret_token"


def test_get_returns_none_for_unknown_service(tmp_path):
    store = CredentialStore(path=tmp_path / "credentials.json")
    assert store.get("nope") is None


def test_delete_removes_entry(tmp_path):
    store = CredentialStore(path=tmp_path / "credentials.json")
    store.set("s3", "AKIAEXAMPLE")
    store.delete("s3")
    assert store.get("s3") is None


def test_delete_unknown_is_noop(tmp_path):
    store = CredentialStore(path=tmp_path / "credentials.json")
    # Should not raise.
    store.delete("never-set")
    assert store.list_services() == []


def test_list_services_returns_sorted_names(tmp_path):
    store = CredentialStore(path=tmp_path / "credentials.json")
    store.set("zeta", "tz")
    store.set("alpha", "ta")
    store.set("middle", "tm")
    assert store.list_services() == ["alpha", "middle", "zeta"]


def test_overwrite_existing_service(tmp_path):
    store = CredentialStore(path=tmp_path / "credentials.json")
    store.set("hub", "first")
    store.set("hub", "second")
    assert store.get("hub") == "second"


def test_empty_service_or_token_rejected(tmp_path):
    store = CredentialStore(path=tmp_path / "credentials.json")
    with pytest.raises(ValueError):
        store.set("", "tok")
    with pytest.raises(ValueError):
        store.set("svc", "")


# ---------------------------------------------------------------------------
# Security: file mode 0o600
# ---------------------------------------------------------------------------


def test_credentials_file_mode_is_0600(tmp_path):
    path = tmp_path / "credentials.json"
    store = CredentialStore(path=path)
    store.set("huggingface", "tok")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_file_mode_is_0600_after_second_write(tmp_path):
    path = tmp_path / "credentials.json"
    store = CredentialStore(path=path)
    store.set("a", "x")
    # Deliberately relax perms; set() must restore them.
    os.chmod(path, 0o644)
    store.set("b", "y")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_file_mode_is_0600_after_delete(tmp_path):
    path = tmp_path / "credentials.json"
    store = CredentialStore(path=path)
    store.set("a", "x")
    store.set("b", "y")
    store.delete("a")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# Persistence: a fresh store instance sees writes from a previous one.
# ---------------------------------------------------------------------------


def test_credentials_persist_across_instances(tmp_path):
    path = tmp_path / "credentials.json"
    CredentialStore(path=path).set("service", "tok123")
    assert CredentialStore(path=path).get("service") == "tok123"


def test_corrupt_file_is_treated_as_empty(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("not valid json {{{")
    store = CredentialStore(path=path)
    assert store.list_services() == []
    # Writing should still succeed and overwrite the corrupt content.
    store.set("svc", "tok")
    assert store.get("svc") == "tok"


def test_non_dict_json_is_treated_as_empty(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps(["not", "a", "dict"]))
    store = CredentialStore(path=path)
    assert store.list_services() == []


# ---------------------------------------------------------------------------
# Security: tokens are never emitted on failure paths.
# ---------------------------------------------------------------------------


def test_set_error_does_not_leak_token_message(tmp_path):
    store = CredentialStore(path=tmp_path / "credentials.json")
    SECRET = "super-secret-token-DO-NOT-LEAK-9f8a"
    try:
        store.set("", SECRET)
    except ValueError as exc:
        assert SECRET not in str(exc)
    else:
        pytest.fail("expected ValueError")


def test_set_does_not_log_token(tmp_path, caplog):
    store = CredentialStore(path=tmp_path / "credentials.json")
    SECRET = "LEAK-CANARY-TOKEN-abc123"
    with caplog.at_level(logging.DEBUG):
        store.set("svc", SECRET)
        store.get("svc")
        store.delete("svc")
    for record in caplog.records:
        assert SECRET not in record.getMessage()


def test_default_path_honours_chimera_fs_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    store = CredentialStore()
    assert store.path == tmp_path / "credentials.json"
    store.set("svc", "tok")
    assert (tmp_path / "credentials.json").exists()
