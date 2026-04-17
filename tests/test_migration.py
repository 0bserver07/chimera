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


# ---------------------------------------------------------------------------
# Preset coverage tests (python2-to-3)
# ---------------------------------------------------------------------------


class TestPython2To3Preset:
    def test_has_key_converted_to_in(self) -> None:
        p = MigrationPlanner.from_preset("python2-to-3")
        out = p.apply({"a.py": "d.has_key(k)\n"})["a.py"]
        assert out == "k in d\n"

    def test_except_comma_syntax_converted_to_as(self) -> None:
        p = MigrationPlanner.from_preset("python2-to-3")
        src = "try:\n    x()\nexcept ValueError, e:\n    raise\n"
        out = p.apply({"a.py": src})["a.py"]
        assert "except ValueError as e:" in out
        assert "except ValueError, e:" not in out

    def test_dict_iter_methods(self) -> None:
        p = MigrationPlanner.from_preset("python2-to-3")
        src = (
            "for k, v in d.iteritems(): pass\n"
            "for k in d.iterkeys(): pass\n"
            "for v in d.itervalues(): pass\n"
        )
        out = p.apply({"a.py": src})["a.py"]
        assert ".items()" in out
        assert ".keys()" in out
        assert ".values()" in out
        assert "iter" not in out  # no stale iter* calls

    def test_basestring_and_unicode(self) -> None:
        p = MigrationPlanner.from_preset("python2-to-3")
        src = "isinstance(x, basestring)\ns = unicode(b)\n"
        out = p.apply({"a.py": src})["a.py"]
        assert "isinstance(x, str)" in out
        assert "s = str(b)" in out

    def test_u_prefix_dropped(self) -> None:
        p = MigrationPlanner.from_preset("python2-to-3")
        src = 'a = u"hello"\nb = u\'world\'\n'
        out = p.apply({"a.py": src})["a.py"]
        assert 'a = "hello"' in out
        assert "b = 'world'" in out


# ---------------------------------------------------------------------------
# Preset coverage tests (commonjs-to-esm)
# ---------------------------------------------------------------------------


class TestCommonJsToEsmPreset:
    def test_let_and_var_require_converted(self) -> None:
        p = MigrationPlanner.from_preset("commonjs-to-esm")
        src = "let path = require('path');\nvar os = require('os');\n"
        out = p.apply({"b.js": src})["b.js"]
        assert 'import path from "path";' in out
        assert 'import os from "os";' in out

    def test_destructuring_require(self) -> None:
        p = MigrationPlanner.from_preset("commonjs-to-esm")
        src = "const { a, b } = require('./util');\n"
        out = p.apply({"b.js": src})["b.js"]
        assert 'import { a, b } from "./util";' in out

    def test_module_exports_property_to_named_export(self) -> None:
        p = MigrationPlanner.from_preset("commonjs-to-esm")
        src = "module.exports.foo = bar;\n"
        out = p.apply({"b.js": src})["b.js"]
        assert "export const foo = bar;" in out

    def test_exports_property_to_named_export(self) -> None:
        p = MigrationPlanner.from_preset("commonjs-to-esm")
        src = "exports.baz = qux;\n"
        out = p.apply({"b.js": src})["b.js"]
        assert "export const baz = qux;" in out

    def test_method_chain_on_require_not_converted(self) -> None:
        """require('fs').promises shouldn't be auto-converted — too risky."""
        p = MigrationPlanner.from_preset("commonjs-to-esm")
        src = "const fsp = require('fs').promises;\n"
        out = p.apply({"b.js": src})["b.js"]
        # Should be left alone rather than producing broken output.
        assert "require('fs').promises" in out

    def test_module_exports_default_still_works(self) -> None:
        p = MigrationPlanner.from_preset("commonjs-to-esm")
        src = "module.exports = handler;\n"
        out = p.apply({"b.js": src})["b.js"]
        assert "export default handler;" in out
