# tests/function_synthesis/test_hub.py
"""Unit tests for the hub adapters.

All network access is mocked — these tests never touch the real internet.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Helper: write a tiny bundle-like file so upload paths have something to read.
def _make_fake_bundle(tmp_path: Path, name: str = "classify-abc123") -> Path:
    path = tmp_path / f"{name}.chi"
    # Preserve byte-exact content: we'll later assert the exact same bytes
    # come back out of pull().
    path.write_bytes(b"PK-fake-chi-bytes-\x00\x01\x02\x03")
    return path


# ---------------------------------------------------------------------------
# URI parsing
# ---------------------------------------------------------------------------


def test_parse_hf_uri_valid() -> None:
    from chimera.function_synthesis.hub import parse_hf_uri

    assert parse_hf_uri("hf://acme/functions/my-slug.chi") == (
        "acme/functions",
        "my-slug.chi",
    )


def test_parse_hf_uri_rejects_non_hf() -> None:
    from chimera.function_synthesis.hub import parse_hf_uri

    with pytest.raises(ValueError):
        parse_hf_uri("s3://bucket/key")


def test_parse_s3_uri_valid() -> None:
    from chimera.function_synthesis.hub import parse_s3_uri

    assert parse_s3_uri("s3://my-bucket/prefix/a.chi") == (
        "my-bucket",
        "prefix/a.chi",
    )


def test_parse_s3_uri_rejects_non_s3() -> None:
    from chimera.function_synthesis.hub import parse_s3_uri

    with pytest.raises(ValueError):
        parse_s3_uri("hf://acme/repo/x.chi")


def test_parse_hub_spec_hf() -> None:
    # Skip if boto3 is missing, but the HF path only needs huggingface_hub.
    pytest.importorskip("huggingface_hub")
    from chimera.function_synthesis.hub import HFHubAdapter, parse_hub_spec

    with patch("huggingface_hub.HfApi", return_value=MagicMock()):
        adapter = parse_hub_spec("hf:acme/functions")
    assert isinstance(adapter, HFHubAdapter)
    assert adapter.repo_id == "acme/functions"


def test_parse_hub_spec_s3_with_prefix() -> None:
    pytest.importorskip("boto3")
    from chimera.function_synthesis.hub import S3HubAdapter, parse_hub_spec

    with patch("boto3.client", return_value=MagicMock()):
        adapter = parse_hub_spec("s3:my-bucket/nested/prefix")
    assert isinstance(adapter, S3HubAdapter)
    assert adapter.bucket == "my-bucket"
    assert adapter.prefix == "nested/prefix"


def test_parse_hub_spec_s3_default_prefix() -> None:
    pytest.importorskip("boto3")
    from chimera.function_synthesis.hub import S3HubAdapter, parse_hub_spec

    with patch("boto3.client", return_value=MagicMock()):
        adapter = parse_hub_spec("s3:just-bucket")
    assert isinstance(adapter, S3HubAdapter)
    assert adapter.bucket == "just-bucket"
    assert adapter.prefix == "chimera-fs"


def test_parse_hub_spec_rejects_unknown() -> None:
    from chimera.function_synthesis.hub import parse_hub_spec

    with pytest.raises(ValueError):
        parse_hub_spec("ftp://nope")


def test_parse_hub_spec_rejects_bad_hf() -> None:
    from chimera.function_synthesis.hub import parse_hub_spec

    with pytest.raises(ValueError):
        parse_hub_spec("hf:no-slash-here")


# ---------------------------------------------------------------------------
# HFHubAdapter
# ---------------------------------------------------------------------------


def _hf_adapter_with_mock() -> tuple[object, MagicMock]:
    """Build an ``HFHubAdapter`` whose ``HfApi`` is a ``MagicMock``."""
    pytest.importorskip("huggingface_hub")
    from chimera.function_synthesis.hub import HFHubAdapter

    api = MagicMock()
    with patch("huggingface_hub.HfApi", return_value=api):
        adapter = HFHubAdapter(repo_id="acme/functions", token="t-***", private=True)
    return adapter, api


def test_hf_push_returns_uri_and_uploads(tmp_path: Path) -> None:
    adapter, api = _hf_adapter_with_mock()
    bundle = _make_fake_bundle(tmp_path)

    uri = adapter.push("classify-abc123", bundle, description="sentiment demo")

    assert uri == "hf://acme/functions/classify-abc123.chi"
    api.create_repo.assert_called_once()
    api.upload_file.assert_called_once()
    kwargs = api.upload_file.call_args.kwargs
    # The bundle path must be passed unmodified (byte-exact preservation).
    assert kwargs["path_or_fileobj"] == str(bundle)
    assert kwargs["path_in_repo"] == "classify-abc123.chi"
    assert kwargs["repo_id"] == "acme/functions"
    assert "sentiment demo" in kwargs["commit_message"]


def test_hf_push_missing_file_raises(tmp_path: Path) -> None:
    adapter, _ = _hf_adapter_with_mock()
    with pytest.raises(FileNotFoundError):
        adapter.push("x", tmp_path / "does-not-exist.chi")


def test_hf_pull_returns_bytes(tmp_path: Path) -> None:
    adapter, _ = _hf_adapter_with_mock()
    bundle = _make_fake_bundle(tmp_path, name="pulled")
    original = bundle.read_bytes()

    with patch(
        "huggingface_hub.hf_hub_download", return_value=str(bundle)
    ) as mock_dl:
        out = adapter.pull("hf://acme/functions/pulled.chi")

    assert out == original
    mock_dl.assert_called_once()
    call_kwargs = mock_dl.call_args.kwargs
    assert call_kwargs["repo_id"] == "acme/functions"
    assert call_kwargs["filename"] == "pulled.chi"


def test_hf_pull_rejects_wrong_scheme() -> None:
    adapter, _ = _hf_adapter_with_mock()
    with pytest.raises(ValueError):
        adapter.pull("s3://bucket/key")


def test_hf_list_filters_to_chi(tmp_path: Path) -> None:
    adapter, api = _hf_adapter_with_mock()
    api.list_repo_files.return_value = ["README.md", "a.chi", "sub/b.chi"]

    out = adapter.list()

    slugs = {row["slug"] for row in out}
    assert slugs == {"a", "sub/b"}
    for row in out:
        assert row["uri"].startswith("hf://acme/functions/")
        assert row["uri"].endswith(".chi")


def test_hf_push_redacts_credentials_in_errors(tmp_path: Path) -> None:
    from chimera.function_synthesis.hub import HubError

    adapter, api = _hf_adapter_with_mock()
    bundle = _make_fake_bundle(tmp_path)
    api.upload_file.side_effect = RuntimeError(
        "auth failed, token=hf_SECRET_ABC123 not accepted"
    )

    with pytest.raises(HubError) as exc_info:
        adapter.push("x", bundle)

    msg = str(exc_info.value)
    assert "hf_SECRET_ABC123" not in msg
    assert "redacted" in msg


def test_hf_constructor_rejects_bad_repo() -> None:
    pytest.importorskip("huggingface_hub")
    from chimera.function_synthesis.hub import HFHubAdapter

    with patch("huggingface_hub.HfApi", return_value=MagicMock()):
        with pytest.raises(ValueError):
            HFHubAdapter(repo_id="no-slash")


def test_hf_import_error_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If huggingface_hub is absent, the adapter raises a friendly ImportError."""
    # Force the import inside the adapter to fail.
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    # Pop chimera's hub module to re-import cleanly.
    sys.modules.pop("chimera.function_synthesis.hub", None)
    from chimera.function_synthesis.hub import HFHubAdapter

    with pytest.raises(ImportError) as exc_info:
        HFHubAdapter(repo_id="acme/functions")
    assert "huggingface_hub" in str(exc_info.value)
    # Clean up so later tests re-import normally.
    monkeypatch.delitem(sys.modules, "huggingface_hub", raising=False)
    sys.modules.pop("chimera.function_synthesis.hub", None)


# ---------------------------------------------------------------------------
# S3HubAdapter
# ---------------------------------------------------------------------------


def _s3_adapter_with_mock(
    *, prefix: str = "chimera-fs", endpoint_url: str | None = None
) -> tuple[object, MagicMock]:
    pytest.importorskip("boto3")
    from chimera.function_synthesis.hub import S3HubAdapter

    client = MagicMock()
    with patch("boto3.client", return_value=client) as mock_boto:
        adapter = S3HubAdapter(
            bucket="my-bucket", prefix=prefix, endpoint_url=endpoint_url
        )
    mock_boto.assert_called_once()
    return adapter, client


def test_s3_push_returns_uri_and_uploads(tmp_path: Path) -> None:
    adapter, client = _s3_adapter_with_mock()
    bundle = _make_fake_bundle(tmp_path, name="fn-1")

    uri = adapter.push("fn-1", bundle, description="demo")

    assert uri == "s3://my-bucket/chimera-fs/fn-1.chi"
    client.upload_file.assert_called_once()
    kwargs = client.upload_file.call_args.kwargs
    assert kwargs["Filename"] == str(bundle)
    assert kwargs["Bucket"] == "my-bucket"
    assert kwargs["Key"] == "chimera-fs/fn-1.chi"


def test_s3_push_respects_empty_prefix(tmp_path: Path) -> None:
    adapter, client = _s3_adapter_with_mock(prefix="")
    bundle = _make_fake_bundle(tmp_path, name="flat")

    uri = adapter.push("flat", bundle)

    assert uri == "s3://my-bucket/flat.chi"
    assert client.upload_file.call_args.kwargs["Key"] == "flat.chi"


def test_s3_push_missing_file_raises(tmp_path: Path) -> None:
    adapter, _ = _s3_adapter_with_mock()
    with pytest.raises(FileNotFoundError):
        adapter.push("x", tmp_path / "missing.chi")


def test_s3_pull_returns_bytes(tmp_path: Path) -> None:
    adapter, client = _s3_adapter_with_mock()
    payload = b"PK-\x00\x01the-actual-zip-bytes"
    body = MagicMock()
    body.read.return_value = payload
    client.get_object.return_value = {"Body": body}

    out = adapter.pull("s3://my-bucket/chimera-fs/fn-1.chi")

    assert out == payload
    client.get_object.assert_called_once_with(
        Bucket="my-bucket", Key="chimera-fs/fn-1.chi"
    )


def test_s3_pull_rejects_wrong_scheme() -> None:
    adapter, _ = _s3_adapter_with_mock()
    with pytest.raises(ValueError):
        adapter.pull("hf://acme/repo/x.chi")


def test_s3_list_filters_to_chi() -> None:
    adapter, client = _s3_adapter_with_mock(prefix="bundles")
    client.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "bundles/a.chi"},
            {"Key": "bundles/README.md"},
            {"Key": "bundles/nested/b.chi"},
        ]
    }

    out = adapter.list()

    slugs = {row["slug"] for row in out}
    assert slugs == {"a", "b"}
    for row in out:
        assert row["uri"].startswith("s3://my-bucket/bundles/")


def test_s3_endpoint_url_forwarded(tmp_path: Path) -> None:
    pytest.importorskip("boto3")
    from chimera.function_synthesis.hub import S3HubAdapter

    client = MagicMock()
    with patch("boto3.client", return_value=client) as mock_boto:
        S3HubAdapter(bucket="b", endpoint_url="https://r2.example.com")
    _, kwargs = mock_boto.call_args
    assert kwargs["endpoint_url"] == "https://r2.example.com"


def test_s3_push_redacts_credentials_in_errors(tmp_path: Path) -> None:
    from chimera.function_synthesis.hub import HubError

    adapter, client = _s3_adapter_with_mock()
    bundle = _make_fake_bundle(tmp_path)
    client.upload_file.side_effect = RuntimeError(
        "auth failed, aws_secret_access_key=AKIAVERY_SECRET_VALUE bad"
    )

    with pytest.raises(HubError) as exc_info:
        adapter.push("x", bundle)

    msg = str(exc_info.value)
    assert "AKIAVERY_SECRET_VALUE" not in msg
    assert "redacted" in msg


def test_s3_constructor_rejects_empty_bucket() -> None:
    pytest.importorskip("boto3")
    from chimera.function_synthesis.hub import S3HubAdapter

    with patch("boto3.client", return_value=MagicMock()):
        with pytest.raises(ValueError):
            S3HubAdapter(bucket="")


def test_s3_import_error_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)
    sys.modules.pop("chimera.function_synthesis.hub", None)
    from chimera.function_synthesis.hub import S3HubAdapter

    with pytest.raises(ImportError) as exc_info:
        S3HubAdapter(bucket="b")
    assert "boto3" in str(exc_info.value)
    monkeypatch.delitem(sys.modules, "boto3", raising=False)
    sys.modules.pop("chimera.function_synthesis.hub", None)


# ---------------------------------------------------------------------------
# Byte-exact round-trip
# ---------------------------------------------------------------------------


def test_hf_round_trip_preserves_bytes(tmp_path: Path) -> None:
    """push + pull must yield the exact bytes originally on disk."""
    adapter, api = _hf_adapter_with_mock()
    bundle = _make_fake_bundle(tmp_path, name="round-trip")
    original = bundle.read_bytes()

    # Capture the uploaded path_or_fileobj and have pull "download" it.
    uploaded: dict[str, str] = {}

    def _capture(**kwargs: object) -> None:
        uploaded["path"] = str(kwargs["path_or_fileobj"])

    api.upload_file.side_effect = _capture
    uri = adapter.push("round-trip", bundle)

    with patch(
        "huggingface_hub.hf_hub_download", return_value=uploaded["path"]
    ):
        downloaded = adapter.pull(uri)

    assert downloaded == original


def test_s3_round_trip_preserves_bytes(tmp_path: Path) -> None:
    adapter, client = _s3_adapter_with_mock()
    bundle = _make_fake_bundle(tmp_path, name="rt2")
    original = bundle.read_bytes()

    def _upload(**kwargs: object) -> None:
        # Simulate the remote object storing exactly what's on disk.
        path = Path(str(kwargs["Filename"]))
        stored = path.read_bytes()
        body = MagicMock()
        body.read.return_value = stored
        client.get_object.return_value = {"Body": body}

    client.upload_file.side_effect = _upload
    uri = adapter.push("rt2", bundle)
    out = adapter.pull(uri)
    assert out == original
