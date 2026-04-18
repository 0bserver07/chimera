from __future__ import annotations
import json
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class InstalledPackage:
    name: str
    source: str          # "npm:@foo/bar", "git:...", "local:/path"
    version: str = ""
    install_path: Path | None = None


class PackageManager:
    """Install and manage chimera extension packages."""

    def __init__(self, packages_dir: Path | None = None):
        self._dir = packages_dir or Path.home() / ".chimera" / "packages"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._dir / "manifest.json"
        self._installed: dict[str, InstalledPackage] = {}
        self._load_manifest()

    def _load_manifest(self) -> None:
        if self._manifest_path.exists():
            data = json.loads(self._manifest_path.read_text())
            for name, info in data.items():
                self._installed[name] = InstalledPackage(
                    name=name,
                    source=info.get("source", ""),
                    version=info.get("version", ""),
                    install_path=Path(info["install_path"]) if info.get("install_path") else None,
                )

    def _save_manifest(self) -> None:
        data: dict[str, Any] = {}
        for name, pkg in self._installed.items():
            data[name] = {
                "source": pkg.source,
                "version": pkg.version,
                "install_path": str(pkg.install_path) if pkg.install_path else None,
            }
        self._manifest_path.write_text(json.dumps(data, indent=2))

    def install(self, source: str) -> InstalledPackage:
        """Install a package from npm, git, or local path.

        Source formats:
          npm:@scope/name or npm:name
          git:github.com/user/repo
          local:/absolute/path
          /absolute/path (shorthand for local:)
        """
        if source.startswith("npm:"):
            return self._install_npm(source[4:])
        elif source.startswith("git:"):
            return self._install_git(source[4:])
        elif source.startswith("local:") or source.startswith("/"):
            path = source.replace("local:", "", 1)
            return self._install_local(path)
        else:
            raise ValueError(f"Unknown source format: {source}. Use npm:, git:, or local:")

    def _install_npm(self, package_name: str) -> InstalledPackage:
        pkg_dir = self._dir / package_name.replace("/", "__").replace("@", "")
        pkg_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["npm", "install", "--prefix", str(pkg_dir), package_name],
                capture_output=True, timeout=120, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(f"npm install failed: {e}")

        pkg = InstalledPackage(name=package_name, source=f"npm:{package_name}", install_path=pkg_dir)
        self._installed[package_name] = pkg
        self._save_manifest()
        return pkg

    def _install_git(self, repo_url: str) -> InstalledPackage:
        if not repo_url.startswith("http"):
            repo_url = f"https://{repo_url}"
        name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        pkg_dir = self._dir / name
        if pkg_dir.exists():
            shutil.rmtree(pkg_dir)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(pkg_dir)],
                capture_output=True, timeout=120, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(f"git clone failed: {e}")

        pkg = InstalledPackage(name=name, source=f"git:{repo_url}", install_path=pkg_dir)
        self._installed[name] = pkg
        self._save_manifest()
        return pkg

    def _install_local(self, path: str) -> InstalledPackage:
        src = Path(path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Local path not found: {path}")
        name = src.name
        pkg = InstalledPackage(name=name, source=f"local:{path}", install_path=src)
        self._installed[name] = pkg
        self._save_manifest()
        return pkg

    def uninstall(self, name: str) -> bool:
        pkg = self._installed.pop(name, None)
        if pkg is None:
            return False
        # Only remove non-local installs
        if pkg.install_path and pkg.install_path.is_relative_to(self._dir):
            shutil.rmtree(pkg.install_path, ignore_errors=True)
        self._save_manifest()
        return True

    def list_packages(self) -> list[InstalledPackage]:
        return list(self._installed.values())

    def get(self, name: str) -> InstalledPackage | None:
        return self._installed.get(name)

    def update(self, name: str) -> InstalledPackage | None:
        pkg = self._installed.get(name)
        if not pkg:
            return None
        # Re-install from original source
        return self.install(pkg.source)
