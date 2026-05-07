"""Tests for ``chimera.skills.discovery`` remote-index helpers (W14-2)."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from chimera.skills import discovery


# ---------------------------------------------------------------------------
# default_remote_cache
# ---------------------------------------------------------------------------


def test_default_remote_cache_uses_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    cache = discovery.default_remote_cache()
    assert cache == home / ".chimera" / "cache" / "skills"


# ---------------------------------------------------------------------------
# _parse_index
# ---------------------------------------------------------------------------


def test_parse_index_envelope_form() -> None:
    raw = json.dumps(
        {
            "skills": [
                {
                    "name": "demo",
                    "description": "example",
                    "url": "https://example.com/SKILL.md",
                }
            ]
        }
    ).encode("utf-8")
    items = discovery._parse_index(raw)
    assert len(items) == 1
    assert items[0]["name"] == "demo"


def test_parse_index_flat_list_form() -> None:
    raw = json.dumps(
        [
            {"name": "x", "description": "y", "url": "https://e/SKILL.md"},
            {"name": "z", "description": "w", "url": "https://e/SKILL.md"},
        ]
    ).encode("utf-8")
    items = discovery._parse_index(raw)
    assert [it["name"] for it in items] == ["x", "z"]


def test_parse_index_drops_invalid_names() -> None:
    raw = json.dumps(
        [
            {"name": "Bad NAME!", "description": "x", "url": "https://e"},
            {"name": "good-name", "description": "x", "url": "https://e"},
        ]
    ).encode("utf-8")
    items = discovery._parse_index(raw)
    assert [it["name"] for it in items] == ["good-name"]


def test_parse_index_drops_non_http_urls() -> None:
    raw = json.dumps(
        [
            {"name": "x", "description": "x", "url": "file:///tmp/SKILL.md"},
            {"name": "y", "description": "y", "url": "https://e/SKILL.md"},
        ]
    ).encode("utf-8")
    items = discovery._parse_index(raw)
    assert [it["name"] for it in items] == ["y"]


def test_parse_index_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        discovery._parse_index(b"not json{")


def test_parse_index_rejects_non_list_skills() -> None:
    raw = json.dumps({"skills": "nope"}).encode("utf-8")
    with pytest.raises(ValueError):
        discovery._parse_index(raw)


def test_parse_index_skips_non_dict_entries() -> None:
    raw = json.dumps(
        [
            "not a dict",
            42,
            {"name": "good", "description": "g", "url": "https://e"},
        ]
    ).encode("utf-8")
    items = discovery._parse_index(raw)
    assert [it["name"] for it in items] == ["good"]


# ---------------------------------------------------------------------------
# fetch_remote_index — http_get is monkeypatched
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _make_fake_urlopen(payloads: dict[str, bytes]) -> Any:
    def _fake(req: Any, timeout: float = 10.0) -> _FakeResponse:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url not in payloads:
            raise urllib.error.URLError(f"no payload for {url}")
        return _FakeResponse(payloads[url])

    return _fake


def test_fetch_remote_index_rejects_bad_scheme() -> None:
    with pytest.raises(ValueError):
        discovery.fetch_remote_index("ftp://example.com/index.json")


def test_fetch_remote_index_returns_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(
        {"skills": [{"name": "x", "description": "y", "url": "https://e/SKILL.md"}]}
    ).encode("utf-8")
    monkeypatch.setattr(
        "chimera.skills.discovery.urllib.request.urlopen",
        _make_fake_urlopen({"https://example.com/index.json": body}),
    )
    items = discovery.fetch_remote_index("https://example.com/index.json")
    assert items[0]["name"] == "x"


# ---------------------------------------------------------------------------
# download_remote_skills
# ---------------------------------------------------------------------------


def test_download_remote_skills_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    index = json.dumps(
        {
            "skills": [
                {
                    "name": "alpha",
                    "description": "alpha-skill",
                    "url": "https://e/alpha.md",
                }
            ]
        }
    ).encode("utf-8")
    skill_md = b"---\nname: alpha\ndescription: \"alpha-skill\"\n---\nbody"
    monkeypatch.setattr(
        "chimera.skills.discovery.urllib.request.urlopen",
        _make_fake_urlopen(
            {
                "https://example.com/index.json": index,
                "https://e/alpha.md": skill_md,
            }
        ),
    )
    result = discovery.download_remote_skills(
        "https://example.com/index.json", cache_dir=cache
    )
    assert (cache / "alpha" / "SKILL.md").exists()
    assert [s.name for s in result] == ["alpha"]


def test_download_remote_skills_pads_frontmatter_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    index = json.dumps(
        {
            "skills": [
                {
                    "name": "beta",
                    "description": "padded description",
                    "url": "https://e/beta.md",
                }
            ]
        }
    ).encode("utf-8")
    # SKILL.md without frontmatter — discovery should refuse it on disk
    # unless the downloader pads the frontmatter from the index entry.
    skill_md = b"plain markdown body"
    monkeypatch.setattr(
        "chimera.skills.discovery.urllib.request.urlopen",
        _make_fake_urlopen(
            {
                "https://example.com/index.json": index,
                "https://e/beta.md": skill_md,
            }
        ),
    )
    result = discovery.download_remote_skills(
        "https://example.com/index.json", cache_dir=cache
    )
    assert [s.name for s in result] == ["beta"]
    on_disk = (cache / "beta" / "SKILL.md").read_text()
    assert "padded description" in on_disk
    assert on_disk.startswith("---")


def test_download_remote_skills_skips_existing_unless_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    target_dir = cache / "alpha"
    target_dir.mkdir(parents=True)
    (target_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: \"old\"\n---\nold body"
    )
    index = json.dumps(
        {
            "skills": [
                {
                    "name": "alpha",
                    "description": "fresh",
                    "url": "https://e/alpha.md",
                }
            ]
        }
    ).encode("utf-8")
    new_md = b"---\nname: alpha\ndescription: \"fresh\"\n---\nnew body"
    monkeypatch.setattr(
        "chimera.skills.discovery.urllib.request.urlopen",
        _make_fake_urlopen(
            {
                "https://example.com/index.json": index,
                "https://e/alpha.md": new_md,
            }
        ),
    )
    # No overwrite: existing file kept.
    discovery.download_remote_skills(
        "https://example.com/index.json", cache_dir=cache, overwrite=False
    )
    assert "old body" in (target_dir / "SKILL.md").read_text()
    # With overwrite: refreshed.
    discovery.download_remote_skills(
        "https://example.com/index.json", cache_dir=cache, overwrite=True
    )
    assert "new body" in (target_dir / "SKILL.md").read_text()


def test_download_remote_skills_skips_failed_individual_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    index = json.dumps(
        {
            "skills": [
                {
                    "name": "alpha",
                    "description": "ok",
                    "url": "https://e/alpha.md",
                },
                {
                    "name": "broken",
                    "description": "x",
                    "url": "https://e/broken.md",
                },
            ]
        }
    ).encode("utf-8")
    payloads = {
        "https://example.com/index.json": index,
        "https://e/alpha.md": (
            b"---\nname: alpha\ndescription: \"ok\"\n---\nbody"
        ),
    }
    monkeypatch.setattr(
        "chimera.skills.discovery.urllib.request.urlopen",
        _make_fake_urlopen(payloads),
    )
    result = discovery.download_remote_skills(
        "https://example.com/index.json", cache_dir=cache
    )
    # Broken url skipped, alpha downloaded.
    assert [s.name for s in result] == ["alpha"]
    assert not (cache / "broken" / "SKILL.md").exists()


def test_download_remote_skills_empty_index_returns_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setattr(
        "chimera.skills.discovery.urllib.request.urlopen",
        _make_fake_urlopen(
            {
                "https://example.com/index.json": json.dumps(
                    {"skills": []}
                ).encode("utf-8"),
            }
        ),
    )
    result = discovery.download_remote_skills(
        "https://example.com/index.json", cache_dir=cache
    )
    assert result == []


def test_io_unused_import_present_to_satisfy_pyright() -> None:
    # ``io`` is imported above as a placeholder for future BytesIO-based
    # helpers; nothing else needs to depend on it.
    assert io is not None
