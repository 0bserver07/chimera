"""Resume a session from its persisted transcript."""

from __future__ import annotations

from chimera.core.content_replacement import (
    ContentReplacementEntry,
    ContentReplacementState,
)
from chimera.core.tool_result_persister import ToolResultPersister
from chimera.sessions.transcript import TranscriptStorage


class SessionResumer:
    """Rebuild conversation state from a previously-persisted session."""

    async def resume(
        self,
        session_id: str,
        storage: TranscriptStorage,
        persister: ToolResultPersister,
    ) -> tuple[list[dict], ContentReplacementState]:
        """Load transcript entries and reconstruct content replacement state.

        Returns:
            A tuple of (messages, content_replacement_state).
        """
        entries = await storage.load_raw()

        # Separate content-replacement metadata entries from regular messages
        messages: list[dict] = []
        cr_entries: list[ContentReplacementEntry] = []

        for entry in entries:
            if entry.get("type") == "content_replacement":
                cr_entries.append(
                    ContentReplacementEntry(
                        tool_use_id=entry["tool_use_id"],
                        persisted_path=entry["persisted_path"],
                        preview=entry.get("preview", ""),
                        original_size=entry.get("original_size", 0),
                        timestamp=entry.get("timestamp", 0.0),
                    )
                )
            else:
                messages.append(entry)

        cr_state = ContentReplacementState.reconstruct_from_transcript(cr_entries)

        return messages, cr_state
