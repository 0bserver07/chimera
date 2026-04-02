"""Tests for chimera.packages.manager — Extension Package Manager."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from chimera.packages.manager import PackageManager, InstalledPackage


@pytest.fixture
def pkg_dir(tmp_path: Path) -> Path:
    """Temporary directory used as the packages root."""
    return tmp_path / "packages"


@pytest.fixture
def manager(pkg_dir: Path) -> PackageManager:
    return PackageManager(packages_dir=pkg_dir)


@pytest.fixture
def fake_local_package(tmp_path: Path) -> Path:
    """Create a minimal fake package directory."""
    pkg = tmp_path / "my_extension"
    pkg.mkdir()
    (pkg / "chimera_plugin.json").write_text(json.dumps({"name": "my_extension"}))
    return pkg


# ---------- test_install_local ----------

def test_install_local(manager: PackageManager, fake_local_package: Path) -> None:
    pkg = manager.install(f"local:{fake_local_package}")
    assert pkg.name == fake_local_package.name
    assert pkg.source == f"local:{fake_local_package}"
    assert pkg.install_path == fake_local_package


def test_install_local_shorthand(manager: PackageManager, fake_local_package: Path) -> None:
    """Absolute path without 'local:' prefix should also work."""
    pkg = manager.install(str(fake_local_package))
    assert pkg.name == fake_local_package.name
    assert pkg.install_path == fake_local_package


# ---------- test_install_unknown_source_raises ----------

def test_install_unknown_source_raises(manager: PackageManager) -> None:
    with pytest.raises(ValueError, match="Unknown source format"):
        manager.install("ftp://bad-source")


# ---------- test_uninstall ----------

def test_uninstall(manager: PackageManager, fake_local_package: Path) -> None:
    manager.install(f"local:{fake_local_package}")
    assert manager.uninstall(fake_local_package.name) is True


def test_uninstall_missing(manager: PackageManager) -> None:
    assert manager.uninstall("nonexistent") is False


# ---------- test_list_packages ----------

def test_list_packages_empty(manager: PackageManager) -> None:
    assert manager.list_packages() == []


def test_list_packages(manager: PackageManager, fake_local_package: Path) -> None:
    manager.install(f"local:{fake_local_package}")
    pkgs = manager.list_packages()
    assert len(pkgs) == 1
    assert pkgs[0].name == fake_local_package.name


# ---------- test_manifest_persists ----------

def test_manifest_persists(pkg_dir: Path, fake_local_package: Path) -> None:
    """Install via one manager, create a new manager from same dir, verify package found."""
    m1 = PackageManager(packages_dir=pkg_dir)
    m1.install(f"local:{fake_local_package}")

    m2 = PackageManager(packages_dir=pkg_dir)
    pkgs = m2.list_packages()
    assert len(pkgs) == 1
    assert pkgs[0].name == fake_local_package.name
    assert pkgs[0].source == f"local:{fake_local_package}"


# ---------- test_get_package ----------

def test_get_package(manager: PackageManager, fake_local_package: Path) -> None:
    manager.install(f"local:{fake_local_package}")
    pkg = manager.get(fake_local_package.name)
    assert pkg is not None
    assert pkg.name == fake_local_package.name


def test_get_package_missing(manager: PackageManager) -> None:
    assert manager.get("does_not_exist") is None
