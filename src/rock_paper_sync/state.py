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
from rock_paper_sync.parser import parse_markdown_file

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


class StateManager:
    """Manages sync state using SQLite database."""

    SCHEMA_VERSION = 6  # Schema v6: Remove file_hash_with_markers, add generation tracking

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
                -- File sync state (vault-aware, schema v6)
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

                -- Insert schema version if not exists
                INSERT OR IGNORE INTO schema_version (version) VALUES (5);
                """
            )

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
                (vault_name, obsidian_path, remarkable_uuid, content_hash, last_sync_time, page_count, status, last_root_generation, last_doc_index_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def find_changed_files(
        self,
        vault_name: str,
        vault_path: Path,
        include_patterns: list[str],
        exclude_patterns: list[str],
    ) -> list[Path]:
        """Find files that need syncing based on content hash.

        Compares current file hashes with stored hashes to identify changes.

        Args:
            vault_name: Name of the vault
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

                # Parse file to get semantic hash (markers stripped by parser)
                # This ensures consistent comparison with state.content_hash
                try:
                    md_doc = parse_markdown_file(file_path)
                    current_hash = md_doc.content_hash  # Semantic hash
                except Exception as e:
                    logger.warning(f"Failed to parse {file_path}: {e}")
                    continue

                # Get existing state
                state = self.get_file_state(vault_name, relative_path)

                # File is new or changed if no state or hash differs
                if state is None:
                    logger.debug(f"New file: {vault_name}:{relative_path}")
                    changed.append(file_path)
                elif state.content_hash != current_hash:
                    logger.debug(f"Changed file: {vault_name}:{relative_path}")
                    changed.append(file_path)

        logger.info(f"Found {len(changed)} changed files in vault '{vault_name}'")
        return changed

    def find_deleted_files(self, vault_name: str, vault_path: Path) -> list[tuple[str, str]]:
        """Find files that have been deleted from vault but still exist in state.

        Returns list of (relative_path, remarkable_uuid) tuples for deleted files.

        Args:
            vault_name: Name of the vault
            vault_path: Path to Obsidian vault root

        Returns:
            List of (relative_path, uuid) tuples for files that no longer exist
        """
        deleted: list[tuple[str, str]] = []

        cursor = self.conn.execute(
            "SELECT obsidian_path, remarkable_uuid FROM sync_state WHERE vault_name = ?",
            (vault_name,),
        )
        for row in cursor.fetchall():
            relative_path = row[0]
            uuid = row[1]
            absolute_path = vault_path / relative_path

            if not absolute_path.exists():
                logger.debug(f"Deleted file: {vault_name}:{relative_path} (UUID: {uuid})")
                deleted.append((relative_path, uuid))

        logger.info(f"Found {len(deleted)} deleted files in vault '{vault_name}'")
        return deleted

    def _is_excluded(self, file_path: Path, vault_path: Path, exclude_patterns: list[str]) -> bool:
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
            return {
                "content_hash": row[0],
                "has_annotations": bool(row[1]),
                "annotation_count": row[2],
                "last_checked": row[3],
            }
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
        return {
            row[0]: {
                "content_hash": row[1],
                "has_annotations": bool(row[2]),
                "annotation_count": row[3],
                "last_checked": row[4],
            }
            for row in cursor.fetchall()
        }

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
            return {
                "paragraph_index": row[0],
                "ocr_text": row[1],
                "ocr_text_hash": row[2],
                "original_text_hash": row[3],
                "image_hash": row[4],
                "confidence": row[5],
                "model_version": row[6],
                "processed_time": row[7],
                "annotation_uuid": annotation_uuid,
            }
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
        return {
            row[1]: {
                "annotation_uuid": row[0],
                "paragraph_index": row[1],
                "ocr_text": row[2],
                "ocr_text_hash": row[3],
                "original_text_hash": row[4],
                "image_hash": row[5],
                "confidence": row[6],
                "model_version": row[7],
                "processed_time": row[8],
            }
            for row in cursor.fetchall()
        }

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

    def close(self) -> None:
        """Close database connection.

        Should be called when application exits to ensure data is flushed.
        """
        if self.conn:
            self.conn.close()
            logger.debug("State database connection closed")
