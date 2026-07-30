"""A grader that matches a reference answer must not accept a *different* value.

Sibling of ``test_no_length_grading.py``. Where that one covers graders with no
reference answer at all, this covers the ones that DO have a ground truth and
compared it with a raw ``in``:

* ``ContextBench.evaluate`` — ground truth ``42`` was satisfied by the answer
  ``142``. Not a near-miss: a different number graded as correct.
* ``TauBench.evaluate``'s best-effort path — the terminal action
  ``transfer_to_agent`` was satisfied by a mention of ``transfer_to_agent_v2``.

Neither fed a published number (``context-bench`` is marked *unverified* in the
capability matrix, tau-bench appears only as n=1 ✓ ticks), so this is prevention
rather than a retraction — but the leniency is exactly what turns a ✓ into a
score nobody re-checks.

**Deliberately not fixed, and asserted as such below:** an answer that negates
or hedges around the truth still grades correct. That is natural-language
judgement, not string matching, and pretending otherwise would be a worse lie
than the documented limitation. The ``judge`` hook exists for it.
"""
from __future__ import annotations

import pytest

from chimera.eval.benchmarks.context_bench import ContextBench
from chimera.eval.benchmarks.tau_bench import TauBench


class TestContextBenchBoundaries:
    @pytest.mark.parametrize(
        "answer,expected",
        [
            ("42", True),
            ("The answer is 42.", True),
            ("  42  ", True),
            ("It is 42, per the log.", True),
            # The defect: a different value that merely CONTAINS the truth.
            ("142", False),
            ("2142 items", False),
            ("420", False),
            ("7", False),
            ("forty-two", False),
            ("", False),
        ],
    )
    def test_truth_must_match_as_a_whole_token(
        self, answer: str, expected: bool
    ) -> None:
        assert ContextBench().evaluate({"answer": "42"}, answer, None) is expected

    def test_case_insensitive_but_still_bounded(self) -> None:
        bench = ContextBench()
        assert bench.evaluate({"answer": "Paris"}, "the answer is paris", None)
        # `cat` must not be found inside `concatenate` — the word-form of the
        # same bug that let 142 match 42.
        assert not bench.evaluate({"answer": "cat"}, "concatenate the rows", None)

    @pytest.mark.parametrize("truth,answer", [("$5", "it costs $5 total"), ("f(x)", "call f(x)")])
    def test_non_alphanumeric_edges_stay_matchable(self, truth: str, answer: str) -> None:
        """A `\\b` anchor beside punctuation can never fire.

        Applying boundaries unconditionally would make `$5` and `f(x)` permanently
        unmatchable — trading a false-accept for a false-reject and silently
        zeroing those tasks. Anchors apply only where the needle's own edge is
        alphanumeric.
        """
        assert ContextBench().evaluate({"answer": truth}, answer, None)

    def test_empty_truth_is_never_a_pass(self) -> None:
        assert not ContextBench().evaluate({"answer": ""}, "anything at all", None)

    def test_judge_hook_still_wins(self) -> None:
        task = {"answer": "42", "judge": lambda _t, out: out == "exactly this"}
        bench = ContextBench()
        assert bench.evaluate(task, "exactly this", None)
        assert not bench.evaluate(task, "42", None)

    def test_negation_and_hedging_remain_lenient_by_design(self) -> None:
        """Pins the KNOWN limitation so it stays visible rather than assumed.

        If someone later teaches this path to reject negations, this test fails
        and forces them to update the docstring that currently discloses it —
        which is the point. A limitation nobody can see is indistinguishable
        from a bug nobody found.
        """
        bench = ContextBench()
        assert bench.evaluate({"answer": "42"}, "The answer is NOT 42.", None)
        assert bench.evaluate({"answer": "42"}, "possibly 42?", None)


class TestTauBenchTerminalActionBoundaries:
    def _task(self) -> dict[str, object]:
        return {"id": "t", "actions": [{"name": "transfer_to_agent"}]}

    @pytest.mark.parametrize(
        "output,expected",
        [
            ("I called transfer_to_agent to finish.", True),
            ("transfer_to_agent", True),
            # The defect: a DIFFERENT action containing the expected name.
            ("called transfer_to_agent_v2 instead", False),
            ("used pre_transfer_to_agent", False),
            ("no action taken", False),
            ("", False),
        ],
    )
    def test_terminal_action_matches_on_boundaries(
        self, output: str, expected: bool
    ) -> None:
        bench = TauBench(domain="mock")
        assert bench.evaluate(self._task(), output, None) is expected

    def test_structured_actions_take_precedence_over_prose(self) -> None:
        """The prose scan is a fallback; a reported action list must decide.

        Otherwise an agent that names the right action while doing the wrong one
        would outscore an agent that honestly reports what it did.
        """
        bench = TauBench(domain="mock")
        payload = '{"actions": [{"name": "something_else"}]}'
        assert not bench.evaluate(self._task(), payload, None)
