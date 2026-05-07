"""Plugin marketplace for discovery, search, and installation.

This module provides two layers:

1. In-memory metadata structures (:class:`PluginInfo`,
   :class:`MarketplaceRegistry`, :class:`Marketplace`) that the rest of
   the codebase has historically relied on. These remain stable.
2. A :class:`MarketplaceClient` plus helper functions for fetching a
   remote registry index, downloading a plugin tarball, and installing
   it into the right per-CLI plugin directory (``~/.<cli>/plugin/<name>/``
   for user scope, or ``./.<cli>/plugin/<name>/`` for project scope).

The remote layer is intentionally minimal:

- The index is a JSON document of the form ``{"plugins": [PluginInfo,
  ...]}`` (extra fields ignored).
- Each :class:`PluginInfo` may carry a ``url`` pointing at a ``.tar.gz``
  archive, plus an optional ``sha256`` checksum.
- ``$CHIMERA_PLUGIN_INDEX`` env var configures the index URL. Values
  starting with ``http://`` or ``https://`` are fetched via httpx;
  anything else is treated as a local file path.
- There is no built-in default index. Hosting one is the user's choice;
  see ``docs/plugins-index.md`` for the schema and a sample at
  ``examples/plugin-index.json``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_INDEX_URL: str | None = None
"""Default registry index URL.

Intentionally ``None``: Chimera does not host a curated public registry.
Operators must point at a self-hosted index via the ``--index`` flag,
the ``$CHIMERA_PLUGIN_INDEX`` env var, or ``chimera config set
plugin_index <url>``. See ``docs/plugins-index.md``.
"""

NO_INDEX_HELP: str = (
    "No plugin index configured.\n"
    "\n"
    "To search the marketplace, set one of:\n"
    "  export CHIMERA_PLUGIN_INDEX=https://your-index.example.com/index.json\n"
    "  chimera plugins search --index https://your-index.example.com/index.json\n"
    "  chimera config set plugin_index https://your-index.example.com/index.json\n"
    "\n"
    "To host your own index, see docs/plugins-index.md\n"
    "To try a sample index, see examples/plugin-index.json"
)
"""Multi-line help printed when no plugin index is configured."""

SUPPORTED_CLIS: tuple[str, ...] = (
    "mink",
    "otter",
    "ferret",
    "weasel",
    "shrew",
    "stoat",
    "badger",
)
"""Per-CLI names that participate in the plugin directory layout."""


@dataclass
class PluginInfo:
    """Metadata describing a plugin available in the marketplace.

    Attributes:
        name: Unique plugin name.
        version: Version string (e.g. "1.0.0").
        description: Human-readable description of the plugin.
        author: Plugin author name or organization.
        url: URL for the plugin's homepage or repository.
        tags: Categorization tags for discovery.
        downloads: Number of times the plugin has been downloaded.
        rating: Average user rating on a 0-5 star scale.
    """

    name: str
    version: str
    description: str = ""
    author: str = ""
    url: str = ""
    tags: list[str] = field(default_factory=list)
    downloads: int = 0
    rating: float = 0.0
    sha256: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginInfo":
        """Build a :class:`PluginInfo` from a plain dict.

        Unknown keys are silently ignored so the registry index can grow
        new fields without breaking older clients.

        Args:
            data: Mapping of plugin metadata.

        Returns:
            New :class:`PluginInfo` instance.

        Raises:
            ValueError: If ``name`` or ``version`` is missing.
        """
        if "name" not in data or not data["name"]:
            raise ValueError("PluginInfo entry missing 'name'")
        if "version" not in data or not data["version"]:
            raise ValueError(
                f"PluginInfo entry '{data['name']}' missing 'version'"
            )
        tags_raw = data.get("tags") or []
        tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
        return cls(
            name=str(data["name"]),
            version=str(data["version"]),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            url=str(data.get("url", "")),
            tags=tags,
            downloads=int(data.get("downloads", 0) or 0),
            rating=float(data.get("rating", 0.0) or 0.0),
            sha256=str(data.get("sha256", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (for JSON manifests)."""
        return asdict(self)


class MarketplaceRegistry:
    """Registry of available plugins with search and filtering.

    Stores :class:`PluginInfo` entries and provides methods to search,
    filter, and retrieve plugin metadata.

    Example:
        ```python
        registry = MarketplaceRegistry()
        registry.register(PluginInfo(name="my-tool", version="1.0.0"))
        results = registry.search("tool")
        ```
    """

    def __init__(self) -> None:
        self._plugins: dict[str, PluginInfo] = {}

    def register(self, info: PluginInfo) -> None:
        """Register a plugin in the registry.

        Args:
            info: Plugin metadata to register.
        """
        self._plugins[info.name] = info

    def unregister(self, name: str) -> None:
        """Remove a plugin from the registry.

        Args:
            name: Name of the plugin to remove.
        """
        self._plugins.pop(name, None)

    def get(self, name: str) -> PluginInfo | None:
        """Look up a plugin by name.

        Args:
            name: Plugin name to look up.

        Returns:
            The plugin info if found, otherwise None.
        """
        return self._plugins.get(name)

    def search(self, query: str) -> list[PluginInfo]:
        """Search plugins by name, description, or tags.

        Performs a case-insensitive substring match against the plugin
        name, description, and tag values.

        Args:
            query: Search string.

        Returns:
            List of matching plugins.
        """
        query_lower = query.lower()
        results: list[PluginInfo] = []
        for info in self._plugins.values():
            if (
                query_lower in info.name.lower()
                or query_lower in info.description.lower()
                or any(query_lower in tag.lower() for tag in info.tags)
            ):
                results.append(info)
        return results

    def list_all(self) -> list[PluginInfo]:
        """Return all registered plugins.

        Returns:
            List of all plugin info entries.
        """
        return list(self._plugins.values())

    def by_tag(self, tag: str) -> list[PluginInfo]:
        """Filter plugins by tag.

        Performs a case-insensitive exact match on tag values.

        Args:
            tag: Tag to filter by.

        Returns:
            List of plugins that have the given tag.
        """
        tag_lower = tag.lower()
        return [
            info
            for info in self._plugins.values()
            if any(tag_lower == t.lower() for t in info.tags)
        ]

    def top_rated(self, limit: int = 10) -> list[PluginInfo]:
        """Return the highest-rated plugins.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of plugins sorted by rating in descending order.
        """
        sorted_plugins = sorted(
            self._plugins.values(), key=lambda p: p.rating, reverse=True
        )
        return sorted_plugins[:limit]


class Marketplace:
    """Plugin marketplace for publishing, searching, and installing plugins.

    Wraps a :class:`MarketplaceRegistry` and adds install/uninstall tracking.

    Example:
        ```python
        mp = Marketplace()
        mp.publish(PluginInfo(name="my-tool", version="1.0.0"))
        mp.install("my-tool")
        assert mp.is_installed("my-tool")
        ```
    """

    def __init__(self, registry: MarketplaceRegistry | None = None) -> None:
        self._registry = registry or MarketplaceRegistry()
        self._installed: set[str] = set()

    @property
    def registry(self) -> MarketplaceRegistry:
        """Access the underlying plugin registry."""
        return self._registry

    def publish(self, info: PluginInfo) -> None:
        """Publish a plugin to the marketplace.

        Args:
            info: Plugin metadata to publish.
        """
        self._registry.register(info)

    def search(self, query: str) -> list[PluginInfo]:
        """Search for plugins in the marketplace.

        Args:
            query: Search string.

        Returns:
            List of matching plugins.
        """
        return self._registry.search(query)

    def install(self, name: str) -> bool:
        """Mark a plugin as installed.

        Args:
            name: Name of the plugin to install.

        Returns:
            True if the plugin was found and installed, False otherwise.
        """
        info = self._registry.get(name)
        if info is None:
            return False
        self._installed.add(name)
        return True

    def uninstall(self, name: str) -> bool:
        """Mark a plugin as uninstalled.

        Args:
            name: Name of the plugin to uninstall.

        Returns:
            True if the plugin was installed and is now removed, False otherwise.
        """
        if name not in self._installed:
            return False
        self._installed.discard(name)
        return True

    @property
    def installed(self) -> list[str]:
        """List of installed plugin names."""
        return sorted(self._installed)

    def is_installed(self, name: str) -> bool:
        """Check whether a plugin is installed.

        Args:
            name: Plugin name to check.

        Returns:
            True if the plugin is currently installed.
        """
        return name in self._installed


# ---------------------------------------------------------------------------
# Remote registry + filesystem install
# ---------------------------------------------------------------------------


class MarketplaceError(RuntimeError):
    """Raised when a marketplace operation fails."""


def resolve_index_url(override: str | None = None) -> str | None:
    """Resolve which registry index URL/path to load.

    Precedence: explicit ``override`` > ``$CHIMERA_PLUGIN_INDEX`` env
    var > ``chimera config`` (``[global] plugin_index``) >
    :data:`DEFAULT_INDEX_URL`.

    Returns ``None`` when no index is configured anywhere — callers
    should treat that as "show the user how to set one up" rather than
    falling back to a baked-in default URL. See :data:`NO_INDEX_HELP`.

    Args:
        override: Optional explicit URL or local path.

    Returns:
        URL or filesystem path string, or ``None`` when nothing is
        configured.
    """
    if override:
        return override
    env_value = os.environ.get("CHIMERA_PLUGIN_INDEX")
    if env_value:
        return env_value
    # Persistent config: ``chimera config set plugin_index <url>``.
    # Lazy import so the marketplace module remains usable even when
    # ``chimera.cli`` is unavailable (e.g. trimmed deploys).
    try:
        from chimera.cli.config_loader import resolve_default

        configured = resolve_default("global", "plugin_index", None)
        if isinstance(configured, str) and configured:
            return configured
    except Exception:  # noqa: BLE001 — config is best-effort
        pass
    return DEFAULT_INDEX_URL


def _is_remote(target: str) -> bool:
    """Return True if ``target`` looks like an http(s) URL."""
    scheme = urlparse(target).scheme
    return scheme in ("http", "https")


def fetch_index(
    url: str | None = None,
    *,
    timeout: float = 10.0,
) -> MarketplaceRegistry:
    """Fetch a registry index and return a populated registry.

    The index is JSON of the form ``{"plugins": [...]}``. Local file
    paths are read directly; ``http(s)://`` URLs go through httpx.

    Args:
        url: Optional registry URL or path. Defaults to
            :func:`resolve_index_url`.
        timeout: HTTP request timeout in seconds.

    Returns:
        A populated :class:`MarketplaceRegistry`.

    Raises:
        MarketplaceError: If the index cannot be loaded or parsed.
    """
    target = resolve_index_url(url)
    if target is None:
        raise MarketplaceError(NO_INDEX_HELP)
    raw: str
    if _is_remote(target):
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise MarketplaceError(
                "httpx is required to fetch remote registry indexes; "
                "install with: uv pip install httpx"
            ) from exc
        try:
            response = httpx.get(target, timeout=timeout)
            response.raise_for_status()
            raw = response.text
        except Exception as exc:  # network/HTTP error
            raise MarketplaceError(
                f"Failed to fetch registry index from {target}: {exc}"
            ) from exc
    else:
        path = Path(target).expanduser()
        if not path.is_file():
            raise MarketplaceError(f"Registry index file not found: {path}")
        raw = path.read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MarketplaceError(
            f"Registry index is not valid JSON: {exc}"
        ) from exc

    entries = data.get("plugins", []) if isinstance(data, dict) else []
    if not isinstance(entries, list):
        raise MarketplaceError(
            "Registry index 'plugins' field must be a list"
        )

    registry = MarketplaceRegistry()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            registry.register(PluginInfo.from_dict(entry))
        except ValueError:
            # Skip malformed entries rather than failing the whole load.
            continue
    return registry


def plugin_root(
    cli: str,
    *,
    scope: str = "user",
    project_root: Path | None = None,
) -> Path:
    """Return the plugin directory for a given CLI and scope.

    Layout:

    - ``user`` scope: ``~/.<cli>/plugin/``
    - ``project`` scope: ``<project_root>/.<cli>/plugin/``

    Args:
        cli: One of :data:`SUPPORTED_CLIS`.
        scope: ``"user"`` or ``"project"``.
        project_root: Project root for project scope. Defaults to the
            current working directory.

    Returns:
        The directory path (not created on disk).

    Raises:
        ValueError: If ``cli`` or ``scope`` is unrecognised.
    """
    if cli not in SUPPORTED_CLIS:
        raise ValueError(
            f"Unknown CLI {cli!r}; expected one of {SUPPORTED_CLIS}"
        )
    if scope == "user":
        return Path.home() / f".{cli}" / "plugin"
    if scope == "project":
        base = project_root or Path.cwd()
        return base / f".{cli}" / "plugin"
    raise ValueError(f"Unknown scope {scope!r}; expected 'user' or 'project'")


def list_installed(
    cli: str,
    *,
    scope: str = "user",
    project_root: Path | None = None,
) -> list[str]:
    """List plugin names installed under a given CLI/scope.

    A directory is reported as a plugin if its name does not start with
    a dot. Manifest parsing is left to each CLI's loader.

    Args:
        cli: One of :data:`SUPPORTED_CLIS`.
        scope: ``"user"`` or ``"project"``.
        project_root: Project root for project scope.

    Returns:
        Sorted list of plugin directory names.
    """
    root = plugin_root(cli, scope=scope, project_root=project_root)
    if not root.is_dir():
        return []
    return sorted(
        child.name
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected.lower():
        raise MarketplaceError(
            f"sha256 mismatch for {path.name}: "
            f"expected {expected}, got {actual}"
        )


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract a tarball, refusing path-traversal entries.

    Re-implements the strict semantics of the ``data_filter`` available
    on Python 3.12+ so we behave identically on 3.11 (Chimera's
    minimum version).
    """
    dest_resolved = dest.resolve()
    members: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        member_name = member.name.lstrip("/")
        if Path(member_name).is_absolute() or ".." in Path(member_name).parts:
            raise MarketplaceError(
                f"Refusing to extract unsafe tar entry: {member.name!r}"
            )
        if member.issym() or member.islnk():
            link_target = (dest / member_name).parent / member.linkname
            try:
                resolved = link_target.resolve()
            except OSError as exc:
                raise MarketplaceError(
                    f"Refusing tar entry with unresolvable link: "
                    f"{member.name!r}"
                ) from exc
            if (
                dest_resolved not in resolved.parents
                and resolved != dest_resolved
            ):
                raise MarketplaceError(
                    f"Refusing tar entry with link escaping dest: "
                    f"{member.name!r}"
                )
        members.append(member)
    # We've already filtered for traversal/symlink escapes; pass
    # ``filter="data"`` so 3.12+ doesn't emit a DeprecationWarning and
    # also so it adds its own defence-in-depth checks. The fallback for
    # 3.11 quietly ignores ``filter``.
    try:
        tar.extractall(path=dest, members=members, filter="data")  # type: ignore[call-arg]
    except TypeError:
        tar.extractall(path=dest, members=members)  # noqa: S202 — filtered above


def _download(url: str, dest: Path, *, timeout: float = 30.0) -> None:
    """Download ``url`` (http/https or local path) into ``dest``."""
    if _is_remote(url):
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise MarketplaceError(
                "httpx is required to download plugins"
            ) from exc
        try:
            with httpx.stream("GET", url, timeout=timeout) as resp:
                resp.raise_for_status()
                with dest.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
        except Exception as exc:
            raise MarketplaceError(
                f"Failed to download {url}: {exc}"
            ) from exc
    else:
        src = Path(url).expanduser()
        if not src.is_file():
            raise MarketplaceError(f"Plugin archive not found: {src}")
        shutil.copyfile(src, dest)


def install_plugin(
    info: PluginInfo,
    cli: str,
    *,
    scope: str = "user",
    project_root: Path | None = None,
    overwrite: bool = False,
    download_dir: Path | None = None,
) -> Path:
    """Download and install ``info`` under the per-CLI plugin directory.

    Steps:

    1. Resolve the destination ``<plugin_root>/<info.name>``.
    2. Download ``info.url`` to a temp file.
    3. Optionally verify ``info.sha256``.
    4. Extract into a fresh destination directory.
    5. Write ``.chimera-marketplace.json`` with the
       :class:`PluginInfo` dict for later inspection.

    Args:
        info: Plugin metadata (must include ``url``).
        cli: One of :data:`SUPPORTED_CLIS`.
        scope: ``"user"`` or ``"project"``.
        project_root: Project root for project scope.
        overwrite: Replace any existing installation when True.
        download_dir: Optional temp directory for the archive download.

    Returns:
        Path to the installed plugin directory.

    Raises:
        MarketplaceError: For any download/extract/verify failure.
    """
    if not info.url:
        raise MarketplaceError(
            f"Plugin {info.name!r} has no 'url' — cannot install"
        )
    root = plugin_root(cli, scope=scope, project_root=project_root)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / info.name
    if dest.exists():
        if not overwrite:
            raise MarketplaceError(
                f"Plugin {info.name!r} already installed at {dest}; "
                f"pass overwrite=True to replace"
            )
        shutil.rmtree(dest)

    tmp_root = download_dir or root
    tmp_root.mkdir(parents=True, exist_ok=True)
    archive = tmp_root / f"{info.name}-{info.version}.tar.gz"

    try:
        _download(info.url, archive)
        if info.sha256:
            _verify_sha256(archive, info.sha256)
        dest.mkdir(parents=True, exist_ok=False)
        try:
            with tarfile.open(archive, "r:*") as tar:
                _safe_extract(tar, dest)
        except tarfile.TarError as exc:
            raise MarketplaceError(
                f"Failed to extract {archive.name}: {exc}"
            ) from exc

        # Best-effort: collapse a single top-level dir if the tarball
        # was packed as <name>-<version>/<contents>.
        children = [c for c in dest.iterdir() if not c.name.startswith(".")]
        if len(children) == 1 and children[0].is_dir():
            inner = children[0]
            for item in list(inner.iterdir()):
                shutil.move(str(item), str(dest / item.name))
            inner.rmdir()

        manifest = dest / ".chimera-marketplace.json"
        manifest.write_text(
            json.dumps(info.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    finally:
        if archive.exists():
            try:
                archive.unlink()
            except OSError:
                pass

    return dest


def uninstall_plugin(
    name: str,
    cli: str,
    *,
    scope: str = "user",
    project_root: Path | None = None,
) -> bool:
    """Remove an installed plugin directory.

    Args:
        name: Plugin name (directory under the plugin root).
        cli: One of :data:`SUPPORTED_CLIS`.
        scope: ``"user"`` or ``"project"``.
        project_root: Project root for project scope.

    Returns:
        True if a directory was removed, False if it was not present.

    Raises:
        MarketplaceError: If ``<plugin_root>/<name>`` exists but is not
            a directory.
    """
    root = plugin_root(cli, scope=scope, project_root=project_root)
    target = root / name
    if not target.exists():
        return False
    if not target.is_dir():
        raise MarketplaceError(
            f"Refusing to delete non-directory entry at {target}"
        )
    shutil.rmtree(target)
    return True


class MarketplaceClient:
    """High-level facade tying the registry index to filesystem installs.

    Example:
        ```python
        client = MarketplaceClient.from_url()  # uses default index
        for hit in client.search("formatter"):
            print(hit.name, hit.version)
        client.install("rufflint", cli="otter", scope="user")
        ```
    """

    def __init__(self, registry: MarketplaceRegistry) -> None:
        self._registry = registry

    @classmethod
    def from_url(
        cls,
        url: str | None = None,
        *,
        timeout: float = 10.0,
    ) -> "MarketplaceClient":
        """Build a client by fetching the index from ``url``.

        Args:
            url: Optional override; otherwise resolved via
                :func:`resolve_index_url`.
            timeout: HTTP timeout in seconds.

        Returns:
            New :class:`MarketplaceClient`.
        """
        return cls(fetch_index(url, timeout=timeout))

    @property
    def registry(self) -> MarketplaceRegistry:
        """Underlying :class:`MarketplaceRegistry`."""
        return self._registry

    def search(self, query: str) -> list[PluginInfo]:
        """Search the loaded index. Empty query returns all plugins."""
        if not query:
            return self._registry.list_all()
        return self._registry.search(query)

    def install(
        self,
        name: str,
        cli: str,
        *,
        scope: str = "user",
        project_root: Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Install a plugin by name.

        Args:
            name: Plugin name (must exist in the registry index).
            cli: Per-CLI plugin directory selector.
            scope: ``"user"`` or ``"project"``.
            project_root: Project root for project scope.
            overwrite: Replace an existing install.

        Returns:
            Path to the installed plugin directory.

        Raises:
            MarketplaceError: If the plugin is not in the index, or any
                download/extract failure occurs.
        """
        info = self._registry.get(name)
        if info is None:
            raise MarketplaceError(
                f"Plugin {name!r} not found in registry index"
            )
        return install_plugin(
            info,
            cli,
            scope=scope,
            project_root=project_root,
            overwrite=overwrite,
        )

    @staticmethod
    def uninstall(
        name: str,
        cli: str,
        *,
        scope: str = "user",
        project_root: Path | None = None,
    ) -> bool:
        """Uninstall a plugin from the per-CLI plugin directory."""
        return uninstall_plugin(
            name, cli, scope=scope, project_root=project_root
        )

    @staticmethod
    def installed(
        cli: str,
        *,
        scope: str = "user",
        project_root: Path | None = None,
    ) -> list[str]:
        """List installed plugin names for ``cli`` at ``scope``."""
        return list_installed(cli, scope=scope, project_root=project_root)
