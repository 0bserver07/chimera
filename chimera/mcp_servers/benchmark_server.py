#!/usr/bin/env python3
"""MCP server for running code evaluations.

Exposes two tools:

- ``chimera_eval(code, test_code)`` -- run Python code against test code
  and return pass/fail with output.
- ``chimera_humaneval(problem_id)`` -- return a HumanEval problem prompt
  for a given problem ID (offline subset).

Usage::

    python -m chimera.mcp_servers.benchmark_server
    # or
    python chimera/mcp_servers/benchmark_server.py

Configure in ``.mcp.json`` for Claude Code::

    {
      "mcpServers": {
        "chimera-benchmark": {
          "command": "python3",
          "args": ["chimera/mcp_servers/benchmark_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

__all__ = ["BenchmarkMCPServer", "run_eval", "EvalResult"]


# -- Server metadata -------------------------------------------------------

SERVER_NAME = "chimera-benchmark"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


# -- Tool definitions ------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "chimera_eval",
        "description": (
            "Run Python code against test code. Combines the code and test "
            "code into a single script, executes it, and reports pass/fail."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python code (function/class implementation) to evaluate.",
                },
                "test_code": {
                    "type": "string",
                    "description": (
                        "Test code that exercises the implementation. "
                        "Should use assert statements or pytest style."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Execution timeout in seconds (default: 30).",
                    "default": 30,
                },
            },
            "required": ["code", "test_code"],
        },
    },
    {
        "name": "chimera_humaneval",
        "description": (
            "Get a HumanEval problem prompt by ID. Returns the function "
            "signature, docstring, and test cases for the problem."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "problem_id": {
                    "type": "string",
                    "description": (
                        "Problem ID (e.g. 'HumanEval/0' or just '0'). "
                        "Available problems: 0-9 (built-in subset)."
                    ),
                },
            },
            "required": ["problem_id"],
        },
    },
]


# -- Eval engine ------------------------------------------------------------

class EvalResult:
    """Result of a code evaluation.

    Attributes:
        passed: Whether all tests passed.
        output: Combined stdout/stderr from execution.
        error: Error message if execution failed.
        returncode: Process return code.
    """

    def __init__(
        self,
        passed: bool,
        output: str = "",
        error: str = "",
        returncode: int = 0,
    ) -> None:
        self.passed = passed
        self.output = output
        self.error = error
        self.returncode = returncode


def run_eval(code: str, test_code: str, timeout: int = 30) -> EvalResult:
    """Run Python code against test code in a subprocess.

    Combines *code* and *test_code* into a temporary file, executes it
    with the current Python interpreter, and reports pass/fail.

    Args:
        code: Python code implementing functions/classes.
        test_code: Test code exercising the implementation.
        timeout: Maximum execution time in seconds.

    Returns:
        An :class:`EvalResult` with pass/fail status and output.
    """
    combined = f"{code}\n\n{test_code}\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False,
    ) as f:
        f.write(combined)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr

        return EvalResult(
            passed=result.returncode == 0,
            output=output.strip(),
            error=result.stderr.strip() if result.returncode != 0 else "",
            returncode=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return EvalResult(
            passed=False,
            output="",
            error=f"Execution timed out after {timeout} seconds",
            returncode=-1,
        )
    except Exception as e:
        return EvalResult(
            passed=False,
            output="",
            error=str(e),
            returncode=-1,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# -- HumanEval problem subset -----------------------------------------------

# A small built-in subset of HumanEval problems for offline use.
# Full dataset requires `datasets` library.
HUMANEVAL_PROBLEMS: dict[str, dict[str, str]] = {
    "0": {
        "prompt": textwrap.dedent("""\
            from typing import List

            def has_close_elements(numbers: List[float], threshold: float) -> bool:
                \"\"\"Check if in given list of numbers, are any two numbers closer
                to each other than given threshold.
                >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
                False
                >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
                True
                \"\"\"
        """),
        "test": textwrap.dedent("""\
            assert has_close_elements([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.3) == True
            assert has_close_elements([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.05) == False
            assert has_close_elements([1.0, 2.0, 5.9, 4.0, 5.0], 0.95) == True
            assert has_close_elements([1.0, 2.0, 5.9, 4.0, 5.0], 0.8) == False
            assert has_close_elements([1.0, 2.0, 3.0, 4.0, 5.0], 2.0) == True
            assert has_close_elements([1.1, 2.2, 3.1, 4.1, 5.1], 1.0) == True
            assert has_close_elements([1.1, 2.2, 3.1, 4.1, 5.1], 0.5) == False
            print("All tests passed!")
        """),
    },
    "1": {
        "prompt": textwrap.dedent("""\
            from typing import List

            def separate_paren_groups(paren_string: str) -> List[str]:
                \"\"\"Input to this function is a string containing multiple groups
                of nested parentheses. Your goal is to separate those groups into
                separate strings and return the list of those.
                Separate groups are balanced (each open brace is properly closed)
                and not nested within each other. Ignore any spaces in the input string.
                >>> separate_paren_groups('( ) (( )) (( )( ))')
                ['()', '(())', '(()())']
                \"\"\"
        """),
        "test": textwrap.dedent("""\
            assert separate_paren_groups('(()()) ((())) () ((())()())') == ['(()())', '((()))', '()', '((())()())']
            assert separate_paren_groups('() (()) ((())) (((())))') == ['()', '(())', '((()))', '(((())))']
            assert separate_paren_groups('(()(()))') == ['(()(()))']
            print("All tests passed!")
        """),
    },
    "2": {
        "prompt": textwrap.dedent("""\
            def truncate_number(number: float) -> float:
                \"\"\"Given a positive floating point number, it can be decomposed
                into an integer part (largest integer smaller than given number)
                and decimals (leftover part always smaller than 1).
                Return the decimal part of the number.
                >>> truncate_number(3.5)
                0.5
                \"\"\"
        """),
        "test": textwrap.dedent("""\
            assert truncate_number(3.5) == 0.5
            assert abs(truncate_number(1.33) - 0.33) < 1e-6
            assert abs(truncate_number(123.456) - 0.456) < 1e-6
            print("All tests passed!")
        """),
    },
    "3": {
        "prompt": textwrap.dedent("""\
            from typing import List

            def below_zero(operations: List[int]) -> bool:
                \"\"\"You're given a list of deposit and withdrawal operations on a
                bank account that starts with zero balance. Your task is to detect
                if at any point the balance of account falls below zero, and at
                that point function should return True. Otherwise it should return
                False.
                >>> below_zero([1, 2, 3])
                False
                >>> below_zero([1, 2, -4, 5])
                True
                \"\"\"
        """),
        "test": textwrap.dedent("""\
            assert below_zero([]) == False
            assert below_zero([1, 2, -3, 1, 2, -3]) == False
            assert below_zero([1, 2, -4, 5, 6]) == True
            assert below_zero([1, -1, 2, -2, 5, -5, 4, -4]) == False
            assert below_zero([1, -1, 2, -2, 5, -5, 4, -5]) == True
            assert below_zero([1, -2]) == True
            assert below_zero([1, -2, 3]) == True
            print("All tests passed!")
        """),
    },
    "4": {
        "prompt": textwrap.dedent("""\
            from typing import List

            def mean_absolute_deviation(numbers: List[float]) -> float:
                \"\"\"For a given list of input numbers, calculate Mean Absolute
                Deviation around the mean of this dataset.
                Mean Absolute Deviation is the average absolute difference between
                each element and a centerpoint (mean in this case):
                MAD = average | x - x_mean |
                >>> mean_absolute_deviation([1.0, 2.0, 3.0, 4.0])
                1.0
                \"\"\"
        """),
        "test": textwrap.dedent("""\
            assert abs(mean_absolute_deviation([1.0, 2.0, 3.0]) - 2/3) < 1e-6
            assert abs(mean_absolute_deviation([1.0, 2.0, 3.0, 4.0]) - 1.0) < 1e-6
            assert abs(mean_absolute_deviation([1.0, 2.0, 3.0, 4.0, 5.0]) - 1.2) < 1e-6
            print("All tests passed!")
        """),
    },
}


def get_humaneval_problem(problem_id: str) -> dict[str, str] | None:
    """Retrieve a HumanEval problem by ID.

    Args:
        problem_id: Problem identifier, e.g. ``"HumanEval/0"`` or ``"0"``.

    Returns:
        Dict with ``"prompt"`` and ``"test"`` keys, or ``None`` if not found.
    """
    # Normalize: "HumanEval/0" -> "0"
    pid = problem_id.replace("HumanEval/", "").strip()
    return HUMANEVAL_PROBLEMS.get(pid)


# -- MCP server --------------------------------------------------------------

class BenchmarkMCPServer:
    """MCP server for code evaluation and benchmarking.

    Reads JSON-RPC messages from stdin (newline-delimited) and writes
    responses to stdout.
    """

    def __init__(self) -> None:
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

        if tool_name == "chimera_eval":
            return self._call_eval(arguments)
        elif tool_name == "chimera_humaneval":
            return self._call_humaneval(arguments)
        else:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    # -- Tool implementations ------------------------------------------------

    def _call_eval(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute chimera_eval tool.

        Args:
            arguments: Must contain ``code`` and ``test_code``.

        Returns:
            MCP content response with evaluation results.
        """
        code = arguments.get("code", "")
        test_code = arguments.get("test_code", "")
        timeout = arguments.get("timeout", 30)

        if not code:
            return {
                "content": [{"type": "text", "text": "Error: code is required"}],
                "isError": True,
            }
        if not test_code:
            return {
                "content": [{"type": "text", "text": "Error: test_code is required"}],
                "isError": True,
            }

        result = run_eval(code, test_code, timeout=timeout)

        status = "PASSED" if result.passed else "FAILED"
        lines = [f"Evaluation: {status}"]

        if result.output:
            lines.append(f"\nOutput:\n{result.output}")
        if result.error:
            lines.append(f"\nError:\n{result.error}")

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    def _call_humaneval(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute chimera_humaneval tool.

        Args:
            arguments: Must contain ``problem_id``.

        Returns:
            MCP content response with the problem prompt and tests.
        """
        problem_id = arguments.get("problem_id", "")

        if not problem_id:
            return {
                "content": [{"type": "text", "text": "Error: problem_id is required"}],
                "isError": True,
            }

        problem = get_humaneval_problem(problem_id)

        if problem is None:
            available = ", ".join(sorted(HUMANEVAL_PROBLEMS.keys()))
            return {
                "content": [{
                    "type": "text",
                    "text": f"Problem '{problem_id}' not found. Available: {available}",
                }],
                "isError": True,
            }

        lines = [
            f"HumanEval Problem {problem_id}",
            "=" * 40,
            "",
            "Prompt:",
            problem["prompt"],
            "",
            "Test Cases:",
            problem["test"],
        ]

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
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
    """Entry point for the MCP benchmark server."""
    server = BenchmarkMCPServer()
    server.run()


if __name__ == "__main__":
    main()
