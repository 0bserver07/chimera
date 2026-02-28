"""Tests for the LSP module."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.lsp.base import Diagnostic, LSPClient, Severity


# ---------------------------------------------------------------------------
# Tests: Diagnostic
# ---------------------------------------------------------------------------

class TestDiagnostic:
    def test_construction(self) -> None:
        d = Diagnostic(
            file="foo.py",
            line=10,
            column=5,
            severity=Severity.ERROR,
            message="undefined variable",
        )
        assert d.file == "foo.py"
        assert d.line == 10
        assert d.severity == Severity.ERROR

    def test_to_feedback_str_basic(self) -> None:
        d = Diagnostic(
            file="bar.py",
            line=3,
            column=1,
            severity=Severity.WARNING,
            message="unused import",
        )
        s = d.to_feedback_str()
        assert "warning" in s
        assert "bar.py:3:1" in s
        assert "unused import" in s

    def test_to_feedback_str_with_source_and_code(self) -> None:
        d = Diagnostic(
            file="baz.py",
            line=1,
            column=1,
            severity=Severity.INFORMATION,
            message="hint",
            source="pyright",
            code="reportMissing",
        )
        s = d.to_feedback_str()
        assert "(pyright)" in s
        assert "[reportMissing]" in s

    def test_optional_fields_default(self) -> None:
        d = Diagnostic(
            file="x.py", line=1, column=1,
            severity=Severity.HINT, message="hi",
        )
        assert d.source is None
        assert d.code is None


# ---------------------------------------------------------------------------
# Tests: Severity
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_values(self) -> None:
        assert Severity.ERROR.value == 1
        assert Severity.WARNING.value == 2
        assert Severity.INFORMATION.value == 3
        assert Severity.HINT.value == 4


# ---------------------------------------------------------------------------
# Tests: LSPClient
# ---------------------------------------------------------------------------

class TestLSPClient:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            LSPClient()  # type: ignore[abstract]

    def test_mock_implementation(self) -> None:
        class MockLSP(LSPClient):
            def initialize(self, root_path: str) -> None:
                pass

            def diagnostics(self, file_path: str) -> list[Diagnostic]:
                return [
                    Diagnostic(
                        file=file_path, line=1, column=1,
                        severity=Severity.ERROR, message="test error",
                    )
                ]

            def shutdown(self) -> None:
                pass

        client = MockLSP()
        client.initialize("/tmp/project")
        diags = client.diagnostics("test.py")
        assert len(diags) == 1
        assert diags[0].severity == Severity.ERROR
        client.shutdown()

    def test_context_manager(self) -> None:
        class MockLSP(LSPClient):
            def __init__(self) -> None:
                self.shut_down = False

            def initialize(self, root_path: str) -> None:
                pass

            def diagnostics(self, file_path: str) -> list[Diagnostic]:
                return []

            def shutdown(self) -> None:
                self.shut_down = True

        with MockLSP() as client:
            client.initialize("/tmp")
        assert client.shut_down is True


# ---------------------------------------------------------------------------
# Tests: imports
# ---------------------------------------------------------------------------

class TestImports:
    def test_chimera_exports(self) -> None:
        from chimera import Diagnostic, LSPClient, Severity
        assert Diagnostic is not None
        assert LSPClient is not None
        assert Severity is not None

    def test_new_type_exports(self) -> None:
        from chimera import ChangeType, FileChange, PendingApproval, drain_steps
        assert ChangeType is not None
        assert FileChange is not None
        assert PendingApproval is not None
        assert drain_steps is not None
