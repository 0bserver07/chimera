"""Tests for the research module."""
from __future__ import annotations

from chimera.research import Finding, ResearchPlan, Researcher, Source


class TestSource:
    def test_source_defaults(self):
        src = Source(name="file.py", content="hello world")
        assert src.name == "file.py"
        assert src.content == "hello world"
        assert src.url == ""
        assert src.relevance == 1.0


class TestFinding:
    def test_finding_with_sources(self):
        src = Source(name="a.py", content="code")
        finding = Finding(
            title="Auth",
            summary="Uses JWT",
            sources=[src],
            confidence=0.9,
            tags=["security"],
        )
        assert finding.title == "Auth"
        assert finding.summary == "Uses JWT"
        assert len(finding.sources) == 1
        assert finding.sources[0].name == "a.py"
        assert finding.confidence == 0.9
        assert finding.tags == ["security"]


class TestResearchPlan:
    def test_research_plan_question(self):
        plan = ResearchPlan(
            question="How does auth work?",
            sub_questions=["What is auth?"],
            search_terms=["auth"],
        )
        assert plan.question == "How does auth work?"
        assert plan.sub_questions == ["What is auth?"]
        assert plan.search_terms == ["auth"]


class TestResearcher:
    def test_researcher_plan_generates_search_terms(self):
        r = Researcher()
        plan = r.plan("How does authentication work in the system?")
        assert "authentication" in plan.search_terms
        assert "system" in plan.search_terms
        assert plan.question == "How does authentication work in the system?"
        # Stop words should be excluded.
        assert "how" not in plan.search_terms
        assert "does" not in plan.search_terms
        assert "the" not in plan.search_terms

    def test_researcher_search_codebase_finds_matches(self):
        r = Researcher()
        files = {
            "auth.py": "def authenticate(user): pass",
            "main.py": "import os",
        }
        results = r.search_codebase("authenticate", files)
        assert len(results) == 1
        assert results[0].name == "auth.py"
        assert results[0].relevance > 0

    def test_researcher_search_codebase_no_matches(self):
        r = Researcher()
        files = {"main.py": "import os"}
        results = r.search_codebase("nonexistent", files)
        assert results == []

    def test_researcher_search_codebase_max_sources(self):
        r = Researcher(max_sources=2)
        files = {f"file{i}.py": "match keyword here" for i in range(5)}
        results = r.search_codebase("match", files)
        assert len(results) == 2

    def test_researcher_add_finding(self):
        r = Researcher()
        finding = Finding(title="F1", summary="S1", sources=[])
        r.add_finding(finding)
        assert len(r.findings) == 1
        assert r.findings[0].title == "F1"

    def test_researcher_findings_property(self):
        r = Researcher()
        assert r.findings == []
        f1 = Finding(title="A", summary="a", sources=[])
        f2 = Finding(title="B", summary="b", sources=[])
        r.add_finding(f1)
        r.add_finding(f2)
        assert len(r.findings) == 2
        # Property should return a copy.
        r.findings.append(Finding(title="C", summary="c", sources=[]))
        assert len(r.findings) == 2

    def test_researcher_synthesize(self):
        src = Source(name="x.py", content="data")
        finding = Finding(
            title="Result",
            summary="Found something",
            sources=[src],
            confidence=0.8,
        )
        r = Researcher()
        report = r.synthesize([finding])
        assert "## Result" in report
        assert "Found something" in report
        assert "x.py" in report
        assert "0.8" in report

    def test_researcher_synthesize_empty(self):
        r = Researcher()
        report = r.synthesize([])
        assert report == "No findings to synthesize."

    def test_plan_sub_questions(self):
        r = Researcher()
        plan = r.plan("How does authentication relate to authorization?")
        assert len(plan.sub_questions) >= 2
        # First sub-question should reference first keyword.
        assert "authentication" in plan.sub_questions[0].lower()
