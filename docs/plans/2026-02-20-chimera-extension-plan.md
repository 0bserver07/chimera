# Chimera Extension Plan — Phases 9-13

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend Chimera from MVP vertical slice (Phases 1-8, 163 tests) to full framework coverage — new tools, providers, agent composition, evaluation, and environments.

**Architecture:** Bottom-up by dependency. Phase 9 (tools/internals) first since providers and composition depend on tool infrastructure. Phase 10 (providers) unlocks real synthesis. Phase 11 (composition/loops/strategies) adds advanced agent patterns. Phase 12 (evaluation) adds benchmarking. Phase 13 (environments/CLI) rounds out the stack.

**Tech Stack:** Python 3.11+, zero required deps, optional extras (`anthropic`, `openai`, `google-generativeai`, `ollama`, `httpx`, `docker`). TDD throughout.

**Reference codebase:** `../UniClaudeProxy` — borrow converter patterns for providers.

---

## Phase 9: Tools, Approval, and Internal Utilities (Tasks 19-31)

### Task 19: EditFileTool

**Files:**
- Create: `chimera/tools/edit.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_edit.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_edit.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.edit import EditFileTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestEditFileTool:
    def test_replace_exact_match(self, env):
        env.write_file("main.py", "def hello():\n    return 'hi'\n")
        tool = EditFileTool()
        result = tool.execute({
            "path": "main.py",
            "old_string": "return 'hi'",
            "new_string": "return 'hello'",
        }, env)
        assert result.success
        assert env.read_file("main.py") == "def hello():\n    return 'hello'\n"

    def test_replace_not_found(self, env):
        env.write_file("main.py", "def hello():\n    pass\n")
        tool = EditFileTool()
        result = tool.execute({
            "path": "main.py",
            "old_string": "NONEXISTENT",
            "new_string": "something",
        }, env)
        assert not result.success
        assert "not found" in result.error.lower()

    def test_replace_ambiguous(self, env):
        env.write_file("main.py", "x = 1\nx = 1\n")
        tool = EditFileTool()
        result = tool.execute({
            "path": "main.py",
            "old_string": "x = 1",
            "new_string": "x = 2",
        }, env)
        assert not result.success
        assert "ambiguous" in result.error.lower() or "multiple" in result.error.lower()

    def test_file_not_found(self, env):
        tool = EditFileTool()
        result = tool.execute({
            "path": "nope.py",
            "old_string": "a",
            "new_string": "b",
        }, env)
        assert not result.success

    def test_schema(self):
        tool = EditFileTool()
        assert tool.name == "edit_file"
        schema = tool.to_anthropic_schema()
        assert "old_string" in str(schema)
        assert "new_string" in str(schema)
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tools_edit.py -v`
Expected: FAIL (ImportError)

**Step 3: Write minimal implementation**

```python
# chimera/tools/edit.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Replace an exact string in a file with a new string. The old_string must appear exactly once."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "old_string": {"type": "string", "description": "Exact string to find (must be unique)"},
            "new_string": {"type": "string", "description": "Replacement string"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        try:
            content = env.read_file(args["path"])
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {args['path']}")

        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)

        if count == 0:
            return ToolResult(output="", error=f"String not found in {args['path']}")
        if count > 1:
            return ToolResult(output="", error=f"Multiple matches ({count}) found — ambiguous. Provide more context.")

        updated = content.replace(old, new, 1)
        env.write_file(args["path"], updated)
        return ToolResult(output=f"Edited {args['path']}")
```

**Step 4: Update `chimera/tools/__init__.py`**

Add to `chimera/tools/__init__.py`:
```python
from chimera.tools.edit import EditFileTool
edit_file = EditFileTool()
# Add to __all__: "EditFileTool", "edit_file"
```

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_edit.py -v`
Expected: 5 PASSED

**Step 6: Commit**

```bash
git add chimera/tools/edit.py chimera/tools/__init__.py tests/test_tools_edit.py
git commit -m "feat: add edit_file tool with exact string replacement"
```

---

### Task 20: SearchTool

**Files:**
- Create: `chimera/tools/search.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_search.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_search.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.search import SearchTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestSearchTool:
    def test_search_finds_match(self, env):
        env.write_file("main.py", "def hello():\n    return 'hi'\n")
        tool = SearchTool()
        result = tool.execute({"pattern": "hello", "path": "."}, env)
        assert result.success
        assert "main.py" in result.output

    def test_search_no_match(self, env):
        env.write_file("main.py", "def hello():\n    pass\n")
        tool = SearchTool()
        result = tool.execute({"pattern": "NONEXISTENT", "path": "."}, env)
        assert result.success
        assert result.output.strip() == "" or "no matches" in result.output.lower()

    def test_search_specific_file(self, env):
        env.write_file("a.py", "foo = 1\n")
        env.write_file("b.py", "bar = 2\n")
        tool = SearchTool()
        result = tool.execute({"pattern": "foo", "path": "a.py"}, env)
        assert result.success
        assert "a.py" in result.output

    def test_search_glob_filter(self, env):
        env.write_file("main.py", "hello\n")
        env.write_file("main.txt", "hello\n")
        tool = SearchTool()
        result = tool.execute({"pattern": "hello", "path": ".", "glob": "*.py"}, env)
        assert result.success
        assert "main.py" in result.output

    def test_schema(self):
        tool = SearchTool()
        assert tool.name == "search"
        schema = tool.to_anthropic_schema()
        assert "pattern" in str(schema)
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tools_search.py -v`
Expected: FAIL (ImportError)

**Step 3: Write minimal implementation**

```python
# chimera/tools/search.py
from __future__ import annotations

import fnmatch
import re
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class SearchTool(BaseTool):
    name = "search"
    description = "Search for a regex pattern across files. Returns matching lines with file paths and line numbers."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "File or directory to search in", "default": "."},
            "glob": {"type": "string", "description": "Glob filter for filenames (e.g. '*.py')", "default": None},
        },
        "required": ["pattern"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        pattern = args["pattern"]
        search_path = args.get("path", ".")
        glob_filter = args.get("glob")

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(output="", error=f"Invalid regex: {e}")

        # Get files to search
        files = env.list_files("**/*")
        if search_path != ".":
            files = [f for f in files if f == search_path or f.startswith(search_path + "/")]
        if glob_filter:
            files = [f for f in files if fnmatch.fnmatch(f.split("/")[-1], glob_filter)]

        matches: list[str] = []
        for filepath in sorted(files):
            try:
                content = env.read_file(filepath)
            except (FileNotFoundError, UnicodeDecodeError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{filepath}:{i}: {line}")

        if not matches:
            return ToolResult(output="No matches found.")
        return ToolResult(output="\n".join(matches))
```

**Step 4: Update `chimera/tools/__init__.py`**

Add: `from chimera.tools.search import SearchTool`, `search = SearchTool()`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_search.py -v`
Expected: 5 PASSED

**Step 6: Commit**

```bash
git add chimera/tools/search.py chimera/tools/__init__.py tests/test_tools_search.py
git commit -m "feat: add search tool with regex and glob filtering"
```

---

### Task 21: ListFilesTool

**Files:**
- Create: `chimera/tools/list_files.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_list_files.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_list_files.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.list_files import ListFilesTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestListFilesTool:
    def test_list_all_files(self, env):
        env.write_file("a.py", "x")
        env.write_file("b.txt", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": "."}, env)
        assert result.success
        assert "a.py" in result.output
        assert "b.txt" in result.output

    def test_list_with_glob(self, env):
        env.write_file("a.py", "x")
        env.write_file("b.txt", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": ".", "glob": "*.py"}, env)
        assert result.success
        assert "a.py" in result.output
        assert "b.txt" not in result.output

    def test_list_subdirectory(self, env):
        env.write_file("src/main.py", "x")
        env.write_file("tests/test.py", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": "src"}, env)
        assert result.success
        assert "main.py" in result.output

    def test_empty_directory(self, env):
        tool = ListFilesTool()
        result = tool.execute({"path": "."}, env)
        assert result.success

    def test_schema(self):
        tool = ListFilesTool()
        assert tool.name == "list_files"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tools_list_files.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/tools/list_files.py
from __future__ import annotations

import fnmatch
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class ListFilesTool(BaseTool):
    name = "list_files"
    description = "List files in a directory, optionally filtered by glob pattern."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list", "default": "."},
            "glob": {"type": "string", "description": "Glob filter (e.g. '*.py')", "default": None},
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        path = args.get("path", ".")
        glob_filter = args.get("glob")

        files = env.list_files("**/*")
        if path != ".":
            files = [f for f in files if f.startswith(path + "/") or f.startswith(path)]
        if glob_filter:
            files = [f for f in files if fnmatch.fnmatch(f.split("/")[-1], glob_filter)]

        return ToolResult(output="\n".join(sorted(files)) if files else "No files found.")
```

**Step 4: Update `chimera/tools/__init__.py`**

Add: `from chimera.tools.list_files import ListFilesTool`, `list_files = ListFilesTool()`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_list_files.py -v`
Expected: 5 PASSED

**Step 6: Commit**

```bash
git add chimera/tools/list_files.py chimera/tools/__init__.py tests/test_tools_list_files.py
git commit -m "feat: add list_files tool with glob filtering"
```

---

### Task 22: TestTool

**Files:**
- Create: `chimera/tools/test.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_test.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_test.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.test import TestTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="python -m pytest")
        e.setup()
        yield e
        e.cleanup()


class TestTestTool:
    def test_run_all_tests(self, env):
        env.write_file("test_hello.py", "def test_pass():\n    assert True\n")
        tool = TestTool()
        result = tool.execute({}, env)
        assert result.success
        assert "1 passed" in result.output

    def test_run_specific_file(self, env):
        env.write_file("test_a.py", "def test_a():\n    assert True\n")
        env.write_file("test_b.py", "def test_b():\n    assert False\n")
        tool = TestTool()
        result = tool.execute({"path": "test_a.py"}, env)
        assert result.success
        assert "1 passed" in result.output

    def test_run_failing_test(self, env):
        env.write_file("test_fail.py", "def test_fail():\n    assert False\n")
        tool = TestTool()
        result = tool.execute({}, env)
        assert "failed" in result.output.lower()

    def test_schema(self):
        tool = TestTool()
        assert tool.name == "test"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tools_test.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/tools/test.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class TestTool(BaseTool):
    name = "test"
    description = "Run the test suite. Optionally specify a path to run specific tests."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Specific test file or directory to run"},
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        path = args.get("path")

        if path:
            # Run specific test file using bash
            result = env.run_command(f"python -m pytest {path} -v")
        else:
            # Run full test suite via env.run_tests()
            test_result = env.run_tests()
            return ToolResult(output=test_result.output)

        output = result.stdout
        if result.stderr:
            output += f"\n{result.stderr}"
        return ToolResult(output=output)
```

**Step 4: Update `chimera/tools/__init__.py`**

Add: `from chimera.tools.test import TestTool`, `test = TestTool()`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_test.py -v`
Expected: 4 PASSED

**Step 6: Commit**

```bash
git add chimera/tools/test.py chimera/tools/__init__.py tests/test_tools_test.py
git commit -m "feat: add test tool for running pytest from agent"
```

---

### Task 23: WebFetchTool

**Files:**
- Create: `chimera/tools/web_fetch.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_web_fetch.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_web_fetch.py
from unittest.mock import patch, MagicMock

import pytest

from chimera.tools.web_fetch import WebFetchTool


class TestWebFetchTool:
    def test_fetch_success(self):
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>Hello World</body></html>"
        mock_response.headers = {"content-type": "text/html"}

        with patch("chimera.tools.web_fetch.httpx") as mock_httpx:
            mock_httpx.get.return_value = mock_response
            result = tool.execute({"url": "https://example.com"}, None)
        assert result.success
        assert "Hello World" in result.output

    def test_fetch_failure(self):
        tool = WebFetchTool()
        with patch("chimera.tools.web_fetch.httpx") as mock_httpx:
            mock_httpx.get.side_effect = Exception("Connection refused")
            result = tool.execute({"url": "https://bad.example.com"}, None)
        assert not result.success
        assert "Connection refused" in result.error

    def test_fetch_no_httpx(self):
        tool = WebFetchTool()
        with patch("chimera.tools.web_fetch.httpx", None):
            result = tool.execute({"url": "https://example.com"}, None)
        assert not result.success
        assert "httpx" in result.error.lower()

    def test_schema(self):
        tool = WebFetchTool()
        assert tool.name == "web_fetch"
        schema = tool.to_anthropic_schema()
        assert "url" in str(schema)
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tools_web_fetch.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/tools/web_fetch.py
from __future__ import annotations

import re
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "Fetch a URL and return its content as text. HTML tags are stripped."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
        },
        "required": ["url"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if httpx is None:
            return ToolResult(output="", error="httpx not installed. pip install httpx")

        url = args["url"]
        try:
            response = httpx.get(url, timeout=30, follow_redirects=True)
            content = response.text
            # Strip HTML tags for readability
            if "text/html" in response.headers.get("content-type", ""):
                content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
                content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
                content = re.sub(r"<[^>]+>", "", content)
                content = re.sub(r"\s+", " ", content).strip()
            return ToolResult(output=content[:50000])  # Truncate large pages
        except Exception as e:
            return ToolResult(output="", error=str(e))
```

**Step 4: Update `chimera/tools/__init__.py`**

Add: `from chimera.tools.web_fetch import WebFetchTool`, `web_fetch = WebFetchTool()`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_web_fetch.py -v`
Expected: 4 PASSED

**Step 6: Commit**

```bash
git add chimera/tools/web_fetch.py chimera/tools/__init__.py tests/test_tools_web_fetch.py
git commit -m "feat: add web_fetch tool with HTML stripping"
```

---

### Task 24: GitTool

**Files:**
- Create: `chimera/tools/git.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_git.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_git.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.git import GitTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        # Initialize a git repo
        e.run_command("git init")
        e.run_command("git config user.email 'test@test.com'")
        e.run_command("git config user.name 'Test'")
        yield e
        e.cleanup()


class TestGitTool:
    def test_git_status(self, env):
        tool = GitTool()
        result = tool.execute({"command": "status"}, env)
        assert result.success

    def test_git_add_and_commit(self, env):
        env.write_file("test.txt", "hello")
        tool = GitTool()
        result = tool.execute({"command": "add test.txt"}, env)
        assert result.success
        result = tool.execute({"command": "commit -m 'initial'"}, env)
        assert result.success

    def test_git_log(self, env):
        env.write_file("test.txt", "hello")
        tool = GitTool()
        tool.execute({"command": "add test.txt"}, env)
        tool.execute({"command": "commit -m 'initial'"}, env)
        result = tool.execute({"command": "log --oneline"}, env)
        assert result.success
        assert "initial" in result.output

    def test_git_diff(self, env):
        env.write_file("test.txt", "hello")
        tool = GitTool()
        tool.execute({"command": "add test.txt"}, env)
        tool.execute({"command": "commit -m 'initial'"}, env)
        env.write_file("test.txt", "hello world")
        result = tool.execute({"command": "diff"}, env)
        assert result.success

    def test_blocked_commands(self, env):
        tool = GitTool()
        result = tool.execute({"command": "push --force"}, env)
        assert not result.success
        assert "blocked" in result.error.lower() or "not allowed" in result.error.lower()

    def test_schema(self):
        tool = GitTool()
        assert tool.name == "git"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tools_git.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/tools/git.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class GitTool(BaseTool):
    name = "git"
    description = "Run git commands in the workspace. Destructive commands (push --force, reset --hard) are blocked."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Git subcommand and arguments (e.g. 'status', 'add .', 'commit -m msg')"},
        },
        "required": ["command"],
    }

    BLOCKED_PATTERNS = [
        "push --force", "push -f",
        "reset --hard",
        "clean -f", "clean -fd",
        "branch -D",
    ]

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        command = args["command"]

        # Safety check
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in command:
                return ToolResult(output="", error=f"Blocked: 'git {pattern}' is not allowed for safety.")

        result = env.run_command(f"git {command}")
        output = result.stdout
        if result.stderr:
            output += f"\n{result.stderr}"
        if result.success:
            return ToolResult(output=output)
        return ToolResult(output=output, error=f"git {command} failed (exit {result.exit_code})")
```

**Step 4: Update `chimera/tools/__init__.py`**

Add: `from chimera.tools.git import GitTool`, `git = GitTool()`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_git.py -v`
Expected: 6 PASSED

**Step 6: Commit**

```bash
git add chimera/tools/git.py chimera/tools/__init__.py tests/test_tools_git.py
git commit -m "feat: add git tool with destructive command blocking"
```

---

### Task 25: ReplaceInFileTool

**Files:**
- Create: `chimera/tools/replace_in_file.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_replace.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_replace.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.replace_in_file import ReplaceInFileTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestReplaceInFileTool:
    def test_replace_all_occurrences(self, env):
        env.write_file("main.py", "x = 1\ny = 1\nz = 1\n")
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "main.py",
            "pattern": "= 1",
            "replacement": "= 2",
        }, env)
        assert result.success
        content = env.read_file("main.py")
        assert content.count("= 2") == 3
        assert "= 1" not in content

    def test_regex_replace(self, env):
        env.write_file("main.py", "foo_bar = 1\nfoo_baz = 2\n")
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "main.py",
            "pattern": r"foo_(\w+)",
            "replacement": r"bar_\1",
        }, env)
        assert result.success
        content = env.read_file("main.py")
        assert "bar_bar" in content
        assert "bar_baz" in content

    def test_no_match(self, env):
        env.write_file("main.py", "hello\n")
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "main.py",
            "pattern": "NOPE",
            "replacement": "yes",
        }, env)
        assert result.success
        assert "0" in result.output or "no" in result.output.lower()

    def test_file_not_found(self, env):
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "nope.py",
            "pattern": "a",
            "replacement": "b",
        }, env)
        assert not result.success

    def test_schema(self):
        tool = ReplaceInFileTool()
        assert tool.name == "replace_in_file"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tools_replace.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/tools/replace_in_file.py
from __future__ import annotations

import re
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class ReplaceInFileTool(BaseTool):
    name = "replace_in_file"
    description = "Replace all occurrences of a regex pattern in a file."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "pattern": {"type": "string", "description": "Regex pattern to match"},
            "replacement": {"type": "string", "description": "Replacement string (supports \\1 backreferences)"},
        },
        "required": ["path", "pattern", "replacement"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        try:
            content = env.read_file(args["path"])
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {args['path']}")

        try:
            updated, count = re.subn(args["pattern"], args["replacement"], content)
        except re.error as e:
            return ToolResult(output="", error=f"Invalid regex: {e}")

        if count == 0:
            return ToolResult(output=f"0 replacements made in {args['path']}")

        env.write_file(args["path"], updated)
        return ToolResult(output=f"{count} replacement(s) made in {args['path']}")
```

**Step 4: Update `chimera/tools/__init__.py`**

Add: `from chimera.tools.replace_in_file import ReplaceInFileTool`, `replace_in_file = ReplaceInFileTool()`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_replace.py -v`
Expected: 5 PASSED

**Step 6: Commit**

```bash
git add chimera/tools/replace_in_file.py chimera/tools/__init__.py tests/test_tools_replace.py
git commit -m "feat: add replace_in_file tool with regex support"
```

---

### Task 26: Approval Workflow

**Files:**
- Create: `chimera/core/approval.py`
- Modify: `chimera/core/tool.py` (add `requires_approval` field)
- Modify: `chimera/core/loop.py` (check approval before executing)
- Modify: `chimera/core/__init__.py`
- Test: `tests/test_approval.py`

**Step 1: Write the failing tests**

```python
# tests/test_approval.py
from __future__ import annotations

from chimera.core.approval import ApprovalPolicy, AutoApprove, AlwaysDeny, AllowList
from chimera.core.tool import BaseTool
from chimera.types import ToolResult


class FakeTool(BaseTool):
    name = "fake"
    description = "A fake tool"
    parameters = {"type": "object", "properties": {}, "required": []}
    requires_approval = True

    def execute(self, args, env=None):
        return ToolResult(output="ok")


class SafeTool(BaseTool):
    name = "safe"
    description = "A safe tool"
    parameters = {"type": "object", "properties": {}, "required": []}
    requires_approval = False

    def execute(self, args, env=None):
        return ToolResult(output="ok")


class TestAutoApprove:
    def test_auto_approve_always_returns_true(self):
        policy = AutoApprove()
        assert policy.should_approve("fake", {}) is True

    def test_auto_approve_any_tool(self):
        policy = AutoApprove()
        assert policy.should_approve("bash", {"command": "rm -rf /"}) is True


class TestAlwaysDeny:
    def test_deny_returns_false(self):
        policy = AlwaysDeny()
        assert policy.should_approve("fake", {}) is False


class TestAllowList:
    def test_allowed_tool(self):
        policy = AllowList(allowed=["read_file", "search"])
        assert policy.should_approve("read_file", {}) is True

    def test_denied_tool(self):
        policy = AllowList(allowed=["read_file"])
        assert policy.should_approve("bash", {}) is False


class TestToolApprovalFlag:
    def test_tool_requires_approval_default(self):
        """BaseTool.requires_approval defaults to False."""
        from chimera.tools.read import ReadFileTool
        tool = ReadFileTool()
        assert tool.requires_approval is False

    def test_tool_can_set_requires_approval(self):
        tool = FakeTool()
        assert tool.requires_approval is True
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_approval.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# chimera/core/approval.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ApprovalPolicy(ABC):
    """Decides whether a tool invocation should proceed."""

    @abstractmethod
    def should_approve(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Return True to allow, False to deny."""


class AutoApprove(ApprovalPolicy):
    """Approve everything. Default for non-interactive use."""

    def should_approve(self, tool_name: str, args: dict[str, Any]) -> bool:
        return True


class AlwaysDeny(ApprovalPolicy):
    """Deny everything. Useful for dry-run mode."""

    def should_approve(self, tool_name: str, args: dict[str, Any]) -> bool:
        return False


class AllowList(ApprovalPolicy):
    """Only approve tools on the allow list."""

    def __init__(self, allowed: list[str]) -> None:
        self._allowed = set(allowed)

    def should_approve(self, tool_name: str, args: dict[str, Any]) -> bool:
        return tool_name in self._allowed
```

**Step 4: Add `requires_approval` to BaseTool**

In `chimera/core/tool.py`, add class attribute to `BaseTool`:
```python
class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]
    requires_approval: bool = False  # ADD THIS LINE
```

**Step 5: Update `chimera/core/__init__.py`**

Add: `from chimera.core.approval import ApprovalPolicy, AutoApprove, AlwaysDeny, AllowList`, update `__all__`.

**Step 6: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_approval.py -v`
Expected: 7 PASSED

**Step 7: Commit**

```bash
git add chimera/core/approval.py chimera/core/tool.py chimera/core/__init__.py tests/test_approval.py
git commit -m "feat: add approval workflow with AutoApprove, AlwaysDeny, AllowList policies"
```

---

### Task 27: ToolGroup

**Files:**
- Create: `chimera/core/tool_group.py`
- Modify: `chimera/core/__init__.py`
- Test: `tests/test_tool_group.py`

**Step 1: Write the failing tests**

```python
# tests/test_tool_group.py
from chimera.core.tool_group import ToolGroup
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool
from chimera.tools.bash import BashTool


class TestToolGroup:
    def test_create_group(self):
        group = ToolGroup("file_ops", [ReadFileTool(), WriteFileTool()])
        assert group.name == "file_ops"
        assert len(group.tools) == 2

    def test_group_has_tool(self):
        group = ToolGroup("file_ops", [ReadFileTool(), WriteFileTool()])
        assert group.has("read_file")
        assert group.has("write_file")
        assert not group.has("bash")

    def test_group_get_tool(self):
        group = ToolGroup("file_ops", [ReadFileTool(), WriteFileTool()])
        tool = group.get("read_file")
        assert tool is not None
        assert tool.name == "read_file"

    def test_group_iter(self):
        group = ToolGroup("all", [ReadFileTool(), WriteFileTool(), BashTool()])
        names = [t.name for t in group]
        assert names == ["read_file", "write_file", "bash"]

    def test_group_add(self):
        group = ToolGroup("ops", [ReadFileTool()])
        group.add(BashTool())
        assert len(group.tools) == 2
        assert group.has("bash")

    def test_predefined_default_group(self):
        from chimera.core.tool_group import DEFAULT_TOOLS
        assert len(DEFAULT_TOOLS.tools) >= 3
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tool_group.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/core/tool_group.py
from __future__ import annotations

from chimera.core.tool import BaseTool


class ToolGroup:
    """A named collection of tools. Like a preset toolkit."""

    def __init__(self, name: str, tools: list[BaseTool]) -> None:
        self.name = name
        self.tools = list(tools)
        self._map = {t.name: t for t in self.tools}

    def has(self, name: str) -> bool:
        return name in self._map

    def get(self, name: str) -> BaseTool | None:
        return self._map.get(name)

    def add(self, tool: BaseTool) -> None:
        self.tools.append(tool)
        self._map[tool.name] = tool

    def __iter__(self):
        return iter(self.tools)

    def __len__(self):
        return len(self.tools)


# Predefined groups
def _make_default_tools() -> ToolGroup:
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.bash import BashTool
    return ToolGroup("default", [ReadFileTool(), WriteFileTool(), BashTool()])


DEFAULT_TOOLS = _make_default_tools()
```

**Step 4: Update `chimera/core/__init__.py`**

Add: `from chimera.core.tool_group import ToolGroup, DEFAULT_TOOLS`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tool_group.py -v`
Expected: 6 PASSED

**Step 6: Commit**

```bash
git add chimera/core/tool_group.py chimera/core/__init__.py tests/test_tool_group.py
git commit -m "feat: add ToolGroup with predefined DEFAULT_TOOLS"
```

---

### Task 28: DelegateTool

**Files:**
- Create: `chimera/tools/delegate.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_delegate.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_delegate.py
from __future__ import annotations

import tempfile

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.tools.delegate import DelegateTool
from chimera.types import Message, ToolCall


class EchoProvider(Provider):
    """Returns the task text as output, no tool calls."""
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        # Find the last user message
        last_user = ""
        for m in messages:
            if m.role == "user":
                last_user = m.content
        return Response(content=f"Processed: {last_user}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})

    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return True
    @property
    def model_name(self): return "echo"


class TestDelegateTool:
    def test_delegate_runs_sub_agent(self):
        sub_agent = Agent(provider=EchoProvider(), tools=[], loop=ReAct(max_steps=5))
        tool = DelegateTool(sub_agent=sub_agent)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = tool.execute({"task": "Fix the bug in main.py"}, env)
            assert result.success
            assert "Processed:" in result.output
            assert "Fix the bug" in result.output

    def test_delegate_schema(self):
        sub_agent = Agent(provider=EchoProvider())
        tool = DelegateTool(sub_agent=sub_agent)
        assert tool.name == "delegate"
        schema = tool.to_anthropic_schema()
        assert "task" in str(schema)

    def test_delegate_custom_name(self):
        sub_agent = Agent(provider=EchoProvider())
        tool = DelegateTool(sub_agent=sub_agent, tool_name="ask_researcher")
        assert tool.name == "ask_researcher"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_tools_delegate.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/tools/delegate.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class DelegateTool(BaseTool):
    """Wraps an Agent as a tool, enabling sub-agent delegation."""

    description = "Delegate a task to a sub-agent."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task to delegate to the sub-agent"},
        },
        "required": ["task"],
    }

    def __init__(self, sub_agent: Any, tool_name: str = "delegate") -> None:
        from chimera.core.agent import Agent
        self._sub_agent: Agent = sub_agent
        self.name = tool_name

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        task = args["task"]
        result = self._sub_agent.run(task, env)
        if result.success:
            return ToolResult(output=result.output)
        return ToolResult(output=result.output, error=result.error)
```

**Step 4: Update `chimera/tools/__init__.py`**

Add: `from chimera.tools.delegate import DelegateTool`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_delegate.py -v`
Expected: 3 PASSED

**Step 6: Commit**

```bash
git add chimera/tools/delegate.py chimera/tools/__init__.py tests/test_tools_delegate.py
git commit -m "feat: add delegate tool for sub-agent dispatch"
```

---

### Task 29: Loop Detection

**Files:**
- Create: `chimera/core/loop_detection.py`
- Modify: `chimera/core/loop.py` (integrate detection into ReAct)
- Modify: `chimera/core/__init__.py`
- Test: `tests/test_loop_detection.py`

**Step 1: Write the failing tests**

```python
# tests/test_loop_detection.py
from chimera.core.loop_detection import LoopDetector


class TestLoopDetector:
    def test_no_loop_initially(self):
        d = LoopDetector()
        assert d.is_looping() is False

    def test_detects_repeated_tool_calls(self):
        d = LoopDetector(window=3, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        assert d.is_looping() is True

    def test_different_args_not_loop(self):
        d = LoopDetector(window=3, threshold=3)
        d.record("read_file", {"path": "a.py"})
        d.record("read_file", {"path": "b.py"})
        d.record("read_file", {"path": "c.py"})
        assert d.is_looping() is False

    def test_window_sliding(self):
        d = LoopDetector(window=3, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        d.record("write_file", {"path": "out.py", "content": "x"})
        d.record("read_file", {"path": "main.py"})
        assert d.is_looping() is False

    def test_pattern_detection(self):
        """Detects A-B-A-B-A-B pattern."""
        d = LoopDetector(window=6, threshold=2)
        for _ in range(3):
            d.record("read_file", {"path": "main.py"})
            d.record("write_file", {"path": "main.py", "content": "x"})
        assert d.is_looping() is True

    def test_reset(self):
        d = LoopDetector(window=3, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        assert d.is_looping() is True
        d.reset()
        assert d.is_looping() is False
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_loop_detection.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/core/loop_detection.py
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any


class LoopDetector:
    """Detects when an agent is stuck in a loop.

    Tracks recent tool calls and detects:
    1. Exact repetition (same call N times)
    2. Pattern repetition (A-B-A-B cycle)
    """

    def __init__(self, window: int = 10, threshold: int = 3) -> None:
        self.window = window
        self.threshold = threshold
        self._history: deque[str] = deque(maxlen=window)

    def record(self, tool_name: str, args: dict[str, Any]) -> None:
        """Record a tool call."""
        sig = self._signature(tool_name, args)
        self._history.append(sig)

    def is_looping(self) -> bool:
        """Check if the agent is stuck in a loop."""
        if len(self._history) < self.threshold:
            return False

        items = list(self._history)

        # Check 1: Same call repeated N times at the tail
        if len(set(items[-self.threshold:])) == 1:
            return True

        # Check 2: Repeating pattern (period 1 to window//2)
        for period in range(2, len(items) // 2 + 1):
            if len(items) >= period * self.threshold:
                # Extract the last `period * threshold` items
                tail = items[-(period * self.threshold):]
                pattern = tail[:period]
                repeats = True
                for i in range(1, self.threshold):
                    chunk = tail[i * period:(i + 1) * period]
                    if chunk != pattern:
                        repeats = False
                        break
                if repeats:
                    return True

        return False

    def reset(self) -> None:
        self._history.clear()

    @staticmethod
    def _signature(tool_name: str, args: dict[str, Any]) -> str:
        """Create a hash signature for a tool call."""
        raw = json.dumps({"name": tool_name, "args": args}, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()
```

**Step 4: Update `chimera/core/__init__.py`**

Add: `from chimera.core.loop_detection import LoopDetector`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_loop_detection.py -v`
Expected: 6 PASSED

**Step 6: Commit**

```bash
git add chimera/core/loop_detection.py chimera/core/__init__.py tests/test_loop_detection.py
git commit -m "feat: add loop detection with sliding window and pattern matching"
```

---

### Task 30: Context Compression

**Files:**
- Create: `chimera/core/compression.py`
- Modify: `chimera/core/__init__.py`
- Test: `tests/test_compression.py`

**Step 1: Write the failing tests**

```python
# tests/test_compression.py
from chimera.core.compression import ContextCompressor
from chimera.types import Message


class TestContextCompressor:
    def test_no_compression_under_limit(self):
        c = ContextCompressor(max_messages=10)
        messages = [Message.user(f"msg {i}") for i in range(5)]
        result = c.compress(messages)
        assert len(result) == 5

    def test_compress_over_limit_keeps_first_and_last(self):
        c = ContextCompressor(max_messages=4, keep_first=1, keep_last=2)
        messages = [Message.user(f"msg {i}") for i in range(10)]
        result = c.compress(messages)
        assert len(result) == 4  # 1 first + 1 summary + 2 last
        assert result[0].content == "msg 0"  # First kept
        assert result[-1].content == "msg 9"  # Last kept
        assert result[-2].content == "msg 8"  # Second-to-last kept

    def test_compress_includes_summary(self):
        c = ContextCompressor(max_messages=4, keep_first=1, keep_last=1)
        messages = [Message.user(f"msg {i}") for i in range(10)]
        result = c.compress(messages)
        # Middle message should be a summary
        summary = result[1]
        assert summary.role == "system" or "summarized" in summary.content.lower() or "compressed" in summary.content.lower()

    def test_tool_messages_compressed(self):
        c = ContextCompressor(max_messages=4, keep_first=1, keep_last=1)
        messages = [
            Message.user("Do something"),
            Message.assistant("I'll read the file", tool_calls=[]),
            Message.tool("call_1", "A" * 10000),  # Large tool output
            Message.user("Thanks"),
        ]
        result = c.compress(messages)
        assert len(result) <= 4

    def test_compress_empty(self):
        c = ContextCompressor(max_messages=10)
        result = c.compress([])
        assert result == []
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_compression.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/core/compression.py
from __future__ import annotations

from chimera.types import Message


class ContextCompressor:
    """Compresses conversation history to fit within context limits.

    Strategy: Keep first N and last M messages, replace middle with a summary.
    """

    def __init__(
        self,
        max_messages: int = 50,
        keep_first: int = 2,
        keep_last: int = 10,
    ) -> None:
        self.max_messages = max_messages
        self.keep_first = keep_first
        self.keep_last = keep_last

    def compress(self, messages: list[Message]) -> list[Message]:
        """Compress messages if they exceed max_messages."""
        if len(messages) <= self.max_messages:
            return list(messages)

        first = messages[:self.keep_first]
        last = messages[-self.keep_last:]
        middle = messages[self.keep_first:-self.keep_last] if self.keep_last > 0 else messages[self.keep_first:]

        # Summarize the middle section
        summary_text = self._summarize(middle)
        summary = Message.system(f"[Compressed {len(middle)} messages] {summary_text}")

        return first + [summary] + last

    def _summarize(self, messages: list[Message]) -> str:
        """Create a brief summary of compressed messages."""
        tool_calls = 0
        user_msgs = 0
        assistant_msgs = 0
        for m in messages:
            if m.role == "user":
                user_msgs += 1
            elif m.role == "assistant":
                assistant_msgs += 1
                tool_calls += len(m.tool_calls)
            elif m.role == "tool":
                tool_calls += 1

        parts = []
        if user_msgs:
            parts.append(f"{user_msgs} user messages")
        if assistant_msgs:
            parts.append(f"{assistant_msgs} assistant messages")
        if tool_calls:
            parts.append(f"{tool_calls} tool interactions")

        return f"Summarized: {', '.join(parts)}" if parts else "Summarized conversation."
```

**Step 4: Update `chimera/core/__init__.py`**

Add: `from chimera.core.compression import ContextCompressor`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_compression.py -v`
Expected: 5 PASSED

**Step 6: Commit**

```bash
git add chimera/core/compression.py chimera/core/__init__.py tests/test_compression.py
git commit -m "feat: add context compression with keep-first/keep-last strategy"
```

---

### Task 31: Streaming Support

**Files:**
- Create: `chimera/core/streaming.py`
- Modify: `chimera/core/__init__.py`
- Test: `tests/test_streaming.py`

**Step 1: Write the failing tests**

```python
# tests/test_streaming.py
from chimera.core.streaming import StreamHandler, PrintStreamHandler, CollectStreamHandler
from chimera.providers.base import StreamEvent


class TestStreamHandler:
    def test_collect_handler(self):
        handler = CollectStreamHandler()
        handler.on_text("Hello ")
        handler.on_text("world")
        handler.on_tool_start("read_file", "call_1")
        handler.on_tool_end("call_1", "file contents")
        assert handler.text == "Hello world"
        assert len(handler.events) == 4

    def test_print_handler_no_crash(self, capsys):
        handler = PrintStreamHandler()
        handler.on_text("Hello")
        handler.on_tool_start("bash", "call_1")
        handler.on_tool_end("call_1", "output")
        handler.on_done()
        captured = capsys.readouterr()
        assert "Hello" in captured.out

    def test_handler_from_stream_events(self):
        handler = CollectStreamHandler()
        events = [
            StreamEvent(type="text_delta", content="Hi"),
            StreamEvent(type="text_delta", content=" there"),
            StreamEvent(type="done"),
        ]
        for event in events:
            handler.handle_event(event)
        assert handler.text == "Hi there"

    def test_custom_handler(self):
        """Verify the base class can be subclassed."""
        class MyHandler(StreamHandler):
            def __init__(self):
                self.chunks = []
            def on_text(self, text): self.chunks.append(text)
            def on_tool_start(self, name, call_id): pass
            def on_tool_end(self, call_id, output): pass
            def on_done(self): pass

        h = MyHandler()
        h.on_text("hello")
        assert h.chunks == ["hello"]
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_streaming.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# chimera/core/streaming.py
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from chimera.providers.base import StreamEvent


class StreamHandler(ABC):
    """Base class for handling streaming agent output."""

    @abstractmethod
    def on_text(self, text: str) -> None:
        """Called when text content is streamed."""

    @abstractmethod
    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        """Called when a tool call begins."""

    @abstractmethod
    def on_tool_end(self, call_id: str, output: str) -> None:
        """Called when a tool call completes."""

    @abstractmethod
    def on_done(self) -> None:
        """Called when streaming is complete."""

    def handle_event(self, event: StreamEvent) -> None:
        """Dispatch a StreamEvent to the appropriate handler method."""
        if event.type == "text_delta":
            self.on_text(event.content)
        elif event.type == "tool_call_start" and event.tool_call:
            self.on_tool_start(event.tool_call.name, event.tool_call.id)
        elif event.type == "done":
            self.on_done()


class PrintStreamHandler(StreamHandler):
    """Prints streaming output to stdout (Claude Code-like experience)."""

    def on_text(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        print(f"\n> Running {tool_name}...", flush=True)

    def on_tool_end(self, call_id: str, output: str) -> None:
        if output.strip():
            # Show truncated output
            lines = output.strip().splitlines()
            if len(lines) > 10:
                shown = "\n".join(lines[:5] + ["...", f"({len(lines)} lines total)"] + lines[-3:])
            else:
                shown = output.strip()
            print(shown, flush=True)

    def on_done(self) -> None:
        print(flush=True)


class CollectStreamHandler(StreamHandler):
    """Collects all events for inspection/testing."""

    def __init__(self) -> None:
        self.text = ""
        self.events: list[dict] = []

    def on_text(self, text: str) -> None:
        self.text += text
        self.events.append({"type": "text", "content": text})

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        self.events.append({"type": "tool_start", "name": tool_name, "call_id": call_id})

    def on_tool_end(self, call_id: str, output: str) -> None:
        self.events.append({"type": "tool_end", "call_id": call_id, "output": output})

    def on_done(self) -> None:
        self.events.append({"type": "done"})
```

**Step 4: Update `chimera/core/__init__.py`**

Add: `from chimera.core.streaming import StreamHandler, PrintStreamHandler, CollectStreamHandler`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_streaming.py -v`
Expected: 4 PASSED

**Step 6: Commit**

```bash
git add chimera/core/streaming.py chimera/core/__init__.py tests/test_streaming.py
git commit -m "feat: add streaming handlers (Print, Collect, base StreamHandler)"
```

---

## Phase 10: Providers (Tasks 32-36)

### Task 32: OpenAI Provider

**Files:**
- Create: `chimera/providers/openai.py`
- Modify: `chimera/providers/__init__.py`
- Test: `tests/test_provider_openai.py`

**Reference:** Borrow conversion patterns from UniClaudeProxy at `../UniClaudeProxy/app/converters/`.

**Step 1: Write the failing tests**

```python
# tests/test_provider_openai.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.openai import OpenAIProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.openai.openai") as mock_mod:
        mock_client = MagicMock()
        mock_mod.OpenAI.return_value = mock_client
        p = OpenAIProvider(model="gpt-4o", api_key="test-key")
        p._client = mock_client
        yield p, mock_client


def test_complete_text_response(provider):
    prov, mock_client = provider

    mock_choice = MagicMock()
    mock_choice.message.content = "Hello!"
    mock_choice.message.tool_calls = None
    mock_choice.finish_reason = "stop"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 20

    mock_client.chat.completions.create.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello!"
    assert result.has_tool_calls is False
    assert result.usage["input_tokens"] == 100


def test_complete_tool_call(provider):
    prov, mock_client = provider

    mock_tc = MagicMock()
    mock_tc.id = "call_1"
    mock_tc.function.name = "read_file"
    mock_tc.function.arguments = '{"path": "main.py"}'

    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_choice.message.tool_calls = [mock_tc]
    mock_choice.finish_reason = "tool_calls"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 150
    mock_response.usage.completion_tokens = 30

    mock_client.chat.completions.create.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "main.py"}


def test_system_message_handling(provider):
    prov, mock_client = provider

    mock_choice = MagicMock()
    mock_choice.message.content = "I'm an AI."
    mock_choice.message.tool_calls = None
    mock_choice.finish_reason = "stop"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 50
    mock_response.usage.completion_tokens = 10
    mock_client.chat.completions.create.return_value = mock_response

    prov.complete([Message.system("You are helpful"), Message.user("Hi")])
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args[1]["messages"]
    assert messages[0]["role"] == "system"


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window > 0


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "gpt-4o"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_provider_openai.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# chimera/providers/openai.py
from __future__ import annotations

import json
import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]


class OpenAIProvider(Provider):
    """OpenAI Chat Completions provider (GPT-4o, o1, o3, Codex, etc.)."""

    CONTEXT_WINDOWS = {
        "gpt-4o": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-4": 8_192,
        "gpt-3.5-turbo": 16_385,
        "o1": 200_000,
        "o3": 200_000,
        "codex": 200_000,
    }

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if openai is None:
            raise ImportError("pip install chimera-ai[openai]")
        self._model = model
        self._client = openai.OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url,
        )

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        api_messages = self._convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Extract text
        content = choice.message.content or ""

        # Extract tool calls
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))

        return Response(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Chimera messages to OpenAI format."""
        api_messages = []
        for msg in messages:
            if msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.call_id,
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                tc_list = []
                for tc in msg.tool_calls:
                    tc_list.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    })
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": tc_list,
                })
            else:
                api_messages.append({"role": msg.role, "content": msg.content})
        return api_messages

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        """Convert Anthropic tool schema to OpenAI function schema."""
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", tool.get("parameters", {})),
                },
            })
        return result

    @property
    def context_window(self) -> int:
        for prefix, size in self.CONTEXT_WINDOWS.items():
            if self._model.startswith(prefix):
                return size
        return 128_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model
```

**Step 4: Update `chimera/providers/__init__.py`**

Add: `from chimera.providers.openai import OpenAIProvider` (inside try/except to handle missing dep).

Actually, follow same pattern as anthropic — just add the import. Users who don't have `openai` installed will get ImportError at instantiation time, not at import time.

```python
# Keep it simple — expose it but lazy-import guards are in the class
```

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_provider_openai.py -v`
Expected: 5 PASSED

**Step 6: Commit**

```bash
git add chimera/providers/openai.py chimera/providers/__init__.py tests/test_provider_openai.py
git commit -m "feat: add OpenAI provider with Chat Completions API"
```

---

### Task 33: Google Gemini Provider

**Files:**
- Create: `chimera/providers/google.py`
- Modify: `chimera/providers/__init__.py`
- Test: `tests/test_provider_google.py`

**Reference:** UniClaudeProxy `app/converters/anthropic_to_gemini.py` and `gemini_to_anthropic.py` for format conversion. Key differences: Gemini uses `contents` with `parts`, tool calls are `functionCall`, tool results are `functionResponse`, schemas need stripping of unsupported JSON Schema keys.

**Step 1: Write the failing tests**

```python
# tests/test_provider_google.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.google import GoogleProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.google.genai") as mock_mod:
        mock_model = MagicMock()
        mock_mod.GenerativeModel.return_value = mock_model
        mock_mod.configure = MagicMock()
        p = GoogleProvider(model="gemini-2.0-flash", api_key="test-key")
        p._model = mock_model
        yield p, mock_model


def test_complete_text_response(provider):
    prov, mock_model = provider

    mock_response = MagicMock()
    mock_response.text = "Hello from Gemini!"
    mock_candidate = MagicMock()
    mock_part = MagicMock()
    mock_part.text = "Hello from Gemini!"
    mock_part.function_call = None
    mock_candidate.content.parts = [mock_part]
    mock_candidate.finish_reason = 1  # STOP
    mock_response.candidates = [mock_candidate]
    mock_response.usage_metadata.prompt_token_count = 100
    mock_response.usage_metadata.candidates_token_count = 20

    mock_model.generate_content.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello from Gemini!"
    assert result.has_tool_calls is False


def test_complete_tool_call(provider):
    prov, mock_model = provider

    mock_fc = MagicMock()
    mock_fc.name = "read_file"
    mock_fc.args = {"path": "main.py"}

    mock_part = MagicMock()
    mock_part.text = None
    mock_part.function_call = mock_fc

    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_candidate.finish_reason = 1
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_response.usage_metadata.prompt_token_count = 150
    mock_response.usage_metadata.candidates_token_count = 30

    mock_model.generate_content.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window > 0


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "gemini-2.0-flash"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_provider_google.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# chimera/providers/google.py
from __future__ import annotations

import os
import uuid
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import google.generativeai as genai
except ImportError:
    genai = None  # type: ignore[assignment]


class GoogleProvider(Provider):
    """Google Gemini provider."""

    CONTEXT_WINDOWS = {
        "gemini-2.0": 1_048_576,
        "gemini-1.5": 1_048_576,
        "gemini-1.0": 32_768,
    }

    def __init__(self, model: str, api_key: str | None = None) -> None:
        if genai is None:
            raise ImportError("pip install chimera-ai[google]")
        genai.configure(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
        self._model_name = model
        self._model = genai.GenerativeModel(model)

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        contents = self._convert_messages(messages)
        kwargs: dict[str, Any] = {}

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        generation_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            generation_config["max_output_tokens"] = max_tokens
        kwargs["generation_config"] = generation_config

        response = self._model.generate_content(contents, **kwargs)

        # Parse response
        text_parts = []
        tool_calls = []
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
                elif part.function_call:
                    fc = part.function_call
                    tool_calls.append(ToolCall(
                        id=f"call_{uuid.uuid4().hex[:12]}",
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    ))

        return Response(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage={
                "input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0),
                "output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0),
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Chimera messages to Gemini contents format."""
        contents = []
        for msg in messages:
            if msg.role == "system":
                # Gemini handles system prompt separately; prepend as user context
                contents.append({"role": "user", "parts": [{"text": f"[System] {msg.content}"}]})
            elif msg.role == "user":
                contents.append({"role": "user", "parts": [{"text": msg.content}]})
            elif msg.role == "assistant":
                parts = []
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls:
                    parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif msg.role == "tool":
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": {"name": "tool", "response": {"result": msg.content}}}],
                })
        return contents

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        """Convert tool schemas to Gemini function declarations."""
        declarations = []
        for tool in tools:
            schema = tool.get("input_schema", tool.get("parameters", {}))
            declarations.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": self._clean_schema(schema),
            })
        return [{"function_declarations": declarations}]

    @staticmethod
    def _clean_schema(schema: dict) -> dict:
        """Strip JSON Schema keys unsupported by Gemini."""
        unsupported = {"$schema", "$id", "$ref", "$comment", "$defs",
                       "additionalProperties", "patternProperties",
                       "anyOf", "oneOf", "allOf", "minLength", "maxLength", "pattern"}
        cleaned = {}
        for k, v in schema.items():
            if k in unsupported:
                continue
            if isinstance(v, dict):
                cleaned[k] = GoogleProvider._clean_schema(v)
            else:
                cleaned[k] = v
        return cleaned

    @property
    def context_window(self) -> int:
        for prefix, size in self.CONTEXT_WINDOWS.items():
            if self._model_name.startswith(prefix):
                return size
        return 1_048_576

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model_name
```

**Step 4: Update `chimera/providers/__init__.py`**

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_provider_google.py -v`
Expected: 4 PASSED

**Step 6: Commit**

```bash
git add chimera/providers/google.py chimera/providers/__init__.py tests/test_provider_google.py
git commit -m "feat: add Google Gemini provider"
```

---

### Task 34: Ollama Provider

**Files:**
- Create: `chimera/providers/ollama.py`
- Modify: `chimera/providers/__init__.py`
- Test: `tests/test_provider_ollama.py`

**Step 1: Write the failing tests**

```python
# tests/test_provider_ollama.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.ollama import OllamaProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.ollama.httpx") as mock_httpx:
        p = OllamaProvider(model="llama3.1", base_url="http://localhost:11434")
        yield p, mock_httpx


def test_complete_text_response(provider):
    prov, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "Hello!"},
        "eval_count": 20,
        "prompt_eval_count": 100,
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello!"
    assert result.has_tool_calls is False


def test_complete_tool_call(provider):
    prov, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "read_file",
                    "arguments": {"path": "main.py"},
                },
            }],
        },
        "eval_count": 30,
        "prompt_eval_count": 150,
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window > 0


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "llama3.1"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_provider_ollama.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# chimera/providers/ollama.py
from __future__ import annotations

import json
import uuid
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class OllamaProvider(Provider):
    """Ollama local model provider. Uses the Ollama HTTP API directly via httpx."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        context_length: int = 128_000,
    ) -> None:
        if httpx is None:
            raise ImportError("pip install httpx")
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._context_length = context_length

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        api_messages = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        if tools:
            payload["tools"] = self._convert_tools(tools)

        resp = httpx.post(
            f"{self._base_url}/api/chat",
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()

        # Parse response
        msg = data.get("message", {})
        content = msg.get("content", "")
        tool_calls = []

        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            args = func.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:12]}",
                name=func.get("name", ""),
                arguments=args,
            ))

        return Response(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": data.get("prompt_eval_count", 0),
                "output_tokens": data.get("eval_count", 0),
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        api_messages = []
        for msg in messages:
            if msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                tc_list = []
                for tc in msg.tool_calls:
                    tc_list.append({
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                    })
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": tc_list,
                })
            else:
                api_messages.append({"role": msg.role, "content": msg.content})
        return api_messages

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", tool.get("parameters", {})),
                },
            })
        return result

    @property
    def context_window(self) -> int:
        return self._context_length

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_provider_ollama.py -v`
Expected: 4 PASSED

**Step 5: Commit**

```bash
git add chimera/providers/ollama.py chimera/providers/__init__.py tests/test_provider_ollama.py
git commit -m "feat: add Ollama provider with httpx HTTP API"
```

---

### Task 35: OpenAI-Compatible Provider

**Files:**
- Create: `chimera/providers/compatible.py`
- Modify: `chimera/providers/__init__.py`
- Test: `tests/test_provider_compatible.py`

**Step 1: Write the failing tests**

```python
# tests/test_provider_compatible.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.compatible import OpenAICompatibleProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.compatible.httpx") as mock_httpx:
        p = OpenAICompatibleProvider(
            model="deepseek-r1",
            base_url="https://api.openrouter.ai/v1",
            api_key="test-key",
        )
        yield p, mock_httpx


def test_complete_text_response(provider):
    prov, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello!"
    assert result.has_tool_calls is False


def test_complete_tool_call(provider):
    prov, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "main.py"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 150, "completion_tokens": 30},
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"


def test_custom_headers(provider):
    prov, mock_httpx = provider
    prov._headers["X-Custom"] = "value"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    mock_httpx.post.return_value = mock_response

    prov.complete([Message.user("Hi")])
    call_args = mock_httpx.post.call_args
    assert "X-Custom" in call_args[1]["headers"]


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "deepseek-r1"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_provider_compatible.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# chimera/providers/compatible.py
from __future__ import annotations

import json
import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class OpenAICompatibleProvider(Provider):
    """Generic OpenAI-compatible provider.

    Works with: OpenRouter, Together, Fireworks, Groq, vLLM, LiteLLM,
    Anthropic Coding API (via OpenAI compatibility), any /v1/chat/completions endpoint.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        context_length: int = 128_000,
    ) -> None:
        if httpx is None:
            raise ImportError("pip install httpx")
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            **(headers or {}),
        }
        self._context_length = context_length

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        api_messages = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = self._convert_tools(tools)

        endpoint = f"{self._base_url}/chat/completions"
        resp = httpx.post(endpoint, json=payload, headers=self._headers, timeout=300)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        content = choice["message"].get("content") or ""

        tool_calls = []
        for tc in choice["message"].get("tool_calls", []) or []:
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(ToolCall(
                id=tc.get("id", f"call_{id(tc)}"),
                name=tc["function"]["name"],
                arguments=args,
            ))

        usage = data.get("usage", {})
        return Response(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        api_messages = []
        for msg in messages:
            if msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.call_id,
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                tc_list = []
                for tc in msg.tool_calls:
                    tc_list.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    })
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": tc_list,
                })
            else:
                api_messages.append({"role": msg.role, "content": msg.content})
        return api_messages

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", tool.get("parameters", {})),
                },
            })
        return result

    @property
    def context_window(self) -> int:
        return self._context_length

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_provider_compatible.py -v`
Expected: 4 PASSED

**Step 5: Commit**

```bash
git add chimera/providers/compatible.py chimera/providers/__init__.py tests/test_provider_compatible.py
git commit -m "feat: add OpenAI-compatible provider for OpenRouter, vLLM, etc."
```

---

### Task 36: Provider Factory

**Files:**
- Create: `chimera/providers/factory.py`
- Modify: `chimera/providers/__init__.py`
- Test: `tests/test_provider_factory.py`

**Step 1: Write the failing tests**

```python
# tests/test_provider_factory.py
from unittest.mock import patch, MagicMock

import pytest

from chimera.providers.factory import create_provider


def test_create_anthropic():
    with patch("chimera.providers.anthropic.anthropic") as mock:
        mock.Anthropic.return_value = MagicMock()
        p = create_provider("anthropic", model="claude-sonnet-4-20250514", api_key="test")
        assert p.model_name == "claude-sonnet-4-20250514"


def test_create_openai():
    with patch("chimera.providers.openai.openai") as mock:
        mock.OpenAI.return_value = MagicMock()
        p = create_provider("openai", model="gpt-4o", api_key="test")
        assert p.model_name == "gpt-4o"


def test_create_google():
    with patch("chimera.providers.google.genai") as mock:
        mock.GenerativeModel.return_value = MagicMock()
        p = create_provider("google", model="gemini-2.0-flash", api_key="test")
        assert p.model_name == "gemini-2.0-flash"


def test_create_ollama():
    with patch("chimera.providers.ollama.httpx") as mock:
        p = create_provider("ollama", model="llama3.1")
        assert p.model_name == "llama3.1"


def test_create_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider("unknown_provider", model="foo")


def test_create_from_model_string():
    """Infer provider from model name pattern."""
    with patch("chimera.providers.anthropic.anthropic") as mock:
        mock.Anthropic.return_value = MagicMock()
        p = create_provider(model="claude-sonnet-4-20250514", api_key="test")
        assert p.model_name == "claude-sonnet-4-20250514"

    with patch("chimera.providers.openai.openai") as mock:
        mock.OpenAI.return_value = MagicMock()
        p = create_provider(model="gpt-4o", api_key="test")
        assert p.model_name == "gpt-4o"
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_provider_factory.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# chimera/providers/factory.py
from __future__ import annotations

from chimera.providers.base import Provider


def create_provider(
    provider_type: str | None = None,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs,
) -> Provider:
    """Factory function to create a provider by type or by model name inference.

    Args:
        provider_type: One of "anthropic", "openai", "google", "ollama", "compatible".
                       If None, inferred from model name.
        model: Model identifier (e.g. "claude-sonnet-4-20250514", "gpt-4o", "gemini-2.0-flash").
        api_key: API key for the provider.
        base_url: Base URL override (for compatible/ollama providers).
    """
    # Infer provider from model name if not specified
    if provider_type is None:
        provider_type = _infer_provider(model)

    if provider_type == "anthropic":
        from chimera.providers.anthropic import AnthropicProvider
        return AnthropicProvider(model=model, api_key=api_key)

    elif provider_type == "openai":
        from chimera.providers.openai import OpenAIProvider
        return OpenAIProvider(model=model, api_key=api_key, base_url=base_url)

    elif provider_type == "google":
        from chimera.providers.google import GoogleProvider
        return GoogleProvider(model=model, api_key=api_key)

    elif provider_type == "ollama":
        from chimera.providers.ollama import OllamaProvider
        return OllamaProvider(
            model=model,
            base_url=base_url or "http://localhost:11434",
            **kwargs,
        )

    elif provider_type == "compatible":
        from chimera.providers.compatible import OpenAICompatibleProvider
        if base_url is None:
            raise ValueError("base_url required for 'compatible' provider")
        return OpenAICompatibleProvider(
            model=model,
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )

    else:
        raise ValueError(
            f"Unknown provider: '{provider_type}'. "
            f"Choose from: anthropic, openai, google, ollama, compatible"
        )


def _infer_provider(model: str) -> str:
    """Infer provider type from model name."""
    model_lower = model.lower()
    if model_lower.startswith("claude"):
        return "anthropic"
    if model_lower.startswith(("gpt", "o1", "o3", "codex")):
        return "openai"
    if model_lower.startswith("gemini"):
        return "google"
    if model_lower.startswith(("llama", "mistral", "qwen", "phi")):
        return "ollama"
    raise ValueError(
        f"Cannot infer provider from model name '{model}'. "
        f"Specify provider_type explicitly."
    )
```

**Step 4: Update `chimera/providers/__init__.py`**

Add: `from chimera.providers.factory import create_provider`, update `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_provider_factory.py -v`
Expected: 7 PASSED

**Step 6: Commit**

```bash
git add chimera/providers/factory.py chimera/providers/__init__.py tests/test_provider_factory.py
git commit -m "feat: add provider factory with model name inference"
```

---

## Phase 11: Agent Composition, Loops, and Strategies (Tasks 37-45)

### Task 37: Pipeline Composition

**Files:**
- Create: `chimera/composition/__init__.py`
- Create: `chimera/composition/pipeline.py`
- Test: `tests/test_composition_pipeline.py`

**Step 1: Write the failing tests**

```python
# tests/test_composition_pipeline.py
from __future__ import annotations

import tempfile

from chimera.composition.pipeline import Pipeline
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import Message


class CounterProvider(Provider):
    """Appends a counter to the output."""
    def __init__(self, label: str):
        self.label = label
        self._called = False
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._called = True
        last = messages[-1].content if messages else ""
        return Response(content=f"{last} -> {self.label}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return f"counter-{self.label}"


class TestPipeline:
    def test_sequential_execution(self):
        agents = [
            Agent(provider=CounterProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=CounterProvider("B"), loop=ReAct(max_steps=1)),
            Agent(provider=CounterProvider("C"), loop=ReAct(max_steps=1)),
        ]
        pipeline = Pipeline(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = pipeline.run("start", env)
            assert result.success
            assert "A" in result.output
            assert "B" in result.output
            assert "C" in result.output

    def test_empty_pipeline(self):
        pipeline = Pipeline([])
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = pipeline.run("input", env)
            assert result.output == "input"

    def test_pipeline_stops_on_failure(self):
        class FailProvider(Provider):
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return Response(content="fail", tool_calls=[], usage={"input_tokens": 0, "output_tokens": 0})
            @property
            def context_window(self): return 100_000
            @property
            def supports_tool_use(self): return False
            @property
            def model_name(self): return "fail"

        agents = [
            Agent(provider=CounterProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=CounterProvider("B"), loop=ReAct(max_steps=1)),
        ]
        pipeline = Pipeline(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = pipeline.run("start", env)
            assert result.success
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_composition_pipeline.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# chimera/composition/__init__.py
from chimera.composition.pipeline import Pipeline

__all__ = ["Pipeline"]
```

```python
# chimera/composition/pipeline.py
from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.types import AgentResult


class Pipeline:
    """Sequential agent composition: output of agent N becomes input of agent N+1."""

    def __init__(self, agents: list[Agent]) -> None:
        self.agents = agents

    def run(self, task: str, env: Environment | None) -> AgentResult:
        current_input = task
        total_steps = 0
        total_tool_calls = 0
        total_cost = 0.0

        for agent in self.agents:
            result = agent.run(current_input, env)
            total_steps += result.steps
            total_tool_calls += result.tool_calls_total
            total_cost += result.cost
            if not result.success:
                return AgentResult(
                    output=result.output,
                    steps=total_steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=False,
                    error=result.error,
                )
            current_input = result.output

        return AgentResult(
            output=current_input,
            steps=total_steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=True,
        )
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_composition_pipeline.py -v`
Expected: 3 PASSED

**Step 5: Commit**

```bash
git add chimera/composition/__init__.py chimera/composition/pipeline.py tests/test_composition_pipeline.py
git commit -m "feat: add Pipeline agent composition (sequential chaining)"
```

---

### Task 38: Ensemble Composition

**Files:**
- Create: `chimera/composition/ensemble.py`
- Modify: `chimera/composition/__init__.py`
- Test: `tests/test_composition_ensemble.py`

**Step 1: Write the failing tests**

```python
# tests/test_composition_ensemble.py
from __future__ import annotations

import tempfile

from chimera.composition.ensemble import Ensemble
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import Message


class LabelProvider(Provider):
    def __init__(self, label: str):
        self.label = label
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content=f"Result from {self.label}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return self.label


class TestEnsemble:
    def test_all_agents_run(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert len(results) == 2
            labels = [r.output for r in results]
            assert any("A" in l for l in labels)
            assert any("B" in l for l in labels)

    def test_best_result(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            best = ensemble.best(results)
            assert best.success

    def test_empty_ensemble(self):
        ensemble = Ensemble([])
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert results == []
```

**Step 2-6:** Standard TDD cycle. Write implementation:

```python
# chimera/composition/ensemble.py
from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.types import AgentResult


class Ensemble:
    """Parallel agent composition: all agents run the same task independently."""

    def __init__(self, agents: list[Agent]) -> None:
        self.agents = agents

    def run(self, task: str, env: Environment | None) -> list[AgentResult]:
        """Run all agents on the same task. Returns list of results."""
        results = []
        for agent in self.agents:
            result = agent.run(task, env)
            results.append(result)
        return results

    def best(self, results: list[AgentResult]) -> AgentResult:
        """Select the best result. Default: first successful result."""
        successful = [r for r in results if r.success]
        if successful:
            return successful[0]
        return results[0] if results else AgentResult(
            output="No results", steps=0, tool_calls_total=0, cost=0.0, success=False
        )
```

**Commit:**

```bash
git add chimera/composition/ensemble.py chimera/composition/__init__.py tests/test_composition_ensemble.py
git commit -m "feat: add Ensemble agent composition (parallel execution)"
```

---

### Task 39: Supervisor Composition

**Files:**
- Create: `chimera/composition/supervisor.py`
- Modify: `chimera/composition/__init__.py`
- Test: `tests/test_composition_supervisor.py`

**Step 1: Write the failing tests**

```python
# tests/test_composition_supervisor.py
from __future__ import annotations

import tempfile

from chimera.composition.supervisor import Supervisor
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall


class CoordinatorProvider(Provider):
    """Simulates a supervisor that delegates to workers."""
    def __init__(self):
        self._step = 0
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._step += 1
        if self._step == 1:
            return Response(
                content="I'll delegate to worker_a",
                tool_calls=[ToolCall(id="c1", name="delegate", arguments={"task": "Do the work"})],
                usage={"input_tokens": 100, "output_tokens": 50},
            )
        return Response(content="All done!", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return True
    @property
    def model_name(self): return "coordinator"


class WorkerProvider(Provider):
    def __init__(self, label: str):
        self.label = label
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content=f"Done by {self.label}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return self.label


class TestSupervisor:
    def test_supervisor_delegates(self):
        workers = {
            "worker_a": Agent(provider=WorkerProvider("A"), loop=ReAct(max_steps=1)),
        }
        supervisor = Supervisor(
            coordinator=Agent(provider=CoordinatorProvider(), loop=ReAct(max_steps=5)),
            workers=workers,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = supervisor.run("Manage the project", env)
            assert result.success

    def test_supervisor_no_workers(self):
        supervisor = Supervisor(
            coordinator=Agent(provider=WorkerProvider("solo"), loop=ReAct(max_steps=1)),
            workers={},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = supervisor.run("Do it yourself", env)
            assert result.success
```

**Step 2-6:** Standard TDD cycle. Implementation:

```python
# chimera/composition/supervisor.py
from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.tools.delegate import DelegateTool
from chimera.types import AgentResult


class Supervisor:
    """Coordinator + workers pattern. The coordinator agent gets delegate tools for each worker."""

    def __init__(self, coordinator: Agent, workers: dict[str, Agent]) -> None:
        self.coordinator = coordinator
        self.workers = workers
        # Add delegate tools for each worker
        for name, worker in workers.items():
            self.coordinator.tools.append(DelegateTool(sub_agent=worker, tool_name=name))

    def run(self, task: str, env: Environment | None) -> AgentResult:
        return self.coordinator.run(task, env)
```

**Commit:**

```bash
git add chimera/composition/supervisor.py chimera/composition/__init__.py tests/test_composition_supervisor.py
git commit -m "feat: add Supervisor composition (coordinator + workers)"
```

---

### Task 40: PlanAndExecute Loop

**Files:**
- Create: `chimera/core/loops/__init__.py`
- Create: `chimera/core/loops/plan_execute.py`
- Move: `chimera/core/loop.py` content into `chimera/core/loops/react.py` (keep backward compat re-export)
- Test: `tests/test_loop_plan_execute.py`

**Note:** The existing `chimera/core/loop.py` stays as a re-export for backward compatibility. New loops go in `chimera/core/loops/`.

**Step 1: Write the failing tests**

```python
# tests/test_loop_plan_execute.py
from __future__ import annotations

from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.core.context import Context
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall


class PlanProvider(Provider):
    """Simulates a plan-then-execute flow."""
    def __init__(self):
        self._step = 0
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._step += 1
        if self._step == 1:
            return Response(
                content="Plan:\n1. Read the file\n2. Fix the bug\n3. Write the file",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )
        if self._step == 2:
            return Response(
                content="Executing step 1",
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "main.py"})],
                usage={"input_tokens": 50, "output_tokens": 30},
            )
        return Response(content="Done!", tool_calls=[], usage={"input_tokens": 20, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return True
    @property
    def model_name(self): return "plan-provider"


class TestPlanAndExecute:
    def test_plan_then_execute(self):
        from chimera.tools.read import ReadFileTool
        from chimera.env.local import LocalEnvironment
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("main.py", "hello")

            loop = PlanAndExecute(max_steps=10)
            provider = PlanProvider()
            context = Context(system="You are helpful")
            context.add(Message.user("Fix the bug"))

            result = loop.run(provider, [ReadFileTool()], context, env)
            assert result.success
            assert result.steps >= 2

    def test_max_steps_respected(self):
        loop = PlanAndExecute(max_steps=1)
        provider = PlanProvider()
        context = Context()
        context.add(Message.user("Do something"))
        result = loop.run(provider, [], context, None)
        assert result.steps == 1
```

**Step 2-6:** Standard TDD cycle. Implementation:

```python
# chimera/core/loops/__init__.py
from chimera.core.loops.react import ReAct
from chimera.core.loops.plan_execute import PlanAndExecute

__all__ = ["ReAct", "PlanAndExecute"]
```

```python
# chimera/core/loops/react.py
# Re-export from original location for the loops package
from chimera.core.loop import ReAct

__all__ = ["ReAct"]
```

```python
# chimera/core/loops/plan_execute.py
from __future__ import annotations

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message


class PlanAndExecute:
    """Two-phase loop: first ask the LLM for a plan, then execute it step by step.

    Phase 1: Generate a plan (no tool calls expected)
    Phase 2: Execute the plan using tools (standard ReAct)
    """

    def __init__(self, max_steps: int = 50) -> None:
        self.max_steps = max_steps

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0

        for _ in range(self.max_steps):
            steps += 1
            response = provider.complete(
                context.to_messages(),
                tools=schemas if schemas else None,
            )
            context.add(Message.assistant(response.content, tool_calls=response.tool_calls))

            if not response.has_tool_calls:
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=0.0,
                    success=True,
                )

            # Execute tool calls
            for tc in response.tool_calls:
                total_tool_calls += 1
                tool = tool_map.get(tc.name)
                if tool is None:
                    context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
                    continue
                result = tool.execute(tc.arguments, env)
                content = result.output if result.success else f"Error: {result.error}\n{result.output}"
                context.add(Message.tool(tc.id, content))

        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=0.0,
            success=False,
            error="Max steps reached",
        )
```

**Commit:**

```bash
git add chimera/core/loops/ tests/test_loop_plan_execute.py
git commit -m "feat: add PlanAndExecute loop and loops package"
```

---

### Task 41: Reflexion Loop

**Files:**
- Create: `chimera/core/loops/reflexion.py`
- Modify: `chimera/core/loops/__init__.py`
- Test: `tests/test_loop_reflexion.py`

Pattern: After each tool execution cycle, append a "reflect on your progress" prompt. The reflection output is added to context for the next iteration.

**Step 1-6:** Standard TDD cycle. Similar structure to PlanAndExecute but with a reflection step between iterations.

```python
# chimera/core/loops/reflexion.py
from __future__ import annotations

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message


class Reflexion:
    """Reflexion loop: Act -> Reflect -> Repeat.

    After each action cycle, asks the model to reflect on progress
    and use that reflection to improve the next action.
    """

    REFLECT_PROMPT = (
        "Reflect on what you just did. What worked? What didn't? "
        "What should you do differently in the next step?"
    )

    def __init__(self, max_steps: int = 50, reflect_every: int = 3) -> None:
        self.max_steps = max_steps
        self.reflect_every = reflect_every

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        action_count = 0

        for _ in range(self.max_steps):
            steps += 1
            response = provider.complete(
                context.to_messages(),
                tools=schemas if schemas else None,
            )
            context.add(Message.assistant(response.content, tool_calls=response.tool_calls))

            if not response.has_tool_calls:
                return AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=0.0,
                    success=True,
                )

            for tc in response.tool_calls:
                total_tool_calls += 1
                action_count += 1
                tool = tool_map.get(tc.name)
                if tool is None:
                    context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
                    continue
                result = tool.execute(tc.arguments, env)
                content = result.output if result.success else f"Error: {result.error}\n{result.output}"
                context.add(Message.tool(tc.id, content))

            # Reflection step
            if action_count % self.reflect_every == 0:
                context.add(Message.user(self.REFLECT_PROMPT))

        return AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=0.0,
            success=False,
            error="Max steps reached",
        )
```

**Test and commit following standard pattern.**

---

### Task 42: TreeOfThought Loop

**Files:**
- Create: `chimera/core/loops/tree_of_thought.py`
- Modify: `chimera/core/loops/__init__.py`
- Test: `tests/test_loop_tree.py`

Simplified version: Generate N candidate responses, evaluate each, pick the best and continue from there.

**Step 1-6:** Standard TDD cycle. Implementation keeps the loop simple (breadth-1 tree with evaluation).

**Commit after tests pass.**

---

### Task 43: Curriculum Strategy

**Files:**
- Create: `chimera/training/strategies/curriculum.py`
- Modify: `chimera/training/strategies/__init__.py`
- Test: `tests/test_strategy_curriculum.py`

Pattern: Use Architecture layers + topological sort to synthesize in dependency order. Each layer is a mini-synthesis.

**Step 1: Write the failing tests**

```python
# tests/test_strategy_curriculum.py
from __future__ import annotations

from chimera.training.strategies.curriculum import CurriculumStrategy
from chimera.training.strategies.base import SynthesisResult, Callback
from chimera.training.architecture import Architecture, Layer
from chimera.training.spec import Spec


# Use the same mock agent/env patterns from test_strategy_convergence.py

class TestCurriculumStrategy:
    def test_runs_layers_in_order(self):
        """Curriculum should process layers in topological order."""
        # ... (mock agent that tracks which layer prompts it receives)
        pass

    def test_single_layer(self):
        """Works with a single layer."""
        pass

    def test_frozen_layers_skipped(self):
        """Frozen layers should not be synthesized."""
        pass
```

**Step 2-6:** Standard TDD cycle.

---

### Task 44: Ensemble Strategy

**Files:**
- Create: `chimera/training/strategies/ensemble.py`
- Modify: `chimera/training/strategies/__init__.py`
- Test: `tests/test_strategy_ensemble.py`

Pattern: Run multiple agents in parallel, pick the result with best pass rate.

**Step 1-6:** Standard TDD cycle.

---

### Task 45: Passthrough Strategy

**Files:**
- Create: `chimera/training/strategies/passthrough.py`
- Modify: `chimera/training/strategies/__init__.py`
- Test: `tests/test_strategy_passthrough.py`

Pattern: Single-shot, no iteration. Run agent once and return whatever happens.

```python
# chimera/training/strategies/passthrough.py
class Passthrough(Strategy):
    """Single-shot strategy: run agent once, no iteration."""

    def run(self, agent, spec, env, constraints=None, callbacks=None):
        callbacks = callbacks or []
        for cb in callbacks:
            cb.on_synthesis_start()

        task = spec.to_prompt()
        agent_result = agent.run(task, env)
        test_result = env.run_tests()

        epoch = EpochResult(
            epoch=1,
            pass_rate=test_result.pass_rate,
            passed=test_result.passed,
            total=test_result.total,
            agent_output=agent_result.output,
            improved=True,
            cost=agent_result.cost,
        )

        result = SynthesisResult(
            converged=test_result.all_passed,
            iterations=1,
            total_cost=agent_result.cost,
            best_pass_rate=test_result.pass_rate,
            history=[epoch],
        )

        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result
```

**Step 1-6:** Standard TDD cycle.

---

## Phase 12: Evaluation Layer (Tasks 46-51)

### Task 46: Evaluation Harness

**Files:**
- Create: `chimera/eval/__init__.py`
- Create: `chimera/eval/harness.py`
- Test: `tests/test_eval_harness.py`

The harness runs an agent against a benchmark suite and collects metrics.

```python
# chimera/eval/harness.py
@dataclass
class EvalResult:
    benchmark: str
    total: int
    passed: int
    pass_rate: float
    results: list[TaskEvalResult]
    total_cost: float

class Harness:
    def __init__(self, benchmark: Benchmark, agent: Agent, env_factory=None):
        ...
    def run(self) -> EvalResult:
        ...
```

**Step 1-6:** Standard TDD cycle.

---

### Task 47: Metrics

**Files:**
- Create: `chimera/eval/metrics.py`
- Modify: `chimera/eval/__init__.py`
- Test: `tests/test_eval_metrics.py`

```python
# chimera/eval/metrics.py
def pass_at_k(results: list[bool], k: int) -> float: ...
def avg_cost(results: list[EvalResult]) -> float: ...
def avg_steps(results: list[EvalResult]) -> float: ...
```

---

### Task 48: AntiOverfit

**Files:**
- Create: `chimera/eval/anti_overfit.py`
- Test: `tests/test_anti_overfit.py`

Detects when an agent is overfitting to specific test patterns rather than solving the underlying problem.

---

### Task 49: SWE-bench Adapter

**Files:**
- Create: `chimera/eval/benchmarks/__init__.py`
- Create: `chimera/eval/benchmarks/swe_bench.py`
- Test: `tests/test_bench_swe.py`

---

### Task 50: HumanEval Adapter

**Files:**
- Create: `chimera/eval/benchmarks/human_eval.py`
- Test: `tests/test_bench_human_eval.py`

---

### Task 51: Custom Benchmark

**Files:**
- Create: `chimera/eval/benchmarks/custom.py`
- Test: `tests/test_bench_custom.py`

---

## Phase 13: Environments, CLI, and Polish (Tasks 52-57)

### Task 52: Docker Environment

**Files:**
- Create: `chimera/env/docker.py`
- Modify: `chimera/env/__init__.py`
- Test: `tests/test_env_docker.py`

```python
# chimera/env/docker.py
class DockerEnvironment(Environment):
    """Docker-based sandboxed environment."""
    def __init__(self, image: str = "python:3.11-slim", workdir: str = "/workspace"):
        ...
```

---

### Task 53: Git Environment

**Files:**
- Create: `chimera/env/git.py`
- Modify: `chimera/env/__init__.py`
- Test: `tests/test_env_git.py`

Git-based checkpointing (commits instead of file copies).

---

### Task 54: CLI `eval` Command

**Files:**
- Modify: `chimera/cli/main.py`
- Test: `tests/test_cli_eval.py`

```bash
chimera eval --benchmark swe-bench-lite --agent default --output results.json
```

---

### Task 55: CLI `bench` Command

**Files:**
- Modify: `chimera/cli/main.py`
- Test: `tests/test_cli_bench.py`

```bash
chimera bench --suite custom --tests ./tests/ --agent default
```

---

### Task 56: Missing Constraints

**Files:**
- Modify: `chimera/training/constraint.py`
- Test: `tests/test_constraints_extended.py`

Add: `no_syntax_errors`, `max_complexity`, `no_security_issues` constraints.

---

### Task 57: ProgressBar Callback

**Files:**
- Modify: `chimera/training/callbacks.py`
- Test: `tests/test_callbacks_progress.py`

```python
class ProgressBar(Callback):
    """Rich progress bar for synthesis."""
    def on_epoch_start(self, epoch): ...
    def on_epoch_end(self, epoch, result): ...
```

---

## Summary

| Phase | Tasks | New Tests (est.) | Description |
|-------|-------|-----------------|-------------|
| 9 | 19-31 (13 tasks) | ~65 | Tools, approval, internals |
| 10 | 32-36 (5 tasks) | ~25 | Providers (OpenAI, Google, Ollama, Compatible, Factory) |
| 11 | 37-45 (9 tasks) | ~30 | Composition, loops, strategies |
| 12 | 46-51 (6 tasks) | ~25 | Evaluation layer |
| 13 | 52-57 (6 tasks) | ~20 | Environments, CLI, polish |

**Total: 39 tasks, ~165 new tests, bringing framework to ~328 tests**

**Dependency order:** Phase 9 first (tools/internals) → Phase 10 (providers) → Phase 11 (composition, can start after core tools) → Phase 12 (eval, needs agents+providers) → Phase 13 (polish, last).

**Parallelization opportunities:**
- Tasks 19-25 (individual tools) can all run in parallel
- Tasks 32-35 (providers) can all run in parallel
- Tasks 37-39 (composition patterns) can all run in parallel
- Tasks 43-45 (strategies) can all run in parallel
- Tasks 49-51 (benchmark adapters) can all run in parallel
