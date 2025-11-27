"""Handler for stroke annotations (Line blocks).

Strokes are hand-drawn annotations with point coordinates. They use the
dual-anchor coordinate transformation system and require OCR for text
extraction.

Characteristics:
- Hand-drawn pen/pencil annotations
- Stored as sequences of (x, y) points
- Use dual-anchor Y transformation:
  - Positive Y: relative to text origin
  - Negative Y: relative to baseline + line height (60px offset)
- Per-parent X anchors from TreeNodeBlocks
- Require OCR to extract text content
- Rendered as OCR blocks in markdown

For coordinate transformation details, see docs/STROKE_ANCHORING.md.

Example:
    handler = StrokeHandler(ocr_processor)
    annotations = handler.detect(rm_file_path)
    mappings = handler.map(annotations, markdown_blocks, rm_file_path)
    output = handler.render(0, mappings[0], "Original paragraph")
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from rock_paper_sync.annotations import Annotation, AnnotationType, read_annotations
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm
from rock_paper_sync.coordinate_transformer import (
    extract_text_origin,
    build_parent_anchor_map,
    CoordinateTransformer,
    is_text_relative,
)

if TYPE_CHECKING:
    from rock_paper_sync.ocr.integration import OCRProcessor

logger = logging.getLogger(__name__)


class StrokeHandler:
    """Handler for stroke annotations with OCR integration.

    Implements AnnotationHandler Protocol for strokes using coordinate
    transformation and OCR processing. Strokes require special handling
    because they use the dual-anchor coordinate system.
    """

    def __init__(self, ocr_processor: "OCRProcessor | None" = None):
        """Initialize stroke handler.

        Args:
            ocr_processor: Optional OCR processor for text extraction
        """
        self.ocr_processor = ocr_processor

    @property
    def annotation_type(self) -> str:
        """Return unique identifier for strokes."""
        return "stroke"

    def detect(self, rm_file_path: Path) -> list[Annotation]:
        """Extract stroke annotations from .rm file.

        Args:
            rm_file_path: Path to reMarkable v6 .rm file

        Returns:
            List of Annotation objects with type=STROKE
        """
        all_annotations = read_annotations(rm_file_path)
        strokes = [
            anno
            for anno in all_annotations
            if anno.type == AnnotationType.STROKE
        ]
        logger.debug(f"Detected {len(strokes)} strokes in {rm_file_path.name}")
        return strokes

    def map(
        self,
        annotations: list[Annotation],
        markdown_blocks: list,
        rm_file_path: Path,
    ) -> dict[int, list[Annotation]]:
        """Map strokes to markdown paragraphs using coordinate transformation.

        Uses dual-anchor Y transformation for accurate positioning:
        - Positive Y strokes: text_origin_y + native_y
        - Negative Y strokes: text_origin_y + 60px + native_y

        Args:
            annotations: List of stroke annotations
            markdown_blocks: List of markdown content blocks
            rm_file_path: Path to .rm file for coordinate extraction

        Returns:
            Dict mapping paragraph_index -> list of matching annotations
        """
        mappings: dict[int, list[Annotation]] = {}

        # Extract coordinate transformation components
        text_origin = extract_text_origin(rm_file_path)
        parent_anchor_map = build_parent_anchor_map(rm_file_path)
        transformer = CoordinateTransformer(
            text_origin_x=text_origin.x,
            text_origin_y=text_origin.y,
        )

        # Extract text blocks for position references
        rm_text_blocks, _ = extract_text_blocks_from_rm(rm_file_path)

        for annotation in annotations:
            if not annotation.stroke or not annotation.stroke.bounding_box:
                logger.warning(
                    f"Stroke annotation {annotation.annotation_id[:8]}... missing bounding box"
                )
                continue

            bbox = annotation.stroke.bounding_box
            native_y = bbox.y

            # Transform to absolute coordinates using proper dual-anchor system
            anchor_x = text_origin.x
            if annotation.parent_id and annotation.parent_id in parent_anchor_map:
                anchor_x = parent_anchor_map[annotation.parent_id].x

            # Calculate stroke center Y to determine coordinate space
            stroke_center_y = bbox.y + bbox.h / 2

            # Apply coordinate transformation
            if is_text_relative(annotation.parent_id):
                # Text-relative: use dual-anchor transform
                _, absolute_y = transformer.to_absolute(
                    native_x=bbox.x,
                    native_y=native_y,
                    parent_id=annotation.parent_id,
                    anchor_x=anchor_x,
                    stroke_center_y=stroke_center_y,
                )
            else:
                # Already absolute
                absolute_y = native_y

            # Find closest paragraph by Y position
            paragraph_index = None
            min_distance = float("inf")

            for idx, md_block in enumerate(markdown_blocks):
                block_y = md_block.page_y_start
                distance = abs(absolute_y - block_y)
                if distance < min_distance:
                    min_distance = distance
                    paragraph_index = idx

            if paragraph_index is not None:
                if paragraph_index not in mappings:
                    mappings[paragraph_index] = []
                mappings[paragraph_index].append(annotation)
                logger.debug(
                    f"Mapped stroke via Y-position: y={absolute_y:.1f} "
                    f"→ paragraph {paragraph_index} (distance={min_distance:.1f})"
                )
            else:
                logger.warning(
                    f"Could not map stroke annotation {annotation.annotation_id[:8]}..."
                )

        return mappings

    def render(
        self,
        paragraph_index: int,
        matches: list[Annotation],
        original_content: str,
    ) -> str:
        """Render stroke annotations as OCR blocks.

        If OCR processor is available, performs text extraction.
        Otherwise, renders as placeholder markers.

        Args:
            paragraph_index: Index of paragraph in markdown
            matches: List of stroke annotations for this paragraph
            original_content: Original paragraph text

        Returns:
            Markdown text with OCR blocks or placeholder markers
        """
        if not matches:
            return original_content

        # If no OCR processor, render as placeholder
        if not self.ocr_processor:
            num_strokes = len(matches)
            marker = f"<!-- {num_strokes} handwritten annotation(s) -->"
            return f"{marker}\n{original_content}"

        # With OCR processor, render would be handled by OCRProcessor.process_annotations
        # This is a placeholder - actual OCR rendering happens in the OCR pipeline
        # For now, just add a marker
        num_strokes = len(matches)
        marker = f"<!-- {num_strokes} stroke(s) pending OCR -->"
        return f"{marker}\n{original_content}"

    def init_state_schema(self, db_connection) -> None:
        """Initialize stroke-specific state schema.

        Strokes track OCR results, image hashes, and confidence scores.
        """
        db_connection.execute("""
            CREATE TABLE IF NOT EXISTS stroke_ocr_state (
                document_id TEXT NOT NULL,
                annotation_id TEXT NOT NULL,
                image_hash TEXT,
                ocr_text TEXT,
                confidence REAL,
                model_version TEXT,
                last_processed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (document_id, annotation_id)
            )
        """)
        db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_stroke_image_hash
            ON stroke_ocr_state(image_hash)
        """)
        db_connection.commit()

    def store_state(
        self,
        db_connection,
        document_id: str,
        annotation_id: str,
        state_data: dict,
    ) -> None:
        """Store stroke OCR state."""
        db_connection.execute(
            """
            INSERT OR REPLACE INTO stroke_ocr_state
            (document_id, annotation_id, image_hash, ocr_text, confidence, model_version)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                annotation_id,
                state_data.get("image_hash"),
                state_data.get("ocr_text"),
                state_data.get("confidence"),
                state_data.get("model_version"),
            ),
        )
        db_connection.commit()

    def load_state(
        self,
        db_connection,
        document_id: str,
        annotation_id: str,
    ) -> dict | None:
        """Load stroke OCR state."""
        cursor = db_connection.execute(
            """
            SELECT image_hash, ocr_text, confidence, model_version, last_processed
            FROM stroke_ocr_state
            WHERE document_id = ? AND annotation_id = ?
            """,
            (document_id, annotation_id),
        )
        row = cursor.fetchone()
        if row:
            return {
                "image_hash": row[0],
                "ocr_text": row[1],
                "confidence": row[2],
                "model_version": row[3],
                "last_processed": row[4],
            }
        return None
