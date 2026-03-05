"""Tests for chimera.migration module."""
from __future__ import annotations

import pytest

from chimera.migration import MigrationPlan, MigrationPlanner, MigrationRule


# ---------------------------------------------------------------------------
# MigrationRule tests
# ---------------------------------------------------------------------------

class TestMigrationRule:
    def test_migration_rule_pattern_match(self) -> None:
        """Rule.matches returns one description per regex hit."""
        rule = MigrationRule(
            pattern=r"\bprint\b",
            replacement="PRINT",
            description="found print",
        )
        source = "print foo\nprint bar\n"
        matches = rule.matches(source)
        assert len(matches) == 2
        assert all(m == "found print" for m in matches)

    def test_migration_rule_pattern_no_match(self) -> None:
        """Rule.matches returns empty list when nothing matches."""
        rule = MigrationRule(
            pattern=r"\bnosuchmatch\b",
            replacement="X",
            description="nothing",
        )
        assert rule.matches("hello world") == []

    def test_migration_rule_replacement(self) -> None:
        """Rule.apply performs regex substitution."""
        rule = MigrationRule(
            pattern=r"\bxrange\s*\(",
            replacement="range(",
            description="xrange -> range",
        )
        source = "for i in xrange(10):"
        assert rule.apply(source) == "for i in range(10):"


# ---------------------------------------------------------------------------
# MigrationPlan tests
# ---------------------------------------------------------------------------

class TestMigrationPlan:
    def test_migration_plan_validate(self) -> None:
        """Plan.validate aggregates match descriptions from all rules."""
        rules = [
            MigrationRule(r"\bprint\b", "PRINT", "found print"),
            MigrationRule(r"\bxrange\b", "range", "found xrange"),
        ]
        plan = MigrationPlan(name="test", description="test plan", rules=rules)
        descs = plan.validate("print xrange print")
        assert descs.count("found print") == 2
        assert descs.count("found xrange") == 1

    def test_migration_plan_apply_single_rule(self) -> None:
        """Plan.apply works with a single rule."""
        rule = MigrationRule(
            pattern=r"\bxrange\s*\(",
            replacement="range(",
            description="xrange -> range",
        )
        plan = MigrationPlan(name="py2", description="py2 fix", rules=[rule])
        assert plan.apply("xrange(5)") == "range(5)"

    def test_migration_plan_apply_multiple_rules(self) -> None:
        """Plan.apply chains multiple rules in order."""
        rules = [
            MigrationRule(r"\bfoo\b", "bar", "foo->bar"),
            MigrationRule(r"\bbar\b", "baz", "bar->baz"),
        ]
        plan = MigrationPlan(name="chain", description="chained", rules=rules)
        # "foo" -> "bar" (rule 1) -> "baz" (rule 2)
        assert plan.apply("foo") == "baz"


# ---------------------------------------------------------------------------
# MigrationPlanner tests
# ---------------------------------------------------------------------------

class TestMigrationPlanner:
    def test_planner_add_rule(self) -> None:
        """add_rule appends to the planner's internal list."""
        planner = MigrationPlanner()
        assert planner._rules == []
        rule = MigrationRule(r"a", "b", "test")
        planner.add_rule(rule)
        assert planner._rules == [rule]

    def test_planner_scan_files(self) -> None:
        """scan returns a dict mapping paths to match descriptions."""
        planner = MigrationPlanner()
        planner.add_rule(
            MigrationRule(r"\bprint\b", "PRINT", "found print", file_glob="*.py")
        )
        files = {
            "app.py": "print hello\nprint world",
            "readme.md": "print is a keyword",
        }
        result = planner.scan(files)
        # Only .py should be scanned
        assert "app.py" in result
        assert len(result["app.py"]) == 2
        assert "readme.md" not in result

    def test_planner_apply_files(self) -> None:
        """apply transforms matching files and leaves others untouched."""
        planner = MigrationPlanner()
        planner.add_rule(
            MigrationRule(
                pattern=r"\bxrange\s*\(",
                replacement="range(",
                description="xrange -> range",
                file_glob="*.py",
            )
        )
        files = {
            "main.py": "for i in xrange(10): pass",
            "notes.txt": "xrange(5) is old",
        }
        result = planner.apply(files)
        assert result["main.py"] == "for i in range(10): pass"
        # .txt does not match *.py glob, so no transformation
        assert result["notes.txt"] == "xrange(5) is old"

    def test_planner_from_preset_python2(self) -> None:
        """from_preset('python2-to-3') loads Python 2 migration rules."""
        planner = MigrationPlanner.from_preset("python2-to-3")
        assert len(planner._rules) > 0
        source = 'print "hello"'
        files = {"script.py": source}
        result = planner.apply(files)
        assert result["script.py"] == 'print("hello")'

    def test_planner_from_preset_commonjs(self) -> None:
        """from_preset('commonjs-to-esm') loads CommonJS migration rules."""
        planner = MigrationPlanner.from_preset("commonjs-to-esm")
        assert len(planner._rules) > 0
        source = "const fs = require('fs');\nmodule.exports = handler;"
        files = {"index.js": source}
        result = planner.apply(files)
        assert 'import fs from "fs";' in result["index.js"]
        assert "export default handler;" in result["index.js"]

    def test_planner_from_preset_unknown_raises(self) -> None:
        """from_preset raises ValueError for unknown preset names."""
        with pytest.raises(ValueError, match="Unknown preset"):
            MigrationPlanner.from_preset("nonexistent-preset")

    def test_file_glob_filtering(self) -> None:
        """Rules only apply to files matching their file_glob."""
        planner = MigrationPlanner()
        planner.add_rule(
            MigrationRule(
                pattern=r"\bvar\b",
                replacement="let",
                description="var -> let",
                file_glob="*.js",
            )
        )
        files = {
            "app.js": "var x = 1;",
            "style.css": "var is not a keyword here",
            "lib.js": "var y = 2;",
        }
        result = planner.apply(files)
        assert result["app.js"] == "let x = 1;"
        assert result["lib.js"] == "let y = 2;"
        assert result["style.css"] == "var is not a keyword here"

    def test_planner_plan_returns_applicable_rules(self) -> None:
        """plan() returns only rules that match at least one file."""
        planner = MigrationPlanner()
        rule_match = MigrationRule(r"\bfoo\b", "bar", "foo->bar", file_glob="*.py")
        rule_nomatch = MigrationRule(r"\bZZZZZ\b", "X", "no match", file_glob="*.py")
        planner.add_rule(rule_match)
        planner.add_rule(rule_nomatch)

        plan = planner.plan({"test.py": "foo baz"})
        assert rule_match in plan.rules
        assert rule_nomatch not in plan.rules
