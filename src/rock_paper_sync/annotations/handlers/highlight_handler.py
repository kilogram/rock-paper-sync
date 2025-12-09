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
from typing import TYPE_CHECKING

from rmscene import scene_items as si

from rock_paper_sync.annotations import Annotation, AnnotationType, read_annotations
from rock_paper_sync.annotations.common.anchors import AnnotationAnchor
from rock_paper_sync.annotations.common.spatial import find_nearest_paragraph_by_y
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm
from rock_paper_sync.annotations.core.data_types import ExtractedAnnotation, RenderConfig
from rock_paper_sync.annotations.core_types import HeuristicTextAnchor
from rock_paper_sync.coordinate_transformer import is_text_relative

if TYPE_CHECKING:
    from typing import Any

    from rock_paper_sync.annotations.core_types import Rectangle
    from rock_paper_sync.layout import DeviceGeometry, LayoutContext, WordWrapLayoutEngine

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
        highlights = [anno for anno in all_annotations if anno.type == AnnotationType.HIGHLIGHT]
        logger.debug(f"Detected {len(highlights)} highlights in {rm_file_path.name}")
        return highlights

    def map(
        self,
        annotations: list[Annotation],
        markdown_blocks: list,
        rm_file_path: Path,
        layout_context: "LayoutContext | None" = None,
    ) -> dict[int, list[Annotation]]:
        """Map highlights to markdown paragraphs using text matching.

        Uses text content matching (most reliable strategy for highlights).
        Falls back to Y-position matching if text not available.

        Args:
            annotations: List of highlight annotations
            markdown_blocks: List of markdown content blocks
            rm_file_path: Path to .rm file (for coordinate extraction if needed)
            layout_context: Optional layout context (not used by highlights since
                text matching is more reliable, but accepted for protocol compliance)

        Returns:
            Dict mapping paragraph_index -> list of matching annotations
        """
        # Note: Highlights use text matching which is more reliable than position-based
        # matching. The layout_context parameter is accepted for protocol compliance
        # but not used. If needed in the future, we could use it to improve
        # position-based fallback matching.
        mappings: dict[int, list[Annotation]] = {}

        # Extract text origin for position-based fallback
        _, text_origin_y = extract_text_blocks_from_rm(rm_file_path)

        for annotation in annotations:
            paragraph_index = None

            # Strategy 1: Text matching (preferred) - uses scoring to find BEST match
            if annotation.highlight and annotation.highlight.text:
                highlight_text = annotation.highlight.text.strip().lower()
                paragraph_index = self._find_best_text_match(
                    highlight_text, markdown_blocks, annotation.bounding_box
                )

            # Strategy 2: Y-position fallback
            # NOTE: Requires page_y_start attribute on ContentBlock
            # See issue #5 for pagination metadata persistence implementation
            if paragraph_index is None and annotation.bounding_box:
                bbox = annotation.bounding_box
                anno_y = bbox.y

                # Simple text-relative transform (no 60px offset for highlights)
                anno_y_absolute = text_origin_y + anno_y

                # Use common spatial matching utility
                paragraph_index = find_nearest_paragraph_by_y(anno_y_absolute, markdown_blocks)

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

    def _find_best_text_match(
        self,
        highlight_text: str,
        markdown_blocks: list,
        bounding_box: "Rectangle | None" = None,
    ) -> int | None:
        """Find the best matching paragraph for highlighted text using scoring.

        Instead of returning the first match (which often matches the wrong
        paragraph when text appears in multiple places), this scores all
        candidates and returns the best match.

        Scoring factors:
        1. Coverage ratio: What percentage of the paragraph is highlighted?
           (Higher = text is more central to paragraph = better match)
        2. Uniqueness: If text only appears once, that's clearly the target
        3. Position hint: Use Y-position from bounding box as tie-breaker

        Args:
            highlight_text: Normalized (lower case, stripped) highlight text
            markdown_blocks: List of ContentBlock objects with .text attribute
            bounding_box: Optional bounding box for Y-position tie-breaking

        Returns:
            Best matching paragraph index, or None if no match found
        """
        candidates: list[tuple[int, float]] = []  # (index, score)

        for idx, md_block in enumerate(markdown_blocks):
            block_text = md_block.text.lower()
            if highlight_text in block_text:
                # Calculate coverage ratio (how much of paragraph is highlighted)
                coverage = len(highlight_text) / len(block_text) if block_text else 0
                candidates.append((idx, coverage))

        if not candidates:
            return None

        # If only one match, it's clearly the target
        if len(candidates) == 1:
            idx = candidates[0][0]
            logger.debug(
                f"Matched highlight via text (unique): '{highlight_text[:30]}...' "
                f"→ paragraph {idx}"
            )
            return idx

        # Multiple matches - score them
        # Sort by coverage ratio (higher = better match)
        candidates.sort(key=lambda x: x[1], reverse=True)

        # Check if top candidate is significantly better than second
        if len(candidates) >= 2:
            top_score = candidates[0][1]
            second_score = candidates[1][1]

            # If top is significantly better (e.g., >50% more coverage), use it
            if top_score > second_score * 1.5:
                idx = candidates[0][0]
                logger.debug(
                    f"Matched highlight via text (best coverage): '{highlight_text[:30]}...' "
                    f"→ paragraph {idx} (score={top_score:.2f} vs {second_score:.2f})"
                )
                return idx

        # Use Y-position as tie-breaker if available
        if bounding_box and hasattr(markdown_blocks[0], "page_y_start"):
            anno_y = bounding_box.y
            best_idx = None
            best_distance = float("inf")

            for idx, _ in candidates:
                block = markdown_blocks[idx]
                if block.page_y_start is not None:
                    distance = abs(block.page_y_start - anno_y)
                    if distance < best_distance:
                        best_distance = distance
                        best_idx = idx

            if best_idx is not None:
                logger.debug(
                    f"Matched highlight via text + Y-position: '{highlight_text[:30]}...' "
                    f"→ paragraph {best_idx} (distance={best_distance:.0f})"
                )
                return best_idx

        # Fallback: return highest coverage match
        idx = candidates[0][0]
        logger.debug(
            f"Matched highlight via text (highest coverage): '{highlight_text[:30]}...' "
            f"→ paragraph {idx} (score={candidates[0][1]:.2f})"
        )
        return idx

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
            context_before = paragraph_text[max(0, offset - 50) : offset]
            context_after = paragraph_text[
                offset + len(highlight_text) : offset + len(highlight_text) + 50
            ]
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
            color=highlight.color if hasattr(highlight, "color") else None,
        )

    def get_position(
        self,
        block: "Any",
        text_origin_y: float,
    ) -> tuple[float, float] | None:
        """Get absolute position for a highlight (Glyph) block.

        Highlights use simple text-relative coordinates:
        absolute_y = text_origin_y + native_y

        Args:
            block: Raw rmscene SceneGlyphItemBlock
            text_origin_y: Y coordinate of text origin from .rm file

        Returns:
            Tuple of (absolute_x, absolute_y), or None if position cannot be determined
        """
        try:
            if not hasattr(block, "item") or not hasattr(block.item, "value"):
                return None

            value = block.item.value

            # Verify this is a Glyph block
            if "Glyph" not in type(value).__name__:
                return None

            # Extract native coordinates from rectangles
            if not hasattr(value, "rectangles") or not value.rectangles:
                return None

            # Calculate center from all rectangles
            xs = [r.x + r.w / 2 for r in value.rectangles if hasattr(r, "x")]
            ys = [r.y + r.h / 2 for r in value.rectangles if hasattr(r, "y")]

            if not xs or not ys:
                return None

            native_x = sum(xs) / len(xs)
            native_y = sum(ys) / len(ys)

            # Check if text-relative (most highlights are)
            is_text_rel = False
            if hasattr(block, "parent_id"):
                is_text_rel = is_text_relative(block.parent_id)

            # Transform to absolute coordinates
            # Highlights use simple offset (no NEGATIVE_Y_OFFSET needed)
            if is_text_rel:
                absolute_y = text_origin_y + native_y
            else:
                absolute_y = native_y

            # X coordinate doesn't need text_origin_x for routing decisions
            absolute_x = native_x

            logger.debug(
                f"Highlight position: native_y={native_y:.1f} → absolute_y={absolute_y:.1f}"
            )
            return (absolute_x, absolute_y)

        except Exception as e:
            logger.warning(f"Failed to get highlight position: {e}")
            return None

    def relocate(
        self,
        block: "Any",
        old_text: str,
        new_text: str,
        old_origin: tuple[float, float],
        new_origin: tuple[float, float],
        layout_engine: "WordWrapLayoutEngine",
        geometry: "DeviceGeometry",
        crdt_base_id: int | None = None,
    ) -> "Any":
        """Relocate highlight using content-based anchoring.

        Uses delta-based approach to preserve pixel-perfect rectangle positions:
        1. Find where highlighted text was in old document (anchor)
        2. Resolve where that text is in new document (new_offset)
        3. Calculate position delta using SAME layout model for both
        4. Apply delta to original pixel-perfect rectangles
        5. Update CRDT anchor in extra_value_data for firmware 3.6+

        Args:
            block: SceneGlyphItemBlock containing highlight rectangles
            old_text: Page text before modification
            new_text: Page text after modification
            old_origin: (x, y) origin of old text block
            new_origin: (x, y) origin of new text block
            layout_engine: WordWrapLayoutEngine for position calculations
            geometry: DeviceGeometry for layout parameters
            crdt_base_id: Base ID from RootTextBlock for CRDT offset calculation

        Returns:
            Modified block with adjusted rectangles and CRDT anchor
        """
        # Lazy import to avoid circular dependency
        from rock_paper_sync.generator import update_glyph_extra_value_data

        # Extract highlighted text
        if not hasattr(block.item, "value"):
            logger.warning("Glyph block has no value, keeping original position")
            return block

        glyph_value = block.item.value
        if not hasattr(glyph_value, "text") or not glyph_value.text:
            logger.warning("Glyph has no text content, keeping original position")
            return block

        highlight_text = glyph_value.text

        # Need rectangles to adjust
        if not hasattr(glyph_value, "rectangles") or not glyph_value.rectangles:
            logger.warning("Glyph has no rectangles, keeping original position")
            return block

        # Get old position (average of rectangles) for anchor finding
        old_x = sum(r.x for r in glyph_value.rectangles) / len(glyph_value.rectangles)
        old_y = sum(r.y for r in glyph_value.rectangles) / len(glyph_value.rectangles)

        # Create text anchor strategy for this relocation
        text_anchor_strategy = HeuristicTextAnchor(context_window=50, fuzzy_threshold=0.8)

        # Find anchor in old document
        anchor = text_anchor_strategy.find_anchor(highlight_text, old_text, (old_x, old_y))

        # Debug: print anchor details for troubleshooting
        print(
            f"[DEBUG-HL] Highlight '{highlight_text[:20]}': old_pos=({old_x:.1f}, {old_y:.1f}), anchor_offset={anchor.char_offset}"
        )

        logger.debug(
            f"Highlight '{highlight_text[:30]}...': old_pos=({old_x:.1f}, {old_y:.1f}), "
            f"old_offset={anchor.char_offset}, confidence={anchor.confidence:.2f}"
        )

        if anchor.confidence < 0.5:
            logger.warning(
                f"Low confidence anchor ({anchor.confidence:.2f}) for '{highlight_text[:30]}...', "
                f"keeping original position"
            )
            return block

        # Resolve anchor in new document
        new_offset = text_anchor_strategy.resolve_anchor(anchor, new_text)

        if new_offset is None:
            logger.warning(
                f"Could not find '{highlight_text[:30]}...' in new document, keeping original position"
            )
            return block

        old_offset = anchor.char_offset
        if old_offset is None:
            logger.warning("Anchor has no char_offset, keeping original position")
            return block

        logger.debug(
            f"  Resolved: old_offset={old_offset} -> new_offset={new_offset} "
            f"(delta={new_offset - old_offset})"
        )

        # Debug: print resolution details
        print(
            f"[DEBUG-HL] Resolved: old_offset={old_offset} -> new_offset={new_offset} (delta={new_offset - old_offset})"
        )
        # Show text context around offsets
        old_context = old_text[max(0, old_offset - 20) : old_offset + len(highlight_text) + 20]
        new_context = new_text[max(0, new_offset - 20) : new_offset + len(highlight_text) + 20]
        print(f"[DEBUG-HL] Old context: '...{old_context}...'")
        print(f"[DEBUG-HL] New context: '...{new_context}...'")

        # DELTA-BASED APPROACH: Calculate positions using SAME layout model
        try:
            # Enable debug for 'bottom' highlight
            is_bottom = "bottom" in highlight_text.lower()
            old_x_model, old_y_model = layout_engine.offset_to_position(
                old_offset, old_text, old_origin, geometry.text_width, debug=is_bottom
            )
            new_x_model, new_y_model = layout_engine.offset_to_position(
                new_offset, new_text, new_origin, geometry.text_width, debug=is_bottom
            )
        except Exception as e:
            logger.warning(f"Failed to calculate positions for highlight: {e}")
            return block

        # Calculate delta between model positions (errors cancel out)
        x_delta = new_x_model - old_x_model
        y_delta = new_y_model - old_y_model

        logger.debug(
            f"  Model positions: old=({old_x_model:.1f}, {old_y_model:.1f}), "
            f"new=({new_x_model:.1f}, {new_y_model:.1f})"
        )
        logger.debug(f"  Delta: ({x_delta:.1f}, {y_delta:.1f})")

        # Debug: print model positions
        print(
            f"[DEBUG-HL] Model: old=({old_x_model:.1f}, {old_y_model:.1f}), new=({new_x_model:.1f}, {new_y_model:.1f})"
        )
        print(f"[DEBUG-HL] Delta: ({x_delta:.1f}, {y_delta:.1f})")

        # REFLOW DETECTION: Check if content significantly changed
        # When text content changes significantly (large char_offset delta), the original
        # "device offset from model" is no longer valid because it was specific to the
        # old page layout. In this case, place highlight at model position directly
        # instead of preserving the old device offset.
        #
        # NOTE: Reflow detection is currently disabled because the delta-based approach
        # gives better results. The model position calculation is inaccurate for cross-paragraph
        # text because joining text_blocks with '\n' creates artificial line breaks that
        # don't match the device's actual paragraph spacing. The delta-based approach
        # cancels out these errors by using the same (flawed) model for both positions.
        # TODO: Fix the model to properly handle paragraph spacing, then re-enable reflow.
        offset_change = abs(new_offset - old_offset)
        SIGNIFICANT_OFFSET_CHANGE = 100000  # Effectively disabled - was 100

        if offset_change > SIGNIFICANT_OFFSET_CHANGE:
            # Significant content reflow - use model position directly
            # Calculate old device offset (how much device drew away from model)
            old_device_x_offset = old_x - old_x_model
            old_device_y_offset = old_y - old_y_model

            logger.debug(
                f"  Significant reflow detected (offset_change={offset_change}), "
                f"old device offset=({old_device_x_offset:.1f}, {old_device_y_offset:.1f})"
            )

            # Don't preserve the old device offset - place at new model position
            # Keep only minor X adjustments (device-specific rendering quirks)
            x_delta = new_x_model - old_x + min(old_device_x_offset, 10.0)
            y_delta = new_y_model - old_y  # Place at model Y position

        # REFLOW DETECTION: Check if highlight now spans different number of lines
        old_rect_count = len(glyph_value.rectangles)
        new_end_offset = new_offset + len(highlight_text)
        new_rects = layout_engine.calculate_highlight_rectangles(
            new_offset, new_end_offset, new_text, new_origin, geometry.text_width
        )
        new_rect_count = len(new_rects)

        if new_rect_count != old_rect_count:
            # REFLOW CASE: Highlight now spans different number of lines
            logger.debug(f"  Reflow detected: {old_rect_count} rect(s) → {new_rect_count} rect(s)")

            original_rect = glyph_value.rectangles[0] if glyph_value.rectangles else None
            original_height = original_rect.h if original_rect else geometry.line_height

            first_new_x, first_new_y, first_new_w, _ = new_rects[0]

            if original_rect:
                first_rect_x = original_rect.x + x_delta
                first_rect_y = original_rect.y + y_delta
            else:
                first_rect_x = first_new_x
                first_rect_y = first_new_y

            glyph_value.rectangles.clear()
            glyph_value.rectangles.append(
                si.Rectangle(first_rect_x, first_rect_y, first_new_w, original_height)
            )

            line_start_x = new_origin[0]
            tolerance = 10.0

            for i, (x, y, w, _) in enumerate(new_rects[1:], start=1):
                is_line_start = abs(x - line_start_x) < tolerance

                if is_line_start:
                    rect_x = geometry.text_pos_x
                else:
                    rel_x = x - first_new_x
                    rect_x = first_rect_x + rel_x

                rect_y = first_rect_y + i * original_height
                glyph_value.rectangles.append(si.Rectangle(rect_x, rect_y, w, original_height))

            logger.debug(
                f"  Created {len(glyph_value.rectangles)} rectangle(s) using delta+geometry"
            )
        else:
            # DELTA CASE: Same line count, apply delta to preserve pixel-perfect positions
            for rect in glyph_value.rectangles:
                rect.x += x_delta
                rect.y += y_delta

        # Update start field for older firmware (< v3.6)
        glyph_value.start = new_offset

        # Update text field to match what's at the new position
        new_highlighted_text = new_text[new_offset : new_offset + len(highlight_text)]
        if new_highlighted_text:
            glyph_value.text = new_highlighted_text
            glyph_value.length = len(new_highlighted_text)

        # UPDATE CRDT ANCHOR in extra_value_data for firmware 3.6+
        if (
            crdt_base_id is not None
            and hasattr(block, "extra_value_data")
            and block.extra_value_data
        ):
            block.extra_value_data = update_glyph_extra_value_data(
                block.extra_value_data, new_offset, len(highlight_text), crdt_base_id
            )

        logger.debug(
            f"Adjusted highlight '{highlight_text[:30]}...' by delta=({x_delta:.1f}, {y_delta:.1f}), "
            f"offset={old_offset}->{new_offset}, confidence={anchor.confidence:.2f}"
        )

        return block

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
            pattern = r"<mark>(.+?)</mark>"
        elif config.highlight_style == "bold":
            # Pattern: **highlighted text**
            pattern = r"\*\*(.+?)\*\*"
        elif config.highlight_style == "italic":
            # Pattern: *highlighted text*
            pattern = r"\*(.+?)\*"
        else:
            logger.warning(f"Unknown highlight style: {config.highlight_style}")
            return []

        # Find all matches with their positions
        for match in re.finditer(pattern, paragraph):
            extracted.append(
                ExtractedAnnotation(
                    text=match.group(1),
                    annotation_type="highlight",
                    start_offset=match.start(),
                    end_offset=match.end(),
                )
            )

        logger.debug(
            f"Extracted {len(extracted)} highlights from paragraph "
            f"(style={config.highlight_style})"
        )

        return extracted
