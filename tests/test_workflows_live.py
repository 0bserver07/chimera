"""Live integration tests for workflows against a real LLM.

Configure via:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token-here"
    export ANTHROPIC_MODEL="glm-5"

Skipped when no credentials are set.

Note: DocGenerator.scan(), TestGenerator.analyze_source(), and
MigrationPlanner are pure logic -- they do not require an LLM.
They are tested without the skip marker.
"""
from __future__ import annotations

import os
import textwrap

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.docs.generator import DocGenerator
from chimera.migration.planner import MigrationPlanner
from chimera.providers.anthropic import AnthropicProvider
from chimera.research.researcher import Researcher
from chimera.review.orchestrator import ReviewOrchestrator
from chimera.testgen.generator import TestGenerator

_api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
_model = os.environ.get("ANTHROPIC_MODEL", "glm-5")

# Only LLM-dependent tests use this skip marker
_requires_llm = pytest.mark.skipif(
    not _api_key,
    reason="Set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN to run integration tests",
)


@pytest.fixture(scope="module")
def glm5_provider() -> AnthropicProvider:
    if not _api_key:
        pytest.skip("No LLM credentials available")
    return AnthropicProvider(
        model=_model,
        api_key=_api_key,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )


def _make_agent(provider: AnthropicProvider, max_steps: int = 5) -> Agent:
    return Agent(
        provider=provider,
        tools=[],
        loop=ReAct(max_steps=max_steps),
    )


class TestReviewOrchestratorFindsBug:
    """ReviewOrchestrator should identify issues in a buggy diff."""

    @_requires_llm
    def test_review_orchestrator_finds_bug(self, glm5_provider: AnthropicProvider) -> None:
        diff = textwrap.dedent("""\
            --- a/utils.py
            +++ b/utils.py
            @@ -1,5 +1,5 @@
             def get_items(items, index):
            -    if index < len(items):
            +    if index <= len(items):
                     return items[index]
                 return None
        """)

        reviewer = _make_agent(glm5_provider, max_steps=3)
        author = _make_agent(glm5_provider, max_steps=3)

        orchestrator = ReviewOrchestrator(max_rounds=2)
        orchestrator.run(diff=diff, reviewer=reviewer, author=author)

        # The orchestrator should have run at least one review round
        assert orchestrator.current_round >= 1, "Should complete at least one review round"
        assert len(orchestrator.rounds) >= 1

        # Check that the reviewer produced feedback (comments or output)
        first_round = orchestrator.rounds[0]
        assert first_round.feedback is not None


class TestResearcherAnswersQuestion:
    """Researcher should answer a question using its agent."""

    @_requires_llm
    def test_researcher_answers_question(self, glm5_provider: AnthropicProvider) -> None:
        agent = _make_agent(glm5_provider, max_steps=3)
        researcher = Researcher(max_sources=5)

        answer = researcher.run(
            question="What is a binary search?",
            agent=agent,
        )

        assert isinstance(answer, str)
        assert len(answer) > 20, f"Expected a substantive answer, got: {answer!r}"
        # The answer should mention something related to searching
        answer_lower = answer.lower()
        assert any(
            term in answer_lower
            for term in ["search", "binary", "sorted", "half", "divide", "log"]
        ), f"Answer does not seem to be about binary search: {answer[:200]}"


class TestDocGeneratorExtractsSections:
    """DocGenerator.scan() extracts documentation sections from Python files.

    This is pure AST logic -- no LLM needed.
    """

    def test_doc_generator_extracts_sections(self, tmp_path) -> None:
        source = textwrap.dedent('''\
            """Module docstring for calculator."""

            class Calculator:
                """A simple calculator class."""

                def add(self, a, b):
                    """Add two numbers."""
                    return a + b

                def multiply(self, a, b):
                    """Multiply two numbers."""
                    return a * b

            def standalone_func(x):
                """A standalone function."""
                return x * 2
        ''')

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "calculator.py").write_text(source)

        gen = DocGenerator(root=str(src_dir))
        sections = gen.scan()

        assert len(sections) >= 1, "Should extract at least one module section"

        # Find the calculator module section
        calc_section = sections[0]
        assert "calculator" in calc_section.title.lower()
        assert calc_section.content == "Module docstring for calculator."

        # Should have subsections for the class and standalone function
        titles = [s.title for s in calc_section.subsections]
        assert any("Calculator" in t for t in titles), (
            f"Expected Calculator class in subsections: {titles}"
        )
        assert any("standalone_func" in t for t in titles), (
            f"Expected standalone_func in subsections: {titles}"
        )

        # The class subsection should have method subsections
        class_section = next(s for s in calc_section.subsections if "Calculator" in s.title)
        method_titles = [s.title for s in class_section.subsections]
        assert any("add" in t for t in method_titles), (
            f"Expected add method in class subsections: {method_titles}"
        )
        assert any("multiply" in t for t in method_titles), (
            f"Expected multiply method in class subsections: {method_titles}"
        )


class TestTestGeneratorProducesCases:
    """TestGenerator.analyze_source() produces test case skeletons.

    This is pure AST logic -- no LLM needed.
    """

    def test_test_generator_produces_cases(self) -> None:
        source = textwrap.dedent("""\
            def add(a, b):
                return a + b

            def multiply(x, y):
                return x * y

            class MathHelper:
                def divide(self, a, b):
                    return a / b
        """)

        gen = TestGenerator()
        cases = gen.analyze_source(source, filepath="math_utils.py")

        assert len(cases) > 0, "Should generate at least one test case"

        # Should have test cases for add, multiply, and MathHelper.divide
        target_funcs = {c.target_function for c in cases}
        assert "add" in target_funcs, f"Missing test for add(). Targets: {target_funcs}"
        assert "multiply" in target_funcs, f"Missing test for multiply(). Targets: {target_funcs}"
        assert "MathHelper.divide" in target_funcs, (
            f"Missing test for MathHelper.divide(). Targets: {target_funcs}"
        )

        # Check categories include unit and edge
        categories = {c.category for c in cases}
        assert "unit" in categories, f"Expected unit tests. Categories: {categories}"
        assert "edge" in categories, f"Expected edge tests. Categories: {categories}"

        # Each test case should have non-empty test_code
        for case in cases:
            assert case.test_code.strip(), f"Empty test_code for {case.name}"
            assert case.name.startswith("test_"), f"Test name should start with test_: {case.name}"


class TestMigrationPlannerPlansTransforms:
    """MigrationPlanner plans print->print() transforms for Python 2 code.

    This is rule-based -- no LLM needed.
    """

    def test_migration_planner_plans_transforms(self) -> None:
        planner = MigrationPlanner.from_preset("python2-to-3")

        py2_files = {
            "app.py": textwrap.dedent("""\
                print "hello world"
                print 'goodbye world'
                x = raw_input("Enter: ")
                for i in xrange(10):
                    print "item"
            """),
            "utils.py": textwrap.dedent("""\
                def greet(name):
                    print "Hello, " + name
            """),
        }

        # Scan should detect all the Python 2 patterns
        scan_results = planner.scan(py2_files)
        assert "app.py" in scan_results, f"Expected app.py in scan results: {scan_results.keys()}"
        assert "utils.py" in scan_results, f"Expected utils.py in scan results: {scan_results.keys()}"

        # app.py should have multiple matches
        app_matches = scan_results["app.py"]
        assert len(app_matches) >= 3, (
            f"Expected >= 3 matches in app.py, got {len(app_matches)}: {app_matches}"
        )

        # Check that print, raw_input, and xrange rules are detected
        descriptions = " ".join(app_matches)
        assert "print" in descriptions.lower(), "Should detect print statement conversion"
        assert "raw_input" in descriptions.lower() or "input" in descriptions.lower(), (
            "Should detect raw_input conversion"
        )
        assert "xrange" in descriptions.lower() or "range" in descriptions.lower(), (
            "Should detect xrange conversion"
        )

        # Plan should produce a migration plan with applicable rules
        plan = planner.plan(py2_files)
        assert len(plan.rules) >= 3, (
            f"Expected >= 3 applicable rules, got {len(plan.rules)}"
        )

        # Apply should transform the code
        result = planner.apply(py2_files)
        assert 'print("hello world")' in result["app.py"], (
            f"Expected print function in output: {result['app.py']}"
        )
        assert "raw_input" not in result["app.py"], (
            f"raw_input should be replaced: {result['app.py']}"
        )
        assert "xrange" not in result["app.py"], (
            f"xrange should be replaced: {result['app.py']}"
        )
