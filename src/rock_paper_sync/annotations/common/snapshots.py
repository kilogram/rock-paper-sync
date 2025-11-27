"""Snapshot storage for correction detection and file restoration.

This module provides content-addressable filesystem storage for:
1. File snapshots - Full file versions for restoration/rollback
2. Annotation block snapshots - Paragraph-level snapshots for correction detection

Architecture:
    ContentStore (filesystem) + SnapshotStore (SQLite metadata)

    Content stored at: ~/.local/share/rock-paper-sync/snapshots/<hash[:2]>/<hash[2:4]>/<hash>
    Metadata stored in: state database (content_store, file_snapshots, annotation_blocks tables)

Design principles:
- Content-addressable storage (automatic deduplication via SHA-256)
- Filesystem storage (not SQLite blobs) for large files
- Dual-purpose: file restoration + correction detection
- Minimal storage overhead (~22 MB for 6 months typical usage)

Example:
    # Initialize stores
    content_store = ContentStore(base_dir)
    snapshot_store = SnapshotStore(db_connection, content_store)

    # Snapshot a file
    snapshot_store.snapshot_file(
        vault_name="my-vault",
        file_path="Notes/Document.md",
        content=b"markdown content..."
    )

    # Snapshot an annotation block
    snapshot_store.snapshot_block(
        vault_name="my-vault",
        file_path="Notes/Document.md",
        paragraph_index=5,
        block_content="<!-- Highlight: text --> paragraph",
        annotation_types=["highlight"]
    )

    # Restore a file
    content = snapshot_store.restore_file(
        vault_name="my-vault",
        file_path="Notes/Document.md",
        sync_time=1234567890
    )
"""

import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ContentStore:
    """Content-addressable filesystem storage.

    Stores content at: base_dir/<hash[:2]>/<hash[2:4]>/<hash>

    This matches Git's object store design for efficient filesystem usage
    and avoids directory size issues with many files.

    Attributes:
        base_dir: Base directory for content storage
    """

    def __init__(self, base_dir: Path):
        """Initialize content store.

        Args:
            base_dir: Base directory for storage (e.g., ~/.local/share/rock-paper-sync/snapshots)
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> str:
        """Store content and return SHA-256 hash.

        Args:
            content: Content to store

        Returns:
            SHA-256 hash (hex) of the content
        """
        # Calculate hash
        content_hash = hashlib.sha256(content).hexdigest()

        # Build storage path: base_dir/<first 2>/<next 2>/<full hash>
        hash_path = self._hash_path(content_hash)

        # Store if not already present (content-addressable = automatic deduplication)
        if not hash_path.exists():
            hash_path.parent.mkdir(parents=True, exist_ok=True)
            hash_path.write_bytes(content)
            logger.debug(f"Stored content: {content_hash} ({len(content)} bytes)")
        else:
            logger.debug(f"Content already exists: {content_hash}")

        return content_hash

    def get(self, content_hash: str) -> bytes:
        """Retrieve content by hash.

        Args:
            content_hash: SHA-256 hash of the content

        Returns:
            Content bytes

        Raises:
            FileNotFoundError: If content not found
        """
        hash_path = self._hash_path(content_hash)

        if not hash_path.exists():
            raise FileNotFoundError(f"Content not found: {content_hash}")

        content = hash_path.read_bytes()
        logger.debug(f"Retrieved content: {content_hash} ({len(content)} bytes)")
        return content

    def exists(self, content_hash: str) -> bool:
        """Check if content exists in store.

        Args:
            content_hash: SHA-256 hash of the content

        Returns:
            True if content exists, False otherwise
        """
        return self._hash_path(content_hash).exists()

    def delete(self, content_hash: str) -> bool:
        """Delete content from store.

        Args:
            content_hash: SHA-256 hash of the content

        Returns:
            True if deleted, False if not found
        """
        hash_path = self._hash_path(content_hash)

        if hash_path.exists():
            hash_path.unlink()
            logger.debug(f"Deleted content: {content_hash}")

            # Clean up empty parent directories
            for parent in [hash_path.parent, hash_path.parent.parent]:
                try:
                    if parent != self.base_dir and not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass

            return True

        return False

    def _hash_path(self, content_hash: str) -> Path:
        """Convert hash to filesystem path.

        Args:
            content_hash: SHA-256 hash (hex)

        Returns:
            Path: base_dir/<first 2>/<next 2>/<full hash>
        """
        return self.base_dir / content_hash[:2] / content_hash[2:4] / content_hash

    def get_size(self) -> tuple[int, int]:
        """Get total storage size.

        Returns:
            Tuple of (total_files, total_bytes)
        """
        total_files = 0
        total_bytes = 0

        for content_file in self.base_dir.rglob("*"):
            if content_file.is_file():
                total_files += 1
                total_bytes += content_file.stat().st_size

        return total_files, total_bytes


class SnapshotStore:
    """High-level API for snapshot management.

    Combines ContentStore (filesystem) with SQLite metadata for:
    - File snapshots (for restoration/rollback)
    - Annotation block snapshots (for correction detection)

    Schema:
        content_store: Content metadata (hash, size, timestamps)
        file_snapshots: File version history
        annotation_blocks: Paragraph-level annotation snapshots
    """

    def __init__(self, db_connection: sqlite3.Connection, content_store: ContentStore):
        """Initialize snapshot store.

        Args:
            db_connection: SQLite database connection
            content_store: Content storage backend
        """
        self.db = db_connection
        self.content_store = content_store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create snapshot tables if they don't exist."""
        self.db.executescript("""
            -- Content metadata (shared by files and blocks)
            CREATE TABLE IF NOT EXISTS content_store (
                content_hash TEXT PRIMARY KEY,
                content_size INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_accessed INTEGER
            ) STRICT;

            -- File-level snapshots (for restoration)
            CREATE TABLE IF NOT EXISTS file_snapshots (
                vault_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                sync_time INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                PRIMARY KEY (vault_name, file_path, sync_time),
                FOREIGN KEY (content_hash) REFERENCES content_store(content_hash)
            ) STRICT;

            -- Annotation block snapshots (for correction detection)
            CREATE TABLE IF NOT EXISTS annotation_blocks (
                vault_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                paragraph_index INTEGER NOT NULL,
                block_hash TEXT NOT NULL,
                annotation_types TEXT NOT NULL,
                sync_time INTEGER NOT NULL,
                PRIMARY KEY (vault_name, file_path, paragraph_index, sync_time),
                FOREIGN KEY (block_hash) REFERENCES content_store(content_hash)
            ) STRICT;

            -- Indexes for efficient queries
            CREATE INDEX IF NOT EXISTS idx_file_snapshots_vault_file
            ON file_snapshots(vault_name, file_path);

            CREATE INDEX IF NOT EXISTS idx_annotation_blocks_vault_file_para
            ON annotation_blocks(vault_name, file_path, paragraph_index);
        """)
        self.db.commit()

    def snapshot_file(
        self,
        vault_name: str,
        file_path: str,
        content: bytes,
        file_type: str = "markdown",
        sync_time: Optional[int] = None
    ) -> str:
        """Create a snapshot of a file.

        Args:
            vault_name: Vault name
            file_path: Relative path within vault
            content: File content
            file_type: File type (default: "markdown")
            sync_time: Snapshot timestamp (default: current time)

        Returns:
            Content hash
        """
        if sync_time is None:
            sync_time = int(time.time())

        # Store content
        content_hash = self.content_store.put(content)

        # Update content metadata
        self.db.execute(
            """
            INSERT INTO content_store (content_hash, content_size, first_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET last_accessed = ?
            """,
            (content_hash, len(content), sync_time, sync_time)
        )

        # Store snapshot metadata
        self.db.execute(
            """
            INSERT OR REPLACE INTO file_snapshots
            (vault_name, file_path, content_hash, sync_time, file_type, file_size)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (vault_name, file_path, content_hash, sync_time, file_type, len(content))
        )
        self.db.commit()

        logger.debug(
            f"Snapshotted file: {vault_name}/{file_path} "
            f"({len(content)} bytes, hash={content_hash[:8]}...)"
        )

        return content_hash

    def snapshot_block(
        self,
        vault_name: str,
        file_path: str,
        paragraph_index: int,
        block_content: str,
        annotation_types: list[str],
        sync_time: Optional[int] = None
    ) -> str:
        """Create a snapshot of an annotation block.

        Args:
            vault_name: Vault name
            file_path: Relative path within vault
            paragraph_index: Paragraph index in markdown
            block_content: Block content (paragraph with annotations)
            annotation_types: List of annotation types in block (e.g., ["highlight", "stroke"])
            sync_time: Snapshot timestamp (default: current time)

        Returns:
            Block hash
        """
        if sync_time is None:
            sync_time = int(time.time())

        # Store content
        block_bytes = block_content.encode('utf-8')
        block_hash = self.content_store.put(block_bytes)

        # Update content metadata
        self.db.execute(
            """
            INSERT INTO content_store (content_hash, content_size, first_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET last_accessed = ?
            """,
            (block_hash, len(block_bytes), sync_time, sync_time)
        )

        # Store block metadata
        types_json = ",".join(annotation_types)  # Simple comma-separated list
        self.db.execute(
            """
            INSERT OR REPLACE INTO annotation_blocks
            (vault_name, file_path, paragraph_index, block_hash, annotation_types, sync_time)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (vault_name, file_path, paragraph_index, block_hash, types_json, sync_time)
        )
        self.db.commit()

        logger.debug(
            f"Snapshotted block: {vault_name}/{file_path}[{paragraph_index}] "
            f"(types={types_json}, hash={block_hash[:8]}...)"
        )

        return block_hash

    def restore_file(
        self,
        vault_name: str,
        file_path: str,
        sync_time: Optional[int] = None
    ) -> Optional[bytes]:
        """Restore a file snapshot.

        Args:
            vault_name: Vault name
            file_path: Relative path within vault
            sync_time: Snapshot timestamp (default: latest)

        Returns:
            File content, or None if not found
        """
        if sync_time is None:
            # Get latest snapshot
            cursor = self.db.execute(
                """
                SELECT content_hash
                FROM file_snapshots
                WHERE vault_name = ? AND file_path = ?
                ORDER BY sync_time DESC
                LIMIT 1
                """,
                (vault_name, file_path)
            )
        else:
            # Get specific snapshot
            cursor = self.db.execute(
                """
                SELECT content_hash
                FROM file_snapshots
                WHERE vault_name = ? AND file_path = ? AND sync_time = ?
                """,
                (vault_name, file_path, sync_time)
            )

        row = cursor.fetchone()
        if not row:
            return None

        content_hash = row[0]

        try:
            content = self.content_store.get(content_hash)
            logger.debug(f"Restored file: {vault_name}/{file_path} (hash={content_hash[:8]}...)")
            return content
        except FileNotFoundError:
            logger.error(f"Content missing for snapshot: {content_hash}")
            return None

    def get_block_snapshot(
        self,
        vault_name: str,
        file_path: str,
        paragraph_index: int,
        sync_time: Optional[int] = None
    ) -> Optional[str]:
        """Get an annotation block snapshot.

        Args:
            vault_name: Vault name
            file_path: Relative path within vault
            paragraph_index: Paragraph index
            sync_time: Snapshot timestamp (default: latest)

        Returns:
            Block content, or None if not found
        """
        if sync_time is None:
            # Get latest snapshot
            cursor = self.db.execute(
                """
                SELECT block_hash
                FROM annotation_blocks
                WHERE vault_name = ? AND file_path = ? AND paragraph_index = ?
                ORDER BY sync_time DESC
                LIMIT 1
                """,
                (vault_name, file_path, paragraph_index)
            )
        else:
            # Get specific snapshot
            cursor = self.db.execute(
                """
                SELECT block_hash
                FROM annotation_blocks
                WHERE vault_name = ? AND file_path = ? AND paragraph_index = ? AND sync_time = ?
                """,
                (vault_name, file_path, paragraph_index, sync_time)
            )

        row = cursor.fetchone()
        if not row:
            return None

        block_hash = row[0]

        try:
            content = self.content_store.get(block_hash)
            return content.decode('utf-8')
        except FileNotFoundError:
            logger.error(f"Content missing for block snapshot: {block_hash}")
            return None

    def list_file_versions(
        self,
        vault_name: str,
        file_path: str
    ) -> list[tuple[int, str, int]]:
        """List all snapshots for a file.

        Args:
            vault_name: Vault name
            file_path: Relative path within vault

        Returns:
            List of (sync_time, content_hash, file_size) tuples, newest first
        """
        cursor = self.db.execute(
            """
            SELECT sync_time, content_hash, file_size
            FROM file_snapshots
            WHERE vault_name = ? AND file_path = ?
            ORDER BY sync_time DESC
            """,
            (vault_name, file_path)
        )

        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]

    def get_storage_stats(self) -> dict:
        """Get storage statistics.

        Returns:
            Dict with storage metrics
        """
        # Content store stats
        total_files, total_bytes = self.content_store.get_size()

        # Snapshot counts
        cursor = self.db.execute("SELECT COUNT(*) FROM file_snapshots")
        file_snapshot_count = cursor.fetchone()[0]

        cursor = self.db.execute("SELECT COUNT(*) FROM annotation_blocks")
        block_snapshot_count = cursor.fetchone()[0]

        cursor = self.db.execute("SELECT COUNT(*) FROM content_store")
        unique_content_count = cursor.fetchone()[0]

        return {
            "total_files": total_files,
            "total_bytes": total_bytes,
            "file_snapshots": file_snapshot_count,
            "block_snapshots": block_snapshot_count,
            "unique_content": unique_content_count,
        }
