"""Migration planning: rule-based codebase transformations.

Provides dataclasses and a planner for scanning, validating, and applying
regex-based migration rules to source files. Built-in presets cover common
migrations such as Python 2 to 3 and CommonJS to ESM.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class MigrationRule:
    """A single find-and-replace migration rule.

    Attributes:
        pattern: Regex pattern to match in source text.
        replacement: Replacement string (may use backreferences like ``\\1``).
        description: Human-readable explanation of what the rule does.
        file_glob: Glob pattern restricting which files the rule applies to.
    """

    pattern: str
    replacement: str
    description: str
    file_glob: str = "*"

    def matches(self, source: str) -> list[str]:
        """Return descriptions for each match of *pattern* in *source*.

        Args:
            source: The source text to scan.

        Returns:
            A list containing *description* once per match found.
        """
        hits = re.findall(self.pattern, source)
        return [self.description] * len(hits)

    def apply(self, source: str) -> str:
        """Apply the rule's regex substitution to *source*.

        Args:
            source: The source text to transform.

        Returns:
            The transformed text with all matches replaced.
        """
        return re.sub(self.pattern, self.replacement, source)


@dataclass
class MigrationPlan:
    """An ordered collection of migration rules with a descriptive name.

    Attributes:
        name: Short identifier for the migration plan.
        description: Human-readable summary of what the migration does.
        rules: Ordered list of :class:`MigrationRule` instances.
    """

    name: str
    description: str
    rules: list[MigrationRule] = field(default_factory=list)

    def validate(self, source: str) -> list[str]:
        """Check which rules match *source* and collect their descriptions.

        Args:
            source: The source text to validate.

        Returns:
            A flat list of match descriptions from all rules.
        """
        descriptions: list[str] = []
        for rule in self.rules:
            descriptions.extend(rule.matches(source))
        return descriptions

    def apply(self, source: str) -> str:
        """Apply all rules sequentially to *source*.

        Rules are applied in list order; later rules see the output of
        earlier rules.

        Args:
            source: The source text to transform.

        Returns:
            The fully transformed text.
        """
        result = source
        for rule in self.rules:
            result = rule.apply(result)
        return result


class MigrationPlanner:
    """Builds, scans, and applies migration plans across file collections.

    Example::

        planner = MigrationPlanner.from_preset("python2-to-3")
        files = {"app.py": 'print "hello"'}
        result = planner.apply(files)

    Attributes:
        _rules: Internal list of :class:`MigrationRule` instances.
    """

    _PRESETS: ClassVar[dict[str, list[MigrationRule]]] = {
        "python2-to-3": [
            MigrationRule(
                pattern=r'\bprint\s+"([^"]*)"',
                replacement=r'print("\1")',
                description="Convert print statement to print function (double-quoted)",
                file_glob="*.py",
            ),
            MigrationRule(
                pattern=r"\bprint\s+'([^']*)'",
                replacement=r"print('\1')",
                description="Convert print statement to print function (single-quoted)",
                file_glob="*.py",
            ),
            MigrationRule(
                pattern=r"\braw_input\s*\(",
                replacement="input(",
                description="Replace raw_input with input",
                file_glob="*.py",
            ),
            MigrationRule(
                pattern=r"\bxrange\s*\(",
                replacement="range(",
                description="Replace xrange with range",
                file_glob="*.py",
            ),
            # d.has_key(k) -> k in d
            MigrationRule(
                pattern=r"\b([A-Za-z_][A-Za-z_0-9]*)\.has_key\(\s*([^()]+?)\s*\)",
                replacement=r"\2 in \1",
                description="Replace dict.has_key(k) with k in dict",
                file_glob="*.py",
            ),
            # except Exception, e:  ->  except Exception as e:
            MigrationRule(
                pattern=r"\bexcept\s+([A-Za-z_][A-Za-z_0-9.]*)\s*,\s*([A-Za-z_][A-Za-z_0-9]*)\s*:",
                replacement=r"except \1 as \2:",
                description="Convert 'except Exception, e:' to 'except Exception as e:'",
                file_glob="*.py",
            ),
            # dict iteration methods
            MigrationRule(
                pattern=r"\.iteritems\(\)",
                replacement=".items()",
                description="Replace dict.iteritems() with dict.items()",
                file_glob="*.py",
            ),
            MigrationRule(
                pattern=r"\.iterkeys\(\)",
                replacement=".keys()",
                description="Replace dict.iterkeys() with dict.keys()",
                file_glob="*.py",
            ),
            MigrationRule(
                pattern=r"\.itervalues\(\)",
                replacement=".values()",
                description="Replace dict.itervalues() with dict.values()",
                file_glob="*.py",
            ),
            # basestring -> str (py3 has no basestring)
            MigrationRule(
                pattern=r"\bbasestring\b",
                replacement="str",
                description="Replace basestring with str",
                file_glob="*.py",
            ),
            # unicode(x) -> str(x); the type alias went away
            MigrationRule(
                pattern=r"\bunicode\s*\(",
                replacement="str(",
                description="Replace unicode() with str()",
                file_glob="*.py",
            ),
            # u"..." / u'...' literals are redundant in py3 (valid but noisy)
            MigrationRule(
                pattern=r'(?<![A-Za-z0-9_])u"([^"\\]*(?:\\.[^"\\]*)*)"',
                replacement=r'"\1"',
                description="Drop redundant u-prefix on double-quoted string literal",
                file_glob="*.py",
            ),
            MigrationRule(
                pattern=r"(?<![A-Za-z0-9_])u'([^'\\]*(?:\\.[^'\\]*)*)'",
                replacement=r"'\1'",
                description="Drop redundant u-prefix on single-quoted string literal",
                file_glob="*.py",
            ),
        ],
        "commonjs-to-esm": [
            # const/let/var X = require('mod');  ->  import X from "mod";
            # Require statement-ending context to avoid swallowing method chains.
            MigrationRule(
                pattern=r"(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)\s*(?=[;\r\n]|$)",
                replacement=r'import \1 from "\2"',
                description="Convert require() to default import",
                file_glob="*.js",
            ),
            # const { a, b } = require('mod');  ->  import { a, b } from "mod";
            # Requires the require() call to end the statement (followed by ; or newline),
            # not a method access like .promises.
            MigrationRule(
                pattern=r"(?:const|let|var)\s*\{\s*([^}]+?)\s*\}\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)\s*(?=[;\r\n]|$)",
                replacement=r'import { \1 } from "\2"',
                description="Convert destructuring require() to named import",
                file_glob="*.js",
            ),
            # module.exports.name = value  ->  export const name = value
            MigrationRule(
                pattern=r"module\.exports\.(\w+)\s*=\s*",
                replacement=r"export const \1 = ",
                description="Convert module.exports.name to named export",
                file_glob="*.js",
            ),
            # exports.name = value  ->  export const name = value
            MigrationRule(
                pattern=r"(?<![A-Za-z0-9_.])exports\.(\w+)\s*=\s*",
                replacement=r"export const \1 = ",
                description="Convert exports.name to named export",
                file_glob="*.js",
            ),
            # module.exports = value  ->  export default value
            MigrationRule(
                pattern=r"module\.exports\s*=\s*",
                replacement="export default ",
                description="Convert module.exports to export default",
                file_glob="*.js",
            ),
        ],
    }

    def __init__(self) -> None:
        """Initialize the planner with an empty rule list."""
        self._rules: list[MigrationRule] = []

    def add_rule(self, rule: MigrationRule) -> None:
        """Append a rule to the planner's rule list.

        Args:
            rule: The migration rule to add.
        """
        self._rules.append(rule)

    def scan(self, files: dict[str, str]) -> dict[str, list[str]]:
        """Scan files and report which rules match.

        Only files whose path matches a rule's ``file_glob`` are checked.

        Args:
            files: Mapping of file paths to their contents.

        Returns:
            A dict mapping each file path to a list of match descriptions.
            Files with no matches are omitted.
        """
        results: dict[str, list[str]] = {}
        for path, content in files.items():
            matches: list[str] = []
            for rule in self._rules:
                if fnmatch.fnmatch(path, rule.file_glob):
                    matches.extend(rule.matches(content))
            if matches:
                results[path] = matches
        return results

    def plan(self, files: dict[str, str]) -> MigrationPlan:
        """Create a :class:`MigrationPlan` from the planner's current rules.

        The plan contains only rules that match at least one file.

        Args:
            files: Mapping of file paths to their contents.

        Returns:
            A :class:`MigrationPlan` containing the applicable rules.
        """
        applicable: list[MigrationRule] = []
        for rule in self._rules:
            for path, content in files.items():
                if fnmatch.fnmatch(path, rule.file_glob) and rule.matches(content):
                    applicable.append(rule)
                    break
        return MigrationPlan(
            name="migration",
            description="Auto-generated migration plan",
            rules=applicable,
        )

    def apply(self, files: dict[str, str]) -> dict[str, str]:
        """Apply all rules to matching files and return the transformed set.

        Args:
            files: Mapping of file paths to their contents.

        Returns:
            A new dict with the same keys but transformed contents for
            files that matched at least one rule's ``file_glob``.
        """
        result: dict[str, str] = {}
        for path, content in files.items():
            transformed = content
            for rule in self._rules:
                if fnmatch.fnmatch(path, rule.file_glob):
                    transformed = rule.apply(transformed)
            result[path] = transformed
        return result

    @classmethod
    def from_preset(cls, name: str) -> MigrationPlanner:
        """Create a planner pre-loaded with a built-in rule set.

        Args:
            name: Preset name. Currently supported: ``"python2-to-3"``,
                ``"commonjs-to-esm"``.

        Returns:
            A new :class:`MigrationPlanner` with the preset rules loaded.

        Raises:
            ValueError: If *name* is not a known preset.
        """
        if name not in cls._PRESETS:
            available = ", ".join(sorted(cls._PRESETS))
            raise ValueError(
                f"Unknown preset {name!r}. Available presets: {available}"
            )
        planner = cls()
        for rule in cls._PRESETS[name]:
            planner.add_rule(rule)
        return planner
