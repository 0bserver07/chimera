"""Built-in graders: file existence, pattern matching, test execution, schema, composite."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from chimera.eval.graders.base import GradeResult, Grader


class FileExistsGrader(Grader):
    """Check that specified files exist on disk.

    Args:
        paths: List of file paths to check for existence.
    """

    name = "file_exists"

    def __init__(self, paths: list[str]) -> None:
        self._paths = paths

    def grade(self, task: dict[str, Any], result: dict[str, Any]) -> GradeResult:
        """Check each path with Path.exists(). Score = existing/total.

        Args:
            task: The task dictionary (unused).
            result: The result dictionary (unused).

        Returns:
            GradeResult with score proportional to files found.
        """
        if not self._paths:
            return GradeResult(
                passed=True,
                score=1.0,
                reason="No paths to check",
                grader_name=self.name,
            )

        existing = [p for p in self._paths if Path(p).exists()]
        score = len(existing) / len(self._paths)
        passed = len(existing) == len(self._paths)
        missing = [p for p in self._paths if not Path(p).exists()]

        if passed:
            reason = f"All {len(self._paths)} files exist"
        else:
            reason = f"Missing files: {', '.join(missing)}"

        return GradeResult(
            passed=passed,
            score=score,
            reason=reason,
            grader_name=self.name,
        )


class PatternMatchGrader(Grader):
    """Check output matches a regex.

    Args:
        pattern: Regular expression pattern to search for.
        target: Key in the result dict to check. Defaults to "output".
    """

    name = "pattern_match"

    def __init__(self, pattern: str, target: str = "output") -> None:
        self._pattern = pattern
        self._target = target

    def grade(self, task: dict[str, Any], result: dict[str, Any]) -> GradeResult:
        """Search for pattern in result[target]. Pass if match found.

        Args:
            task: The task dictionary (unused).
            result: The result dictionary containing the target key.

        Returns:
            GradeResult indicating whether the pattern was found.
        """
        text = result.get(self._target, "")
        match = re.search(self._pattern, str(text))
        passed = match is not None

        if passed:
            reason = f"Pattern '{self._pattern}' found in {self._target}"
        else:
            reason = f"Pattern '{self._pattern}' not found in {self._target}"

        return GradeResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=reason,
            grader_name=self.name,
        )


class TestPassGrader(Grader):
    """Run a command and check exit code 0.

    Args:
        command: Shell command to execute.
        timeout: Maximum seconds to wait for the command. Defaults to 60.
    """

    name = "test_pass"

    def __init__(self, command: str, timeout: int = 60) -> None:
        self._command = command
        self._timeout = timeout

    def grade(self, task: dict[str, Any], result: dict[str, Any]) -> GradeResult:
        """Run command via subprocess. Pass if returncode == 0.

        Args:
            task: The task dictionary (unused).
            result: The result dictionary (unused).

        Returns:
            GradeResult based on command exit code.
        """
        try:
            proc = subprocess.run(
                self._command,
                shell=True,
                timeout=self._timeout,
                capture_output=True,
                text=True,
            )
            passed = proc.returncode == 0
            if passed:
                reason = f"Command succeeded: {self._command}"
            else:
                reason = f"Command failed (exit {proc.returncode}): {self._command}"
            return GradeResult(
                passed=passed,
                score=1.0 if passed else 0.0,
                reason=reason,
                grader_name=self.name,
            )
        except subprocess.TimeoutExpired:
            return GradeResult(
                passed=False,
                score=0.0,
                reason=f"Command timed out after {self._timeout}s: {self._command}",
                grader_name=self.name,
            )


class SchemaGrader(Grader):
    """Validate output against a JSON schema.

    Uses manual key/type validation (no jsonschema library). Supports
    checking required keys and their expected types.

    Args:
        schema: Dict describing expected structure. Keys map to type names
            (e.g., ``{"name": "str", "age": "int", "tags": "list"}``).
    """

    name = "schema"

    # Map type name strings to Python types for validation
    _TYPE_MAP: dict[str, type] = {
        "str": str,
        "string": str,
        "int": int,
        "integer": int,
        "float": float,
        "number": float,
        "bool": bool,
        "boolean": bool,
        "list": list,
        "array": list,
        "dict": dict,
        "object": dict,
        "null": type(None),
    }

    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema

    def grade(self, task: dict[str, Any], result: dict[str, Any]) -> GradeResult:
        """Parse JSON from result['output'] and validate against schema.

        Args:
            task: The task dictionary (unused).
            result: The result dictionary with an 'output' key containing JSON.

        Returns:
            GradeResult indicating validation success/failure.
        """
        output = result.get("output", "")
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, TypeError) as e:
            return GradeResult(
                passed=False,
                score=0.0,
                reason=f"Invalid JSON: {e}",
                grader_name=self.name,
            )

        if not isinstance(data, dict):
            return GradeResult(
                passed=False,
                score=0.0,
                reason=f"Expected object, got {type(data).__name__}",
                grader_name=self.name,
            )

        errors: list[str] = []
        total_keys = len(self._schema)
        matched = 0

        for key, expected_type in self._schema.items():
            if key not in data:
                errors.append(f"Missing key: {key}")
                continue

            if isinstance(expected_type, str):
                # Map string type name to Python type
                py_type = self._TYPE_MAP.get(expected_type.lower())
                if py_type is not None:
                    if not isinstance(data[key], py_type):
                        errors.append(
                            f"Key '{key}': expected {expected_type}, "
                            f"got {type(data[key]).__name__}"
                        )
                        continue
            elif isinstance(expected_type, type):
                if not isinstance(data[key], expected_type):
                    errors.append(
                        f"Key '{key}': expected {expected_type.__name__}, "
                        f"got {type(data[key]).__name__}"
                    )
                    continue

            matched += 1

        passed = len(errors) == 0
        score = matched / total_keys if total_keys > 0 else 1.0

        if passed:
            reason = f"All {total_keys} schema keys validated"
        else:
            reason = "; ".join(errors)

        return GradeResult(
            passed=passed,
            score=score,
            reason=reason,
            grader_name=self.name,
        )


class CompositeGrader(Grader):
    """Combine graders with AND/OR logic.

    Args:
        graders: List of graders to combine.
        mode: "all" for AND (all must pass), "any" for OR (at least one passes).
    """

    name = "composite"

    def __init__(self, graders: list[Grader], mode: str = "all") -> None:
        self._graders = graders
        self._mode = mode

    def grade(self, task: dict[str, Any], result: dict[str, Any]) -> GradeResult:
        """Run all sub-graders and combine results.

        For mode='all': all must pass (AND). Score = mean of sub-scores.
        For mode='any': at least one passes (OR). Score = max of sub-scores.

        Args:
            task: The task dictionary.
            result: The result dictionary.

        Returns:
            Combined GradeResult.
        """
        if not self._graders:
            return GradeResult(
                passed=True,
                score=1.0,
                reason="No graders to run",
                grader_name=self.name,
            )

        sub_results = [g.grade(task, result) for g in self._graders]
        scores = [r.score for r in sub_results]
        reasons = [r.reason for r in sub_results]

        if self._mode == "all":
            passed = all(r.passed for r in sub_results)
            score = sum(scores) / len(scores)
        else:  # mode == "any"
            passed = any(r.passed for r in sub_results)
            score = max(scores)

        return GradeResult(
            passed=passed,
            score=score,
            reason="; ".join(reasons),
            grader_name=self.name,
        )
