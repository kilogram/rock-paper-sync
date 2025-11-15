"""State management for reMarkable-Obsidian Sync.

Persists sync state, file mappings, and sync history using SQLite.
"""

import hashlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rm_obsidian_sync.state")


class StateError(Exception):
    """Exception raised for state management errors."""

    pass


@dataclass
class SyncRecord:
    """Record of a synced file's state."""

    obsidian_path: str
    remarkable_uuid: str
    content_hash: str
    last_sync_time: int
    page_count: int
    status: str  # 'synced', 'pending', 'error'


class StateManager:
    """Manages sync state using SQLite database."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path) -> None:
        """Initialize state manager with database at given path.

        Args:
            db_path: Path to SQLite database file (will be created if it doesn't exist)
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access
            self.conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema()
            logger.debug(f"State database initialized at {db_path}")
        except Exception as e:
            raise StateError(f"Failed to initialize state database: {e}")

    def _ensure_schema(self) -> None:
        """Create database tables if they don't exist."""
        with self.conn:
            self.conn.executescript(
                """
                -- File sync state
                CREATE TABLE IF NOT EXISTS sync_state (
                    obsidian_path TEXT PRIMARY KEY,
                    remarkable_uuid TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    last_sync_time INTEGER NOT NULL,
                    page_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'synced'
                );

                -- Folder mappings (Obsidian folder path -> reMarkable UUID)
                CREATE TABLE IF NOT EXISTS folder_mapping (
                    obsidian_folder TEXT PRIMARY KEY,
                    remarkable_uuid TEXT NOT NULL
                );

                -- Sync history for debugging and auditing
                CREATE TABLE IF NOT EXISTS sync_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    obsidian_path TEXT NOT NULL,
                    action TEXT NOT NULL,  -- 'created', 'updated', 'deleted', 'error'
                    timestamp INTEGER NOT NULL,
                    details TEXT
                );

                -- Schema version for migrations
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );

                -- Insert schema version if not exists
                INSERT OR IGNORE INTO schema_version (version) VALUES (1);
                """
            )

    def get_file_state(self, obsidian_path: str) -> Optional[SyncRecord]:
        """Get sync state for a file.

        Args:
            obsidian_path: Relative path of file in Obsidian vault

        Returns:
            SyncRecord if file has been synced before, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT * FROM sync_state WHERE obsidian_path = ?", (obsidian_path,)
        )
        row = cursor.fetchone()
        if row:
            return SyncRecord(**dict(row))
        return None

    def update_file_state(self, record: SyncRecord) -> None:
        """Insert or update sync state for a file.

        Args:
            record: SyncRecord to store

        Note:
            This operation is atomic - uses a transaction internally.
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO sync_state
                (obsidian_path, remarkable_uuid, content_hash, last_sync_time, page_count, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.obsidian_path,
                    record.remarkable_uuid,
                    record.content_hash,
                    record.last_sync_time,
                    record.page_count,
                    record.status,
                ),
            )
        logger.debug(f"Updated sync state for {record.obsidian_path}")

    def delete_file_state(self, obsidian_path: str) -> None:
        """Delete sync state for a file.

        Args:
            obsidian_path: Relative path of file in Obsidian vault
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM sync_state WHERE obsidian_path = ?", (obsidian_path,)
            )
        logger.debug(f"Deleted sync state for {obsidian_path}")

    def get_folder_uuid(self, folder_path: str) -> Optional[str]:
        """Get reMarkable UUID for an Obsidian folder.

        Args:
            folder_path: Relative path of folder in Obsidian vault

        Returns:
            UUID string if folder has been mapped, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT remarkable_uuid FROM folder_mapping WHERE obsidian_folder = ?",
            (folder_path,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def create_folder_mapping(self, folder_path: str, uuid: str) -> None:
        """Store folder→UUID mapping.

        Args:
            folder_path: Relative path of folder in Obsidian vault
            uuid: reMarkable UUID for this folder
        """
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO folder_mapping (obsidian_folder, remarkable_uuid) VALUES (?, ?)",
                (folder_path, uuid),
            )
        logger.debug(f"Created folder mapping: {folder_path} -> {uuid}")

    def get_all_synced_files(self) -> list[SyncRecord]:
        """Get all files in sync state.

        Returns:
            List of all SyncRecords in database
        """
        cursor = self.conn.execute("SELECT * FROM sync_state")
        return [SyncRecord(**dict(row)) for row in cursor.fetchall()]

    def compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA-256 hash of file content.

        Args:
            file_path: Path to file to hash

        Returns:
            Hexadecimal hash string

        Example:
            >>> manager.compute_file_hash(Path("test.md"))
            'a1b2c3d4e5f6...'
        """
        hasher = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                # Read in chunks to handle large files efficiently
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            logger.error(f"Failed to hash file {file_path}: {e}")
            raise StateError(f"Cannot compute hash for {file_path}: {e}")

    def find_changed_files(
        self, vault_path: Path, include_patterns: list[str], exclude_patterns: list[str]
    ) -> list[Path]:
        """Find files that need syncing based on content hash.

        Compares current file hashes with stored hashes to identify changes.

        Args:
            vault_path: Path to Obsidian vault root
            include_patterns: Glob patterns for files to include
            exclude_patterns: Glob patterns for files to exclude

        Returns:
            List of Path objects for files that have changed or are new
        """
        changed: list[Path] = []

        for pattern in include_patterns:
            for file_path in vault_path.glob(pattern):
                # Skip if not a file
                if not file_path.is_file():
                    continue

                # Check exclusions
                if self._is_excluded(file_path, vault_path, exclude_patterns):
                    logger.debug(f"Excluded: {file_path}")
                    continue

                relative_path = str(file_path.relative_to(vault_path))
                current_hash = self.compute_file_hash(file_path)

                # Get existing state
                state = self.get_file_state(relative_path)

                # File is new or changed if no state or hash differs
                if state is None:
                    logger.debug(f"New file: {relative_path}")
                    changed.append(file_path)
                elif state.content_hash != current_hash:
                    logger.debug(f"Changed file: {relative_path}")
                    changed.append(file_path)

        logger.info(f"Found {len(changed)} changed files")
        return changed

    def _is_excluded(
        self, file_path: Path, vault_path: Path, exclude_patterns: list[str]
    ) -> bool:
        """Check if file matches any exclude pattern.

        Args:
            file_path: Absolute path to file
            vault_path: Vault root path
            exclude_patterns: List of glob patterns to exclude

        Returns:
            True if file should be excluded, False otherwise
        """
        relative = file_path.relative_to(vault_path)
        for pattern in exclude_patterns:
            if relative.match(pattern):
                return True
        return False

    def log_sync_action(
        self, path: str, action: str, details: str = ""
    ) -> None:
        """Record sync action in history.

        Args:
            path: File path (relative to vault)
            action: Action type ('created', 'updated', 'deleted', 'error')
            details: Additional information about the action
        """
        with self.conn:
            self.conn.execute(
                "INSERT INTO sync_history (obsidian_path, action, timestamp, details) VALUES (?, ?, ?, ?)",
                (path, action, int(time.time()), details),
            )
        logger.debug(f"Logged action: {action} for {path}")

    def get_recent_history(self, limit: int = 10) -> list[tuple[str, str, int, str]]:
        """Get recent sync history entries.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of tuples: (obsidian_path, action, timestamp, details)
        """
        cursor = self.conn.execute(
            "SELECT obsidian_path, action, timestamp, details FROM sync_history "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return cursor.fetchall()

    def get_stats(self) -> dict[str, int]:
        """Get sync statistics.

        Returns:
            Dictionary with counts by status: {'synced': 10, 'pending': 2, 'error': 1}
        """
        cursor = self.conn.execute(
            "SELECT status, COUNT(*) FROM sync_state GROUP BY status"
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    def reset(self) -> None:
        """Clear all sync state (force full re-sync).

        Warning:
            This will delete all state information. Use with caution.
        """
        with self.conn:
            self.conn.execute("DELETE FROM sync_state")
            self.conn.execute("DELETE FROM folder_mapping")
            self.conn.execute("DELETE FROM sync_history")
        logger.warning("All sync state has been reset")

    def close(self) -> None:
        """Close database connection.

        Should be called when application exits to ensure data is flushed.
        """
        if self.conn:
            self.conn.close()
            logger.debug("State database connection closed")
