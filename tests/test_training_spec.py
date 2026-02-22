"""Tests for chimera.training — Spec, Layer, and Architecture."""

from __future__ import annotations

import pytest

from chimera.training import Spec, Layer, Architecture


# ── Spec ────────────────────────────────────────────────────────────────


class TestSpecFromString:
    def test_spec_from_string(self):
        spec = Spec.from_string("Build a CLI calculator")
        assert spec.text == "Build a CLI calculator"
        assert spec.tests_dir is None
        assert spec.source_file is None


class TestSpecFromFile:
    def test_spec_from_file(self, tmp_path):
        p = tmp_path / "spec.md"
        p.write_text("# My Spec\nBuild something great.")
        spec = Spec.from_file(str(p))
        assert spec.text == "# My Spec\nBuild something great."
        assert spec.source_file == str(p)
        assert spec.tests_dir is None


class TestSpecFromTests:
    def test_spec_from_tests(self):
        spec = Spec.from_tests("tests/unit")
        assert spec.tests_dir == "tests/unit"
        assert "tests/unit" in spec.text

    def test_spec_from_tests_with_description(self):
        spec = Spec.from_tests("tests/unit", description="All unit tests must pass")
        assert spec.text == "All unit tests must pass"
        assert spec.tests_dir == "tests/unit"


class TestSpecToPrompt:
    def test_spec_to_prompt(self):
        spec = Spec(text="Build a web server")
        prompt = spec.to_prompt()
        assert prompt == "Build a web server"

    def test_spec_to_prompt_with_tests(self):
        spec = Spec(text="Build a web server", tests_dir="tests/integration")
        prompt = spec.to_prompt()
        assert "Build a web server" in prompt
        assert "tests/integration" in prompt


# ── Layer ───────────────────────────────────────────────────────────────


class TestLayerLevel:
    def test_layer_abstract(self):
        layer = Layer(name="core")
        assert layer.level == "abstract"

    def test_layer_guided(self):
        layer = Layer(name="core", description="The core engine")
        assert layer.level == "guided"

    def test_layer_guided_by_constraints(self):
        layer = Layer(name="core", constraints=["Must be async"])
        assert layer.level == "guided"

    def test_layer_templated(self):
        layer = Layer(name="core", template="templates/core.py")
        assert layer.level == "templated"

    def test_layer_frozen(self):
        layer = Layer(name="core", frozen=True)
        assert layer.level == "frozen"

    def test_frozen_overrides_template(self):
        """Frozen takes precedence over template."""
        layer = Layer(name="core", template="t.py", frozen=True)
        assert layer.level == "frozen"


# ── Architecture ────────────────────────────────────────────────────────


class TestArchitectureSimple:
    def test_architecture_simple(self):
        arch = Architecture(layers=[
            Layer(name="core"),
            Layer(name="cli"),
        ])
        assert len(arch.layers) == 2


class TestArchitectureWithDeps:
    def test_architecture_with_deps(self):
        arch = Architecture(layers=[
            Layer(name="core"),
            Layer(name="cli", depends_on=["core"]),
        ])
        assert arch.get_layer("cli").depends_on == ["core"]


class TestArchitectureBuildOrder:
    def test_architecture_build_order(self):
        arch = Architecture(layers=[
            Layer(name="cli", depends_on=["core"]),
            Layer(name="core"),
            Layer(name="api", depends_on=["core"]),
        ])
        order = arch.build_order()
        names = [l.name for l in order]
        assert names.index("core") < names.index("cli")
        assert names.index("core") < names.index("api")

    def test_architecture_build_order_chain(self):
        """A -> B -> C should produce C, B, A."""
        arch = Architecture(layers=[
            Layer(name="A", depends_on=["B"]),
            Layer(name="B", depends_on=["C"]),
            Layer(name="C"),
        ])
        order = arch.build_order()
        names = [l.name for l in order]
        assert names.index("C") < names.index("B") < names.index("A")


class TestArchitectureUnknownDep:
    def test_architecture_unknown_dep(self):
        with pytest.raises(ValueError, match="unknown layer"):
            Architecture(layers=[
                Layer(name="cli", depends_on=["nonexistent"]),
            ])


class TestArchitectureCycleDetection:
    def test_architecture_cycle_detection(self):
        with pytest.raises(ValueError, match="circular"):
            Architecture(layers=[
                Layer(name="A", depends_on=["B"]),
                Layer(name="B", depends_on=["A"]),
            ])

    def test_architecture_self_cycle(self):
        with pytest.raises(ValueError, match="circular"):
            Architecture(layers=[
                Layer(name="A", depends_on=["A"]),
            ])


class TestArchitectureGetLayer:
    def test_architecture_get_layer(self):
        arch = Architecture(layers=[
            Layer(name="core"),
            Layer(name="cli"),
        ])
        layer = arch.get_layer("core")
        assert layer.name == "core"

    def test_architecture_get_layer_missing(self):
        arch = Architecture(layers=[
            Layer(name="core"),
        ])
        with pytest.raises(KeyError, match="No layer named"):
            arch.get_layer("missing")
