# tests/cli/test_fs_hub_cli.py
"""CLI tests for ``chimera fs push`` and ``chimera fs pull``.

These tests patch the underlying hub clients (``HfApi``, ``hf_hub_download``,
``boto3.client``) so no network traffic is performed.  They drive the CLI
in-process via ``chimera.cli.main.build_parser`` / ``cmd_push`` / ``cmd_pull``
so we can capture stdout without a subprocess hop.
"""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fs_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``CHIMERA_FS_HOME`` at a fresh tmp dir for every test."""
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_FS_OFFLINE", "1")
    return tmp_path


def _install_mock_bundle(slug: str = "demo-00000000") -> Path:
    """Compile a mock bundle into the default registry and return its path."""
    from chimera.function_synthesis.compilers.mock import MockCompiler
    from chimera.function_synthesis.registry import ProgramRegistry
    from chimera.function_synthesis.spec import FunctionSpec

    spec = FunctionSpec(name="demo", description="a demo function")
    bundle = MockCompiler().compile(spec)
    registry = ProgramRegistry.default()
    returned = registry.install(spec=spec, bundle=bundle)
    entry = registry.resolve(returned)
    return entry.bundle_path


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


def test_cli_push_hf_prints_uri(fs_home: Path) -> None:
    from chimera.cli.fs import cmd_push

    bundle_path = _install_mock_bundle()
    slug = bundle_path.stem

    api = MagicMock()
    with patch("huggingface_hub.HfApi", return_value=api):
        args = argparse.Namespace(
            slug=slug, hub="hf:acme/functions", description="demo desc"
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_push(args)

    assert rc == 0
    printed = buf.getvalue().strip()
    assert printed == f"hf://acme/functions/{slug}.chi"
    api.upload_file.assert_called_once()
    kwargs = api.upload_file.call_args.kwargs
    assert kwargs["path_or_fileobj"] == str(bundle_path)
    assert kwargs["path_in_repo"] == f"{slug}.chi"


def test_cli_push_s3_prints_uri(fs_home: Path) -> None:
    from chimera.cli.fs import cmd_push

    bundle_path = _install_mock_bundle()
    slug = bundle_path.stem

    client = MagicMock()
    with patch("boto3.client", return_value=client):
        args = argparse.Namespace(
            slug=slug, hub="s3:my-bucket/bundles", description=None
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_push(args)

    assert rc == 0
    assert buf.getvalue().strip() == f"s3://my-bucket/bundles/{slug}.chi"
    kwargs = client.upload_file.call_args.kwargs
    assert kwargs["Bucket"] == "my-bucket"
    assert kwargs["Key"] == f"bundles/{slug}.chi"
    assert kwargs["Filename"] == str(bundle_path)


def test_cli_push_missing_slug_raises(fs_home: Path) -> None:
    from chimera.cli.fs import cmd_push
    from chimera.function_synthesis.errors import CacheMissError

    args = argparse.Namespace(
        slug="no-such-slug", hub="hf:acme/functions", description=None
    )
    with patch("huggingface_hub.HfApi", return_value=MagicMock()):
        with pytest.raises(CacheMissError):
            cmd_push(args)


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


def _produce_remote_bundle(tmp_path: Path) -> Path:
    """Build a real ``.chi`` file that pull() will 'download'."""
    from chimera.function_synthesis.compilers.mock import MockCompiler
    from chimera.function_synthesis.spec import FunctionSpec

    spec = FunctionSpec(name="pulled", description="pulled demo")
    bundle = MockCompiler().compile(spec)
    path = tmp_path / "remote.chi"
    bundle.save(path)
    return path


def test_cli_pull_hf_installs_into_registry(
    fs_home: Path, tmp_path: Path
) -> None:
    from chimera.cli.fs import cmd_pull
    from chimera.function_synthesis.registry import ProgramRegistry

    # A "remote" bundle sitting in a scratch dir.
    remote_bundle = _produce_remote_bundle(tmp_path)

    with patch("huggingface_hub.HfApi", return_value=MagicMock()), patch(
        "huggingface_hub.hf_hub_download", return_value=str(remote_bundle)
    ):
        args = argparse.Namespace(
            uri="hf://acme/functions/pulled-00000000.chi", slug=None
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_pull(args)

    assert rc == 0
    printed_slug = buf.getvalue().strip()
    assert printed_slug.startswith("pulled-")

    registry = ProgramRegistry.default()
    entry = registry.resolve(printed_slug)
    assert entry.bundle_path.exists()
    # Byte-exact preservation: the installed bundle must match what we
    # "downloaded".
    assert entry.bundle_path.read_bytes() == remote_bundle.read_bytes()


def test_cli_pull_s3_installs_into_registry(
    fs_home: Path, tmp_path: Path
) -> None:
    from chimera.cli.fs import cmd_pull
    from chimera.function_synthesis.registry import ProgramRegistry

    remote_bundle = _produce_remote_bundle(tmp_path)
    remote_bytes = remote_bundle.read_bytes()

    client = MagicMock()
    body = MagicMock()
    body.read.return_value = remote_bytes
    client.get_object.return_value = {"Body": body}

    with patch("boto3.client", return_value=client):
        args = argparse.Namespace(
            uri="s3://my-bucket/bundles/pulled-00000000.chi", slug=None
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_pull(args)

    assert rc == 0
    slug = buf.getvalue().strip()
    assert slug.startswith("pulled-")
    registry = ProgramRegistry.default()
    entry = registry.resolve(slug)
    assert entry.bundle_path.read_bytes() == remote_bytes


def test_cli_pull_with_override_slug(fs_home: Path, tmp_path: Path) -> None:
    from chimera.cli.fs import cmd_pull
    from chimera.function_synthesis.registry import ProgramRegistry

    remote_bundle = _produce_remote_bundle(tmp_path)

    with patch("huggingface_hub.HfApi", return_value=MagicMock()), patch(
        "huggingface_hub.hf_hub_download", return_value=str(remote_bundle)
    ):
        args = argparse.Namespace(
            uri="hf://acme/functions/pulled-00000000.chi",
            slug="custom-slug",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_pull(args)

    assert rc == 0
    assert buf.getvalue().strip() == "custom-slug"
    registry = ProgramRegistry.default()
    entry = registry.resolve("custom-slug")
    assert entry.slug == "custom-slug"
    assert entry.bundle_path.exists()
    # The index JSON should also know about the new slug.
    index = json.loads((fs_home / "index.json").read_text())
    assert "custom-slug" in index


def test_cli_pull_rejects_unknown_scheme(fs_home: Path) -> None:
    from chimera.cli.fs import cmd_pull

    args = argparse.Namespace(uri="ftp://nope/here.chi", slug=None)
    with pytest.raises(SystemExit):
        cmd_pull(args)


# ---------------------------------------------------------------------------
# Registered as argparse subcommands
# ---------------------------------------------------------------------------


def test_push_and_pull_are_registered() -> None:
    """Ensure both subcommands survived ``register()``."""
    import argparse as _argparse

    from chimera.cli.fs import register

    parser = _argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    register(subparsers)

    push_args = parser.parse_args(
        ["fs", "push", "a-slug", "--hub", "hf:org/repo"]
    )
    assert push_args.fs_cmd == "push"
    assert push_args.hub == "hf:org/repo"

    pull_args = parser.parse_args(
        ["fs", "pull", "hf://org/repo/a.chi", "--slug", "x"]
    )
    assert pull_args.fs_cmd == "pull"
    assert pull_args.uri == "hf://org/repo/a.chi"
    assert pull_args.slug == "x"


def test_push_pull_end_to_end_mocked(fs_home: Path, tmp_path: Path) -> None:
    """Full round-trip: install -> push -> clear -> pull -> resolve."""
    from chimera.cli.fs import cmd_pull, cmd_push
    from chimera.function_synthesis.registry import ProgramRegistry

    bundle_path = _install_mock_bundle()
    slug = bundle_path.stem
    original_bytes = bundle_path.read_bytes()

    captured: dict[str, bytes] = {}

    def _capture_upload(**kwargs: object) -> None:
        captured["bytes"] = Path(str(kwargs["path_or_fileobj"])).read_bytes()

    api = MagicMock()
    api.upload_file.side_effect = _capture_upload
    with patch("huggingface_hub.HfApi", return_value=api):
        args = argparse.Namespace(
            slug=slug, hub="hf:acme/functions", description=None
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_push(args)
        pushed_uri = buf.getvalue().strip()

    assert captured["bytes"] == original_bytes
    # Simulate a second machine: wipe the registry.
    ProgramRegistry.default().remove(slug)

    # On the "other side", pull stages the captured bytes into a temp file.
    scratch = tmp_path / "remote_copy.chi"
    scratch.write_bytes(captured["bytes"])

    with patch("huggingface_hub.HfApi", return_value=MagicMock()), patch(
        "huggingface_hub.hf_hub_download", return_value=str(scratch)
    ):
        args = argparse.Namespace(uri=pushed_uri, slug=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_pull(args)
        reinstalled_slug = buf.getvalue().strip()

    assert reinstalled_slug == slug
    restored = ProgramRegistry.default().resolve(slug)
    assert restored.bundle_path.read_bytes() == original_bytes
