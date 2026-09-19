"""State management for reMarkable-Obsidian Sync.

Persists sync state, file mappings, and sync history using SQLite.
"""

import hashlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from rock_paper_sync.annotations.common.snapshots import ContentStore, SnapshotStore

logger = logging.getLogger("rock_paper_sync.state")


class StateError(Exception):
    """Exception raised for state management errors."""

    pass


@dataclass
class SyncRecord:
    """Record of a synced file's state.

    Schema v6 changes:
    - Removed: file_hash_with_markers (unused, parser provides semantic hash)
    - Added: last_root_generation (cloud versioning for annotation detection)
    - Added: last_doc_index_hash (per-document change detection including .rm files)
    """

    vault_name: str
    obsidian_path: str
    remarkable_uuid: str
    content_hash: str  # Semantic hash (markers stripped by parser)
    last_sync_time: int
    page_count: int
    status: str  # 'synced', 'pending', 'error'
    last_root_generation: int | None = None  # Cloud root generation number (schema v6)
    last_doc_index_hash: str | None = None  # Document index hash (schema v6)
    pending_pdf_sha256: str | None = None  # PDF hash during push (schema v9)
    pushed_pdf_sha256: str | None = None  # Last pushed PDF hash (schema v9)
    word_map: bytes | None = None  # Serialised WordMap blob (schema v9)
    ink_clearance_hash: str | None = None  # Doc index hash at clearance (schema v9)
    render_config_hash: str | None = None  # Render config hash (schema v9)


@dataclass
class PullState:
    """Record of pull sync state for a file (schema v7).

    Tracks the last pulled annotation state from device for change detection.
    """

    vault_name: str
    obsidian_path: str
    remarkable_uuid: str
    last_annotation_hash: str | None  # Hash of annotation UUIDs for change detection
    last_pull_time: int | None
    last_rm_files_hash: str | None  # Content hash of cached .rm files


@dataclass
class OrphanedAnnotation:
    """Record of an orphaned annotation (schema v8).

    Tracks annotations that could not be reanchored after content changes.
    blocks_blob stores serialized rmscene blocks for hidden-layer preservation
    (added in schema v8 / M5.5).
    """

    vault_name: str
    obsidian_path: str
    annotation_id: str
    annotation_type: str  # 'highlight' or 'stroke'
    original_anchor_text: str | None  # What the annotation was anchored to
    orphaned_at: int
    blocks_blob: bytes | None = None  # Serialized rmscene blocks (v8)
    source_page_idx: int | None = None  # Source page index on device (v8)


class StateManager:
    """Manages sync state using SQLite database."""

    SCHEMA_VERSION = 11  # Schema v11: word_map format includes blocks/token_block

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

        # Initialize snapshot store (lazy-loaded)
        self._snapshot_store: SnapshotStore | None = None

    def _ensure_schema(self) -> None:
        """Create database tables if they don't exist."""
        with self.conn:
            self.conn.executescript(
                """
                -- File sync state (vault-aware, schema v9)
                CREATE TABLE IF NOT EXISTS sync_state (
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    remarkable_uuid TEXT NOT NULL,
                    content_hash TEXT NOT NULL,  -- Semantic hash (markers stripped)
                    last_sync_time INTEGER NOT NULL,
                    page_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'synced',
                    last_root_generation INTEGER,  -- Cloud generation number (v6)
                    last_doc_index_hash TEXT,       -- Document index hash (v6)
                    pending_pdf_sha256 TEXT,        -- PDF hash during push (v9)
                    pushed_pdf_sha256 TEXT,         -- Last pushed PDF hash (v9)
                    word_map BLOB,                  -- Serialised WordMap (v9)
                    ink_clearance_hash TEXT,        -- Doc index hash at clearance (v9)
                    render_config_hash TEXT,        -- Render config hash (v9)
                    PRIMARY KEY (vault_name, obsidian_path)
                );

                -- Folder mappings (vault-aware: Obsidian folder path -> reMarkable UUID)
                CREATE TABLE IF NOT EXISTS folder_mapping (
                    vault_name TEXT NOT NULL,
                    obsidian_folder TEXT NOT NULL,
                    remarkable_uuid TEXT NOT NULL,
                    PRIMARY KEY (vault_name, obsidian_folder)
                );

                -- Sync history for debugging and auditing (vault-aware)
                CREATE TABLE IF NOT EXISTS sync_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    action TEXT NOT NULL,  -- 'created', 'updated', 'deleted', 'error'
                    timestamp INTEGER NOT NULL,
                    details TEXT
                );

                -- Paragraph annotation state (for annotation-aware editing)
                CREATE TABLE IF NOT EXISTS paragraph_state (
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    paragraph_index INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    has_annotations BOOLEAN NOT NULL DEFAULT 0,
                    annotation_count INTEGER NOT NULL DEFAULT 0,
                    last_checked INTEGER NOT NULL,
                    PRIMARY KEY (vault_name, obsidian_path, paragraph_index)
                );

                -- Schema version for migrations
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );

                -- OCR results (schema v5)
                CREATE TABLE IF NOT EXISTS ocr_results (
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    annotation_uuid TEXT NOT NULL,
                    paragraph_index INTEGER NOT NULL,
                    ocr_text TEXT NOT NULL,
                    ocr_text_hash TEXT NOT NULL,
                    original_text_hash TEXT NOT NULL,
                    image_hash TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    model_version TEXT NOT NULL,
                    processed_time INTEGER NOT NULL,
                    PRIMARY KEY (vault_name, obsidian_path, annotation_uuid)
                );

                -- OCR corrections for fine-tuning (schema v5)
                CREATE TABLE IF NOT EXISTS ocr_corrections (
                    id TEXT PRIMARY KEY,
                    image_hash TEXT NOT NULL UNIQUE,
                    image_path TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    corrected_text TEXT NOT NULL,
                    paragraph_context TEXT,
                    document_id TEXT NOT NULL,
                    dataset_version TEXT,
                    created_at INTEGER NOT NULL
                );

                -- Index for finding pending corrections
                CREATE INDEX IF NOT EXISTS idx_corrections_pending
                ON ocr_corrections(dataset_version) WHERE dataset_version IS NULL;

                -- Index for finding OCR results by document
                CREATE INDEX IF NOT EXISTS idx_ocr_results_document
                ON ocr_results(vault_name, obsidian_path);

                -- Pull state for annotation change detection (schema v7)
                CREATE TABLE IF NOT EXISTS pull_state (
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    remarkable_uuid TEXT NOT NULL,
                    last_annotation_hash TEXT,
                    last_pull_time INTEGER,
                    last_rm_files_hash TEXT,
                    PRIMARY KEY (vault_name, obsidian_path)
                ) STRICT;

                -- Orphaned annotations tracking (schema v8)
                CREATE TABLE IF NOT EXISTS orphaned_annotations (
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    annotation_id TEXT NOT NULL,
                    annotation_type TEXT NOT NULL,
                    original_anchor_text TEXT,
                    orphaned_at INTEGER NOT NULL,
                    blocks_blob BLOB,
                    source_page_idx INTEGER,
                    PRIMARY KEY (vault_name, obsidian_path, annotation_id)
                ) STRICT;

                -- Index for finding orphaned annotations by document
                CREATE INDEX IF NOT EXISTS idx_orphaned_annotations_document
                ON orphaned_annotations(vault_name, obsidian_path);

                -- Ink stroke ledger: one row per processed stroke (schema v10, I4/I5)
                CREATE TABLE IF NOT EXISTS ink_strokes (
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    doc_uuid TEXT NOT NULL,
                    stroke_id TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    PRIMARY KEY (vault_name, obsidian_path, stroke_id)
                );

                -- Ink cluster records (schema v10)
                CREATE TABLE IF NOT EXISTS ink_clusters (
                    cluster_id TEXT PRIMARY KEY,
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    deferred_reason TEXT,
                    page_redir INTEGER,
                    rect TEXT,
                    anchor_kind TEXT,
                    anchor_ref TEXT,
                    attachment TEXT,
                    edit_id TEXT,
                    pushed_pdf_sha256 TEXT,
                    created_at INTEGER NOT NULL
                );

                -- Index for finding ink data by document
                CREATE INDEX IF NOT EXISTS idx_ink_strokes_document
                ON ink_strokes(vault_name, obsidian_path);

                CREATE INDEX IF NOT EXISTS idx_ink_clusters_document
                ON ink_clusters(vault_name, obsidian_path);
                """
            )
            # Initialize or migrate schema version
            self._ensure_schema_version()

    def _ensure_schema_version(self) -> None:
        """Ensure schema version is set and run migrations if needed."""
        cursor = self.conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()

        if row is None:
            # Fresh database - set to current version
            with self.conn:
                self.conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (self.SCHEMA_VERSION,),
                )
            logger.debug(f"Initialized schema version to {self.SCHEMA_VERSION}")
        else:
            current_version = row[0]
            if current_version < self.SCHEMA_VERSION:
                self._run_migrations(current_version)

    def _run_migrations(self, current_version: int) -> None:
        """Run any pending schema migrations."""
        if current_version < 7:
            self._migrate_to_v7()
        if current_version < 8:
            self._migrate_to_v8()
        if current_version < 9:
            self._migrate_to_v9()
        if current_version < 10:
            self._migrate_to_v10()
        if current_version < 11:
            self._migrate_to_v11()

    def _migrate_to_v7(self) -> None:
        """Migrate from v5/v6 to v7: Add pull_state and orphaned_annotations tables."""
        logger.info("Migrating database schema to v7...")
        with self.conn:
            self.conn.execute("DELETE FROM schema_version")
            self.conn.execute("INSERT INTO schema_version (version) VALUES (7)")
        logger.info("Database schema migrated to v7")

    def _migrate_to_v8(self) -> None:
        """Migrate from v7 to v8: Add blocks_blob and source_page_idx to orphaned_annotations."""
        logger.info("Migrating database schema to v8...")
        with self.conn:
            self.conn.executescript("""
                ALTER TABLE orphaned_annotations ADD COLUMN blocks_blob BLOB;
                ALTER TABLE orphaned_annotations ADD COLUMN source_page_idx INTEGER;
            """)
            self.conn.execute("DELETE FROM schema_version")
            self.conn.execute("INSERT INTO schema_version (version) VALUES (8)")
        logger.info("Database schema migrated to v8")

    def _migrate_to_v9(self) -> None:
        """Migrate from v8 to v9: Add PDF substrate columns to sync_state."""
        logger.info("Migrating database schema to v9...")
        with self.conn:
            # Add columns for PDF push workflow:
            # - pending_pdf_sha256: hash of the PDF being pushed (during push)
            # - pushed_pdf_sha256: hash of the last successfully pushed PDF
            # - word_map: serialised WordMap blob
            # - ink_clearance_hash: doc index hash at clearance mint time
            # - render_config_hash: hash of render config for re-push detection
            self.conn.executescript("""
                ALTER TABLE sync_state ADD COLUMN pending_pdf_sha256 TEXT;
                ALTER TABLE sync_state ADD COLUMN pushed_pdf_sha256 TEXT;
                ALTER TABLE sync_state ADD COLUMN word_map BLOB;
                ALTER TABLE sync_state ADD COLUMN ink_clearance_hash TEXT;
                ALTER TABLE sync_state ADD COLUMN render_config_hash TEXT;
            """)
            self.conn.execute("DELETE FROM schema_version")
            self.conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        logger.info("Database schema migrated to v9")

    def _migrate_to_v10(self) -> None:
        """Migrate from v9 to v10: Add ink_strokes and ink_clusters tables."""
        logger.info("Migrating database schema to v10...")
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS ink_strokes (
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    doc_uuid TEXT NOT NULL,
                    stroke_id TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    PRIMARY KEY (vault_name, obsidian_path, stroke_id)
                );
                CREATE TABLE IF NOT EXISTS ink_clusters (
                    cluster_id TEXT PRIMARY KEY,
                    vault_name TEXT NOT NULL,
                    obsidian_path TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    deferred_reason TEXT,
                    page_redir INTEGER,
                    rect TEXT,
                    anchor_kind TEXT,
                    anchor_ref TEXT,
                    attachment TEXT,
                    edit_id TEXT,
                    pushed_pdf_sha256 TEXT,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ink_strokes_document
                ON ink_strokes(vault_name, obsidian_path);
                CREATE INDEX IF NOT EXISTS idx_ink_clusters_document
                ON ink_clusters(vault_name, obsidian_path);
            """)
            self.conn.execute("DELETE FROM schema_version")
            self.conn.execute("INSERT INTO schema_version (version) VALUES (10)")
        logger.info("Database schema migrated to v10")

    def _migrate_to_v11(self) -> None:
        """Migrate from v10 to v11: clear word_map to force re-push.

        The word-map JSON format now includes ``blocks`` and
        ``token_block`` fields (populated from the renderer's
        ``data-anchor`` box-tree walk).  Existing stored maps are
        dev-only; clearing them forces a re-push that produces the
        new format.
        """
        logger.info("Migrating database schema to v11...")
        with self.conn:
            self.conn.execute("UPDATE sync_state SET word_map = NULL")
            self.conn.execute("DELETE FROM schema_version")
            self.conn.execute("INSERT INTO schema_version (version) VALUES (11)")
        logger.info("Database schema migrated to v11 (word_map cleared; re-push required)")

    def get_file_state(self, vault_name: str, obsidian_path: str) -> SyncRecord | None:
        """Get sync state for a file.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault

        Returns:
            SyncRecord if file has been synced before, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT * FROM sync_state WHERE vault_name = ? AND obsidian_path = ?",
            (vault_name, obsidian_path),
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
                (vault_name, obsidian_path, remarkable_uuid, content_hash,
                 last_sync_time, page_count, status,
                 last_root_generation, last_doc_index_hash,
                 pending_pdf_sha256, pushed_pdf_sha256, word_map,
                 ink_clearance_hash, render_config_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.vault_name,
                    record.obsidian_path,
                    record.remarkable_uuid,
                    record.content_hash,
                    record.last_sync_time,
                    record.page_count,
                    record.status,
                    record.last_root_generation,
                    record.last_doc_index_hash,
                    record.pending_pdf_sha256,
                    record.pushed_pdf_sha256,
                    record.word_map,
                    record.ink_clearance_hash,
                    record.render_config_hash,
                ),
            )
        logger.debug(f"Updated sync state for {record.vault_name}:{record.obsidian_path}")

    def delete_file_state(self, vault_name: str, obsidian_path: str) -> None:
        """Delete sync state for a file.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM sync_state WHERE vault_name = ? AND obsidian_path = ?",
                (vault_name, obsidian_path),
            )
        logger.debug(f"Deleted sync state for {vault_name}:{obsidian_path}")

    def get_folder_uuid(self, vault_name: str, folder_path: str) -> str | None:
        """Get reMarkable UUID for an Obsidian folder.

        Args:
            vault_name: Name of the vault
            folder_path: Relative path of folder in Obsidian vault

        Returns:
            UUID string if folder has been mapped, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT remarkable_uuid FROM folder_mapping WHERE vault_name = ? AND obsidian_folder = ?",
            (vault_name, folder_path),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def create_folder_mapping(self, vault_name: str, folder_path: str, uuid: str) -> None:
        """Store folder→UUID mapping.

        Args:
            vault_name: Name of the vault
            folder_path: Relative path of folder in Obsidian vault
            uuid: reMarkable UUID for this folder
        """
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO folder_mapping (vault_name, obsidian_folder, remarkable_uuid) VALUES (?, ?, ?)",
                (vault_name, folder_path, uuid),
            )
        logger.debug(f"Created folder mapping: {vault_name}:{folder_path} -> {uuid}")

    def get_all_synced_files(self, vault_name: str | None = None) -> list[SyncRecord]:
        """Get all files in sync state.

        Args:
            vault_name: Optional vault name to filter by. If None, returns all vaults.

        Returns:
            List of all SyncRecords in database (optionally filtered by vault)
        """
        if vault_name:
            cursor = self.conn.execute(
                "SELECT * FROM sync_state WHERE vault_name = ?", (vault_name,)
            )
        else:
            cursor = self.conn.execute("SELECT * FROM sync_state")
        return [SyncRecord(**dict(row)) for row in cursor.fetchall()]

    def get_all_folders(self, vault_name: str) -> list[tuple[str, str]]:
        """Get all folder mappings for a vault.

        Args:
            vault_name: Name of the vault

        Returns:
            List of (folder_path, remarkable_uuid) tuples, ordered by depth (deepest first)
            This order ensures child folders are processed before parent folders.
        """
        cursor = self.conn.execute(
            """
            SELECT obsidian_folder, remarkable_uuid
            FROM folder_mapping
            WHERE vault_name = ?
            ORDER BY LENGTH(obsidian_folder) DESC
            """,
            (vault_name,),
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]

    def delete_folder_mapping(self, vault_name: str, folder_path: str) -> None:
        """Delete a folder mapping from the database.

        Args:
            vault_name: Name of the vault
            folder_path: Relative path of folder in Obsidian vault
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM folder_mapping WHERE vault_name = ? AND obsidian_folder = ?",
                (vault_name, folder_path),
            )
        logger.debug(f"Deleted folder mapping: {vault_name}:{folder_path}")

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

    def log_sync_action(self, vault_name: str, path: str, action: str, details: str = "") -> None:
        """Record sync action in history.

        Args:
            vault_name: Name of the vault
            path: File path (relative to vault)
            action: Action type ('created', 'updated', 'deleted', 'error')
            details: Additional information about the action
        """
        with self.conn:
            self.conn.execute(
                "INSERT INTO sync_history (vault_name, obsidian_path, action, timestamp, details) VALUES (?, ?, ?, ?, ?)",
                (vault_name, path, action, int(time.time()), details),
            )
        logger.debug(f"Logged action: {action} for {vault_name}:{path}")

    def get_recent_history(
        self, limit: int = 10, vault_name: str | None = None
    ) -> list[tuple[str, str, str, int, str]]:
        """Get recent sync history entries.

        Args:
            limit: Maximum number of entries to return
            vault_name: Optional vault name to filter by

        Returns:
            List of tuples: (vault_name, obsidian_path, action, timestamp, details)
        """
        if vault_name:
            cursor = self.conn.execute(
                "SELECT vault_name, obsidian_path, action, timestamp, details FROM sync_history "
                "WHERE vault_name = ? ORDER BY timestamp DESC, id DESC LIMIT ?",
                (vault_name, limit),
            )
        else:
            cursor = self.conn.execute(
                "SELECT vault_name, obsidian_path, action, timestamp, details FROM sync_history "
                "ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,),
            )
        return cursor.fetchall()

    def get_stats(self, vault_name: str | None = None) -> dict[str, int]:
        """Get sync statistics.

        Args:
            vault_name: Optional vault name to filter by

        Returns:
            Dictionary with counts by status: {'synced': 10, 'pending': 2, 'error': 1}
        """
        if vault_name:
            cursor = self.conn.execute(
                "SELECT status, COUNT(*) FROM sync_state WHERE vault_name = ? GROUP BY status",
                (vault_name,),
            )
        else:
            cursor = self.conn.execute("SELECT status, COUNT(*) FROM sync_state GROUP BY status")
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
            self.conn.execute("DELETE FROM paragraph_state")
        logger.warning("All sync state has been reset")

    def get_paragraph_state(
        self, vault_name: str, obsidian_path: str, paragraph_index: int
    ) -> dict | None:
        """Get annotation state for a specific paragraph.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault
            paragraph_index: Index of paragraph in document

        Returns:
            Dictionary with paragraph state or None if not found
            Keys: content_hash, has_annotations, annotation_count, last_checked
        """
        cursor = self.conn.execute(
            """
            SELECT content_hash, has_annotations, annotation_count, last_checked
            FROM paragraph_state
            WHERE vault_name = ? AND obsidian_path = ? AND paragraph_index = ?
            """,
            (vault_name, obsidian_path, paragraph_index),
        )
        row = cursor.fetchone()
        if row:
            result = dict(row)
            result["has_annotations"] = bool(result["has_annotations"])  # Convert to bool
            return result
        return None

    def update_paragraph_state(
        self,
        vault_name: str,
        obsidian_path: str,
        paragraph_index: int,
        content_hash: str,
        has_annotations: bool,
        annotation_count: int,
    ) -> None:
        """Update annotation state for a paragraph.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault
            paragraph_index: Index of paragraph in document
            content_hash: Hash of paragraph content
            has_annotations: Whether paragraph has annotations
            annotation_count: Number of annotations on paragraph
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO paragraph_state
                (vault_name, obsidian_path, paragraph_index, content_hash,
                 has_annotations, annotation_count, last_checked)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vault_name,
                    obsidian_path,
                    paragraph_index,
                    content_hash,
                    1 if has_annotations else 0,
                    annotation_count,
                    int(time.time()),
                ),
            )
        logger.debug(
            f"Updated paragraph state: {vault_name}:{obsidian_path}[{paragraph_index}] "
            f"({annotation_count} annotations)"
        )

    def get_all_paragraph_states(self, vault_name: str, obsidian_path: str) -> dict[int, dict]:
        """Get annotation state for all paragraphs in a document.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault

        Returns:
            Dictionary mapping paragraph_index to state dict
        """
        cursor = self.conn.execute(
            """
            SELECT paragraph_index, content_hash, has_annotations, annotation_count, last_checked
            FROM paragraph_state
            WHERE vault_name = ? AND obsidian_path = ?
            ORDER BY paragraph_index
            """,
            (vault_name, obsidian_path),
        )
        result = {}
        for row in cursor.fetchall():
            state = dict(row)
            paragraph_index = state.pop("paragraph_index")
            state["has_annotations"] = bool(state["has_annotations"])  # Convert to bool
            result[paragraph_index] = state
        return result

    def delete_paragraph_states(self, vault_name: str, obsidian_path: str) -> None:
        """Delete all paragraph states for a document.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM paragraph_state WHERE vault_name = ? AND obsidian_path = ?",
                (vault_name, obsidian_path),
            )
        logger.debug(f"Deleted paragraph states for {vault_name}:{obsidian_path}")

    # OCR-related methods (schema v5)

    def update_ocr_result(
        self,
        vault_name: str,
        obsidian_path: str,
        annotation_uuid: str,
        paragraph_index: int,
        ocr_text: str,
        ocr_text_hash: str,
        original_text_hash: str,
        image_hash: str,
        confidence: float,
        model_version: str,
    ) -> None:
        """Store or update an OCR result.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file
            annotation_uuid: UUID of the annotation
            paragraph_index: Index of paragraph in document
            ocr_text: Recognized text
            ocr_text_hash: Hash of OCR text
            original_text_hash: Hash of original paragraph text
            image_hash: Hash of annotation image
            confidence: OCR confidence score
            model_version: Model version used
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO ocr_results
                (vault_name, obsidian_path, annotation_uuid, paragraph_index,
                 ocr_text, ocr_text_hash, original_text_hash, image_hash,
                 confidence, model_version, processed_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vault_name,
                    obsidian_path,
                    annotation_uuid,
                    paragraph_index,
                    ocr_text,
                    ocr_text_hash,
                    original_text_hash,
                    image_hash,
                    confidence,
                    model_version,
                    int(time.time()),
                ),
            )
        logger.debug(f"Updated OCR result for {vault_name}:{obsidian_path}[{paragraph_index}]")

    def get_ocr_result(
        self, vault_name: str, obsidian_path: str, annotation_uuid: str
    ) -> dict | None:
        """Get OCR result for an annotation.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file
            annotation_uuid: UUID of the annotation

        Returns:
            Dictionary with OCR result or None
        """
        cursor = self.conn.execute(
            """
            SELECT paragraph_index, ocr_text, ocr_text_hash, original_text_hash,
                   image_hash, confidence, model_version, processed_time
            FROM ocr_results
            WHERE vault_name = ? AND obsidian_path = ? AND annotation_uuid = ?
            """,
            (vault_name, obsidian_path, annotation_uuid),
        )
        row = cursor.fetchone()
        if row:
            result = dict(row)
            result["annotation_uuid"] = annotation_uuid
            return result
        return None

    def get_all_ocr_results(self, vault_name: str, obsidian_path: str) -> dict[int, dict]:
        """Get all OCR results for a document.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file

        Returns:
            Dictionary mapping paragraph_index to OCR result dict
        """
        cursor = self.conn.execute(
            """
            SELECT annotation_uuid, paragraph_index, ocr_text, ocr_text_hash,
                   original_text_hash, image_hash, confidence, model_version, processed_time
            FROM ocr_results
            WHERE vault_name = ? AND obsidian_path = ?
            ORDER BY paragraph_index
            """,
            (vault_name, obsidian_path),
        )
        result = {}
        for row in cursor.fetchall():
            state = dict(row)
            paragraph_index = state["paragraph_index"]
            result[paragraph_index] = state
        return result

    def delete_ocr_results(self, vault_name: str, obsidian_path: str) -> None:
        """Delete all OCR results for a document.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM ocr_results WHERE vault_name = ? AND obsidian_path = ?",
                (vault_name, obsidian_path),
            )
        logger.debug(f"Deleted OCR results for {vault_name}:{obsidian_path}")

    def add_ocr_correction(
        self,
        correction_id: str,
        image_hash: str,
        image_path: str,
        original_text: str,
        corrected_text: str,
        paragraph_context: str,
        document_id: str,
    ) -> None:
        """Add an OCR correction for fine-tuning.

        Args:
            correction_id: Unique correction ID
            image_hash: Hash of annotation image
            image_path: Path to annotation image
            original_text: Original OCR text
            corrected_text: User-corrected text
            paragraph_context: Context paragraph text
            document_id: Source document identifier
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO ocr_corrections
                (id, image_hash, image_path, original_text, corrected_text,
                 paragraph_context, document_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    correction_id,
                    image_hash,
                    image_path,
                    original_text,
                    corrected_text,
                    paragraph_context,
                    document_id,
                    int(time.time()),
                ),
            )
        logger.debug(f"Added OCR correction {correction_id}")

    def get_pending_ocr_corrections(self) -> list[dict]:
        """Get all corrections not yet assigned to a dataset.

        Returns:
            List of correction dictionaries
        """
        cursor = self.conn.execute(
            """
            SELECT id, image_hash, image_path, original_text, corrected_text,
                   paragraph_context, document_id, created_at
            FROM ocr_corrections
            WHERE dataset_version IS NULL
            ORDER BY created_at
            """
        )
        return [
            {
                "id": row[0],
                "image_hash": row[1],
                "image_path": row[2],
                "original_text": row[3],
                "corrected_text": row[4],
                "paragraph_context": row[5],
                "document_id": row[6],
                "created_at": row[7],
            }
            for row in cursor.fetchall()
        ]

    def assign_corrections_to_dataset(
        self, correction_ids: list[str], dataset_version: str
    ) -> None:
        """Assign corrections to a dataset version.

        Args:
            correction_ids: List of correction IDs
            dataset_version: Dataset version string
        """
        with self.conn:
            self.conn.executemany(
                "UPDATE ocr_corrections SET dataset_version = ? WHERE id = ?",
                [(dataset_version, cid) for cid in correction_ids],
            )
        logger.info(f"Assigned {len(correction_ids)} corrections to dataset {dataset_version}")

    def get_all_correction_image_hashes(self) -> list[str]:
        """Get all image hashes referenced by corrections.

        Returns:
            List of image hash strings
        """
        cursor = self.conn.execute("SELECT DISTINCT image_hash FROM ocr_corrections")
        return [row[0] for row in cursor.fetchall()]

    def get_ocr_correction_stats(self) -> dict[str, int]:
        """Get OCR correction statistics.

        Returns:
            Dictionary with counts: {'pending': N, 'total': M, 'datasets': K}
        """
        cursor = self.conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN dataset_version IS NULL THEN 1 ELSE 0 END) as pending,
                COUNT(DISTINCT dataset_version) as datasets
            FROM ocr_corrections
            """
        )
        row = cursor.fetchone()
        return {
            "total": row[0] or 0,
            "pending": row[1] or 0,
            "datasets": row[2] or 0,
        }

    @property
    def snapshots(self) -> SnapshotStore:
        """Get snapshot store for file and annotation block snapshots.

        Returns:
            SnapshotStore instance (lazily initialized)
        """
        if self._snapshot_store is None:
            # Create content store in data directory alongside state database
            snapshots_dir = self.db_path.parent / "snapshots"
            content_store = ContentStore(snapshots_dir)
            self._snapshot_store = SnapshotStore(self.conn, content_store)

        return self._snapshot_store

    # Pull state methods (schema v7)

    def get_pull_state(self, vault_name: str, obsidian_path: str) -> PullState | None:
        """Get pull sync state for a file.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault

        Returns:
            PullState if file has been pulled before, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT * FROM pull_state WHERE vault_name = ? AND obsidian_path = ?",
            (vault_name, obsidian_path),
        )
        row = cursor.fetchone()
        if row:
            return PullState(**dict(row))
        return None

    def update_pull_state(self, state: PullState) -> None:
        """Insert or update pull sync state for a file.

        Args:
            state: PullState to store
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO pull_state
                (vault_name, obsidian_path, remarkable_uuid, last_annotation_hash,
                 last_pull_time, last_rm_files_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state.vault_name,
                    state.obsidian_path,
                    state.remarkable_uuid,
                    state.last_annotation_hash,
                    state.last_pull_time,
                    state.last_rm_files_hash,
                ),
            )
        logger.debug(f"Updated pull state for {state.vault_name}:{state.obsidian_path}")

    def delete_pull_state(self, vault_name: str, obsidian_path: str) -> None:
        """Delete pull state for a file.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM pull_state WHERE vault_name = ? AND obsidian_path = ?",
                (vault_name, obsidian_path),
            )
        logger.debug(f"Deleted pull state for {vault_name}:{obsidian_path}")

    def get_all_pull_states(self, vault_name: str | None = None) -> list[PullState]:
        """Get all pull states.

        Args:
            vault_name: Optional vault name to filter by

        Returns:
            List of all PullState records (optionally filtered by vault)
        """
        if vault_name:
            cursor = self.conn.execute(
                "SELECT * FROM pull_state WHERE vault_name = ?", (vault_name,)
            )
        else:
            cursor = self.conn.execute("SELECT * FROM pull_state")
        return [PullState(**dict(row)) for row in cursor.fetchall()]

    # Orphaned annotations methods (schema v7/v8)

    def add_orphaned_annotation(self, orphan: OrphanedAnnotation) -> None:
        """Record an orphaned annotation.

        Args:
            orphan: OrphanedAnnotation to store
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO orphaned_annotations
                (vault_name, obsidian_path, annotation_id, annotation_type,
                 original_anchor_text, orphaned_at, blocks_blob, source_page_idx)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    orphan.vault_name,
                    orphan.obsidian_path,
                    orphan.annotation_id,
                    orphan.annotation_type,
                    orphan.original_anchor_text,
                    orphan.orphaned_at,
                    orphan.blocks_blob,
                    orphan.source_page_idx,
                ),
            )
        logger.debug(
            f"Added orphaned annotation {orphan.annotation_id} for "
            f"{orphan.vault_name}:{orphan.obsidian_path}"
        )

    def get_orphaned_annotations(
        self, vault_name: str, obsidian_path: str
    ) -> list[OrphanedAnnotation]:
        """Get all orphaned annotations for a document.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault

        Returns:
            List of OrphanedAnnotation records
        """
        cursor = self.conn.execute(
            "SELECT * FROM orphaned_annotations WHERE vault_name = ? AND obsidian_path = ?",
            (vault_name, obsidian_path),
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            # Provide defaults for pre-v8 rows missing the new columns
            d.setdefault("blocks_blob", None)
            d.setdefault("source_page_idx", None)
            result.append(OrphanedAnnotation(**d))
        return result

    def get_orphan_blobs_for_document(
        self, vault_name: str, obsidian_path: str
    ) -> list[tuple[str, int | None, bytes]]:
        """Get orphaned annotation blobs for document, for hidden-layer preservation.

        Returns only orphans that have a stored blocks_blob (i.e. were recorded
        after the v8 migration with M5.5 pull sync).

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault

        Returns:
            List of (annotation_id, source_page_idx, blocks_blob) for orphans
            that have stored blocks.
        """
        cursor = self.conn.execute(
            """
            SELECT annotation_id, source_page_idx, blocks_blob
            FROM orphaned_annotations
            WHERE vault_name = ? AND obsidian_path = ?
              AND blocks_blob IS NOT NULL
            """,
            (vault_name, obsidian_path),
        )
        return [
            (row["annotation_id"], row["source_page_idx"], row["blocks_blob"])
            for row in cursor.fetchall()
        ]

    def delete_orphaned_annotation(
        self, vault_name: str, obsidian_path: str, annotation_id: str
    ) -> None:
        """Delete a specific orphaned annotation.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault
            annotation_id: ID of the annotation to delete
        """
        with self.conn:
            self.conn.execute(
                """
                DELETE FROM orphaned_annotations
                WHERE vault_name = ? AND obsidian_path = ? AND annotation_id = ?
                """,
                (vault_name, obsidian_path, annotation_id),
            )
        logger.debug(
            f"Deleted orphaned annotation {annotation_id} for {vault_name}:{obsidian_path}"
        )

    def delete_all_orphaned_annotations(self, vault_name: str, obsidian_path: str) -> None:
        """Delete all orphaned annotations for a document.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file in Obsidian vault
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM orphaned_annotations WHERE vault_name = ? AND obsidian_path = ?",
                (vault_name, obsidian_path),
            )
        logger.debug(f"Deleted all orphaned annotations for {vault_name}:{obsidian_path}")

    def get_orphan_count(self, vault_name: str | None = None) -> int:
        """Get total count of orphaned annotations.

        Args:
            vault_name: Optional vault name to filter by

        Returns:
            Count of orphaned annotations
        """
        if vault_name:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM orphaned_annotations WHERE vault_name = ?",
                (vault_name,),
            )
        else:
            cursor = self.conn.execute("SELECT COUNT(*) FROM orphaned_annotations")
        return cursor.fetchone()[0]

    # --- Ink ledger methods (schema v10, Phase 4) ---

    def get_known_stroke_ids(self, vault_name: str, obsidian_path: str) -> frozenset[str]:
        """Get all stroke ids already in the ink_strokes ledger.

        Args:
            vault_name: Name of the vault.
            obsidian_path: Relative path of the file.

        Returns:
            Frozenset of ``"part1:part2"`` stroke id strings.
        """
        cursor = self.conn.execute(
            "SELECT stroke_id FROM ink_strokes WHERE vault_name = ? AND obsidian_path = ?",
            (vault_name, obsidian_path),
        )
        return frozenset(row[0] for row in cursor.fetchall())

    def insert_ink_strokes(
        self,
        rows: list[tuple[str, str, str, str, str]],
    ) -> None:
        """Insert stroke rows into the ink_strokes ledger.

        Each row is ``(vault_name, obsidian_path, doc_uuid, stroke_id, cluster_id)``.
        Raises ``sqlite3.IntegrityError`` if a duplicate stroke_id is inserted (I5).

        Args:
            rows: List of row tuples.

        Raises:
            sqlite3.IntegrityError: On duplicate stroke id.
        """
        self.conn.executemany(
            """
            INSERT INTO ink_strokes (vault_name, obsidian_path, doc_uuid, stroke_id, cluster_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

    def insert_ink_cluster(
        self,
        cluster_id: str,
        vault_name: str,
        obsidian_path: str,
        disposition: str,
        deferred_reason: str | None,
        page_redir: int | None,
        rect: str | None,
        anchor_kind: str | None,
        anchor_ref: str | None,
        attachment: str | None,
        edit_id: str | None,
        pushed_pdf_sha256: str | None,
        created_at: int,
    ) -> None:
        """Insert a cluster record into ink_clusters.

        Args:
            cluster_id: sha256 cluster identifier.
            vault_name: Vault name.
            obsidian_path: Relative file path.
            disposition: One of 'highlight', 'consumed', 'baked', 'deferred'.
            deferred_reason: Reason string (only for 'deferred').
            page_redir: PDF page index or None.
            rect: JSON-encoded bounding rect or None.
            anchor_kind: 'paragraph' or 'document_end' or None.
            anchor_ref: Anchor reference string or None.
            attachment: Attachment filename or None.
            edit_id: Edit identifier or None.
            pushed_pdf_sha256: PDF hash at the time of this pull.
            created_at: Unix timestamp.
        """
        self.conn.execute(
            """
            INSERT OR REPLACE INTO ink_clusters
            (cluster_id, vault_name, obsidian_path, disposition, deferred_reason,
             page_redir, rect, anchor_kind, anchor_ref, attachment, edit_id,
             pushed_pdf_sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cluster_id,
                vault_name,
                obsidian_path,
                disposition,
                deferred_reason,
                page_redir,
                rect,
                anchor_kind,
                anchor_ref,
                attachment,
                edit_id,
                pushed_pdf_sha256,
                created_at,
            ),
        )

    def get_ink_clusters(self, vault_name: str, obsidian_path: str) -> list[dict[str, object]]:
        """Get all ink cluster records for a document.

        Returns:
            List of dicts with cluster record fields.
        """
        cursor = self.conn.execute(
            "SELECT * FROM ink_clusters WHERE vault_name = ? AND obsidian_path = ?",
            (vault_name, obsidian_path),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_baked_clusters(self, vault_name: str, obsidian_path: str) -> list[dict[str, object]]:
        """Get all baked ink clusters for a document (for overlay re-push).

        Returns:
            List of dicts for clusters with disposition='baked'.
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM ink_clusters
            WHERE vault_name = ? AND obsidian_path = ? AND disposition = 'baked'
            """,
            (vault_name, obsidian_path),
        )
        return [dict(row) for row in cursor.fetchall()]

    def has_deferred_clusters(self, vault_name: str, obsidian_path: str) -> bool:
        """Check if any ink clusters are deferred (blocking clearance).

        Returns:
            True if any deferred clusters exist.
        """
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) FROM ink_clusters
            WHERE vault_name = ? AND obsidian_path = ? AND disposition = 'deferred'
            """,
            (vault_name, obsidian_path),
        )
        return cursor.fetchone()[0] > 0

    def update_ink_clearance_hash(
        self, vault_name: str, obsidian_path: str, clearance_hash: str
    ) -> None:
        """Set the ink_clearance_hash on sync_state (only after all clusters are terminal).

        Args:
            vault_name: Vault name.
            obsidian_path: Relative path.
            clearance_hash: Document index hash to store.
        """
        self.conn.execute(
            """
            UPDATE sync_state SET ink_clearance_hash = ?
            WHERE vault_name = ? AND obsidian_path = ?
            """,
            (clearance_hash, vault_name, obsidian_path),
        )

    def close(self) -> None:
        """Close database connection.

        Should be called when application exits to ensure data is flushed.
        """
        if self.conn:
            self.conn.close()
            logger.debug("State database connection closed")
