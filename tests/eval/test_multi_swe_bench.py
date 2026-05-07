"""Tests for MultiSWE-bench multi-language adapter."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import pytest

from chimera.eval.benchmarks.multi_swe_bench import (
    SUPPORTED_LANGUAGES,
    MultiSWEBench,
    MultiSWEBenchInstance,
)
from chimera.eval.benchmarks.runners import (
    GO_RUNNER,
    JAVA_RUNNER,
    JAVASCRIPT_RUNNER,
    PYTHON_RUNNER,
    RUST_RUNNER,
    LanguageRunner,
    get_runner,
)
from chimera.eval.benchmarks.runners.base import RunnerResult, SkipReason

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeCommandResult:
    """Mimics the surface of a real env command result."""

    success: bool = True
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


class FakeEnv:
    """Fake execution environment that records every command it sees.

    ``responses`` maps command-prefixes to :class:`FakeCommandResult`. The
    first matching prefix wins (so ``"pytest"`` matches ``"pytest -x"``).
    Unmapped commands default to a successful no-op.
    """

    def __init__(
        self,
        responses: dict[str, FakeCommandResult] | None = None,
        toolchains: set[str] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.toolchains = (
            toolchains
            if toolchains is not None
            else {"python", "mvn", "go", "node", "cargo"}
        )
        self.commands: list[str] = []
        self.files: dict[str, str] = {}

    def write_file(self, path: str, contents: str) -> None:
        self.files[path] = contents

    def run_command(self, command: str) -> FakeCommandResult:
        self.commands.append(command)
        # Toolchain probes
        for probe, name in (
            ("python --version", "python"),
            ("mvn --version", "mvn"),
            ("go version", "go"),
            ("node --version", "node"),
            ("cargo --version", "cargo"),
        ):
            if command == probe:
                if name in self.toolchains:
                    return FakeCommandResult(success=True, exit_code=0)
                return FakeCommandResult(success=False, exit_code=127)

        # Explicit overrides
        for prefix, result in self.responses.items():
            if command.startswith(prefix):
                return result

        return FakeCommandResult(success=True, exit_code=0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIVE_LANG_INSTANCES = [
    {
        "instance_id": "py__demo__1",
        "repo": "demo/py",
        "base_commit": "p001",
        "problem_statement": "Fix index bug in utils.py",
        "language": "python",
        "test_patch": "diff --git a/test_x.py b/test_x.py\n",
    },
    {
        "instance_id": "java__demo__1",
        "repo": "demo/java",
        "base_commit": "j001",
        "problem_statement": "NPE in JsonParser",
        "language": "java",
        "test_patch": "",
    },
    {
        "instance_id": "go__demo__1",
        "repo": "demo/go",
        "base_commit": "g001",
        "problem_statement": "Race in worker pool",
        "language": "go",
        "test_patch": "",
    },
    {
        "instance_id": "js__demo__1",
        "repo": "demo/js",
        "base_commit": "n001",
        "problem_statement": "Off-by-one in pagination",
        "language": "javascript",
        "test_patch": "",
    },
    {
        "instance_id": "rust__demo__1",
        "repo": "demo/rust",
        "base_commit": "r001",
        "problem_statement": "Lifetime mismatch in iter()",
        "language": "rust",
        "test_patch": "",
    },
]


@pytest.fixture
def five_lang_dataset(tmp_path):
    path = tmp_path / "multi.json"
    path.write_text(json.dumps(FIVE_LANG_INSTANCES))
    yield str(path)
    if path.exists():
        os.unlink(path)


@pytest.fixture
def jsonl_dataset(tmp_path):
    path = tmp_path / "multi.jsonl"
    with open(path, "w") as f:
        for item in FIVE_LANG_INSTANCES:
            f.write(json.dumps(item) + "\n")
    yield str(path)
    if path.exists():
        os.unlink(path)


# ---------------------------------------------------------------------------
# Loading & filtering
# ---------------------------------------------------------------------------


class TestLoading:
    def test_load_json_array(self, five_lang_dataset):
        bench = MultiSWEBench(dataset_path=five_lang_dataset)
        assert len(bench.tasks()) == 5

    def test_load_jsonl(self, jsonl_dataset):
        bench = MultiSWEBench(dataset_path=jsonl_dataset)
        assert len(bench.tasks()) == 5

    def test_language_filter(self, five_lang_dataset):
        bench = MultiSWEBench(dataset_path=five_lang_dataset, language="java")
        assert len(bench.tasks()) == 1
        assert bench.tasks()[0]["language"] == "java"

    def test_limit(self, five_lang_dataset):
        bench = MultiSWEBench(dataset_path=five_lang_dataset, limit=2)
        assert len(bench.tasks()) == 2

    def test_unsupported_language_filter_rejected(self):
        with pytest.raises(ValueError):
            MultiSWEBench(language="cobol")

    def test_missing_dataset_raises(self):
        with pytest.raises(FileNotFoundError):
            MultiSWEBench(dataset_path="/nonexistent/multi.json")

    def test_skip_unsupported_languages_default(self, tmp_path):
        path = tmp_path / "mixed.json"
        path.write_text(
            json.dumps(
                [
                    {"instance_id": "a", "language": "python", "problem_statement": ""},
                    {"instance_id": "b", "language": "cobol", "problem_statement": ""},
                ]
            )
        )
        bench = MultiSWEBench(dataset_path=str(path))
        assert len(bench.tasks()) == 1

    def test_keep_unsupported_languages_when_disabled(self, tmp_path):
        path = tmp_path / "mixed.json"
        path.write_text(
            json.dumps(
                [
                    {"instance_id": "a", "language": "python", "problem_statement": ""},
                    {"instance_id": "b", "language": "cobol", "problem_statement": ""},
                ]
            )
        )
        bench = MultiSWEBench(
            dataset_path=str(path),
            skip_unsupported=False,
        )
        assert len(bench.tasks()) == 2

    def test_language_aliases_normalize(self, tmp_path):
        path = tmp_path / "alias.json"
        path.write_text(
            json.dumps(
                [
                    {"instance_id": "a", "language": "JS", "problem_statement": ""},
                    {"instance_id": "b", "language": "Golang", "problem_statement": ""},
                    {"instance_id": "c", "language": "TS", "problem_statement": ""},
                ]
            )
        )
        bench = MultiSWEBench(dataset_path=str(path))
        langs = sorted(t["language"] for t in bench.tasks())
        assert langs == ["go", "javascript", "typescript"]


class TestShape:
    def test_name_default(self):
        assert MultiSWEBench().name() == "multi-swe-bench"

    def test_name_with_language(self):
        bench = MultiSWEBench(language="rust")
        assert bench.name() == "multi-swe-bench-rust"

    def test_supported_languages_class_method(self):
        assert set(MultiSWEBench.supported_languages()) == set(SUPPORTED_LANGUAGES)

    def test_add_instance(self):
        bench = MultiSWEBench()
        bench.add_instance(
            MultiSWEBenchInstance(
                instance_id="x",
                repo="a/b",
                base_commit="c",
                problem_statement="p",
                language="python",
            )
        )
        assert len(bench.tasks()) == 1

    def test_instance_to_task_includes_language(self):
        inst = MultiSWEBenchInstance(
            instance_id="i",
            repo="r/r",
            base_commit="c1",
            problem_statement="desc",
            language="rust",
            test_patch="patch",
        )
        task = inst.to_task()
        assert task["language"] == "rust"
        assert task["test_patch"] == "patch"

    def test_language_breakdown(self, five_lang_dataset):
        bench = MultiSWEBench(dataset_path=five_lang_dataset)
        breakdown = bench.language_breakdown()
        assert breakdown == {
            "python": 1,
            "java": 1,
            "go": 1,
            "javascript": 1,
            "rust": 1,
        }

    def test_evaluate_without_env_returns_false(self):
        bench = MultiSWEBench()
        assert bench.evaluate({"language": "python"}, "output", env=None) is False


# ---------------------------------------------------------------------------
# Runner registry
# ---------------------------------------------------------------------------


class TestRunnerRegistry:
    @pytest.mark.parametrize(
        "language, expected",
        [
            ("python", PYTHON_RUNNER),
            ("Python", PYTHON_RUNNER),
            ("PYTHON", PYTHON_RUNNER),
            ("java", JAVA_RUNNER),
            ("go", GO_RUNNER),
            ("Golang", None),  # only the canonical name registers; aliases are
            # normalized in MultiSWEBench, not the registry directly.
            ("javascript", JAVASCRIPT_RUNNER),
            ("js", JAVASCRIPT_RUNNER),
            ("typescript", JAVASCRIPT_RUNNER),
            ("ts", JAVASCRIPT_RUNNER),
            ("rust", RUST_RUNNER),
            ("cobol", None),
            ("", None),
        ],
    )
    def test_get_runner(self, language: str, expected: LanguageRunner | None):
        assert get_runner(language) is expected

    def test_runner_test_commands(self):
        assert PYTHON_RUNNER.test_command.startswith("pytest")
        assert JAVA_RUNNER.test_command.startswith("mvn")
        assert GO_RUNNER.test_command.startswith("go test")
        assert JAVASCRIPT_RUNNER.test_command.startswith("npm test")
        assert RUST_RUNNER.test_command.startswith("cargo test")


# ---------------------------------------------------------------------------
# Dispatch — the main contract: route to the right runner per language
# ---------------------------------------------------------------------------


class TestRunnerSelection:
    """Five mocked instances (one per language) verifying runner selection."""

    @pytest.mark.parametrize(
        "instance, expected_command_prefix",
        [
            (FIVE_LANG_INSTANCES[0], "pytest"),
            (FIVE_LANG_INSTANCES[1], "mvn"),
            (FIVE_LANG_INSTANCES[2], "go test"),
            (FIVE_LANG_INSTANCES[3], "npm test"),
            (FIVE_LANG_INSTANCES[4], "cargo test"),
        ],
    )
    def test_dispatches_to_correct_runner(
        self, instance: dict, expected_command_prefix: str, five_lang_dataset
    ):
        bench = MultiSWEBench(dataset_path=five_lang_dataset)
        env = FakeEnv()
        task = next(t for t in bench.tasks() if t["id"] == instance["instance_id"])

        passed = bench.evaluate(task, "irrelevant", env=env)

        assert passed is True
        # Verify the language test command was actually executed
        assert any(c.startswith(expected_command_prefix) for c in env.commands), (
            f"Expected a command starting with {expected_command_prefix!r}; "
            f"saw: {env.commands}"
        )

    def test_skip_when_toolchain_missing(self):
        bench = MultiSWEBench()
        bench.add_instance(
            MultiSWEBenchInstance(
                instance_id="java__missing__1",
                repo="x/y",
                base_commit="c",
                problem_statement="p",
                language="java",
            )
        )
        env = FakeEnv(toolchains=set())  # no toolchain installed
        task = bench.tasks()[0]
        assert bench.evaluate(task, "", env=env) is False
        # Skip reason must be recorded
        skips = bench.last_skip_reasons
        assert len(skips) == 1
        assert skips[0][1] == "java"
        assert skips[0][2] == SkipReason.TOOLCHAIN_MISSING.value

    def test_skip_when_test_patch_fails(self):
        bench = MultiSWEBench()
        bench.add_instance(
            MultiSWEBenchInstance(
                instance_id="py__patch__1",
                repo="x/y",
                base_commit="c",
                problem_statement="p",
                language="python",
                test_patch="diff --git a/x b/x\n",
            )
        )
        env = FakeEnv(
            responses={"git apply": FakeCommandResult(success=False, exit_code=1)},
        )
        task = bench.tasks()[0]
        assert bench.evaluate(task, "", env=env) is False
        skips = bench.last_skip_reasons
        assert skips and skips[0][2] == SkipReason.PATCH_FAILED.value

    def test_test_command_failure_is_not_a_skip(self):
        bench = MultiSWEBench()
        bench.add_instance(
            MultiSWEBenchInstance(
                instance_id="py__fail__1",
                repo="x/y",
                base_commit="c",
                problem_statement="p",
                language="python",
            )
        )
        env = FakeEnv(
            responses={"pytest": FakeCommandResult(success=False, exit_code=1)},
        )
        task = bench.tasks()[0]
        assert bench.evaluate(task, "", env=env) is False
        # Genuine test failure ≠ skip
        assert bench.last_skip_reasons == []

    def test_evaluate_detailed_returns_runner_result(self):
        bench = MultiSWEBench()
        bench.add_instance(
            MultiSWEBenchInstance(
                instance_id="rust__d__1",
                repo="x/y",
                base_commit="c",
                problem_statement="p",
                language="rust",
            )
        )
        env = FakeEnv(
            responses={
                "cargo test": FakeCommandResult(
                    success=True, exit_code=0, stdout="tests ok"
                )
            }
        )
        result = bench.evaluate_detailed(bench.tasks()[0], env=env)
        assert isinstance(result, RunnerResult)
        assert result.passed is True
        assert "tests ok" in result.stdout

    def test_unknown_language_records_skip(self):
        bench = MultiSWEBench(skip_unsupported=False)
        bench.add_instance(
            MultiSWEBenchInstance(
                instance_id="cobol__1",
                repo="x/y",
                base_commit="c",
                problem_statement="p",
                language="cobol",
            )
        )
        env = FakeEnv()
        assert bench.evaluate(bench.tasks()[0], "", env=env) is False
        assert bench.last_skip_reasons[0][1] == "cobol"

    def test_reset_skip_log(self):
        bench = MultiSWEBench()
        bench.add_instance(
            MultiSWEBenchInstance(
                instance_id="py__1",
                repo="x/y",
                base_commit="c",
                problem_statement="p",
                language="python",
            )
        )
        env = FakeEnv(toolchains=set())
        bench.evaluate(bench.tasks()[0], "", env=env)
        assert len(bench.last_skip_reasons) == 1
        bench.reset_skip_log()
        assert bench.last_skip_reasons == []
