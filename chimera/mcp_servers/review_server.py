#!/usr/bin/env python3
"""MCP server providing multi-perspective code review.

Exposes a single tool:

- ``chimera_review_diff(diff_text)`` -- review a diff from 4 perspectives
  (logic, security, tests, architecture) and return structured findings.

The review uses rule-based heuristic analysis that works without an LLM
provider.  When an LLM provider is available, it can be injected for
richer analysis.

Usage::

    python -m chimera.mcp_servers.review_server
    # or
    python chimera/mcp_servers/review_server.py

Configure in ``.mcp.json`` for any compatible MCP host::

    {
      "mcpServers": {
        "chimera-review": {
          "command": "python3",
          "args": ["chimera/mcp_servers/review_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any

__all__ = ["ReviewMCPServer", "ReviewFinding", "review_diff"]


# -- Server metadata -------------------------------------------------------

SERVER_NAME = "chimera-review"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


# -- Tool definitions ------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "chimera_review_diff",
        "description": (
            "Review a code diff from 4 perspectives: logic, security, "
            "tests, and architecture. Returns structured findings with "
            "severity, file, line, category, and message."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff_text": {
                    "type": "string",
                    "description": "The unified diff text to review.",
                },
            },
            "required": ["diff_text"],
        },
    },
]


# -- Data types -------------------------------------------------------------

@dataclass
class ReviewFinding:
    """A single finding from code review.

    Attributes:
        severity: One of ``"info"``, ``"warning"``, ``"error"``,
            ``"critical"``.
        file: File path from the diff header.
        line: Line number (0 if unknown).
        category: Review perspective: ``"logic"``, ``"security"``,
            ``"tests"``, ``"architecture"``.
        message: Human-readable description of the finding.
    """

    severity: str
    file: str
    line: int
    category: str
    message: str


# -- Rule-based review engine -----------------------------------------------

# Patterns for security issues
_SECURITY_PATTERNS: list[tuple[str, str, str]] = [
    (r"eval\s*\(", "warning", "Use of eval() can lead to code injection"),
    (r"exec\s*\(", "warning", "Use of exec() can lead to code injection"),
    (r"subprocess\.call\s*\(.*shell\s*=\s*True", "error", "Shell injection risk: shell=True with subprocess"),
    (r"os\.system\s*\(", "warning", "Prefer subprocess over os.system for security"),
    (r"password\s*=\s*['\"]", "critical", "Hardcoded password detected"),
    (r"(?:api[_-]?key|secret|token)\s*=\s*['\"][^'\"]{8,}", "critical", "Hardcoded secret/API key detected"),
    (r"pickle\.loads?\s*\(", "warning", "Pickle deserialization can execute arbitrary code"),
    (r"yaml\.load\s*\([^)]*\)(?!.*Loader)", "warning", "Use yaml.safe_load() instead of yaml.load()"),
    (r"import\s+marshal", "info", "Marshal module used — ensure inputs are trusted"),
    (r"chmod\s+777", "error", "Overly permissive file permissions (777)"),
]

# Patterns for logic issues
_LOGIC_PATTERNS: list[tuple[str, str, str]] = [
    (r"except\s*:", "warning", "Bare except clause catches all exceptions including SystemExit"),
    (r"except\s+Exception\s*:", "info", "Broad exception handler — consider catching specific exceptions"),
    (r"# ?TODO", "info", "TODO comment found — unfinished work"),
    (r"# ?FIXME", "warning", "FIXME comment found — known issue needs attention"),
    (r"# ?HACK", "warning", "HACK comment found — technical debt"),
    (r"\.get\([^)]+\)\.[^\s]", "info", "Chained call after .get() may fail if key is missing (returns None)"),
    (r"== None\b", "info", "Use 'is None' instead of '== None'"),
    (r"!= None\b", "info", "Use 'is not None' instead of '!= None'"),
    (r"type\(\w+\)\s*==", "info", "Use isinstance() instead of type() comparison"),
    (r"while\s+True\s*:", "info", "Infinite loop — ensure break condition exists"),
]

# Patterns for architecture issues
_ARCHITECTURE_PATTERNS: list[tuple[str, str, str]] = [
    (r"from\s+\.\.\.", "info", "Deep relative import — consider absolute imports"),
    (r"global\s+\w+", "warning", "Global variable mutation reduces testability"),
    (r"class\s+\w+.*\(.*,.*,.*,.*\)", "info", "Class with many bases — consider composition over inheritance"),
    (r"def\s+\w+\([^)]{200,}\)", "warning", "Function with many parameters — consider a config object"),
    (r"import\s+\*", "warning", "Wildcard import pollutes namespace"),
]

# Patterns for missing tests
_TEST_PATTERNS: list[tuple[str, str, str]] = [
    (r"def\s+(test_)", "info", "New test function added"),
    (r"assert\s+", "info", "Assert statement found"),
]


def _parse_diff_files(diff_text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Parse a unified diff into per-file (filename, [(line, text)]) pairs.

    Args:
        diff_text: Unified diff text.

    Returns:
        List of (filename, added_lines) tuples.
    """
    files: list[tuple[str, list[tuple[int, str]]]] = []
    current_file = ""
    current_lines: list[tuple[int, str]] = []
    line_number = 0

    for raw_line in diff_text.splitlines():
        # Detect file header
        m = re.match(r"^\+\+\+\s+b/(.+)", raw_line)
        if m:
            if current_file and current_lines:
                files.append((current_file, current_lines))
            current_file = m.group(1)
            current_lines = []
            continue

        # Detect hunk header to track line numbers
        hunk = re.match(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)", raw_line)
        if hunk:
            line_number = int(hunk.group(1))
            continue

        # Added lines
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            current_lines.append((line_number, raw_line[1:]))
            line_number += 1
        elif not raw_line.startswith("-"):
            line_number += 1

    if current_file and current_lines:
        files.append((current_file, current_lines))

    return files


def _check_patterns(
    file_name: str,
    lines: list[tuple[int, str]],
    patterns: list[tuple[str, str, str]],
    category: str,
) -> list[ReviewFinding]:
    """Check lines against a set of regex patterns.

    Args:
        file_name: Source file path.
        lines: List of (line_number, line_text) tuples.
        patterns: List of (regex, severity, message) tuples.
        category: Review category for findings.

    Returns:
        List of findings.
    """
    findings: list[ReviewFinding] = []
    for line_no, text in lines:
        for pattern, severity, message in patterns:
            if re.search(pattern, text):
                findings.append(ReviewFinding(
                    severity=severity,
                    file=file_name,
                    line=line_no,
                    category=category,
                    message=message,
                ))
    return findings


def _check_test_coverage(
    diff_files: list[tuple[str, list[tuple[int, str]]]],
) -> list[ReviewFinding]:
    """Check if modified source files have corresponding test updates.

    Args:
        diff_files: Parsed diff files.

    Returns:
        Findings about missing test coverage.
    """
    findings: list[ReviewFinding] = []
    source_files = set()
    test_files = set()

    for file_name, lines in diff_files:
        if file_name.startswith("test_") or "/test_" in file_name or "/tests/" in file_name:
            test_files.add(file_name)
        elif file_name.endswith(".py"):
            # Check if new public functions were added
            for line_no, text in lines:
                if re.match(r"\s*def\s+[a-z]\w*\s*\(", text) and not text.strip().startswith("def _"):
                    source_files.add(file_name)

    for src in source_files:
        if not test_files:
            findings.append(ReviewFinding(
                severity="warning",
                file=src,
                line=0,
                category="tests",
                message="New public function(s) added but no test file changes in this diff",
            ))

    return findings


def review_diff(diff_text: str) -> list[ReviewFinding]:
    """Review a unified diff from four perspectives.

    Performs rule-based analysis across:
    - **logic**: Code correctness patterns
    - **security**: Vulnerability and secret patterns
    - **tests**: Test coverage gaps
    - **architecture**: Code organization patterns

    Args:
        diff_text: Unified diff text.

    Returns:
        List of :class:`ReviewFinding` objects.
    """
    if not diff_text.strip():
        return []

    diff_files = _parse_diff_files(diff_text)
    findings: list[ReviewFinding] = []

    for file_name, lines in diff_files:
        findings.extend(_check_patterns(file_name, lines, _SECURITY_PATTERNS, "security"))
        findings.extend(_check_patterns(file_name, lines, _LOGIC_PATTERNS, "logic"))
        findings.extend(_check_patterns(file_name, lines, _ARCHITECTURE_PATTERNS, "architecture"))

    # Test coverage analysis
    findings.extend(_check_test_coverage(diff_files))

    return findings


# -- MCP server --------------------------------------------------------------

class ReviewMCPServer:
    """MCP server that exposes multi-perspective code review.

    Reads JSON-RPC messages from stdin (newline-delimited) and writes
    responses to stdout.

    Args:
        reviewer: Optional callable that takes diff text and returns
            findings.  Defaults to the rule-based :func:`review_diff`.
    """

    def __init__(
        self,
        reviewer: Any | None = None,
    ) -> None:
        self._reviewer = reviewer or review_diff
        self._initialized = False

    # -- JSON-RPC dispatch ---------------------------------------------------

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC message.

        Args:
            message: Parsed JSON-RPC request or notification.

        Returns:
            JSON-RPC response dict, or None for notifications.
        """
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        if msg_id is None:
            return None

        handler = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }.get(method)

        if handler is None:
            return self._error_response(msg_id, -32601, f"Method not found: {method}")

        try:
            result = handler(params)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:
            return self._error_response(msg_id, -32603, str(e))

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        self._initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": TOOL_DEFINITIONS}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "chimera_review_diff":
            return self._call_review_diff(arguments)
        else:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    # -- Tool implementation -------------------------------------------------

    def _call_review_diff(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute chimera_review_diff tool.

        Args:
            arguments: Must contain ``diff_text``.

        Returns:
            MCP content response with structured findings.
        """
        diff_text = arguments.get("diff_text", "")

        if not diff_text:
            return {
                "content": [{"type": "text", "text": "Error: diff_text is required"}],
                "isError": True,
            }

        findings = self._reviewer(diff_text)

        if not findings:
            return {
                "content": [{"type": "text", "text": "No issues found in the diff."}],
            }

        # Format as structured JSON
        findings_data = [asdict(f) if isinstance(f, ReviewFinding) else f for f in findings]
        result_text = json.dumps(findings_data, indent=2)

        # Also format a human-readable summary
        summary_lines = [f"Found {len(findings)} issue(s):\n"]
        for f in findings:
            if isinstance(f, ReviewFinding):
                loc = f.file
                if f.line:
                    loc += f":{f.line}"
                summary_lines.append(
                    f"  [{f.severity.upper()}] ({f.category}) {loc}: {f.message}"
                )

        return {
            "content": [
                {"type": "text", "text": "\n".join(summary_lines)},
                {"type": "text", "text": f"\nStructured findings:\n{result_text}"},
            ],
        }

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _error_response(msg_id: int | str, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    # -- Stdio loop ----------------------------------------------------------

    def run(self) -> None:
        """Run the MCP server, reading from stdin and writing to stdout."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                error = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                sys.stdout.write(json.dumps(error) + "\n")
                sys.stdout.flush()
                continue

            response = self.handle_message(message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


def main() -> None:
    """Entry point for the MCP review server."""
    server = ReviewMCPServer()
    server.run()


if __name__ == "__main__":
    main()
