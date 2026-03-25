"""SQLite-backed learning store with FTS5 full-text search."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from chimera.learning.observation import Observation, ObservationCategory

__all__ = ["LearningStore"]

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    tags TEXT DEFAULT '[]',
    source TEXT DEFAULT '',
    project_path TEXT DEFAULT '',
    error_signature TEXT DEFAULT '',
    observation_count INTEGER DEFAULT 1,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(error_signature) ON CONFLICT REPLACE
);
"""

_FTS_SCHEMA = """\
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    topic, key, value, tags, content=observations, content_rowid=id
);
"""

_FTS_TRIGGERS = """\
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, topic, key, value, tags)
    VALUES (new.id, new.topic, new.key, new.value, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, topic, key, value, tags)
    VALUES ('delete', old.id, old.topic, old.key, old.value, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, topic, key, value, tags)
    VALUES ('delete', old.id, old.topic, old.key, old.value, old.tags);
    INSERT INTO observations_fts(rowid, topic, key, value, tags)
    VALUES (new.id, new.topic, new.key, new.value, new.tags);
END;
"""


class LearningStore:
    """Persistent SQLite store for learned observations.

    Uses WAL mode for concurrent reads and FTS5 for full-text search.

    Args:
        db_path: Path to the SQLite database file.
            Defaults to ``~/.chimera/learning/observations.db``.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_dir = Path.home() / ".chimera" / "learning"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "observations.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables, FTS index, and triggers if they don't exist."""
        self._conn.executescript(_SCHEMA)
        self._conn.executescript(_FTS_SCHEMA)
        self._conn.executescript(_FTS_TRIGGERS)
        self._conn.commit()

    def record(self, observation: Observation) -> None:
        """Insert or update an observation.

        Deduplicates by ``error_signature`` via UNIQUE ON CONFLICT REPLACE.
        If an observation with the same ``error_signature`` already exists,
        the new row replaces it (SQLite REPLACE semantics).

        Args:
            observation: The observation to record.
        """
        now = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(observation.tags)

        self._conn.execute(
            """\
            INSERT INTO observations
                (topic, key, value, category, confidence, tags, source,
                 project_path, error_signature, observation_count,
                 success_count, failure_count, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.topic,
                observation.key,
                observation.value,
                observation.category.value,
                observation.confidence,
                tags_json,
                observation.source,
                observation.project_path,
                observation.error_signature,
                observation.observation_count,
                observation.success_count,
                observation.failure_count,
                now,
                now,
            ),
        )
        self._conn.commit()

    def query(
        self,
        text: str,
        *,
        category: ObservationCategory | None = None,
        project_path: str | None = None,
        min_confidence: float | None = None,
        limit: int = 5,
    ) -> list[Observation]:
        """Full-text search over observations.

        Ranks results by FTS5 relevance multiplied by confidence.

        Args:
            text: Search query string.
            category: Filter by observation category.
            project_path: Filter by project path.
            min_confidence: Minimum confidence threshold.
            limit: Maximum number of results to return.

        Returns:
            List of matching observations, highest relevance first.
        """
        conditions: list[str] = []
        params: list[str | float | int] = []

        if category is not None:
            conditions.append("o.category = ?")
            params.append(category.value)
        if project_path is not None:
            conditions.append("o.project_path = ?")
            params.append(project_path)
        if min_confidence is not None:
            conditions.append("o.confidence >= ?")
            params.append(min_confidence)

        where = ""
        if conditions:
            where = "AND " + " AND ".join(conditions)

        sql = f"""\
            SELECT o.id, o.topic, o.key, o.value, o.category, o.confidence,
                   o.tags, o.source, o.project_path, o.error_signature,
                   o.observation_count, o.success_count, o.failure_count
            FROM observations_fts fts
            JOIN observations o ON o.id = fts.rowid
            WHERE observations_fts MATCH ? {where}
            ORDER BY (fts.rank * -1) * o.confidence DESC
            LIMIT ?
        """
        params_full: list[str | float | int] = [text, *params, limit]
        cursor = self._conn.execute(sql, params_full)
        rows = cursor.fetchall()
        return [self._row_to_observation(row) for row in rows]

    def query_by_signature(self, error_signature: str) -> Observation | None:
        """Look up an observation by its error signature.

        Args:
            error_signature: MD5 hash of the normalized error message.

        Returns:
            The matching observation, or None.
        """
        cursor = self._conn.execute(
            """\
            SELECT id, topic, key, value, category, confidence,
                   tags, source, project_path, error_signature,
                   observation_count, success_count, failure_count
            FROM observations WHERE error_signature = ?
            """,
            (error_signature,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_observation(row)

    def update_confidence(self, observation_id: int, success: bool) -> float:
        """Update confidence for an observation based on outcome.

        Asymmetric update: success adds +0.10, failure subtracts -0.15.
        Result is clamped to [0.0, 1.0].

        Args:
            observation_id: The database ID of the observation.
            success: Whether the fix was successful.

        Returns:
            The new confidence value after the update.
        """
        delta = 0.10 if success else -0.15
        count_field = "success_count" if success else "failure_count"
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            f"""\
            UPDATE observations
            SET confidence = MIN(1.0, MAX(0.0, confidence + ?)),
                {count_field} = {count_field} + 1,
                observation_count = observation_count + 1,
                last_seen = ?
            WHERE id = ?
            """,
            (delta, now, observation_id),
        )
        self._conn.commit()

        cursor = self._conn.execute(
            "SELECT confidence FROM observations WHERE id = ?",
            (observation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return 0.0
        return float(row[0])

    def prune(self, max_age_days: int = 90, min_confidence: float = 0.1) -> int:
        """Remove stale low-confidence observations.

        Args:
            max_age_days: Maximum age in days before eligible for pruning.
            min_confidence: Observations below this confidence are pruned
                if they also exceed ``max_age_days``.

        Returns:
            Number of observations removed.
        """
        cutoff = datetime.now(timezone.utc).isoformat()
        # Calculate cutoff date
        from datetime import timedelta

        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cutoff = cutoff_dt.isoformat()

        cursor = self._conn.execute(
            """\
            DELETE FROM observations
            WHERE confidence < ? AND last_seen < ?
            """,
            (min_confidence, cutoff),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    @staticmethod
    def _row_to_observation(row: tuple) -> Observation:  # type: ignore[type-arg]
        """Convert a database row tuple to an Observation."""
        return Observation(
            id=row[0],
            topic=row[1],
            key=row[2],
            value=row[3],
            category=ObservationCategory(row[4]),
            confidence=row[5],
            tags=json.loads(row[6]) if row[6] else [],
            source=row[7],
            project_path=row[8],
            error_signature=row[9],
            observation_count=row[10],
            success_count=row[11],
            failure_count=row[12],
        )
