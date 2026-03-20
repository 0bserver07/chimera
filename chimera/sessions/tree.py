"""JSONL-based session persistence with in-place branching."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.types import Message, ToolCall


@dataclass
class SessionEntry:
    """A single entry in the session log."""
    type: str
    id: str
    parent_id: str | None
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionHeader:
    id: str
    parent_id: str | None
    timestamp: float
    type: str = "header"
    version: int = 1
    cwd: str = ""
    system_prompt: str = ""


@dataclass
class MessageEntry:
    id: str
    parent_id: str | None
    timestamp: float
    type: str = "message"
    message: Message | None = None


@dataclass
class CompactionEntry:
    id: str
    parent_id: str | None
    timestamp: float
    type: str = "compaction"
    summary: str = ""
    first_kept_entry_id: str = ""
    tokens_before: int = 0
    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)


@dataclass
class LabelEntry:
    id: str
    parent_id: str | None
    timestamp: float
    type: str = "label"
    target_id: str = ""
    label: str = ""


# Union type for all entry kinds
AnyEntry = SessionEntry | SessionHeader | MessageEntry | CompactionEntry | LabelEntry


class SessionTree:
    """JSONL-based session persistence with in-place branching.

    Each session is a single JSONL file. Entries form a tree via parent_id.
    Branching appends new entries pointing to the branch point.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._entries: list[AnyEntry] = []
        self._by_id: dict[str, AnyEntry] = {}
        self._children: dict[str | None, list[str]] = {}
        self._active_leaf: str | None = None
        self._lock = threading.Lock()
        if self._path.exists():
            self._load()

    def append(self, entry: AnyEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            self._by_id[entry.id] = entry
            self._children.setdefault(entry.parent_id, []).append(entry.id)
            self._active_leaf = entry.id
            self._append_to_file(entry)

    def add_message(self, message: Message, parent_id: str | None = None) -> str:
        entry_id = self._generate_id()
        pid = parent_id if parent_id is not None else self._active_leaf
        entry = MessageEntry(
            id=entry_id, parent_id=pid, timestamp=time.time(), message=message,
        )
        self.append(entry)
        return entry_id

    def add_compaction(self, summary: str, first_kept_id: str,
                       tokens_before: int,
                       read_files: list[str] | None = None,
                       modified_files: list[str] | None = None) -> str:
        entry_id = self._generate_id()
        entry = CompactionEntry(
            id=entry_id, parent_id=self._active_leaf, timestamp=time.time(),
            summary=summary, first_kept_entry_id=first_kept_id,
            tokens_before=tokens_before,
            read_files=read_files or [], modified_files=modified_files or [],
        )
        self.append(entry)
        return entry_id

    def add_label(self, target_id: str, label: str) -> str:
        entry_id = self._generate_id()
        entry = LabelEntry(
            id=entry_id, parent_id=self._active_leaf, timestamp=time.time(),
            target_id=target_id, label=label,
        )
        self.append(entry)
        return entry_id

    def get_branch(self, leaf_id: str | None = None) -> list[AnyEntry]:
        leaf = leaf_id or self._active_leaf
        if leaf is None:
            return []
        chain: list[AnyEntry] = []
        current: str | None = leaf
        while current is not None:
            entry = self._by_id.get(current)
            if entry is None:
                break
            chain.append(entry)
            current = entry.parent_id
        chain.reverse()
        return chain

    def get_messages(self, leaf_id: str | None = None) -> list[Message]:
        branch = self.get_branch(leaf_id)
        messages: list[Message] = []
        for entry in branch:
            if isinstance(entry, MessageEntry) and entry.message is not None:
                messages.append(entry.message)
            elif isinstance(entry, CompactionEntry):
                messages.append(Message.user(
                    f"[Session compacted. Summary: {entry.summary}]"
                ))
        return messages

    def fork(self, from_entry_id: str) -> str:
        if from_entry_id not in self._by_id:
            raise ValueError(f"Entry {from_entry_id} not found")
        self._active_leaf = from_entry_id
        return from_entry_id

    def get_branch_points(self) -> list[str]:
        return [
            pid for pid, children in self._children.items()
            if pid is not None and len(children) > 1
        ]

    def get_leaves(self) -> list[str]:
        all_parents: set[str] = set()
        for entry in self._entries:
            if entry.parent_id is not None:
                all_parents.add(entry.parent_id)
        return [e.id for e in self._entries if e.id not in all_parents]

    def switch_branch(self, leaf_id: str) -> None:
        if leaf_id not in self._by_id:
            raise ValueError(f"Entry {leaf_id} not found")
        self._active_leaf = leaf_id

    @property
    def active_leaf(self) -> str | None:
        return self._active_leaf

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def _load(self) -> None:
        with open(self._path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entry = self._deserialize(raw)
                    self._entries.append(entry)
                    self._by_id[entry.id] = entry
                    self._children.setdefault(entry.parent_id, []).append(entry.id)
                    self._active_leaf = entry.id
                except (json.JSONDecodeError, KeyError):
                    continue

    def _append_to_file(self, entry: AnyEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(json.dumps(self._serialize(entry)) + "\n")

    def _serialize(self, entry: AnyEntry) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": entry.type, "id": entry.id,
            "parent_id": entry.parent_id, "timestamp": entry.timestamp,
        }
        if isinstance(entry, MessageEntry) and entry.message:
            d["message"] = {"role": entry.message.role, "content": entry.message.content}
            if entry.message.tool_calls:
                d["message"]["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in entry.message.tool_calls
                ]
            if entry.message.call_id:
                d["message"]["call_id"] = entry.message.call_id
        elif isinstance(entry, CompactionEntry):
            d["summary"] = entry.summary
            d["first_kept_entry_id"] = entry.first_kept_entry_id
            d["tokens_before"] = entry.tokens_before
            d["read_files"] = entry.read_files
            d["modified_files"] = entry.modified_files
        elif isinstance(entry, SessionHeader):
            d["version"] = entry.version
            d["cwd"] = entry.cwd
            d["system_prompt"] = entry.system_prompt
        elif isinstance(entry, LabelEntry):
            d["target_id"] = entry.target_id
            d["label"] = entry.label
        return d

    def _deserialize(self, raw: dict[str, Any]) -> AnyEntry:
        entry_type = raw["type"]
        base: dict[str, Any] = {
            "id": raw["id"], "parent_id": raw.get("parent_id"),
            "timestamp": raw.get("timestamp", 0.0),
        }
        if entry_type == "header":
            return SessionHeader(**base, version=raw.get("version", 1),
                                 cwd=raw.get("cwd", ""), system_prompt=raw.get("system_prompt", ""))
        elif entry_type == "message":
            msg_data = raw.get("message", {})
            msg = Message(role=msg_data["role"], content=msg_data.get("content", ""))
            if "tool_calls" in msg_data:
                msg.tool_calls = [
                    ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                    for tc in msg_data["tool_calls"]
                ]
            if "call_id" in msg_data:
                msg.call_id = msg_data["call_id"]
            return MessageEntry(**base, message=msg)
        elif entry_type == "compaction":
            return CompactionEntry(**base, summary=raw.get("summary", ""),
                                   first_kept_entry_id=raw.get("first_kept_entry_id", ""),
                                   tokens_before=raw.get("tokens_before", 0),
                                   read_files=raw.get("read_files", []),
                                   modified_files=raw.get("modified_files", []))
        elif entry_type == "label":
            return LabelEntry(**base, target_id=raw.get("target_id", ""),
                              label=raw.get("label", ""))
        else:
            return SessionEntry(type=entry_type, **base, data=raw)

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:12]
