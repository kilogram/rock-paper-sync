"""Protocol definitions for pluggable annotation handlers.

This module defines the AnnotationHandler Protocol, which enables type-safe,
extensible annotation processing without tight coupling. Each annotation type
(highlights, strokes, sketches, diagrams, etc.) implements this protocol.

Design principles:
- Use typing.Protocol for structural subtyping (not ABC)
- Handlers are pure processors (no state beyond config)
- Corrections are managed separately by CorrectionManager
- Coordinate transformation is a shared utility (not handler-specific)

Example usage:
    class StrokeHandler:
        @property
        def annotation_type(self) -> str:
            return "stroke"

        def detect(self, rm_file_path: Path) -> list[Annotation]:
            # Extract stroke annotations from .rm file
            ...

        def map(self, annotations, markdown_blocks, rm_file_path) -> dict:
            # Map strokes to paragraph indices
            ...

        def render(self, paragraph_index, matches, content) -> str:
            # Generate OCR markdown output
            ...
"""

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AnnotationHandler(Protocol):
    """Protocol for annotation type handlers.

    Each annotation type (highlights, strokes, sketches, etc.) implements
    this protocol to provide type-specific detection, mapping, rendering,
    and state management.

    Handler responsibilities:
    1. Detection: Extract annotations from .rm files
    2. Mapping: Associate annotations with markdown paragraphs
    3. Rendering: Generate markdown output with annotation markers
    4. State: Manage handler-specific stateful concerns

    Coordinate transformation and generic corrections are handled by
    shared utilities. Handlers encapsulate type-specific state operations.
    """

    @property
    def annotation_type(self) -> str:
        """Unique identifier for this annotation type.

        Returns:
            Type identifier (e.g., "highlight", "stroke", "sketch")
        """
        ...

    def detect(self, rm_file_path: Path) -> list[Any]:
        """Extract annotations of this type from .rm file.

        Args:
            rm_file_path: Path to reMarkable v6 .rm file

        Returns:
            List of annotation objects (type-specific structure)
        """
        ...

    def map(
        self,
        annotations: list[Any],
        markdown_blocks: list[Any],
        rm_file_path: Path,
    ) -> dict[int, list[Any]]:
        """Map annotations to markdown paragraph indices.

        Args:
            annotations: List of annotations from detect()
            markdown_blocks: List of markdown content blocks
            rm_file_path: Path to .rm file (for coordinate extraction)

        Returns:
            Dict mapping paragraph_index -> list of matching annotations
        """
        ...

    def render(
        self,
        paragraph_index: int,
        matches: list[Any],
        original_content: str,
    ) -> str:
        """Generate markdown output for annotations at a paragraph.

        Args:
            paragraph_index: Index of paragraph in markdown
            matches: List of annotations mapped to this paragraph
            original_content: Original paragraph text

        Returns:
            Markdown text with annotation markers/content
        """
        ...

    def init_state_schema(self, db_connection: Any) -> None:
        """Initialize handler-specific state schema in database.

        Called once at startup to ensure handler's state tables exist.
        Handlers can create their own tables for type-specific concerns.

        Example (StrokeHandler):
            CREATE TABLE stroke_ocr_cache (
                annotation_id TEXT PRIMARY KEY,
                image_hash TEXT,
                ocr_text TEXT,
                confidence REAL
            )

        Args:
            db_connection: SQLite database connection
        """
        ...

    def store_state(
        self,
        db_connection: Any,
        document_id: str,
        annotation_id: str,
        state_data: dict[str, Any],
    ) -> None:
        """Store handler-specific state for an annotation.

        Args:
            db_connection: SQLite database connection
            document_id: Document UUID
            annotation_id: Annotation identifier
            state_data: Type-specific state data to store
        """
        ...

    def load_state(
        self,
        db_connection: Any,
        document_id: str,
        annotation_id: str,
    ) -> dict[str, Any] | None:
        """Load handler-specific state for an annotation.

        Args:
            db_connection: SQLite database connection
            document_id: Document UUID
            annotation_id: Annotation identifier

        Returns:
            Type-specific state data, or None if not found
        """
        ...
