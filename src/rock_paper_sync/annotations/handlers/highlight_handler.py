"""Handler for highlight annotations (Glyph blocks).

Highlights are text selections with bounding rectangles. They use simple
text-relative coordinate transformation and are matched to paragraphs
via text content matching (most reliable).

Characteristics:
- Created by selecting text on device
- Include extracted text content
- Simple coordinate transform: absolute_y = text_origin_y + native_y
- Rendered as HTML comments in markdown

Example:
    handler = HighlightHandler()
    annotations = handler.detect(rm_file_path)
    mappings = handler.map(annotations, markdown_blocks, rm_file_path)
    output = handler.render(0, mappings[0], "Original paragraph")
"""

import logging
import re
from pathlib import Path

from rock_paper_sync.annotations import Annotation, AnnotationType, read_annotations
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm
from rock_paper_sync.annotations.common.anchors import AnnotationAnchor
from rock_paper_sync.annotations.core.data_types import ExtractedAnnotation, RenderConfig

logger = logging.getLogger(__name__)


class HighlightHandler:
    """Handler for highlight annotations.

    Implements AnnotationHandler Protocol for highlights using text-based
    matching. Highlights are the most stable annotation type because they
    include the actual highlighted text content.
    """

    @property
    def annotation_type(self) -> str:
        """Return unique identifier for highlights."""
        return "highlight"

    def detect(self, rm_file_path: Path) -> list[Annotation]:
        """Extract highlight annotations from .rm file.

        Args:
            rm_file_path: Path to reMarkable v6 .rm file

        Returns:
            List of Annotation objects with type=HIGHLIGHT
        """
        all_annotations = read_annotations(rm_file_path)
        highlights = [
            anno
            for anno in all_annotations
            if anno.type == AnnotationType.HIGHLIGHT
        ]
        logger.debug(f"Detected {len(highlights)} highlights in {rm_file_path.name}")
        return highlights

    def map(
        self,
        annotations: list[Annotation],
        markdown_blocks: list,
        rm_file_path: Path,
    ) -> dict[int, list[Annotation]]:
        """Map highlights to markdown paragraphs using text matching.

        Uses text content matching (most reliable strategy for highlights).
        Falls back to Y-position matching if text not available.

        Args:
            annotations: List of highlight annotations
            markdown_blocks: List of markdown content blocks
            rm_file_path: Path to .rm file (for coordinate extraction if needed)

        Returns:
            Dict mapping paragraph_index -> list of matching annotations
        """
        mappings: dict[int, list[Annotation]] = {}

        # Extract text origin for position-based fallback
        _, text_origin_y = extract_text_blocks_from_rm(rm_file_path)

        for annotation in annotations:
            paragraph_index = None

            # Strategy 1: Text matching (preferred)
            if annotation.highlight and annotation.highlight.text:
                highlight_text = annotation.highlight.text.strip().lower()
                for idx, md_block in enumerate(markdown_blocks):
                    if highlight_text in md_block.text.lower():
                        paragraph_index = idx
                        logger.debug(
                            f"Matched highlight via text: '{highlight_text[:30]}...' "
                            f"→ paragraph {idx}"
                        )
                        break

            # Strategy 2: Y-position fallback
            if paragraph_index is None and annotation.bounding_box:
                bbox = annotation.bounding_box
                anno_y = bbox.y

                # Simple text-relative transform (no 60px offset for highlights)
                anno_y_absolute = text_origin_y + anno_y

                # Find closest paragraph by Y position
                min_distance = float("inf")
                for idx, md_block in enumerate(markdown_blocks):
                    block_y = md_block.page_y_start
                    distance = abs(anno_y_absolute - block_y)
                    if distance < min_distance:
                        min_distance = distance
                        paragraph_index = idx

                if paragraph_index is not None:
                    logger.debug(
                        f"Matched highlight via Y-position: y={anno_y_absolute:.1f} "
                        f"→ paragraph {paragraph_index} (distance={min_distance:.1f})"
                    )

            # Store mapping
            if paragraph_index is not None:
                if paragraph_index not in mappings:
                    mappings[paragraph_index] = []
                mappings[paragraph_index].append(annotation)
            else:
                logger.warning(
                    f"Could not map highlight annotation {annotation.annotation_id[:8]}..."
                )

        return mappings

    def render(
        self,
        paragraph_index: int,
        matches: list[Annotation],
        original_content: str,
    ) -> str:
        """Render highlight annotations as HTML comments.

        Args:
            paragraph_index: Index of paragraph in markdown
            matches: List of highlight annotations for this paragraph
            original_content: Original paragraph text

        Returns:
            Markdown text with HTML comment markers
        """
        if not matches:
            return original_content

        # Collect all highlight texts
        highlight_texts = []
        for annotation in matches:
            if annotation.highlight and annotation.highlight.text:
                highlight_texts.append(annotation.highlight.text.strip())

        if not highlight_texts:
            return original_content

        # Render as HTML comment
        highlights_str = " | ".join(highlight_texts)
        comment = f"<!-- Highlights: {highlights_str} -->"

        return f"{comment}\n{original_content}"

    def init_state_schema(self, db_connection) -> None:
        """Initialize highlight-specific state schema.

        Highlights track text hashes for change detection.
        """
        db_connection.execute("""
            CREATE TABLE IF NOT EXISTS highlight_state (
                document_id TEXT NOT NULL,
                annotation_id TEXT NOT NULL,
                text_hash TEXT,
                highlighted_text TEXT,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (document_id, annotation_id)
            )
        """)
        db_connection.commit()

    def store_state(
        self,
        db_connection,
        document_id: str,
        annotation_id: str,
        state_data: dict,
    ) -> None:
        """Store highlight state (text hash for change detection)."""
        db_connection.execute(
            """
            INSERT OR REPLACE INTO highlight_state
            (document_id, annotation_id, text_hash, highlighted_text)
            VALUES (?, ?, ?, ?)
            """,
            (
                document_id,
                annotation_id,
                state_data.get("text_hash"),
                state_data.get("highlighted_text"),
            ),
        )
        db_connection.commit()

    def load_state(
        self,
        db_connection,
        document_id: str,
        annotation_id: str,
    ) -> dict | None:
        """Load highlight state."""
        cursor = db_connection.execute(
            """
            SELECT text_hash, highlighted_text, last_seen
            FROM highlight_state
            WHERE document_id = ? AND annotation_id = ?
            """,
            (document_id, annotation_id),
        )
        row = cursor.fetchone()
        if row:
            return {
                "text_hash": row[0],
                "highlighted_text": row[1],
                "last_seen": row[2],
            }
        return None

    def create_anchor(
        self,
        annotation: Annotation,
        paragraph_text: str,
        paragraph_index: int,
        page_num: int = 0,
    ) -> AnnotationAnchor:
        """Create anchor from highlight annotation for matching and correction detection.

        Args:
            annotation: Highlight annotation from detect()
            paragraph_text: Full text of the matched paragraph
            paragraph_index: Index of paragraph in markdown
            page_num: Page number (default: 0)

        Returns:
            AnnotationAnchor with highlight location/content information
        """
        if not annotation.highlight:
            raise ValueError("Annotation is not a highlight")

        highlight = annotation.highlight
        highlight_text = highlight.text.strip() if highlight.text else ""

        # Calculate position from rectangles
        if highlight.rectangles:
            # Use first rectangle for primary position
            first_rect = highlight.rectangles[0]
            center_x = first_rect.x + first_rect.w / 2
            center_y = first_rect.y + first_rect.h / 2

            # Calculate overall bounding box
            min_x = min(r.x for r in highlight.rectangles)
            min_y = min(r.y for r in highlight.rectangles)
            max_x = max(r.x + r.w for r in highlight.rectangles)
            max_y = max(r.y + r.h for r in highlight.rectangles)
            bbox = (min_x, min_y, max_x - min_x, max_y - min_y)
        else:
            # Fallback if no rectangles
            center_x, center_y = 0.0, 0.0
            bbox = None

        # Extract context from paragraph
        if highlight_text and highlight_text in paragraph_text:
            offset = paragraph_text.find(highlight_text)
            context_before = paragraph_text[max(0, offset - 50):offset]
            context_after = paragraph_text[offset + len(highlight_text):offset + len(highlight_text) + 50]
        else:
            # Fallback: use paragraph boundaries
            context_before = paragraph_text[:50] if paragraph_text else ""
            context_after = paragraph_text[-50:] if len(paragraph_text) > 50 else ""

        return AnnotationAnchor.from_highlight(
            highlight_text=highlight_text,
            page_num=page_num,
            position=(center_x, center_y),
            bounding_box=bbox,
            paragraph_index=paragraph_index,
            context_before=context_before,
            context_after=context_after,
            color=highlight.color if hasattr(highlight, 'color') else None,
        )

    def extract_from_markdown(
        self,
        paragraph: str,
        config: RenderConfig,
    ) -> list[ExtractedAnnotation]:
        """Extract highlights from markdown based on rendering style.

        Supports three rendering styles:
        - mark: <mark>text</mark>
        - bold: **text**
        - italic: *text*

        Args:
            paragraph: Markdown paragraph text
            config: Rendering configuration

        Returns:
            List of extracted highlight annotations
        """
        extracted = []

        if config.highlight_style == "mark":
            # Pattern: <mark>highlighted text</mark>
            pattern = r'<mark>(.+?)</mark>'
        elif config.highlight_style == "bold":
            # Pattern: **highlighted text**
            pattern = r'\*\*(.+?)\*\*'
        elif config.highlight_style == "italic":
            # Pattern: *highlighted text*
            pattern = r'\*(.+?)\*'
        else:
            logger.warning(f"Unknown highlight style: {config.highlight_style}")
            return []

        # Find all matches with their positions
        for match in re.finditer(pattern, paragraph):
            extracted.append(ExtractedAnnotation(
                text=match.group(1),
                annotation_type="highlight",
                start_offset=match.start(),
                end_offset=match.end()
            ))

        logger.debug(
            f"Extracted {len(extracted)} highlights from paragraph "
            f"(style={config.highlight_style})"
        )

        return extracted
