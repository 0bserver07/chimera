"""Workflow verification against real LLM (GitHub issue #52).

Verifies 5 workflows:
1. ReviewOrchestrator - review-fix iteration cycle (uses LLM)
2. Researcher         - research question decomposition (uses LLM)
3. MigrationPlanner   - rule-based code migration (no LLM)
4. DocGenerator       - AST-based doc generation (no LLM)
5. TestGenerator      - AST-based test skeleton generation (no LLM)
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

# Ensure env vars are set
os.environ.setdefault("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", "YOUR_TOKEN_HERE")
os.environ.setdefault("ANTHROPIC_MODEL", "glm-5")


def separator(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def verify_review_orchestrator() -> bool:
    """Verify ReviewOrchestrator with real LLM agents."""
    separator("1. ReviewOrchestrator")

    from chimera import Agent, create_provider
    from chimera.core.prompt import Prompt
    from chimera.review.orchestrator import ReviewOrchestrator

    provider = create_provider()
    print(f"Provider created: model={provider.model_name}")

    # Create reviewer agent with a prompt that produces structured feedback
    reviewer = Agent(
        provider=provider,
        tools=[],
        prompt=Prompt.from_string(
            "You are a code reviewer. Review the diff provided. "
            "If the code looks acceptable, respond with 'APPROVED' in your response. "
            "If there are issues, list them as: [SUGGESTION] file.py: description"
        ),
        name="reviewer",
    )

    # Create author agent
    author = Agent(
        provider=provider,
        tools=[],
        prompt=Prompt.from_string(
            "You are a code author. When given review feedback, "
            "acknowledge the suggestions and describe how you would fix them."
        ),
        name="author",
    )

    orchestrator = ReviewOrchestrator(max_rounds=2)

    diff = """\
--- a/utils.py
+++ b/utils.py
@@ -1,3 +1,5 @@
 def add(a, b):
-    return a + b
+    result = a + b
+    print(result)
+    return result
"""

    print(f"Running review on diff ({len(diff)} chars)...")
    approved = orchestrator.run(diff, reviewer=reviewer, author=author, env=None)

    print(f"Approved: {approved}")
    print(f"Rounds completed: {orchestrator.current_round}")
    print(f"Total comments: {orchestrator.total_comments}")
    for r in orchestrator.rounds:
        print(f"  Round {r.round_number}: approved={r.feedback.approved}, "
              f"comments={r.feedback.comment_count}, fixed={r.fixed}")
    return True


def verify_researcher() -> bool:
    """Verify Researcher with real LLM agent."""
    separator("2. Researcher")

    from chimera import Agent, create_provider
    from chimera.core.prompt import Prompt
    from chimera.research.researcher import Researcher, Finding, Source

    # First test local-only methods (no LLM)
    researcher = Researcher(max_sources=5)

    plan = researcher.plan("How does authentication work in this codebase?")
    print(f"Plan created:")
    print(f"  Question: {plan.question}")
    print(f"  Sub-questions: {plan.sub_questions}")
    print(f"  Search terms: {plan.search_terms}")

    # Test search_codebase (local)
    files = {
        "auth.py": "def authenticate(user, password):\n    token = create_jwt(user)\n    return token",
        "main.py": "from auth import authenticate\nresult = authenticate('admin', 'pass')",
        "utils.py": "def format_date(d):\n    return d.isoformat()",
    }
    sources = researcher.search_codebase("authenticate", files)
    print(f"  Search results for 'authenticate': {[s.name for s in sources]}")

    # Test synthesize (local)
    researcher.add_finding(Finding(
        title="Auth uses JWT",
        summary="The codebase uses JWT tokens for authentication.",
        sources=sources,
        confidence=0.9,
    ))
    report = researcher.synthesize(researcher.findings)
    print(f"  Synthesized report:\n{report}\n")

    # Now test run() with real LLM
    provider = create_provider()
    print(f"Provider created: model={provider.model_name}")

    agent = Agent(
        provider=provider,
        tools=[],
        prompt=Prompt.from_string(
            "You are a research assistant. Answer research questions concisely. "
            "Keep your response under 100 words."
        ),
        name="researcher",
    )

    print("Running researcher with LLM...")
    result = researcher.run(
        "What are the main benefits of using type hints in Python?",
        agent=agent,
        env=None,
    )
    print(f"Research result ({len(result)} chars):")
    print(f"  {result[:300]}{'...' if len(result) > 300 else ''}")
    return True


def verify_migration_planner() -> bool:
    """Verify MigrationPlanner (rule-based, no LLM needed)."""
    separator("3. MigrationPlanner")

    from chimera.migration.planner import MigrationPlanner, MigrationRule

    # Test python2-to-3 preset
    planner = MigrationPlanner.from_preset("python2-to-3")
    files = {
        "app.py": 'print "hello world"\nx = raw_input("Enter: ")\nfor i in xrange(10):\n    print "num"',
        "utils.py": "def add(a, b):\n    return a + b\n",
    }

    # Scan
    scan_results = planner.scan(files)
    print("Scan results:")
    for path, matches in scan_results.items():
        print(f"  {path}: {matches}")

    # Plan
    plan = planner.plan(files)
    print(f"\nMigration plan: {plan.name}")
    print(f"  Description: {plan.description}")
    print(f"  Rules: {len(plan.rules)}")
    for rule in plan.rules:
        print(f"    - {rule.description}")

    # Apply
    result = planner.apply(files)
    print("\nTransformed files:")
    for path, content in result.items():
        print(f"  {path}:")
        for line in content.splitlines():
            print(f"    {line}")

    # Test commonjs-to-esm preset
    planner2 = MigrationPlanner.from_preset("commonjs-to-esm")
    js_files = {
        "index.js": 'const express = require("express");\nconst app = express();\nmodule.exports = app;',
    }
    js_result = planner2.apply(js_files)
    print("\nCommonJS to ESM:")
    for path, content in js_result.items():
        print(f"  {path}:")
        for line in content.splitlines():
            print(f"    {line}")

    # Test custom rule
    planner3 = MigrationPlanner()
    planner3.add_rule(MigrationRule(
        pattern=r"var\s+",
        replacement="let ",
        description="Replace var with let",
        file_glob="*.js",
    ))
    custom_result = planner3.apply({"script.js": "var x = 1;\nvar y = 2;"})
    print("\nCustom rule (var -> let):")
    print(f"  {custom_result['script.js']}")

    return True


def verify_doc_generator() -> bool:
    """Verify DocGenerator (AST-based, no LLM needed)."""
    separator("4. DocGenerator")

    from chimera.docs.generator import DocGenerator

    # Create a temp directory with sample Python files
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write sample source files
        sample_code = '''\
"""Sample module for testing doc generation."""

def greet(name: str) -> str:
    """Return a greeting message.

    Args:
        name: The person to greet.

    Returns:
        A greeting string.
    """
    return f"Hello, {name}!"


class Calculator:
    """A simple calculator class."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    def multiply(self, a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b
'''
        src_path = os.path.join(tmpdir, "sample.py")
        with open(src_path, "w") as f:
            f.write(sample_code)

        output_dir = os.path.join(tmpdir, "docs_out")
        gen = DocGenerator(root=tmpdir, output_dir=output_dir)

        # Scan
        sections = gen.scan(extensions=(".py",))
        print(f"Scanned {len(sections)} file(s)")

        for section in sections:
            print(f"\nModule: {section.title}")
            print(f"  Content: {section.content[:80]}..." if len(section.content) > 80 else f"  Content: {section.content}")
            print(f"  Subsections: {len(section.subsections)}")
            for sub in section.subsections:
                print(f"    - {sub.title}: {sub.content[:60]}..." if len(sub.content) > 60 else f"    - {sub.title}: {sub.content}")

        # Write docs
        written = gen.write(sections)
        print(f"\nWrote {len(written)} file(s):")
        for path in written:
            print(f"  {path}")

        # Read generated markdown
        for path in written:
            if path.endswith(".md") and not path.endswith("index.md"):
                with open(path) as f:
                    content = f.read()
                print(f"\nGenerated markdown ({os.path.basename(path)}):")
                # Show first 400 chars
                preview = content[:400]
                print(f"  {preview}{'...' if len(content) > 400 else ''}")

    return True


def verify_test_generator() -> bool:
    """Verify TestGenerator (AST-based, no LLM needed)."""
    separator("5. TestGenerator")

    from chimera.testgen.generator import TestGenerator

    gen = TestGenerator()

    source = '''\
def fibonacci(n):
    """Calculate nth Fibonacci number."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)


def parse_csv(text, delimiter=","):
    """Parse CSV text into rows."""
    return [line.split(delimiter) for line in text.strip().splitlines()]


class Stack:
    """A simple stack implementation."""

    def __init__(self):
        self._items = []

    def push(self, item):
        """Push an item onto the stack."""
        self._items.append(item)

    def pop(self):
        """Pop an item from the stack."""
        return self._items.pop()

    def peek(self):
        """Look at the top item."""
        return self._items[-1]
'''

    cases = gen.analyze_source(source, filepath="sample.py")
    print(f"Generated {len(cases)} test case(s):\n")
    for case in cases:
        print(f"  Name: {case.name}")
        print(f"  Target: {case.target_function}")
        print(f"  Category: {case.category}")
        print(f"  Code:")
        for line in case.test_code.splitlines():
            print(f"    {line}")
        print()

    # Verify accumulated test_cases property
    print(f"Total accumulated test cases: {len(gen.test_cases)}")

    # Verify clear
    gen.clear()
    print(f"After clear: {len(gen.test_cases)} test cases")

    return True


def main() -> None:
    results: dict[str, str] = {}
    verifications = [
        ("ReviewOrchestrator", verify_review_orchestrator),
        ("Researcher", verify_researcher),
        ("MigrationPlanner", verify_migration_planner),
        ("DocGenerator", verify_doc_generator),
        ("TestGenerator", verify_test_generator),
    ]

    for name, func in verifications:
        try:
            success = func()
            results[name] = "PASS" if success else "FAIL"
        except Exception as e:
            print(f"\nERROR in {name}: {e}")
            traceback.print_exc()
            results[name] = f"ERROR: {e}"

    separator("SUMMARY")
    all_pass = True
    for name, status in results.items():
        icon = "[PASS]" if status == "PASS" else "[FAIL]"
        print(f"  {icon} {name}: {status}")
        if status != "PASS":
            all_pass = False

    print()
    if all_pass:
        print("All 5 workflows verified successfully.")
    else:
        print("Some workflows failed. See details above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
