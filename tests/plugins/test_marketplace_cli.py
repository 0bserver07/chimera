"""Tests for the plugin marketplace CLI and remote install layer.

Covers:

- Index loading from local file (no network).
- Index loading via a stubbed httpx (for the http(s) path).
- Round-trip install/uninstall against per-CLI plugin directories.
- Refusal of unsafe tar entries (path traversal).
- ``chimera plugins {search,install,uninstall,list}`` driven through the
  CLI's ``main()`` with ``CHIMERA_PLUGIN_INDEX`` pointing at a local
  index.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import types
from pathlib import Path

import pytest

from chimera.cli.main import main as cli_main
from chimera.plugins.marketplace import (
    DEFAULT_INDEX_URL,
    MarketplaceClient,
    MarketplaceError,
    PluginInfo,
    fetch_index,
    install_plugin,
    list_installed,
    plugin_root,
    resolve_index_url,
    uninstall_plugin,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tarball(
    target: Path,
    *,
    files: dict[str, str] | None = None,
    top_dir: str | None = None,
) -> Path:
    """Build a deterministic .tar.gz containing ``files``.

    Args:
        target: Output path.
        files: Mapping of in-archive path -> file content (default a
            single ``plugin.json`` and ``hooks.json``).
        top_dir: Optional top-level dir to nest entries under (so we
            also exercise the auto-collapse path).

    Returns:
        The path to the tarball.
    """
    files = files or {
        "plugin.json": json.dumps({"name": "demo", "version": "1.0.0"}),
        "hooks.json": "{}",
    }
    with tarfile.open(target, "w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            arcname = f"{top_dir}/{name}" if top_dir else name
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return target


def _make_index(target: Path, plugins: list[dict[str, object]]) -> Path:
    target.write_text(
        json.dumps({"plugins": plugins}, indent=2), encoding="utf-8"
    )
    return target


# ---------------------------------------------------------------------------
# resolve_index_url
# ---------------------------------------------------------------------------


def test_resolve_index_url_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no env / no config / no override, resolution falls all the way
    through to ``DEFAULT_INDEX_URL`` (which is intentionally ``None``)."""
    monkeypatch.delenv("CHIMERA_PLUGIN_INDEX", raising=False)
    # Redirect $CHIMERA_CONFIG_HOME at an empty dir so any host-side
    # ``~/.chimera/config.toml`` cannot leak ``plugin_index`` in.
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    assert resolve_index_url() == DEFAULT_INDEX_URL


def test_resolve_index_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_PLUGIN_INDEX", "/tmp/local-index.json")
    assert resolve_index_url() == "/tmp/local-index.json"


def test_resolve_index_url_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_PLUGIN_INDEX", "ignored")
    assert resolve_index_url("/explicit/path.json") == "/explicit/path.json"


# ---------------------------------------------------------------------------
# fetch_index — local file
# ---------------------------------------------------------------------------


def test_fetch_index_local_file(tmp_path: Path) -> None:
    index_path = _make_index(
        tmp_path / "index.json",
        [
            {
                "name": "alpha",
                "version": "1.0.0",
                "description": "Alpha plugin",
                "tags": ["formatter"],
            },
            {
                "name": "beta",
                "version": "0.2.0",
                "description": "Beta plugin",
            },
        ],
    )
    registry = fetch_index(str(index_path))
    assert {p.name for p in registry.list_all()} == {"alpha", "beta"}
    assert registry.get("alpha").description == "Alpha plugin"  # type: ignore[union-attr]


def test_fetch_index_skips_malformed_entries(tmp_path: Path) -> None:
    index_path = _make_index(
        tmp_path / "index.json",
        [
            {"name": "good", "version": "1.0.0"},
            {"name": "missing-version"},  # missing required field
            "not-a-dict",  # type: ignore[list-item]
        ],
    )
    registry = fetch_index(str(index_path))
    assert {p.name for p in registry.list_all()} == {"good"}


def test_fetch_index_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MarketplaceError, match="not found"):
        fetch_index(str(tmp_path / "does-not-exist.json"))


def test_fetch_index_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "index.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(MarketplaceError, match="not valid JSON"):
        fetch_index(str(bad))


# ---------------------------------------------------------------------------
# fetch_index — stubbed httpx (no network)
# ---------------------------------------------------------------------------


def test_fetch_index_remote_via_stub_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the URL is http(s), httpx.get is used. We stub it."""
    payload = json.dumps(
        {"plugins": [{"name": "remote-tool", "version": "9.9.9"}]}
    )
    calls: list[str] = []

    class _StubResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def _stub_get(url: str, timeout: float = 0.0) -> _StubResponse:
        calls.append(url)
        return _StubResponse(payload)

    stub_module = types.ModuleType("httpx")
    stub_module.get = _stub_get  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "httpx", stub_module)

    registry = fetch_index("https://example.com/index.json")
    assert calls == ["https://example.com/index.json"]
    assert registry.get("remote-tool") is not None


# ---------------------------------------------------------------------------
# Install / uninstall round-trip (local archive — no network)
# ---------------------------------------------------------------------------


def test_install_uninstall_round_trip(tmp_path: Path) -> None:
    archive = _make_tarball(tmp_path / "demo.tar.gz")
    info = PluginInfo(
        name="demo", version="1.0.0", url=str(archive)
    )
    project_root = tmp_path / "proj"
    project_root.mkdir()

    dest = install_plugin(
        info, "otter", scope="project", project_root=project_root
    )

    assert dest == project_root / ".otter" / "plugin" / "demo"
    assert dest.is_dir()
    assert (dest / "plugin.json").is_file()
    manifest = json.loads(
        (dest / ".chimera-marketplace.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "demo"
    assert manifest["version"] == "1.0.0"

    assert list_installed(
        "otter", scope="project", project_root=project_root
    ) == ["demo"]

    # archive is cleaned up
    assert not (
        project_root / ".otter" / "plugin" / "demo-1.0.0.tar.gz"
    ).exists()

    # uninstall round-trip
    assert uninstall_plugin(
        "demo", "otter", scope="project", project_root=project_root
    )
    assert not dest.exists()
    assert (
        list_installed("otter", scope="project", project_root=project_root)
        == []
    )

    # second uninstall is a no-op (returns False)
    assert not uninstall_plugin(
        "demo", "otter", scope="project", project_root=project_root
    )


def test_install_collapses_single_top_level_dir(tmp_path: Path) -> None:
    archive = _make_tarball(
        tmp_path / "nested.tar.gz",
        files={
            "plugin.json": json.dumps({"name": "nested", "version": "2.0.0"}),
        },
        top_dir="nested-2.0.0",
    )
    info = PluginInfo(name="nested", version="2.0.0", url=str(archive))
    project_root = tmp_path / "proj"
    project_root.mkdir()

    dest = install_plugin(
        info, "weasel", scope="project", project_root=project_root
    )
    # The "nested-2.0.0" wrapper should be collapsed.
    assert (dest / "plugin.json").is_file()
    assert not (dest / "nested-2.0.0").exists()


def test_install_refuses_existing_without_overwrite(tmp_path: Path) -> None:
    archive = _make_tarball(tmp_path / "demo.tar.gz")
    info = PluginInfo(name="demo", version="1.0.0", url=str(archive))
    project_root = tmp_path / "proj"
    project_root.mkdir()

    install_plugin(info, "ferret", scope="project", project_root=project_root)
    with pytest.raises(MarketplaceError, match="already installed"):
        install_plugin(
            info, "ferret", scope="project", project_root=project_root
        )

    # overwrite=True succeeds.
    install_plugin(
        info,
        "ferret",
        scope="project",
        project_root=project_root,
        overwrite=True,
    )


def test_install_verifies_sha256(tmp_path: Path) -> None:
    archive = _make_tarball(tmp_path / "demo.tar.gz")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    project_root = tmp_path / "proj"
    project_root.mkdir()

    good = PluginInfo(
        name="demo", version="1.0.0", url=str(archive), sha256=digest
    )
    install_plugin(good, "shrew", scope="project", project_root=project_root)

    bad = PluginInfo(
        name="demo2", version="1.0.0", url=str(archive), sha256="0" * 64
    )
    with pytest.raises(MarketplaceError, match="sha256 mismatch"):
        install_plugin(
            bad, "shrew", scope="project", project_root=project_root
        )
    # Failed install does not leave the dest directory behind.
    assert not (
        project_root / ".shrew" / "plugin" / "demo2"
    ).exists()


def test_install_refuses_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"pwn"
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    project_root = tmp_path / "proj"
    project_root.mkdir()
    bad_info = PluginInfo(name="evil", version="1.0.0", url=str(archive))
    with pytest.raises(MarketplaceError, match="unsafe tar entry"):
        install_plugin(
            bad_info, "stoat", scope="project", project_root=project_root
        )


def test_install_requires_url(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    info = PluginInfo(name="demo", version="1.0.0")  # no url
    with pytest.raises(MarketplaceError, match="no 'url'"):
        install_plugin(
            info, "badger", scope="project", project_root=project_root
        )


def test_plugin_root_rejects_unknown_cli() -> None:
    with pytest.raises(ValueError, match="Unknown CLI"):
        plugin_root("zebra")


def test_plugin_root_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="Unknown scope"):
        plugin_root("otter", scope="global")


# ---------------------------------------------------------------------------
# MarketplaceClient
# ---------------------------------------------------------------------------


def test_marketplace_client_search_and_install(tmp_path: Path) -> None:
    archive = _make_tarball(tmp_path / "demo.tar.gz")
    index_path = _make_index(
        tmp_path / "index.json",
        [
            {
                "name": "demo",
                "version": "1.0.0",
                "url": str(archive),
                "tags": ["formatter"],
            }
        ],
    )
    client = MarketplaceClient.from_url(str(index_path))
    results = client.search("formatter")
    assert [p.name for p in results] == ["demo"]

    project_root = tmp_path / "proj"
    project_root.mkdir()
    # Install — but plugin_root() reads cwd for project scope, so set it.
    monkey_cwd = os.getcwd()
    try:
        os.chdir(project_root)
        dest = client.install("demo", "otter", scope="project")
    finally:
        os.chdir(monkey_cwd)
    assert dest.is_dir()
    assert (dest / "plugin.json").is_file()


def test_marketplace_client_install_unknown_raises(tmp_path: Path) -> None:
    index_path = _make_index(tmp_path / "index.json", [])
    client = MarketplaceClient.from_url(str(index_path))
    with pytest.raises(MarketplaceError, match="not found in registry"):
        client.install("ghost", "otter", scope="project")


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


@pytest.fixture
def configured_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Set up a project root + local index + tarball; return (project, index)."""
    archive = _make_tarball(tmp_path / "demo.tar.gz")
    index_path = _make_index(
        tmp_path / "index.json",
        [
            {
                "name": "demo",
                "version": "1.0.0",
                "description": "Demo plugin",
                "url": str(archive),
                "tags": ["test"],
            },
            {
                "name": "other",
                "version": "0.1.0",
                "description": "Another plugin",
                "url": str(archive),
            },
        ],
    )
    project_root = tmp_path / "proj"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("CHIMERA_PLUGIN_INDEX", str(index_path))
    return project_root, index_path


def test_cli_search(
    configured_cli: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_main(["plugins", "search", "demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "Demo plugin" in out


def test_cli_search_empty_query_lists_all(
    configured_cli: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_main(["plugins", "search"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "demo" in out and "other" in out


def test_cli_install_then_list_then_uninstall(
    configured_cli: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root, _ = configured_cli

    rc = cli_main(
        ["plugins", "install", "demo", "--cli", "otter", "--scope", "project"]
    )
    assert rc == 0
    installed_dir = project_root / ".otter" / "plugin" / "demo"
    assert installed_dir.is_dir()
    capsys.readouterr()  # drain

    rc = cli_main(
        ["plugins", "list", "--cli", "otter", "--scope", "project"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "demo" in out

    rc = cli_main(
        [
            "plugins",
            "uninstall",
            "demo",
            "--cli",
            "otter",
            "--scope",
            "project",
        ]
    )
    assert rc == 0
    assert not installed_dir.exists()


def test_cli_install_unknown_returns_error(
    configured_cli: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_main(
        [
            "plugins",
            "install",
            "ghost",
            "--cli",
            "otter",
            "--scope",
            "project",
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found in registry" in err


def test_cli_uninstall_missing_returns_error(
    configured_cli: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_main(
        [
            "plugins",
            "uninstall",
            "ghost",
            "--cli",
            "otter",
            "--scope",
            "project",
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "not installed" in err


def test_cli_list_empty(
    configured_cli: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_main(
        ["plugins", "list", "--cli", "weasel", "--scope", "project"]
    )
    assert rc == 0
    assert "No plugins installed" in capsys.readouterr().out


def test_cli_install_overwrite(
    configured_cli: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root, _ = configured_cli
    rc = cli_main(
        ["plugins", "install", "demo", "--cli", "otter", "--scope", "project"]
    )
    assert rc == 0
    capsys.readouterr()

    # second install fails without --overwrite
    rc = cli_main(
        ["plugins", "install", "demo", "--cli", "otter", "--scope", "project"]
    )
    assert rc == 1

    # but succeeds with --overwrite
    rc = cli_main(
        [
            "plugins",
            "install",
            "demo",
            "--cli",
            "otter",
            "--scope",
            "project",
            "--overwrite",
        ]
    )
    assert rc == 0
    assert (project_root / ".otter" / "plugin" / "demo").is_dir()


def test_cli_index_flag_overrides_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--index should win over $CHIMERA_PLUGIN_INDEX."""
    bogus = tmp_path / "bogus.json"
    bogus.write_text("not json", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_PLUGIN_INDEX", str(bogus))

    real_index = _make_index(
        tmp_path / "real.json",
        [{"name": "found-via-flag", "version": "1.0.0"}],
    )
    rc = cli_main(["plugins", "search", "found", "--index", str(real_index)])
    assert rc == 0
    assert "found-via-flag" in capsys.readouterr().out
