"""Annotation processor that orchestrates handlers.

This module provides the AnnotationProcessor class, which coordinates
detection, mapping, and rendering of annotations using pluggable handlers.

Replaces the old annotation_mapper.map_annotations_to_paragraphs() with
a composable, extensible architecture.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from rock_paper_sync.annotations.core.data_types import AnnotationInfo
from rock_paper_sync.annotations.core.protocol import AnnotationHandler
from rock_paper_sync.parser import ContentBlock

if TYPE_CHECKING:
    from rock_paper_sync.layout import LayoutContext

logger = logging.getLogger(__name__)


class AnnotationProcessor:
    """Orchestrates annotation detection, mapping, and rendering.

    This processor coordinates multiple annotation handlers (highlights,
    strokes, sketches, etc.) to provide a unified annotation processing
    pipeline.

    Example:
        # Initialize with handlers
        processor = AnnotationProcessor(db_path)
        processor.register_handler(HighlightHandler())
        processor.register_handler(StrokeHandler(ocr_processor))

        # Process annotations
        annotation_map = processor.map_annotations_to_paragraphs(
            rm_file_path,
            markdown_blocks
        )
    """

    def __init__(self, db_path: Path | None = None):
        """Initialize annotation processor.

        Args:
            db_path: Optional path to SQLite database for state management
        """
        self.handlers: dict[str, AnnotationHandler] = {}
        self.db_path = db_path

        # Initialize database connection if provided
        if db_path:
            import sqlite3

            self.db_connection = sqlite3.connect(db_path)
        else:
            self.db_connection = None

    def register_handler(self, handler: AnnotationHandler) -> None:
        """Register an annotation handler.

        Args:
            handler: Handler implementing AnnotationHandler Protocol
        """
        handler_type = handler.annotation_type
        self.handlers[handler_type] = handler

        # Initialize handler's state schema
        if self.db_connection:
            handler.init_state_schema(self.db_connection)

        logger.debug(f"Registered handler for annotation type: {handler_type}")

    def map_annotations_to_paragraphs(
        self,
        rm_file_path: Path | str | BinaryIO,
        markdown_blocks: list[ContentBlock],
        layout_context: LayoutContext | None = None,
    ) -> dict[int, AnnotationInfo]:
        """Map annotations from .rm file to markdown paragraph indices.

        Replacement for annotation_mapper.map_annotations_to_paragraphs()
        using the new handler architecture.

        Args:
            rm_file_path: Path to .rm file (or file-like object)
            markdown_blocks: List of content blocks from parsed markdown
            layout_context: Optional layout context for position calculations.
                When provided, handlers can use position_to_offset() for
                accurate content-based mapping.

        Returns:
            Dictionary mapping paragraph index to annotation summary
            Example: {0: AnnotationInfo(highlights=2), 3: AnnotationInfo(strokes=1)}
        """
        # Handle file-like objects vs paths
        if isinstance(rm_file_path, str | Path):
            rm_path = Path(rm_file_path)
            if not rm_path.exists():
                logger.warning(f".rm file not found: {rm_path}")
                return {}
        else:
            # File-like object - limited support
            logger.debug("Reading annotations from file-like object")
            rm_path = None

        if not rm_path:
            logger.warning("Cannot process file-like objects yet")
            return {}

        # Build paragraph annotation map
        paragraph_annotations: dict[int, AnnotationInfo] = {}

        # Process each handler
        for handler_type, handler in self.handlers.items():
            logger.debug(f"Processing {handler_type} annotations")

            # Detect annotations
            annotations = handler.detect(rm_path)
            if not annotations:
                logger.debug(f"No {handler_type} annotations found")
                continue

            logger.debug(f"Detected {len(annotations)} {handler_type} annotations")

            # Map to paragraphs (pass layout context if available)
            mappings = handler.map(annotations, markdown_blocks, rm_path, layout_context)

            # Update annotation counts
            for paragraph_index, matches in mappings.items():
                if paragraph_index not in paragraph_annotations:
                    paragraph_annotations[paragraph_index] = AnnotationInfo()

                # Increment appropriate counter based on handler type
                if handler_type == "highlight":
                    paragraph_annotations[paragraph_index].highlights += len(matches)
                elif handler_type == "stroke":
                    paragraph_annotations[paragraph_index].strokes += len(matches)
                # Future types: sketch, diagram, etc.

        logger.info(
            f"Mapped annotations to {len(paragraph_annotations)} paragraphs using {len(self.handlers)} handlers"
        )

        return paragraph_annotations

    def close(self) -> None:
        """Close database connection."""
        if self.db_connection:
            self.db_connection.close()
            self.db_connection = None
