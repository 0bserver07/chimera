from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Literal

from chimera.training.strategies.base import Callback, EpochResult, SynthesisResult

if TYPE_CHECKING:
    from chimera.providers.base import Provider


class OracleCallback(Callback):
    """Grow the test suite during synthesis.

    After each epoch where all tests pass, generates new test cases
    targeting edge cases. The agent must pass old + new tests next epoch.

    Two modes:
    - llm: uses a Provider to generate adversarial tests
    - property: generates property-based test stubs from function signatures

    Args:
        provider: LLM provider for generating adversarial tests (llm mode).
        tests_dir: Directory to write generated test files into.
        max_new_tests_per_epoch: Maximum number of new test functions to
            generate per epoch.
        mode: Generation mode -- ``"llm"`` uses the provider, ``"property"``
            generates simple property-based stubs.
    """

    def __init__(
        self,
        provider: Provider | None = None,
        tests_dir: str | None = None,
        max_new_tests_per_epoch: int = 3,
        mode: Literal["llm", "property"] = "llm",
    ) -> None:
        self._provider = provider
        self._tests_dir = tests_dir
        self._max_new = max_new_tests_per_epoch
        self._mode = mode
        self.generated_tests: list[str] = []
        self._epoch_count: int = 0

    def on_epoch_end(
        self, epoch: int | EpochResult, result: EpochResult | None = None
    ) -> bool:
        """Generate new tests when all current tests pass.

        Supports both callback signatures:
        - ``on_epoch_end(epoch_num, epoch_result)``
        - ``on_epoch_end(epoch_result)``

        Returns:
            Always ``True`` (never stops synthesis).
        """
        er = result if result is not None else epoch
        if isinstance(er, int):
            return True
        self._epoch_count += 1
        if er.pass_rate == 1.0 and self._tests_dir:
            new_tests = self._generate_tests(er)
            for test_code in new_tests:
                self._write_test(test_code)
                self.generated_tests.append(test_code)
        return True

    def on_synthesis_start(self) -> None:
        """Reset epoch counter at synthesis start."""
        self._epoch_count = 0

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        """No-op at synthesis end."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_tests(self, result: EpochResult) -> list[str]:
        """Dispatch to the appropriate generation mode."""
        if self._mode == "llm" and self._provider:
            return self._generate_tests_llm(result)
        return self._generate_tests_property(result)

    def _generate_tests_llm(self, result: EpochResult) -> list[str]:
        """Use LLM to generate adversarial test cases.

        Prompts the provider to write edge-case tests targeting boundary
        conditions, empty inputs, negative numbers, and type edge cases.

        Args:
            result: The epoch result containing the agent's output.

        Returns:
            A list of test function strings (up to ``max_new_tests_per_epoch``).
        """
        from chimera.types import Message

        prompt = (
            f"Here is an implementation that passes all current tests:\n\n"
            f"{result.agent_output}\n\n"
            f"Write {self._max_new} new test functions that target edge cases "
            f"the implementation might fail on. Focus on boundary conditions, "
            f"empty inputs, negative numbers, type edge cases. "
            f"Output each test as a standalone function starting with test_. "
            f"Do not include import statements -- they will be added automatically."
        )
        assert self._provider is not None
        response = self._provider.complete([Message.user(prompt)], max_tokens=1000)
        return self._parse_test_functions(response.content)

    def _generate_tests_property(self, result: EpochResult) -> list[str]:
        """Generate simple property-based test stubs from the agent output.

        Produces basic skeleton tests that verify type contracts and
        idempotency properties.

        Args:
            result: The epoch result containing the agent's output.

        Returns:
            A list of test function strings (up to ``max_new_tests_per_epoch``).
        """
        tests: list[str] = []
        test = (
            f"def test_oracle_epoch{self._epoch_count}_type_check():\n"
            f"    # Auto-generated property test\n"
            f"    # Verify functions return expected types\n"
            f"    pass\n"
        )
        tests.append(test)
        return tests[: self._max_new]

    def _parse_test_functions(self, content: str) -> list[str]:
        """Extract test functions from LLM response.

        Splits the response on ``def test_`` boundaries and returns up to
        ``max_new_tests_per_epoch`` functions.

        Args:
            content: Raw text from the LLM containing test functions.

        Returns:
            A list of test function source strings.
        """
        functions: list[str] = []
        # Split on def test_ boundaries
        parts = re.split(r"(?=\ndef test_)", "\n" + content)
        for part in parts:
            part = part.strip()
            if part.startswith("def test_"):
                functions.append(part)
        return functions[: self._max_new]

    def _write_test(self, test_code: str) -> None:
        """Write a single test function to a new file in tests_dir.

        Each test is written to a file named
        ``test_oracle_epoch{N}_{M}.py`` where N is the epoch number
        and M is the index within ``generated_tests``.

        Args:
            test_code: The test function source code to write.
        """
        if not self._tests_dir:
            return
        os.makedirs(self._tests_dir, exist_ok=True)
        fname = f"test_oracle_epoch{self._epoch_count}_{len(self.generated_tests)}.py"
        path = os.path.join(self._tests_dir, fname)
        with open(path, "w") as f:
            f.write(test_code + "\n")
