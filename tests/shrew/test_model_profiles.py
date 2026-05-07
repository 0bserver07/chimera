"""Tests for chimera.shrew.model_profiles — settings.json loader.

Five groups:

1. :func:`load_settings` — file present / absent / malformed.
2. :func:`merge_profiles` — shallow merge semantics.
3. :func:`resolve_profile` — defaults → model → benchmark cascade.
4. :class:`ModelProfile.as_dict` — round-trip with extras.
5. Type coercion edge cases (string-int, bool-as-numeric, ``None``).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.shrew.model_profiles import (
    PROFILE_DEFAULTS,
    PROFILE_KEYS,
    ModelProfile,
    ModelProfileError,
    ModelProfileSettings,
    default_settings_path,
    load_settings,
    merge_profiles,
    resolve_profile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Path:
    """Yield a fresh settings.json path under a tmp_path."""
    return tmp_path / "settings.json"


def _write_settings(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. load_settings
# ---------------------------------------------------------------------------


class TestLoadSettings:
    def test_missing_file_returns_defaults(self, tmp_settings: Path) -> None:
        settings = load_settings(tmp_settings)
        assert isinstance(settings, ModelProfileSettings)
        assert settings.profiles == {}
        assert settings.default_profile.max_tokens == PROFILE_DEFAULTS["max_tokens"]

    def test_load_basic(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {
            "default_model_profile": {"temperature": 0.5},
            "model_profiles": {
                "qwen-9b": {"max_tokens": 2048},
            },
        })
        settings = load_settings(tmp_settings)
        assert settings.default_profile.temperature == 0.5
        assert "qwen-9b" in settings.profiles
        assert settings.profiles["qwen-9b"]["max_tokens"] == 2048

    def test_load_with_benchmark_overrides(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {
            "model_profiles": {
                "qwen-35b": {
                    "temperature": 0.3,
                    "benchmark_overrides": {
                        "terminal_bench": {"temperature": 0.2},
                    },
                },
            },
        })
        settings = load_settings(tmp_settings)
        assert "benchmark_overrides" in settings.profiles["qwen-35b"]

    def test_invalid_json_raises(self, tmp_settings: Path) -> None:
        tmp_settings.write_text("{not real json", encoding="utf-8")
        with pytest.raises(ModelProfileError, match="not valid JSON"):
            load_settings(tmp_settings)

    def test_top_level_must_be_object(self, tmp_settings: Path) -> None:
        tmp_settings.write_text("[]", encoding="utf-8")
        with pytest.raises(ModelProfileError, match="must be a JSON object"):
            load_settings(tmp_settings)

    def test_default_profile_must_be_object(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {"default_model_profile": "not a dict"})
        with pytest.raises(ModelProfileError, match="default_model_profile"):
            load_settings(tmp_settings)

    def test_model_profiles_must_be_object(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {"model_profiles": []})
        with pytest.raises(ModelProfileError, match="model_profiles"):
            load_settings(tmp_settings)

    def test_per_model_value_must_be_object(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {"model_profiles": {"x": "string"}})
        with pytest.raises(ModelProfileError, match="must be an object"):
            load_settings(tmp_settings)

    def test_default_settings_path_uses_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        path = default_settings_path()
        assert path == tmp_path / ".chimera" / "shrew" / "settings.json"


# ---------------------------------------------------------------------------
# 2. merge_profiles
# ---------------------------------------------------------------------------


class TestMergeProfiles:
    def test_overlay_overrides_base(self) -> None:
        base = {"a": 1, "b": 2}
        overlay = {"b": 99, "c": 3}
        out = merge_profiles(base, overlay)
        assert out == {"a": 1, "b": 99, "c": 3}

    def test_inputs_unmodified(self) -> None:
        base = {"a": 1}
        overlay = {"a": 2}
        merge_profiles(base, overlay)
        assert base == {"a": 1}
        assert overlay == {"a": 2}

    def test_none_overlay_value_skipped(self) -> None:
        # ``"key": null`` should fall through to the base.
        base = {"a": 1}
        out = merge_profiles(base, {"a": None})
        assert out == {"a": 1}

    def test_empty_overlay_returns_copy(self) -> None:
        base = {"a": 1}
        out = merge_profiles(base, {})
        assert out == base
        assert out is not base


# ---------------------------------------------------------------------------
# 3. resolve_profile cascade
# ---------------------------------------------------------------------------


class TestResolveProfile:
    def _build_settings(
        self,
        default: dict | None = None,
        profiles: dict | None = None,
    ) -> ModelProfileSettings:
        from chimera.shrew.model_profiles import _profile_from_dict

        merged_default = merge_profiles(PROFILE_DEFAULTS, default or {})
        return ModelProfileSettings(
            default_profile=_profile_from_dict("", merged_default),
            profiles=profiles or {},
        )

    def test_unknown_model_falls_back_to_default(self) -> None:
        settings = self._build_settings(default={"temperature": 0.7})
        profile = resolve_profile(settings, "unknown-model")
        assert profile.model_id == "unknown-model"
        assert profile.temperature == 0.7

    def test_per_model_overrides_default(self) -> None:
        settings = self._build_settings(
            default={"temperature": 0.7},
            profiles={"qwen-9b": {"temperature": 0.3, "max_tokens": 8192}},
        )
        profile = resolve_profile(settings, "qwen-9b")
        assert profile.temperature == 0.3
        assert profile.max_tokens == 8192

    def test_benchmark_override_layered(self) -> None:
        settings = self._build_settings(
            profiles={
                "qwen-35b": {
                    "temperature": 0.3,
                    "max_tokens": 4096,
                    "benchmark_overrides": {
                        "terminal_bench": {"temperature": 0.2, "max_turns": 40},
                    },
                },
            },
        )
        profile = resolve_profile(settings, "qwen-35b", benchmark="terminal_bench")
        assert profile.temperature == 0.2
        assert profile.max_turns == 40
        # max_tokens not overridden by benchmark — falls through.
        assert profile.max_tokens == 4096

    def test_benchmark_override_only_for_named(self) -> None:
        settings = self._build_settings(
            profiles={
                "qwen-35b": {
                    "temperature": 0.3,
                    "benchmark_overrides": {"terminal_bench": {"temperature": 0.2}},
                },
            },
        )
        # benchmark="gaia" — no override block; falls back to per-model.
        profile = resolve_profile(settings, "qwen-35b", benchmark="gaia")
        assert profile.temperature == 0.3

    def test_extras_pass_through(self) -> None:
        settings = self._build_settings(
            profiles={"x": {"custom_flag": True, "experiment_name": "foo"}},
        )
        profile = resolve_profile(settings, "x")
        assert profile.extras == {"custom_flag": True, "experiment_name": "foo"}


# ---------------------------------------------------------------------------
# 4. ModelProfile.as_dict
# ---------------------------------------------------------------------------


class TestModelProfileAsDict:
    def test_basic_round_trip(self) -> None:
        profile = ModelProfile(
            model_id="qwen-9b",
            max_tokens=8192,
            temperature=0.4,
        )
        d = profile.as_dict()
        assert d["model_id"] == "qwen-9b"
        assert d["max_tokens"] == 8192
        assert d["temperature"] == 0.4

    def test_extras_included(self) -> None:
        profile = ModelProfile(model_id="x", extras={"foo": "bar"})
        d = profile.as_dict()
        assert d["foo"] == "bar"

    def test_canonical_keys_listed(self) -> None:
        # Sanity: PROFILE_KEYS lines up with ModelProfile fields.
        profile = ModelProfile()
        d = profile.as_dict()
        for key in PROFILE_KEYS:
            assert key in d


# ---------------------------------------------------------------------------
# 5. Coercion edge cases
# ---------------------------------------------------------------------------


class TestCoercion:
    def test_string_int_coerced(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {
            "model_profiles": {"x": {"max_tokens": "4096"}},
        })
        settings = load_settings(tmp_settings)
        profile = resolve_profile(settings, "x")
        assert profile.max_tokens == 4096

    def test_bool_int_falls_back(self, tmp_settings: Path) -> None:
        # JSON ``true`` for max_tokens — bool subclass of int, but we
        # treat it as fallback to avoid surprising 1-token caps.
        _write_settings(tmp_settings, {
            "model_profiles": {"x": {"max_tokens": True}},
        })
        settings = load_settings(tmp_settings)
        profile = resolve_profile(settings, "x")
        assert profile.max_tokens == PROFILE_DEFAULTS["max_tokens"]

    def test_float_for_temperature(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {
            "model_profiles": {"x": {"temperature": "0.42"}},
        })
        settings = load_settings(tmp_settings)
        profile = resolve_profile(settings, "x")
        assert profile.temperature == pytest.approx(0.42)

    def test_garbage_value_falls_back(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {
            "model_profiles": {"x": {"max_tokens": "not-a-number"}},
        })
        settings = load_settings(tmp_settings)
        profile = resolve_profile(settings, "x")
        assert profile.max_tokens == PROFILE_DEFAULTS["max_tokens"]

    def test_null_system_prompt_falls_back(self, tmp_settings: Path) -> None:
        _write_settings(tmp_settings, {
            "model_profiles": {"x": {"system_prompt_prefix": None}},
        })
        settings = load_settings(tmp_settings)
        profile = resolve_profile(settings, "x")
        # Null falls through to default empty string.
        assert profile.system_prompt_prefix == ""
