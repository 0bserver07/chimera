# tests/test_consolidation.py
"""Tests for chimera.context.consolidation — two-phase memory pipeline."""
from chimera.context.consolidation import (
    Fact,
    MemoryConsolidator,
)
from chimera.types import Message


class TestMemoryConsolidator:
    def test_add_and_consolidate(self):
        mc = MemoryConsolidator()
        mc.add_fact("The project uses pytest for testing.", category="testing")
        mc.add_fact("Python 3.11 is required.", category="config")
        result = mc.consolidate()
        assert len(result.facts) == 2
        assert "testing" in result.categories
        assert "config" in result.categories

    def test_deduplication(self):
        mc = MemoryConsolidator()
        mc.add_fact("Uses pytest.", confidence=0.8)
        mc.add_fact("Uses pytest.", confidence=0.9)
        mc.add_fact("Uses pytest.", confidence=0.5)
        result = mc.consolidate()
        assert len(result.facts) == 1
        # Highest confidence wins
        assert result.facts[0].confidence == 0.9

    def test_auto_categorize(self):
        mc = MemoryConsolidator()
        mc.add_fact("The API endpoint is /v1/users")
        mc.add_fact("Tests are in the tests/ directory")
        result = mc.consolidate()
        categories = {f.category for f in result.facts}
        assert "api" in categories
        assert "testing" in categories

    def test_extract_from_messages(self):
        mc = MemoryConsolidator()
        messages = [
            Message.user("What framework is used?"),
            Message.assistant("The project uses Flask for the web server."),
            Message.assistant("The database is PostgreSQL."),
        ]
        count = mc.extract_from_messages(messages)
        assert count >= 2
        assert len(mc.raw_facts) >= 2

    def test_query(self):
        mc = MemoryConsolidator()
        mc.add_fact("The project uses pytest for testing.")
        mc.add_fact("Python 3.11 is required for the build.")
        result = mc.consolidate()
        found = result.query("pytest")
        assert len(found) == 1
        assert "pytest" in found[0].content

    def test_summary_contains_categories(self):
        mc = MemoryConsolidator()
        mc.add_fact("Tests use pytest.", category="testing")
        result = mc.consolidate()
        assert "[testing]" in result.summary

    def test_clear(self):
        mc = MemoryConsolidator()
        mc.add_fact("something")
        mc.clear()
        assert len(mc.raw_facts) == 0

    def test_consolidate_does_not_mutate_raw_facts(self):
        """Bug fix: consolidate() must not mutate the Fact objects it
        stores in _raw_facts. Previously it assigned fact.category
        directly, which leaked back into the caller's view of raw_facts.
        """
        mc = MemoryConsolidator()
        # Uncategorized fact that would be auto-categorized during
        # consolidate(). Before the fix, its .category was written in
        # place.
        mc.add_fact("The API endpoint is /v1/users", source="docs")
        # Snapshot the raw facts before consolidation.
        pre_category = mc.raw_facts[0].category
        pre_content = mc.raw_facts[0].content
        assert pre_category == ""

        result = mc.consolidate()

        # The consolidated copy gets the inferred category…
        assert result.facts[0].category == "api"
        # …but the raw fact remains pristine.
        assert mc.raw_facts[0].category == pre_category == ""
        assert mc.raw_facts[0].content == pre_content
        # And the returned Fact is a different object.
        assert result.facts[0] is not mc.raw_facts[0]

    def test_consolidate_does_not_mutate_externally_held_facts(self):
        """External references to Fact objects must also remain unchanged."""
        mc = MemoryConsolidator()
        mc.add_fact("Tests use pytest.")
        # Grab an external reference BEFORE consolidate runs.
        external_ref = mc.raw_facts[0]
        assert external_ref.category == ""

        mc.consolidate()

        # The external reference to the underlying Fact is untouched.
        assert external_ref.category == ""

    def test_fact_is_dataclass(self):
        # Sanity check: Fact must still be importable and constructible
        # the same way callers expect.
        f = Fact(content="x", category="testing")
        assert f.content == "x"
        assert f.category == "testing"
