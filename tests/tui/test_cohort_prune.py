"""Cohort auto-pruning retention policy (T13 / #173).

Pins the data-preserving contract: OFF by default (no config prunes nothing),
a retain floor always keeps the newest N, an age limit drops only what is old
enough, and the cohort being run/resumed is never deleted.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("rich")  # chimera.tui.cohort pulls the tui extra; CI installs none

from chimera.tui.cohort import CohortRetention, prune_cohorts  # noqa: E402


def _cohort_root(tmp_path):
    r = tmp_path / "cohorts"
    r.mkdir(exist_ok=True)
    return r


def _make_cohort(root, cohort_id, *, created=None):
    d = root / cohort_id
    d.mkdir(parents=True)
    manifest = {"cohort_id": cohort_id}
    if created is not None:
        manifest["created_at"] = created.isoformat()
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _ids(root):
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# -- default-safe: nothing prunes without a policy ---------------------------
def test_inactive_policy_prunes_nothing(tmp_path):
    root = _cohort_root(tmp_path)
    for i in range(5):
        _make_cohort(root, f"2026010{i}-000000-aaaa")
    assert prune_cohorts(root=root, retention=CohortRetention()) == []
    assert prune_cohorts(root=root, retention=None) == []
    assert len(_ids(root)) == 5


def test_missing_root_is_noop(tmp_path):
    assert prune_cohorts(root=tmp_path / "nope",
                         retention=CohortRetention(retain=1)) == []


# -- retain floor: keep newest N ---------------------------------------------
def test_retain_keeps_newest_n(tmp_path):
    root = _cohort_root(tmp_path)
    # ids sort lexically == chronologically; make 5, retain 2.
    for i in range(5):
        _make_cohort(root, f"2026010{i}-000000-aaaa")
    removed = prune_cohorts(root=root, retention=CohortRetention(retain=2))
    assert set(removed) == {"20260100-000000-aaaa", "20260101-000000-aaaa",
                            "20260102-000000-aaaa"}
    assert _ids(root) == ["20260103-000000-aaaa", "20260104-000000-aaaa"]


# -- age limit ---------------------------------------------------------------
def test_max_age_drops_only_old(tmp_path):
    root = _cohort_root(tmp_path)
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    _make_cohort(root, "20260101-000000-old", created=now - timedelta(days=40))
    _make_cohort(root, "20260128-000000-new", created=now - timedelta(days=4))
    removed = prune_cohorts(root=root,
                            retention=CohortRetention(max_age_days=30), now=now)
    assert removed == ["20260101-000000-old"]
    assert _ids(root) == ["20260128-000000-new"]


# -- the live cohort is never touched ----------------------------------------
def test_exclude_protects_running_cohort(tmp_path):
    root = _cohort_root(tmp_path)
    for i in range(5):
        _make_cohort(root, f"2026010{i}-000000-aaaa")
    # retain=1 would normally delete all but the newest; exclude the OLDEST
    # (as if it were the one being resumed) — it must survive anyway.
    oldest = "20260100-000000-aaaa"
    removed = prune_cohorts(root=root, retention=CohortRetention(retain=1),
                            exclude=(oldest,))
    assert oldest not in removed
    assert oldest in _ids(root)


# -- only manifest-bearing dirs are considered -------------------------------
def test_non_cohort_dirs_are_ignored(tmp_path):
    root = _cohort_root(tmp_path)
    _make_cohort(root, "20260101-000000-aaaa")
    _make_cohort(root, "20260102-000000-aaaa")
    (root / "not-a-cohort").mkdir()  # no manifest.json
    (root / "stray.txt").write_text("x")
    prune_cohorts(root=root, retention=CohortRetention(retain=1))
    assert (root / "not-a-cohort").is_dir()  # untouched
    assert (root / "stray.txt").is_file()


# -- one retention implementation, not two (M2) ------------------------------
def test_the_pruner_routes_through_the_shared_selector(tmp_path, monkeypatch):
    """Cohorts must not keep a private copy of the keep/drop rules.

    The point of M2's engine is that ``chimera gc`` and this pruner cannot
    drift apart, so the keep/drop decision has to be *made* in the shared
    selector rather than merely resemble it. The spy delegates to the real
    implementation, so this asserts the call happens without freezing the
    outcome. (``cohort.py`` imports the seam inside the function, which is what
    makes it patchable here.)
    """
    from chimera.config import storage as storage_engine

    root = _cohort_root(tmp_path)
    for i in range(4):
        _make_cohort(root, f"2026010{i}-000000-aaaa")

    real = storage_engine.select_for_prune
    seen: list[dict] = []

    def spy(entries, retention, **kwargs):
        seen.append({"n": len(entries), "store": kwargs.get("store")})
        return real(entries, retention, **kwargs)

    monkeypatch.setattr(storage_engine, "select_for_prune", spy)
    removed = prune_cohorts(root=root, retention=CohortRetention(retain=1))

    assert seen == [{"n": 4, "store": "cohorts"}]
    assert len(removed) == 3
    assert _ids(root) == ["20260103-000000-aaaa"]


def test_the_pruner_inherits_the_registry_guard(tmp_path, monkeypatch):
    """Cohort deletions are revalidated against the registry like any other.

    Flipping the ``cohorts`` row to ``prunable=False`` must stop the pruner
    dead — the guarantee is structural, not a courtesy the caller extends.
    """
    import dataclasses

    from chimera.config import paths

    root = _cohort_root(tmp_path)
    for i in range(4):
        _make_cohort(root, f"2026010{i}-000000-aaaa")

    locked = dataclasses.replace(paths.get_store("cohorts"), prunable=False)
    monkeypatch.setattr(paths, "_BY_NAME", {**paths._BY_NAME, "cohorts": locked})

    with pytest.raises(ValueError, match="prunable=False"):
        prune_cohorts(root=root, retention=CohortRetention(retain=1))
    assert len(_ids(root)) == 4


# -- config parsing ----------------------------------------------------------
def test_from_tui_config_parsing():
    assert CohortRetention.from_tui_config(None) == CohortRetention()
    assert CohortRetention.from_tui_config({}) == CohortRetention()
    got = CohortRetention.from_tui_config(
        {"cohorts": {"retain": 20, "max-age-days": 30}})
    assert got == CohortRetention(retain=20, max_age_days=30.0)
    # underscore alias + non-positive values disable the knob
    assert CohortRetention.from_tui_config(
        {"cohorts": {"retain": 0, "max_age_days": 15}}
    ) == CohortRetention(retain=None, max_age_days=15.0)
    assert not CohortRetention.from_tui_config({"cohorts": {}}).active
