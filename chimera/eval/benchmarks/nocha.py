"""NoCha: Novel Challenges in long-context code & narrative reasoning.

NoCha (Karpinska et al., 2024) is a long-context benchmark constructed
from recently-published books and code corpora. Each task gives the model
a long document plus two competing claims, and asks which claim is
consistent with the document. The code variant uses long source files
(>50K tokens) drawn from open-source repositories and asks the agent to
reason about cross-file behavior.

This is a SCAFFOLD — load_instances + claim-pair grading is wired, but
the upstream JSON dump must be downloaded separately. There is no public
HuggingFace mirror of the code split as of 2026-05; we currently support
loading from a local JSON file matching the repo's schema.

References:
    - GitHub: github.com/marzenakrp/nocha
    - Paper: arXiv:2406.16264
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


@dataclass
class NoChaInstance:
    """A single NoCha long-context task.

    Attributes:
        instance_id: Unique identifier (e.g. ``"nocha_code_0123"``).
        document: The long document or concatenated source-tree text.
        true_claim: Statement consistent with ``document``.
        false_claim: Statement inconsistent with ``document``.
        token_count: Approximate token count of ``document`` (used for
            window-size filtering).
        domain: ``"book"`` or ``"code"``. Defaults to ``"code"``.
        metadata: Any additional upstream fields.
    """

    instance_id: str
    document: str
    true_claim: str
    false_claim: str
    token_count: int = 0
    domain: str = "code"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "prompt": self._prompt(),
            "document": self.document,
            "true_claim": self.true_claim,
            "false_claim": self.false_claim,
            "token_count": self.token_count,
            "domain": self.domain,
            "metadata": dict(self.metadata),
        }

    def _prompt(self) -> str:
        return (
            "Read the following document and identify which of two claims is "
            "consistent with it.\n\n"
            f"DOCUMENT:\n{self.document}\n\n"
            f"CLAIM A: {self.true_claim}\n"
            f"CLAIM B: {self.false_claim}\n\n"
            "Reply with exactly one letter: 'A' or 'B'."
        )


class NoCha(Benchmark):
    """NoCha long-context scaffold.

    Loads instances from a JSON or JSON-lines file matching the upstream
    repo's schema. Grading is in-process and side-effect free — the
    agent's textual answer is parsed for ``"A"`` or ``"B"`` and compared
    to the true claim. Token counts are taken at face value from the
    upstream metadata; we do not run a tokenizer on every document.

    Args:
        dataset_path: Path to JSON or JSON-lines file. If ``None``, the
            benchmark starts empty.
        domain: Optional filter (``"book"`` or ``"code"``).
        min_tokens: Drop instances shorter than this many tokens (used
            to focus on the >50K long-context regime).
        max_tokens: Drop instances longer than this many tokens.
        limit: Maximum number of tasks to keep after filtering.

    Raises:
        FileNotFoundError: If ``dataset_path`` is set but missing.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        domain: str | None = None,
        min_tokens: int = 0,
        max_tokens: int | None = None,
        limit: int | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._domain = domain
        self._min_tokens = int(min_tokens)
        self._max_tokens = max_tokens
        self._limit = limit
        self._instances: list[NoChaInstance] = []
        if dataset_path:
            self._load(dataset_path)

    def _load(self, path: str) -> None:
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")

        text = data_path.read_text()
        try:
            items = json.loads(text)
            if isinstance(items, dict) and "tasks" in items:
                items = items["tasks"]
            if not isinstance(items, list):
                items = [items]
        except json.JSONDecodeError:
            items = []
            for raw_line in text.strip().splitlines():
                line = raw_line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            domain = item.get("domain", "code")
            if self._domain and domain != self._domain:
                continue
            tokens = int(item.get("token_count", 0) or 0)
            if tokens < self._min_tokens:
                continue
            if self._max_tokens is not None and tokens > self._max_tokens:
                continue
            self._instances.append(
                NoChaInstance(
                    instance_id=item.get("instance_id", item.get("id", "")),
                    document=item.get("document", item.get("context", "")),
                    true_claim=item.get("true_claim", item.get("claim_true", "")),
                    false_claim=item.get(
                        "false_claim", item.get("claim_false", "")
                    ),
                    token_count=tokens,
                    domain=domain,
                    metadata={
                        k: v
                        for k, v in item.items()
                        if k
                        not in {
                            "instance_id",
                            "id",
                            "document",
                            "context",
                            "true_claim",
                            "claim_true",
                            "false_claim",
                            "claim_false",
                            "token_count",
                            "domain",
                        }
                    },
                )
            )

        if self._limit:
            self._instances = self._instances[: self._limit]

    def name(self) -> str:
        suffix = f"-{self._domain}" if self._domain else ""
        return f"nocha{suffix}"

    def tasks(self) -> list[dict[str, Any]]:
        return [i.to_dict() for i in self._instances]

    def evaluate(
        self, task: dict[str, Any], agent_output: str, env: Any = None
    ) -> bool:
        """Grade by checking whether the agent picked claim A (true).

        The agent's output is normalized: leading/trailing whitespace
        stripped, and the first occurrence of an isolated ``A`` or ``B``
        token is taken as the answer. This matches the upstream protocol.

        Args:
            task: Task dictionary from :meth:`tasks`.
            agent_output: Agent's reply.
            env: Unused (kept for :class:`Benchmark` compatibility).

        Returns:
            ``True`` iff the agent answered ``A`` (the true claim).
        """
        choice = self._parse_choice(agent_output)
        return choice == "A"

    @staticmethod
    def _parse_choice(text: str) -> str | None:
        """Extract the first standalone ``A`` or ``B`` from ``text``."""
        if not text:
            return None
        normalized = text.strip().upper()
        # Fast path: single-letter reply
        if normalized in {"A", "B"}:
            return normalized
        # Otherwise look for the first standalone A / B token
        for token in normalized.replace(",", " ").replace(".", " ").split():
            if token in {"A", "B"}:
                return token
        return None

    def long_context_share(self, threshold: int = 50_000) -> float:
        """Fraction of loaded instances above ``threshold`` tokens."""
        if not self._instances:
            return 0.0
        long_ones = sum(1 for i in self._instances if i.token_count >= threshold)
        return long_ones / len(self._instances)

    @property
    def instances(self) -> list[NoChaInstance]:
        return list(self._instances)

    def add_instance(self, instance: NoChaInstance) -> None:
        self._instances.append(instance)


__all__ = ["NoCha", "NoChaInstance"]
