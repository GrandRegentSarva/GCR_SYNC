"""SQLite persistence layer for gcr-sync.

Manages the cache.db database for tracking courses, seen items,
downloads, and sync history. Provides duplicate detection and
sync state management.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from src.logger import get_logger
from src.models import ItemType

logger = get_logger()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS courses (
    course_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    section     TEXT DEFAULT '',
    state       TEXT DEFAULT 'ACTIVE',
    first_seen  TEXT NOT NULL,
    last_synced TEXT
);

CREATE TABLE IF NOT EXISTS seen_items (
    item_id     TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    item_type   TEXT NOT NULL,
    title       TEXT DEFAULT '',
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (item_id, course_id)
);

CREATE TABLE IF NOT EXISTS downloads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    filename    TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    mime_type   TEXT DEFAULT '',
    downloaded  TEXT NOT NULL,
    success     INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sync_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    courses_synced  INTEGER DEFAULT 0,
    new_items_found INTEGER DEFAULT 0,
    downloads_count INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running',
    error_message   TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_seen_items_course
    ON seen_items(course_id);

CREATE INDEX IF NOT EXISTS idx_downloads_item
    ON downloads(item_id, course_id);

CREATE INDEX IF NOT EXISTS idx_sync_history_started
    ON sync_history(started_at);
"""


class Database:
    """SQLite database manager for gcr-sync.

    Handles connection management, schema initialization,
    and all CRUD operations for sync tracking.

    Attributes:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: Path) -> None:
        """Initialize the database manager.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Create a database connection context manager.

        Yields:
            An active SQLite connection with row_factory set.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """Create database tables if they don't exist."""
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.debug("Database schema initialized at %s", self.db_path)

    # ── Course Operations ──────────────────────────────────────────────

    def upsert_course(self, course_id: str, name: str, section: str = "", state: str = "ACTIVE") -> None:
        """Insert or update a course record.

        Args:
            course_id: Unique Google Classroom course ID.
            name: Course display name.
            section: Optional section identifier.
            state: Course state (ACTIVE, ARCHIVED, etc.).
        """
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO courses (course_id, name, section, state, first_seen, last_synced)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(course_id) DO UPDATE SET
                    name = excluded.name,
                    section = excluded.section,
                    state = excluded.state,
                    last_synced = excluded.last_synced
                """,
                (course_id, name, section, state, now, now),
            )

    def get_all_courses(self) -> list[dict]:
        """Retrieve all tracked courses.

        Returns:
            List of course records as dictionaries.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM courses ORDER BY name").fetchall()
            return [dict(row) for row in rows]

    # ── Seen Items Operations ──────────────────────────────────────────

    def is_item_seen(self, item_id: str, course_id: str) -> bool:
        """Check if an item has already been processed.

        Args:
            item_id: Unique Google Classroom item ID.
            course_id: Parent course ID.

        Returns:
            True if the item has been seen before.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_items WHERE item_id = ? AND course_id = ?",
                (item_id, course_id),
            ).fetchone()
            return row is not None

    def mark_item_seen(
        self,
        item_id: str,
        course_id: str,
        item_type: ItemType,
        title: str = "",
    ) -> None:
        """Mark an item as processed.

        Args:
            item_id: Unique Google Classroom item ID.
            course_id: Parent course ID.
            item_type: Type of the item.
            title: Item title for reference.
        """
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_items (item_id, course_id, item_type, title, first_seen)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, course_id, item_type.value, title, now),
            )

    def get_seen_item_ids(self, course_id: str) -> set[str]:
        """Get all seen item IDs for a course.

        Args:
            course_id: The course to query.

        Returns:
            Set of item IDs that have been processed.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT item_id FROM seen_items WHERE course_id = ?",
                (course_id,),
            ).fetchall()
            return {row["item_id"] for row in rows}

    # ── Download Operations ────────────────────────────────────────────

    def is_file_downloaded(self, item_id: str, course_id: str, filename: str) -> bool:
        """Check if a specific file has already been downloaded.

        Args:
            item_id: Parent item ID.
            course_id: Parent course ID.
            filename: The filename to check.

        Returns:
            True if the file has been downloaded successfully.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM downloads
                WHERE item_id = ? AND course_id = ? AND filename = ? AND success = 1
                """,
                (item_id, course_id, filename),
            ).fetchone()
            return row is not None

    def record_download(
        self,
        item_id: str,
        course_id: str,
        filename: str,
        file_path: str,
        mime_type: str = "",
        success: bool = True,
    ) -> None:
        """Record a file download attempt.

        Args:
            item_id: Parent item ID.
            course_id: Parent course ID.
            filename: Name of the downloaded file.
            file_path: Local path where the file was saved.
            mime_type: MIME type of the file.
            success: Whether the download succeeded.
        """
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO downloads (item_id, course_id, filename, file_path, mime_type, downloaded, success)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, course_id, filename, file_path, mime_type, now, int(success)),
            )

    # ── Sync History Operations ────────────────────────────────────────

    def start_sync(self) -> int:
        """Record the start of a sync operation.

        Returns:
            The sync history record ID.
        """
        now = datetime.now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sync_history (started_at, status) VALUES (?, 'running')",
                (now,),
            )
            sync_id = cursor.lastrowid
            assert sync_id is not None
            return sync_id

    def complete_sync(
        self,
        sync_id: int,
        courses_synced: int = 0,
        new_items_found: int = 0,
        downloads_count: int = 0,
        status: str = "completed",
        error_message: str = "",
    ) -> None:
        """Record the completion of a sync operation.

        Args:
            sync_id: The sync history record ID.
            courses_synced: Number of courses processed.
            new_items_found: Number of new items discovered.
            downloads_count: Number of files downloaded.
            status: Final status (completed, failed, partial).
            error_message: Error details if applicable.
        """
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sync_history SET
                    completed_at = ?,
                    courses_synced = ?,
                    new_items_found = ?,
                    downloads_count = ?,
                    status = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (now, courses_synced, new_items_found, downloads_count, status, error_message, sync_id),
            )

    def get_last_sync(self) -> Optional[dict]:
        """Get the most recent sync history record.

        Returns:
            The last sync record as a dictionary, or None.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
