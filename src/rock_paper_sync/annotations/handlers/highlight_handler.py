"""Handler for highlight annotations (Glyph blocks).

Highlights are text selections with bounding rectangles. They use simple
text-relative coordinate transformation and are matched to paragraphs
via text content matching (most reliable).

Characteristics:
- Created by selecting text on device
- Include extracted text content
- Simple coordinate transform: absolute_y = text_origin_y + native_y

Relocation:
    Highlights own their relocation logic. When content changes, the handler:
    1. Extracts highlight info from the rmscene block
    2. Uses transform module utilities for coordinate math
    3. Applies results back to the rmscene block

    The transform module provides decoupled utilities (Position, PositionDelta,
    Rectangle, etc.) that are reusable across annotation types.

Example:
    handler = HighlightHandler()
    annotations = handler.detect(rm_file_path)
    mappings = handler.map(annotations, markdown_blocks, rm_file_path)
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from rmscene import scene_items as si

from rock_paper_sync.annotations import Annotation, AnnotationType, read_annotations
from rock_paper_sync.annotations.common.spatial import find_nearest_paragraph_by_y
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm
from rock_paper_sync.annotations.core.data_types import ExtractedAnnotation, RenderConfig
from rock_paper_sync.annotations.document_model import AnchorContext
from rock_paper_sync.transform import (
    PositionDelta,
    Rectangle,
    TextSpan,
    calculate_relocation_delta,
)

if TYPE_CHECKING:
    from typing import Any

    from rock_paper_sync.layout import DeviceGeometry, LayoutContext, WordWrapLayoutEngine

logger = logging.getLogger(__name__)


# =============================================================================
# Pure functions for highlight relocation
# These functions are extracted from the monolithic relocate() method to improve
# testability and separation of concerns.
# =============================================================================


def extract_glyph_highlight_info(
    block: "Any",
) -> tuple[str, list["si.Rectangle"], tuple[float, float]] | None:
    """Extract highlight information from a SceneGlyphItemBlock.

    Args:
        block: SceneGlyphItemBlock containing highlight data

    Returns:
        Tuple of (highlight_text, rectangles, average_position) or None if extraction fails
    """
    if not hasattr(block.item, "value"):
        logger.warning("Glyph block has no value, cannot extract highlight info")
        return None

    glyph_value = block.item.value
    if not hasattr(glyph_value, "text") or not glyph_value.text:
        logger.warning("Glyph has no text content")
        return None

    if not hasattr(glyph_value, "rectangles") or not glyph_value.rectangles:
        logger.warning("Glyph has no rectangles")
        return None

    highlight_text = glyph_value.text
    rectangles = glyph_value.rectangles

    # Calculate average position from rectangles
    avg_x = sum(r.x for r in rectangles) / len(rectangles)
    avg_y = sum(r.y for r in rectangles) / len(rectangles)

    return (highlight_text, rectangles, (avg_x, avg_y))


def find_and_resolve_anchor(
    highlight_text: str,
    old_text: str,
    new_text: str,
    old_position: tuple[float, float],
    context_window: int = 50,  # noqa: ARG001
    fuzzy_threshold: float = 0.8,
    min_confidence: float = 0.5,
) -> tuple[int, int, float] | None:
    """Find anchor in old text and resolve to new text position.

    Args:
        highlight_text: The highlighted text to find
        old_text: Document text before modification
        new_text: Document text after modification
        old_position: (x, y) position of highlight in old document
        context_window: Characters of context for matching
        fuzzy_threshold: Minimum fuzzy match ratio
        min_confidence: Minimum confidence to accept anchor

    Returns:
        Tuple of (old_offset, new_offset, confidence) or None if resolution fails
    """
    # Find highlight text in old document
    old_offset = old_text.find(highlight_text)
    if old_offset == -1:
        logger.warning(f"Could not find '{highlight_text[:30]}...' in old document")
        return None

    logger.debug(
        f"Highlight '{highlight_text[:30]}...': old_pos=({old_position[0]:.1f}, {old_position[1]:.1f}), "
        f"old_offset={old_offset}"
    )

    # Create anchor context from the highlight
    anchor_context = AnchorContext.from_text_span(
        full_text=old_text,
        start=old_offset,
        end=old_offset + len(highlight_text),
        y_position=old_position[1],
    )

    # Resolve anchor in new document
    resolved = anchor_context.resolve(
        old_text,
        new_text,
        fuzzy_threshold=fuzzy_threshold,
    )

    if resolved is None:
        logger.warning(f"Could not resolve '{highlight_text[:30]}...' in new document")
        return None

    if resolved.confidence < min_confidence:
        logger.warning(
            f"Low confidence resolution ({resolved.confidence:.2f}) for '{highlight_text[:30]}...', "
            f"cannot relocate"
        )
        return None

    logger.debug(
        f"  Resolved: old_offset={old_offset} -> new_offset={resolved.start_offset} "
        f"(delta={resolved.start_offset - old_offset}), confidence={resolved.confidence:.2f}"
    )

    return (old_offset, resolved.start_offset, resolved.confidence)


def calculate_position_delta(
    old_offset: int,
    new_offset: int,
    old_text: str,
    new_text: str,
    old_origin: tuple[float, float],
    new_origin: tuple[float, float],
    layout_engine: "WordWrapLayoutEngine",
    text_width: float,
) -> PositionDelta | None:
    """Calculate position delta using layout engine.

    Delegates to transform.calculate_relocation_delta() for the math,
    providing a handler-friendly interface.

    Args:
        old_offset: Character offset in old text
        new_offset: Character offset in new text
        old_text: Document text before modification
        new_text: Document text after modification
        old_origin: (x, y) origin of old text block
        new_origin: (x, y) origin of new text block
        layout_engine: WordWrapLayoutEngine for position calculations
        text_width: Text width for layout calculations

    Returns:
        PositionDelta or None if calculation fails
    """
    try:
        # Use transform module for the coordinate math
        delta = calculate_relocation_delta(
            old_span=TextSpan(old_offset, old_offset + 1),  # Single char span for position
            new_offset=new_offset,
            layout_engine=layout_engine,
            text_width=text_width,
            old_text=old_text,
            new_text=new_text,
            old_origin=old_origin,
            new_origin=new_origin,
        )
        logger.debug(f"  Delta: ({delta.dx:.1f}, {delta.dy:.1f})")
        return delta
    except Exception as e:
        logger.warning(f"Failed to calculate positions: {e}")
        return None


def apply_delta_to_rmscene_rectangles(
    rectangles: list["si.Rectangle"],
    delta: PositionDelta,
) -> None:
    """Apply position delta to rmscene rectangles in-place.

    This is the rmscene-specific adapter that applies a PositionDelta
    (from the transform module) to rmscene Rectangle objects.

    Args:
        rectangles: List of rmscene Rectangle objects to modify
        delta: PositionDelta to apply
    """
    for rect in rectangles:
        rect.x += delta.dx
        rect.y += delta.dy


def rebuild_rmscene_rectangles_for_reflow(
    original_rectangles: list["si.Rectangle"],
    new_rects_from_layout: list[tuple[float, float, float, float]],
    delta: PositionDelta,
    geometry: "DeviceGeometry",
    new_origin: tuple[float, float],
) -> list["si.Rectangle"]:
    """Rebuild rmscene rectangles when highlight reflows to different line count.

    This is the rmscene-specific adapter for rectangle rebuilding. Uses the
    transform module's Rectangle type internally for math, then creates
    rmscene Rectangle objects for the output.

    When text reflows to a different number of lines, we can't simply apply
    a delta. Instead, we use the layout engine's calculated positions for
    line structure while preserving original rectangle dimensions.

    Args:
        original_rectangles: Original rmscene highlight rectangles
        new_rects_from_layout: Layout engine's calculated rectangles (x, y, w, h)
        delta: PositionDelta from position calculation
        geometry: DeviceGeometry for layout parameters
        new_origin: (x, y) origin of new text block

    Returns:
        New list of rmscene Rectangle objects for the reflowed highlight
    """
    from rock_paper_sync.transform import rebuild_for_reflow

    if not new_rects_from_layout:
        return []

    # Convert first original rectangle to transform.Rectangle for math
    original_rect = original_rectangles[0] if original_rectangles else None
    if original_rect:
        first_rect = Rectangle(
            x=original_rect.x,
            y=original_rect.y,
            width=original_rect.w,
            height=original_rect.h,
        )
    else:
        # Fallback if no original rectangles
        x, y, w, h = new_rects_from_layout[0]
        first_rect = Rectangle(x=x, y=y, width=w, height=geometry.line_height)

    # Use transform module for the math
    new_rects = rebuild_for_reflow(
        original_first_rect=first_rect,
        layout_rects=new_rects_from_layout,
        delta=delta,
        text_origin_x=new_origin[0],
    )

    # Convert back to rmscene rectangles
    result = [si.Rectangle(r.x, r.y, r.width, r.height) for r in new_rects]

    logger.debug(f"  Created {len(result)} rectangle(s) for reflowed highlight")
    return result


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

    def create_anchor(
        self,
        annotation: Annotation,
        paragraph_text: str,
        paragraph_index: int,
        page_num: int = 0,  # noqa: ARG002
    ) -> AnchorContext:
        """Create anchor from highlight annotation for matching and correction detection.

        Args:
            annotation: Highlight annotation from detect()
            paragraph_text: Full text of the matched paragraph
            paragraph_index: Index of paragraph in markdown
            page_num: Page number (default: 0)

        Returns:
            AnchorContext with content-based anchor using multi-signal approach
        """
        if not annotation.highlight:
            raise ValueError("Annotation is not a highlight")

        highlight = annotation.highlight
        highlight_text = highlight.text.strip() if highlight.text else ""

        # Calculate Y position from rectangles for spatial hint
        if highlight.rectangles:
            # Use first rectangle for primary position
            first_rect = highlight.rectangles[0]
            center_y = first_rect.y + first_rect.h / 2
        else:
            # Fallback if no rectangles
            center_y = None

        # Find highlight text in paragraph to get offsets
        if highlight_text and highlight_text in paragraph_text:
            offset = paragraph_text.find(highlight_text)
            return AnchorContext.from_text_span(
                full_text=paragraph_text,
                start=offset,
                end=offset + len(highlight_text),
                paragraph_index=paragraph_index,
                y_position=center_y,
            )
        else:
            # Fallback: anchor to entire paragraph if text not found
            # This can happen if the highlight text doesn't match extracted markdown
            return AnchorContext.from_text_span(
                full_text=paragraph_text,
                start=0,
                end=len(paragraph_text),
                paragraph_index=paragraph_index,
                y_position=center_y,
            )

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

        # Step 1: Extract highlight information from block
        highlight_info = extract_glyph_highlight_info(block)
        if highlight_info is None:
            logger.warning("Could not extract highlight info, keeping original position")
            return block

        highlight_text, rectangles, old_position = highlight_info
        glyph_value = block.item.value

        # Step 2: Find anchor in old text and resolve to new position
        anchor_result = find_and_resolve_anchor(
            highlight_text=highlight_text,
            old_text=old_text,
            new_text=new_text,
            old_position=old_position,
        )
        if anchor_result is None:
            logger.warning("Could not resolve anchor, keeping original position")
            return block

        old_offset, new_offset, confidence = anchor_result

        # Step 3: Calculate position delta using layout engine
        delta = calculate_position_delta(
            old_offset=old_offset,
            new_offset=new_offset,
            old_text=old_text,
            new_text=new_text,
            old_origin=old_origin,
            new_origin=new_origin,
            layout_engine=layout_engine,
            text_width=geometry.text_width,
        )
        if delta is None:
            logger.warning("Could not calculate position delta, keeping original position")
            return block

        # Step 4: Check for reflow (highlight spanning different number of lines)
        old_rect_count = len(rectangles)
        new_end_offset = new_offset + len(highlight_text)
        new_rects = layout_engine.calculate_highlight_rectangles(
            new_offset, new_end_offset, new_text, new_origin, geometry.text_width
        )
        new_rect_count = len(new_rects)

        if new_rect_count != old_rect_count:
            # Reflow case: rebuild rectangles for new line structure
            logger.debug(f"  Reflow detected: {old_rect_count} rect(s) → {new_rect_count} rect(s)")
            new_rectangles = rebuild_rmscene_rectangles_for_reflow(
                original_rectangles=list(rectangles),
                new_rects_from_layout=new_rects,
                delta=delta,
                geometry=geometry,
                new_origin=new_origin,
            )
            glyph_value.rectangles.clear()
            glyph_value.rectangles.extend(new_rectangles)
        else:
            # Delta case: apply delta to preserve pixel-perfect positions
            apply_delta_to_rmscene_rectangles(rectangles, delta)

        # Step 5: Update glyph metadata
        glyph_value.start = new_offset

        new_highlighted_text = new_text[new_offset : new_offset + len(highlight_text)]
        if new_highlighted_text:
            glyph_value.text = new_highlighted_text
            glyph_value.length = len(new_highlighted_text)

        # Step 6: Update CRDT anchor for firmware 3.6+
        if (
            crdt_base_id is not None
            and hasattr(block, "extra_value_data")
            and block.extra_value_data
        ):
            block.extra_value_data = update_glyph_extra_value_data(
                block.extra_value_data, new_offset, len(highlight_text), crdt_base_id
            )

        logger.debug(
            f"Adjusted highlight '{highlight_text[:30]}...' by delta=({delta.dx:.1f}, {delta.dy:.1f}), "
            f"offset={old_offset}->{new_offset}, confidence={confidence:.2f}"
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
