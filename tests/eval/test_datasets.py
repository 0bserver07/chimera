"""Tests for dataset staging (``chimera bench-fetch``) — no real network."""

from __future__ import annotations

import io
import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

import chimera.eval.datasets as ds


class _FakeResponse(io.BytesIO):
    """Minimal context-manager response like urlopen's."""

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


@pytest.fixture()
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIMERA_DATASETS_DIR", str(tmp_path))
    return tmp_path


def test_staged_path_none_when_absent(staging: Path) -> None:
    assert ds.staged_path("mbpp") is None
    assert ds.staged_path("no-such-bench") is None


def test_fetch_url_writes_file(staging: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps([{"task_id": 1, "prompt": "p", "test_list": []}]).encode()
    monkeypatch.setattr(ds, "_urlopen", lambda url, timeout=0: _FakeResponse(payload))

    path = ds.fetch("mbpp")

    assert path.exists()
    assert json.loads(path.read_text())[0]["task_id"] == 1
    # Cached on second call (fetcher not re-invoked): poison the network.
    monkeypatch.setattr(ds, "_urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert ds.fetch("mbpp") == path
    # staged_path now resolves, including via alias-free canonical name.
    assert ds.staged_path("mbpp") == path


def test_fetch_hf_rows_paginates(staging: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(url: str, timeout: int = 0) -> _FakeResponse:
        offset = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["offset"][0])
        if offset == 0:
            rows = [{"row": {"instance_id": f"i{n}"}} for n in range(100)]
        else:
            rows = [{"row": {"instance_id": f"i{100 + n}"}} for n in range(3)]
        return _FakeResponse(json.dumps({"rows": rows}).encode())

    monkeypatch.setattr(ds, "_urlopen", fake_urlopen)

    path = ds.fetch("swe-bench")

    lines = path.read_text().splitlines()
    assert len(lines) == 103
    assert json.loads(lines[0])["instance_id"] == "i0"
    assert json.loads(lines[-1])["instance_id"] == "i102"


def test_aliases_resolve_to_same_spec(staging: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ds, "_urlopen", lambda url, timeout=0: _FakeResponse(json.dumps({"rows": []}).encode())
    )
    path = ds.fetch("swebench")  # alias
    assert ds.staged_path("swe-bench-lite") == path  # another alias, same spec


def test_unknown_name_raises_with_available_list(staging: Path) -> None:
    with pytest.raises(ValueError, match="Fetchable:"):
        ds.fetch("definitely-not-a-bench")


def test_load_benchmark_autodiscovers_staged_dataset(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged file makes `chimera bench mbpp` work with no --dataset flag."""
    tasks = [
        {"task_id": 7, "prompt": "write add", "code": "def add(a,b): return a+b", "test_list": []}
    ]
    payload = json.dumps(tasks).encode()
    monkeypatch.setattr(ds, "_urlopen", lambda url, timeout=0: _FakeResponse(payload))
    ds.fetch("mbpp")

    from chimera.cli.main import _load_benchmark

    bench = _load_benchmark("mbpp")
    assert len(bench.tasks()) == 1
    assert bench.tasks()[0]["task_id"] == 7
