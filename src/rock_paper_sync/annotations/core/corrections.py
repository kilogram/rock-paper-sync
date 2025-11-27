"""Generic annotation correction system.

This module implements type-agnostic correction storage and retrieval for
all annotation types. Corrections apply to highlights, strokes, sketches,
diagrams, and any future annotation types.

Correction types:
- text_edit: Text content corrections (e.g., OCR fixes)
- replacement: Full annotation replacement (e.g., diagram → SVG)
- type_change: Annotation type reclassification (e.g., stroke → sketch)
- format_change: Output format changes (e.g., Mermaid → Excalidraw)

Matching strategies (priority order):
1. Image hash: Visual fingerprint of annotation rendering
2. Content hash: Hash of annotation data (points, text, etc.)
3. Position key: Composite of document + paragraph + offset
4. Annotation ID: Direct ID match (fragile, lowest priority)

Design:
- Hybrid schema: Base table + JSON payloads for type-specific data
- Multi-strategy matching: Multiple keys for robust retrieval
- Versioning: Corrections can supersede previous corrections
- Type-agnostic: Works for any annotation type

Example:
    manager = CorrectionManager(state_manager)

    # Store OCR correction
    manager.store_correction(
        document_id="doc-123",
        annotation_id="anno-456",
        annotation_type="stroke",
        correction_kind="text_edit",
        payload={"original": "helo", "corrected": "hello"},
        content_hash="abc123",
    )

    # Retrieve by content hash
    correction = manager.get_correction(
        document_id="doc-123",
        content_hash="abc123",
    )
"""

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

CorrectionKind = Literal["text_edit", "replacement", "type_change", "format_change"]


@dataclass
class Correction:
    """Generic annotation correction.

    Attributes:
        correction_id: Unique correction identifier
        document_id: Document UUID
        annotation_id: Annotation identifier (may be unstable)
        annotation_type: Type of annotation (highlight, stroke, sketch, etc.)
        correction_kind: Type of correction (text_edit, replacement, etc.)
        payload: Type-specific correction data (JSON-serializable dict)
        content_hash: Hash of annotation content
        image_hash: Hash of annotation visual rendering
        position_key: Composite position key (doc+para+offset)
        version: Correction version number
        supersedes_id: Previous correction this replaces
        created_at: Timestamp of creation
    """

    correction_id: str
    document_id: str
    annotation_id: str
    annotation_type: str
    correction_kind: CorrectionKind
    payload: dict[str, Any]
    content_hash: str | None = None
    image_hash: str | None = None
    position_key: str | None = None
    version: int = 1
    supersedes_id: str | None = None
    created_at: str | None = None


class CorrectionManager:
    """Manages generic annotation corrections.

    Central manager for all annotation corrections. Handlers remain pure
    processors; corrections are stored and retrieved here.

    Schema:
        annotation_corrections: Base correction metadata
        correction_data: Type-specific JSON payloads

    Multi-strategy matching enables robust retrieval even when annotation
    IDs change or positions shift slightly.
    """

    def __init__(self, db_path: Path):
        """Initialize correction manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize correction database schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Base correction metadata
            conn.execute("""
                CREATE TABLE IF NOT EXISTS annotation_corrections (
                    correction_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    annotation_id TEXT NOT NULL,
                    annotation_type TEXT NOT NULL,
                    correction_kind TEXT NOT NULL,
                    content_hash TEXT,
                    image_hash TEXT,
                    position_key TEXT,
                    version INTEGER DEFAULT 1,
                    supersedes_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Type-specific correction data (JSON payloads)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS correction_data (
                    correction_id TEXT PRIMARY KEY,
                    correction_payload TEXT NOT NULL,
                    FOREIGN KEY (correction_id) REFERENCES annotation_corrections(correction_id)
                )
            """)

            # Indexes for fast matching
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_content_hash
                ON annotation_corrections(content_hash)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_image_hash
                ON annotation_corrections(image_hash)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_position_key
                ON annotation_corrections(position_key)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_annotation_id
                ON annotation_corrections(annotation_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_document_id
                ON annotation_corrections(document_id)
            """)

            conn.commit()

    def store_correction(
        self,
        document_id: str,
        annotation_id: str,
        annotation_type: str,
        correction_kind: CorrectionKind,
        payload: dict[str, Any],
        content_hash: str | None = None,
        image_hash: str | None = None,
        position_key: str | None = None,
        supersedes_id: str | None = None,
    ) -> str:
        """Store a new correction.

        Args:
            document_id: Document UUID
            annotation_id: Annotation identifier
            annotation_type: Type of annotation (highlight, stroke, etc.)
            correction_kind: Type of correction
            payload: Type-specific correction data
            content_hash: Optional content hash for matching
            image_hash: Optional image hash for matching
            position_key: Optional position key for matching
            supersedes_id: Optional previous correction this replaces

        Returns:
            correction_id: Unique ID of stored correction
        """
        correction_id = str(uuid4())

        # Determine version
        version = 1
        if supersedes_id:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT version FROM annotation_corrections WHERE correction_id = ?",
                    (supersedes_id,),
                ).fetchone()
                if row:
                    version = row[0] + 1

        with sqlite3.connect(self.db_path) as conn:
            # Store base metadata
            conn.execute(
                """
                INSERT INTO annotation_corrections (
                    correction_id, document_id, annotation_id, annotation_type,
                    correction_kind, content_hash, image_hash, position_key,
                    version, supersedes_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    correction_id,
                    document_id,
                    annotation_id,
                    annotation_type,
                    correction_kind,
                    content_hash,
                    image_hash,
                    position_key,
                    version,
                    supersedes_id,
                ),
            )

            # Store type-specific payload
            conn.execute(
                """
                INSERT INTO correction_data (correction_id, correction_payload)
                VALUES (?, ?)
                """,
                (correction_id, json.dumps(payload)),
            )

            conn.commit()

        logger.debug(
            f"Stored {correction_kind} correction for {annotation_type} "
            f"annotation {annotation_id[:8]}... (version {version})"
        )

        return correction_id

    def get_correction(
        self,
        document_id: str,
        image_hash: str | None = None,
        content_hash: str | None = None,
        position_key: str | None = None,
        annotation_id: str | None = None,
    ) -> Correction | None:
        """Retrieve correction using multi-strategy matching.

        Tries matching strategies in priority order:
        1. Image hash (most stable)
        2. Content hash (very stable)
        3. Position key (moderately stable)
        4. Annotation ID (least stable)

        Args:
            document_id: Document UUID (required)
            image_hash: Optional image hash
            content_hash: Optional content hash
            position_key: Optional position key
            annotation_id: Optional annotation ID

        Returns:
            Correction if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Try image hash (highest priority)
            if image_hash:
                row = conn.execute(
                    """
                    SELECT c.*, d.correction_payload
                    FROM annotation_corrections c
                    JOIN correction_data d ON c.correction_id = d.correction_id
                    WHERE c.document_id = ? AND c.image_hash = ?
                    ORDER BY c.version DESC
                    LIMIT 1
                    """,
                    (document_id, image_hash),
                ).fetchone()
                if row:
                    return self._row_to_correction(row)

            # Try content hash
            if content_hash:
                row = conn.execute(
                    """
                    SELECT c.*, d.correction_payload
                    FROM annotation_corrections c
                    JOIN correction_data d ON c.correction_id = d.correction_id
                    WHERE c.document_id = ? AND c.content_hash = ?
                    ORDER BY c.version DESC
                    LIMIT 1
                    """,
                    (document_id, content_hash),
                ).fetchone()
                if row:
                    return self._row_to_correction(row)

            # Try position key
            if position_key:
                row = conn.execute(
                    """
                    SELECT c.*, d.correction_payload
                    FROM annotation_corrections c
                    JOIN correction_data d ON c.correction_id = d.correction_id
                    WHERE c.document_id = ? AND c.position_key = ?
                    ORDER BY c.version DESC
                    LIMIT 1
                    """,
                    (document_id, position_key),
                ).fetchone()
                if row:
                    return self._row_to_correction(row)

            # Try annotation ID (lowest priority)
            if annotation_id:
                row = conn.execute(
                    """
                    SELECT c.*, d.correction_payload
                    FROM annotation_corrections c
                    JOIN correction_data d ON c.correction_id = d.correction_id
                    WHERE c.document_id = ? AND c.annotation_id = ?
                    ORDER BY c.version DESC
                    LIMIT 1
                    """,
                    (document_id, annotation_id),
                ).fetchone()
                if row:
                    return self._row_to_correction(row)

            return None

    def get_correction_history(
        self, correction_id: str
    ) -> list[Correction]:
        """Get full version history for a correction.

        Follows supersedes_id chain to get all versions.

        Args:
            correction_id: Starting correction ID

        Returns:
            List of corrections in chronological order (oldest first)
        """
        corrections = []
        current_id = correction_id

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            while current_id:
                row = conn.execute(
                    """
                    SELECT c.*, d.correction_payload
                    FROM annotation_corrections c
                    JOIN correction_data d ON c.correction_id = d.correction_id
                    WHERE c.correction_id = ?
                    """,
                    (current_id,),
                ).fetchone()

                if not row:
                    break

                correction = self._row_to_correction(row)
                corrections.insert(0, correction)  # Insert at beginning
                current_id = correction.supersedes_id

        return corrections

    def delete_correction(self, correction_id: str) -> bool:
        """Delete a correction.

        Args:
            correction_id: Correction to delete

        Returns:
            True if deleted, False if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            # Delete payload
            conn.execute(
                "DELETE FROM correction_data WHERE correction_id = ?",
                (correction_id,),
            )

            # Delete metadata
            cursor = conn.execute(
                "DELETE FROM annotation_corrections WHERE correction_id = ?",
                (correction_id,),
            )

            conn.commit()
            return cursor.rowcount > 0

    def _row_to_correction(self, row: sqlite3.Row) -> Correction:
        """Convert database row to Correction object."""
        return Correction(
            correction_id=row["correction_id"],
            document_id=row["document_id"],
            annotation_id=row["annotation_id"],
            annotation_type=row["annotation_type"],
            correction_kind=row["correction_kind"],
            payload=json.loads(row["correction_payload"]),
            content_hash=row["content_hash"],
            image_hash=row["image_hash"],
            position_key=row["position_key"],
            version=row["version"],
            supersedes_id=row["supersedes_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def compute_content_hash(content: str | bytes) -> str:
        """Compute SHA256 hash of content.

        Args:
            content: String or bytes to hash

        Returns:
            Hex digest of SHA256 hash
        """
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def compute_position_key(
        document_id: str, paragraph_index: int, offset: int = 0
    ) -> str:
        """Compute position key for annotation.

        Args:
            document_id: Document UUID
            paragraph_index: Paragraph index in markdown
            offset: Optional offset within paragraph

        Returns:
            Position key string
        """
        return f"{document_id}:para{paragraph_index}:offset{offset}"
