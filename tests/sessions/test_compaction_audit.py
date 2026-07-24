"""Regression locks for the Tier-2 compaction/session audit.

Pins three structural properties of the durable session log so a future
refactor cannot silently regress them. All tests are hermetic — a faux
provider / fake summarizer stands in for the LLM, since these are
persistence-and-branching properties, not model-quality ones.

Audit doc: ``docs/notes/compaction-audit.md``.

* **P1 — reversible compaction.** :meth:`SessionTree.add_compaction`
  *appends* a boundary entry; it never rewrites or truncates prior
  entries, so a branch can fork/switch back to the pre-compaction node
  and recover the full history with the summary absent.
* **P2 — iterative summary merge.** Re-compacting an already-compacted
  session feeds the prior summary back into the summarizer (both the
  :class:`SummaryCompaction` strategy and
  :meth:`SessionTree.summarize_branch`), rather than dropping it.
* **P3 — typed non-message entries.** The log is a true typed event
  store: model/thinking state changes and generic extension entries are
  first-class, round-trip through persistence, and are excluded from the
  reconstructed message stream.
"""
from __future__ import annotations

from pathlib import Path

from chimera.compaction.summary import SummaryCompaction
from chimera.sessions.tree import (
    SessionEntry,
    SessionHeader,
    SessionTree,
    StateChangeEntry,
)
from chimera.types import Message


# =====================================================================
# P1 — Reversible compaction (append-not-rewrite; fork back)
# =====================================================================


class TestReversibleCompaction:
    def test_add_compaction_is_append_only_on_disk(self, tmp_path: Path) -> None:
        """The JSONL grows by one line; existing lines are byte-identical."""
        path = tmp_path / "session.jsonl"
        tree = SessionTree(path)
        tree.add_message(Message.user("q1"))
        tree.add_message(Message.assistant("a1"))
        text_before = path.read_text()
        lines_before = text_before.splitlines()

        tree.add_compaction(summary="SUMMARY-X", first_kept_id="", tokens_before=100)

        text_after = path.read_text()
        # Append-only: the whole prior file is an untouched prefix.
        assert text_after.startswith(text_before)
        assert len(text_after.splitlines()) == len(lines_before) + 1

    def test_fork_back_recovers_full_history_without_summary(
        self, tmp_path: Path
    ) -> None:
        """Switching to the pre-compaction leaf yields the full history, no summary."""
        tree = SessionTree(tmp_path / "session.jsonl")
        tree.add_message(Message.user("q1"))
        tree.add_message(Message.assistant("a1"))
        pre_leaf = tree.add_message(Message.user("q2"))

        tree.add_compaction(summary="SUMMARY-X", first_kept_id="", tokens_before=100)

        tree.switch_branch(pre_leaf)
        reverted = [m.content for m in tree.get_messages()]
        assert reverted == ["q1", "a1", "q2"]
        assert not any("SUMMARY-X" in c for c in reverted)

    def test_pre_compaction_history_survives_reload(self, tmp_path: Path) -> None:
        """Both the pre-compaction entries and the boundary survive a fresh load."""
        path = tmp_path / "session.jsonl"
        tree = SessionTree(path)
        tree.add_message(Message.user("q1"))
        tree.add_message(Message.assistant("a1"))
        pre_leaf = tree.add_message(Message.user("q2"))
        comp_id = tree.add_compaction(
            summary="SUMMARY-X", first_kept_id="", tokens_before=100
        )

        reloaded = SessionTree(path)
        # Every entry is still present (3 messages + 1 compaction).
        assert reloaded.entry_count == 4
        # The compaction branch still surfaces the summary...
        assert any("SUMMARY-X" in m.content for m in reloaded.get_messages(comp_id))
        # ...and the pre-compaction leaf still recovers the full raw history.
        reverted = [m.content for m in reloaded.get_messages(pre_leaf)]
        assert reverted == ["q1", "a1", "q2"]


# =====================================================================
# P2 — Iterative summary merge (re-compaction feeds prior summary)
# =====================================================================


class _SpyProvider:
    """Faux provider capturing the summarization prompt it receives."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, messages: list[Message], max_tokens: int | None = None,
                 **kwargs: object) -> object:
        self.prompts.append(messages[0].content)

        class _R:
            content = "NEW-SUMMARY"

        return _R()


class TestIterativeSummaryMerge:
    def test_summary_compaction_feeds_prior_summary_on_recompaction(self) -> None:
        """A 2nd SummaryCompaction pass sees the 1st summary in its input."""
        spy = _SpyProvider()
        sc = SummaryCompaction(provider=spy, keep_first=2, keep_last=10)

        msgs = [
            Message.user(f"m{i}") if i % 2 == 0 else Message.assistant(f"m{i}")
            for i in range(30)
        ]
        once = sc.compact(msgs, budget=9999)
        # The injected summary is a system message carrying the marker prefix.
        assert once[2].role == "system"
        assert once[2].content.startswith("[Compacted")

        more = once + [Message.user(f"n{i}") for i in range(20)]
        sc.compact(more, budget=9999)

        assert len(spy.prompts) == 2
        # The prior summary's marker is present in the 2nd summarization prompt.
        assert "[Compacted" in spy.prompts[1]

    def test_summarize_branch_feeds_prior_summary_on_recompaction(
        self, tmp_path: Path
    ) -> None:
        """A 2nd summarize_branch sees the 1st summary rendered into its input."""
        seen: list[list[str]] = []

        def capturing(messages: list[Message]) -> str:
            seen.append([m.content for m in messages])
            return f"S{len(seen)}"

        tree = SessionTree(tmp_path / "session.jsonl")
        tree.add_message(Message.user("hello"))
        tree.add_message(Message.assistant("hi"))
        tree.summarize_branch(None, capturing)  # stores S1

        tree.add_message(Message.user("more"))
        tree.summarize_branch(None, capturing)  # stores S2

        assert len(seen) == 2
        # The 2nd summarizer input carries the first summary as a message.
        assert any("S1" in c for c in seen[1])


# =====================================================================
# P3 — Typed non-message log entries (true event store)
# =====================================================================


class TestTypedNonMessageEntries:
    def test_state_change_entry_round_trips(self, tmp_path: Path) -> None:
        """model/thinking state changes persist as first-class typed entries."""
        path = tmp_path / "session.jsonl"
        tree = SessionTree(path)
        tree.add_message(Message.user("hello"))
        model_id = tree.add_state_change("model", "glm-5")
        think_id = tree.add_state_change("thinking", "high")

        reloaded = SessionTree(path)
        model_entry = reloaded._by_id[model_id]
        think_entry = reloaded._by_id[think_id]
        assert isinstance(model_entry, StateChangeEntry)
        assert (model_entry.kind, model_entry.value) == ("model", "glm-5")
        assert isinstance(think_entry, StateChangeEntry)
        assert (think_entry.kind, think_entry.value) == ("thinking", "high")

    def test_state_change_entry_excluded_from_messages(self, tmp_path: Path) -> None:
        """State-change entries are metadata; they never enter the message stream."""
        tree = SessionTree(tmp_path / "session.jsonl")
        tree.add_message(Message.user("hello"))
        tree.add_state_change("model", "glm-5")
        tree.add_message(Message.assistant("hi"))

        assert [m.content for m in tree.get_messages()] == ["hello", "hi"]

    def test_generic_custom_entry_payload_round_trips(self, tmp_path: Path) -> None:
        """Extension entries preserve their custom ``data`` across reload."""
        path = tmp_path / "session.jsonl"
        tree = SessionTree(path)
        tree.add_message(Message.user("hello"))
        tree.append(
            SessionEntry(
                type="model_change",
                id="cx1",
                parent_id=tree.active_leaf,
                timestamp=1.0,
                data={"model": "glm-5", "reason": "user /model"},
            )
        )

        reloaded = SessionTree(path)
        got = reloaded._by_id["cx1"]
        assert isinstance(got, SessionEntry)
        assert got.type == "model_change"
        assert got.data == {"model": "glm-5", "reason": "user /model"}

    def test_all_entry_kinds_coexist_and_round_trip(self, tmp_path: Path) -> None:
        """One branch carries header, message, compaction, label, state-change,
        and a generic extension entry — each keeps its type through a reload."""
        path = tmp_path / "session.jsonl"
        tree = SessionTree(path)
        tree.append(
            SessionHeader(
                id="h1", parent_id=None, timestamp=0.0,
                cwd="/tmp/x", system_prompt="You are helpful.",
            )
        )
        msg_id = tree.add_message(Message.user("hello"))
        tree.add_label(msg_id, "checkpoint-1")
        tree.add_state_change("thinking", "high")
        tree.add_compaction(summary="did stuff", first_kept_id="", tokens_before=42)
        tree.append(
            SessionEntry(
                type="custom_x", id="cx1", parent_id=tree.active_leaf,
                timestamp=9.0, data={"note": "extension"},
            )
        )

        reloaded = SessionTree(path)
        by_type: dict[str, int] = {}
        for entry in reloaded._entries:
            by_type[type(entry).__name__] = by_type.get(type(entry).__name__, 0) + 1

        assert by_type["SessionHeader"] == 1
        assert by_type["MessageEntry"] == 1
        assert by_type["LabelEntry"] == 1
        assert by_type["StateChangeEntry"] == 1
        assert by_type["CompactionEntry"] == 1
        assert by_type["SessionEntry"] == 1
        # The typed metadata entries are not conflated with messages.
        assert [m.content for m in reloaded.get_messages()] == [
            "hello",
            "[Session compacted. Summary: did stuff]",
        ]
