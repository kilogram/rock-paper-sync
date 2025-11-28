"""reMarkable document generator with rmscene integration.

This module converts parsed markdown documents into reMarkable v6 format files.
It handles pagination, text positioning, and generates binary .rm files using
the rmscene library.
"""

import io
import json
import logging
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uuid import uuid4

import rmscene
from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtId, CrdtSequence, CrdtSequenceItem
from rmscene.scene_stream import (
    AuthorIdsBlock,
    MigrationInfoBlock,
    PageInfoBlock,
    RootTextBlock,
    SceneGroupItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)
from rmscene.tagged_block_common import LwwValue

from .annotations import (
    Annotation,
    HeuristicTextAnchor,
    TextBlock,
    WordWrapLayoutEngine,
    associate_annotations_with_content,
    calculate_position_mapping,
    read_annotations,
)
from .config import LayoutConfig
from .coordinate_transformer import (
    apply_y_offset_to_block,
    get_annotation_center_y,
)
from .parser import BlockType, ContentBlock, FormatStyle, MarkdownDocument, TextFormat

logger = logging.getLogger("rock_paper_sync.generator")


@dataclass
class TextItem:
    """A positioned text element on a page.

    Attributes:
        text: Plain text content
        x: X position in pixels (from left edge)
        y: Y position in pixels (from top edge)
        width: Text box width in pixels
        formatting: List of inline formatting ranges
    """

    text: str
    x: float
    y: float
    width: float
    formatting: list[TextFormat] = field(default_factory=list)


@dataclass
class RemarkablePage:
    """A single page in a reMarkable document.

    Attributes:
        uuid: Unique page identifier
        text_items: List of positioned text items on this page
        text_blocks: List of text blocks with position info (for annotation mapping)
        annotations: List of annotations to preserve on this page
        annotation_blocks: Raw rmscene blocks for preserved annotations
        original_rm_path: Path to original .rm file (for annotation preservation)
    """

    uuid: str
    text_items: list[TextItem] = field(default_factory=list)
    text_blocks: list[TextBlock] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    annotation_blocks: list = field(default_factory=list)
    original_rm_path: Optional[Path] = None


@dataclass
class RemarkableDocument:
    """A complete reMarkable document with pages.

    Attributes:
        uuid: Unique document identifier
        visible_name: Display name in reMarkable UI
        parent_uuid: Parent folder UUID (empty for root)
        pages: List of document pages
        modified_time: Last modification timestamp (milliseconds)
    """

    uuid: str
    visible_name: str
    parent_uuid: str
    pages: list[RemarkablePage]
    modified_time: int


class RemarkableGenerator:
    """Generates reMarkable v6 format documents from parsed markdown.

    This generator converts markdown content blocks into reMarkable pages with
    properly positioned text items. It uses the rmscene library to create
    binary .rm files compatible with reMarkable firmware v3.0+.

    Pagination Algorithm
    --------------------

    The pagination algorithm breaks markdown content into pages by estimating
    line counts and applying smart breaking rules:

    1. **Line Estimation**: Calculate lines per block based on:
       - Text length and available width (considering margins)
       - Block type (headers get extra spacing, code blocks count newlines)
       - List item indentation and bullets

    2. **Page Breaking Rules**:
       - Never exceed lines_per_page (default: 28 lines)
       - By default: Never split blocks mid-way (atomic block placement)
       - If allow_paragraph_splitting=True: Fill pages by splitting paragraphs
       - Headers near page bottom (< 10 lines remaining) start new page
       - Prevents orphan headers at bottom of pages

    3. **Text Positioning**:
       - Y position accumulates from margin_top
       - X position respects margin_left plus list indentation
       - Text width accounts for right margin
       - List items get 20px indent per nesting level

    4. **rmscene Integration**:
       - Custom scene tree construction with optimized text width (750px)
       - Ensures 1.0x display zoom on Paper Pro (vs 0.8x with default 936px)
       - Combines all text items with newlines (Phase 1 simplification)
       - Future: Multiple Text scene items for precise positioning

    Attributes:
        layout: Page layout configuration
        page_width: Page width in pixels (1404 for reMarkable Paper Pro)
        page_height: Page height in pixels (1872 for reMarkable Paper Pro)
        line_height: Approximate pixels per line (35px)
        char_width: Pixels per character (15px, measured from device)
    """

    # reMarkable Paper Pro dimensions
    PAGE_WIDTH = 1404
    PAGE_HEIGHT = 1872
    LINE_HEIGHT = 35  # Approximate pixels per line
    CHAR_WIDTH = 15  # Pixels per character (measured: 50-51 chars/line max)

    # Text area dimensions for 1.0x display (optimized for Paper Pro)
    TEXT_WIDTH = 750.0  # Width that displays at 1.0x zoom
    TEXT_POS_X = -375.0  # Centered: -TEXT_WIDTH/2
    TEXT_POS_Y = 94.0   # Top margin: ~2 lines worth (was 234.0 = 5 lines)

    # Calculated default lines per page based on actual device measurements
    # Visible lines on Paper Pro: 26 lines
    # Conservative default with safety margin: 28 lines
    # Total height: 94 (top) + 980 (content) + 94 (bottom) = 1168px
    DEFAULT_LINES_PER_PAGE = 28

    def __init__(self, layout_config: LayoutConfig) -> None:
        """Initialize generator with layout settings.

        Args:
            layout_config: Page layout configuration
        """
        self.layout = layout_config
        self.page_width = self.PAGE_WIDTH
        self.page_height = self.PAGE_HEIGHT
        self.line_height = self.LINE_HEIGHT
        self.char_width = self.CHAR_WIDTH

        # Initialize annotation adjustment strategies (Phase 1)
        self.text_anchor_strategy = HeuristicTextAnchor(
            context_window=50,
            fuzzy_threshold=0.8
        )
        self.layout_engine = WordWrapLayoutEngine(
            text_width=self.TEXT_WIDTH,
            avg_char_width=self.CHAR_WIDTH,
            line_height=self.LINE_HEIGHT
        )

        logger.info("RemarkableGenerator initialized with rmscene integration and Phase 1 annotation anchoring")

    def generate_document(
        self,
        md_doc: MarkdownDocument,
        parent_uuid: str = "",
        doc_uuid: str | None = None,
        existing_page_uuids: list[str] | None = None,
        existing_rm_files: list[Path | None] | None = None,
    ) -> RemarkableDocument:
        """Convert markdown document to reMarkable format.

        Args:
            md_doc: Parsed markdown document
            parent_uuid: UUID of parent folder (empty for root)
            doc_uuid: Existing document UUID to reuse (for updates), or None for new documents
            existing_page_uuids: Existing page UUIDs to reuse (avoids CRDT conflicts on updates)
            existing_rm_files: List of paths to existing .rm files for annotation preservation.
                              List should match existing_page_uuids in length and order.
                              None entries indicate no existing file for that page.

        Returns:
            RemarkableDocument ready to be written to disk

        Note:
            When existing_rm_files is provided, annotations (strokes and highlights) from
            those files will be extracted and preserved in the new document, repositioned
            to match the updated content based on text proximity.
        """
        # Reuse existing UUID for updates, or generate new one for new documents
        doc_uuid = doc_uuid or str(uuid_module.uuid4())
        timestamp = int(time.time() * 1000)
        existing_page_uuids = existing_page_uuids or []

        # Paginate content blocks
        page_blocks = self.paginate_content(md_doc.content)

        # Generate pages with positioned text items
        pages: list[RemarkablePage] = []
        for i, blocks in enumerate(page_blocks):
            # Reuse existing page UUID if available, otherwise generate new one
            if i < len(existing_page_uuids):
                page_uuid = existing_page_uuids[i]
                logger.debug(f"Reusing existing page UUID: {page_uuid}")
            else:
                page_uuid = str(uuid_module.uuid4())
                logger.debug(f"Generated new page UUID: {page_uuid}")

            text_items, text_blocks = self.blocks_to_text_items(blocks)
            pages.append(
                RemarkablePage(
                    uuid=page_uuid, text_items=text_items, text_blocks=text_blocks
                )
            )

        # Preserve annotations from existing .rm files if provided
        if existing_rm_files:
            self._preserve_annotations(pages, existing_rm_files)

        logger.debug(
            f"Generated document {doc_uuid} with {len(pages)} page(s) "
            f"from {len(md_doc.content)} content blocks"
        )

        return RemarkableDocument(
            uuid=doc_uuid,
            visible_name=md_doc.title,
            parent_uuid=parent_uuid,
            pages=pages,
            modified_time=timestamp,
        )

    def _preserve_annotations(
        self, pages: list[RemarkablePage], existing_rm_files: list[Path | None]
    ) -> None:
        """Preserve annotations from existing .rm files with position adjustment.

        This method:
        1. Reads annotation blocks from existing .rm files (as rmscene objects)
        2. Extracts old text blocks to understand original layout
        3. Matches old text blocks to new text blocks based on content
        4. Adjusts annotation Y-coordinates in place based on text repositioning
        5. Stores modified annotation blocks for writing

        This avoids conversion bugs by modifying rmscene blocks directly.

        Args:
            pages: List of newly generated pages with new text layout
            existing_rm_files: List of paths to existing .rm files (or None)
        """
        from .annotations import calculate_position_mapping

        # Extract FULL DOCUMENT text from all old .rm files for content anchoring
        old_full_text_parts = []
        for rm_file_path in existing_rm_files:
            if rm_file_path and Path(rm_file_path).exists():
                _, _, page_text = self._extract_text_blocks_from_rm(rm_file_path)
                old_full_text_parts.append(page_text)
        old_full_text = '\n\n'.join(old_full_text_parts)  # Join pages with double newline

        # Compute FULL DOCUMENT text from all new pages
        new_full_text_parts = []
        for page in pages:
            page_text = '\n'.join(block.content for block in page.text_blocks)
            new_full_text_parts.append(page_text)
        new_full_text = '\n\n'.join(new_full_text_parts)  # Join pages with double newline

        logger.debug(f"Full document text: old={len(old_full_text)} chars, new={len(new_full_text)} chars")

        for i, (page, rm_file_path) in enumerate(zip(pages, existing_rm_files)):
            if rm_file_path is None or not Path(rm_file_path).exists():
                logger.debug(f"Page {i}: No existing .rm file to preserve annotations from")
                continue

            try:
                # Read all blocks from existing file using rmscene
                with open(rm_file_path, 'rb') as f:
                    existing_blocks = list(rmscene.read_blocks(f))

                # Extract annotation blocks (Lines and Glyphs)
                annotation_blocks = [
                    block for block in existing_blocks
                    if 'Line' in type(block).__name__ or 'Glyph' in type(block).__name__
                ]

                if not annotation_blocks:
                    logger.debug(f"Page {i}: No annotation blocks found in {rm_file_path}")
                    continue

                # Extract old text blocks for spatial positioning (per-page)
                old_text_blocks, old_text_origin_y, _ = self._extract_text_blocks_from_rm(rm_file_path)
                new_text_blocks = page.text_blocks
                new_text_origin_y = self.TEXT_POS_Y  # New documents use TEXT_POS_Y constant

                # Use FULL DOCUMENT text for content anchoring (not per-page)
                old_text = old_full_text
                new_text = new_full_text

                if not old_text_blocks or not new_text_blocks:
                    # No text to match - keep annotations at original positions
                    logger.warning(
                        f"Page {i}: Cannot calculate position mapping (old={len(old_text_blocks)}, new={len(new_text_blocks)})"
                    )
                    page.annotation_blocks = annotation_blocks
                    continue

                logger.debug(
                    f"Page {i}: Text origins - old={old_text_origin_y:.1f}, new={new_text_origin_y:.1f}"
                )

                # Calculate mapping between old and new text positions
                position_map = calculate_position_mapping(old_text_blocks, new_text_blocks)

                # Adjust annotation blocks based on text repositioning
                # Phase 1: Uses content anchoring for Glyph blocks, spatial for Line blocks
                adjusted_blocks = []
                for block in annotation_blocks:
                    adjusted_block = self._adjust_annotation_block_position(
                        block, old_text, new_text, old_text_blocks, new_text_blocks,
                        position_map, old_text_origin_y, new_text_origin_y
                    )
                    adjusted_blocks.append(adjusted_block)

                # Store the adjusted rmscene blocks AND original file path
                page.annotation_blocks = adjusted_blocks
                page.original_rm_path = rm_file_path

                logger.info(
                    f"Page {i}: Preserved and adjusted {len(adjusted_blocks)} annotation blocks from {rm_file_path}"
                )

            except Exception as e:
                logger.warning(
                    f"Page {i}: Failed to preserve annotations from {rm_file_path}: {e}"
                )

    def _extract_text_blocks_from_rm(
        self, rm_file_path: Path
    ) -> tuple[list[TextBlock], float, str]:
        """Extract text blocks, positions, and full text from an existing .rm file.

        Parses the .rm file to extract the RootTextBlock and creates TextBlock
        objects with Y-coordinates for each line of text. Also returns the text
        origin Y coordinate for coordinate space transformations and the full
        text content for annotation content anchoring (Phase 1).

        Args:
            rm_file_path: Path to existing .rm file

        Returns:
            Tuple of (text_blocks, text_origin_y, full_text) where:
            - text_blocks: List of TextBlock objects with position information
            - text_origin_y: Y-coordinate of the text origin (RootTextBlock.pos_y)
            - full_text: Full text content as a single string
        """
        try:
            with open(rm_file_path, 'rb') as f:
                blocks = list(rmscene.read_blocks(f))

            text_blocks = []
            text_origin_y = self.TEXT_POS_Y  # Default to constant
            full_text = ""

            # Find RootTextBlock to get text content and position
            for block in blocks:
                if 'RootText' in type(block).__name__:
                    text_data = block.value
                    text_origin_y = text_data.pos_y  # Capture the actual text origin

                    # Extract actual text from CrdtSequence
                    # The text is in the 'value' field of each CrdtSequenceItem
                    text_parts = []
                    for item in text_data.items.sequence_items():
                        if hasattr(item, 'value') and isinstance(item.value, str):
                            text_parts.append(item.value)

                    # Full text for content anchoring (join without splitting first)
                    full_text = ''.join(text_parts)

                    # Split into lines for TextBlock creation
                    lines = full_text.split('\n')

                    # Create TextBlock for each line with estimated Y positions
                    y_pos = text_data.pos_y
                    for line in lines:
                        if line.strip():  # Skip empty lines
                            text_blocks.append(
                                TextBlock(
                                    content=line,
                                    y_start=y_pos,
                                    y_end=y_pos + self.line_height,
                                    block_type="paragraph"
                                )
                            )
                            y_pos += self.line_height

            return text_blocks, text_origin_y, full_text

        except Exception as e:
            logger.warning(f"Failed to extract text blocks from {rm_file_path}: {e}")
            return [], self.TEXT_POS_Y, ""

    def _adjust_annotation_block_position(
        self,
        block,
        old_text: str,
        new_text: str,
        old_text_blocks: list[TextBlock],
        new_text_blocks: list[TextBlock],
        position_map: dict[int, int],
        old_text_origin_y: float,
        new_text_origin_y: float
    ):
        """Adjust rmscene annotation block coordinates based on text repositioning.

        Phase 1: Uses content anchoring for Glyph blocks (highlights) to adjust both
        X and Y coordinates. Uses spatial approach for Line blocks (strokes) for Y only.

        All calculations are done in ABSOLUTE page coordinates to handle both
        text-relative and absolute annotation coordinate spaces correctly.

        Args:
            block: rmscene annotation block (SceneLineItemBlock or SceneGlyphItemBlock)
            old_text: Full text from the old document
            new_text: Full text from the new document
            old_text_blocks: Text blocks from the old document version (in absolute coords)
            new_text_blocks: Text blocks from the new document version (in absolute coords)
            position_map: Mapping from old text block indices to new indices
            old_text_origin_y: The RootTextBlock.pos_y from the old document
            new_text_origin_y: The RootTextBlock.pos_y for the new document

        Returns:
            Modified block with adjusted coordinates
        """
        # Check if this is a Glyph (highlight) - use content anchoring
        if 'Glyph' in type(block).__name__:
            return self._adjust_glyph_with_content_anchoring(
                block, old_text, new_text,
                (self.TEXT_POS_X, old_text_origin_y),
                (self.TEXT_POS_X, new_text_origin_y)
            )

        # For Lines (strokes), use spatial Y-only approach
        # Get the annotation's center Y position in ABSOLUTE coordinates
        center_y_absolute = get_annotation_center_y(block, old_text_origin_y)

        if center_y_absolute is None:
            logger.debug("Cannot determine annotation center Y, keeping original position")
            return block

        # Find the nearest old text block to this annotation (both in absolute coords)
        nearest_old_idx = None
        min_distance = float('inf')

        for idx, text_block in enumerate(old_text_blocks):
            # Calculate distance from annotation center to text block center
            block_center_y = (text_block.y_start + text_block.y_end) / 2
            distance = abs(center_y_absolute - block_center_y)

            if distance < min_distance:
                min_distance = distance
                nearest_old_idx = idx

        if nearest_old_idx is None:
            logger.debug("Cannot find nearest old text block, keeping original position")
            return block

        # Look up the corresponding new text block
        new_idx = position_map.get(nearest_old_idx)

        if new_idx is None or new_idx >= len(new_text_blocks):
            logger.debug(
                f"No mapping for old block {nearest_old_idx} or invalid new index, keeping original position"
            )
            return block

        # Calculate Y offset between old and new text block positions (absolute coords)
        old_block_y = (old_text_blocks[nearest_old_idx].y_start + old_text_blocks[nearest_old_idx].y_end) / 2
        new_block_y = (new_text_blocks[new_idx].y_start + new_text_blocks[new_idx].y_end) / 2
        y_offset = new_block_y - old_block_y

        if abs(y_offset) < 0.1:  # No significant movement
            logger.debug(
                f"Annotation at y={center_y_absolute:.1f} requires no adjustment (offset={y_offset:.1f})"
            )
            return block

        # Apply the Y offset to the annotation block
        apply_y_offset_to_block(block, y_offset)

        logger.debug(
            f"Adjusted annotation at y={center_y_absolute:.1f} by offset={y_offset:.1f} "
            f"(old block {nearest_old_idx} → new block {new_idx})"
        )

        return block


    def _adjust_glyph_with_content_anchoring(
        self,
        glyph_block,
        old_text: str,
        new_text: str,
        old_origin: tuple[float, float],
        new_origin: tuple[float, float]
    ):
        """Adjust Glyph (highlight) using content anchoring (Phase 1).

        This method anchors highlights to their text content, so they move both
        horizontally and vertically as text reflows.

        Args:
            glyph_block: SceneGlyphItemBlock
            old_text: Full text of old document
            new_text: Full text of new document
            old_origin: (x, y) origin of old text block
            new_origin: (x, y) origin of new text block

        Returns:
            Modified glyph_block with adjusted rectangles
        """
        # Extract highlighted text
        if not hasattr(glyph_block.item, 'value'):
            logger.warning("Glyph block has no value, keeping original position")
            return glyph_block

        glyph_value = glyph_block.item.value
        if not hasattr(glyph_value, 'text') or not glyph_value.text:
            logger.warning("Glyph has no text content, keeping original position")
            return glyph_block

        highlight_text = glyph_value.text

        # Get old position (average of rectangles)
        if hasattr(glyph_value, 'rectangles') and glyph_value.rectangles:
            old_x = sum(r.x for r in glyph_value.rectangles) / len(glyph_value.rectangles)
            old_y = sum(r.y for r in glyph_value.rectangles) / len(glyph_value.rectangles)
        else:
            logger.warning("Glyph has no rectangles, keeping original position")
            return glyph_block

        # Find anchor in old document
        anchor = self.text_anchor_strategy.find_anchor(
            highlight_text, old_text, (old_x, old_y)
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
            # Keep original position for low-confidence matches
            return glyph_block

        # Resolve anchor in new document
        new_offset = self.text_anchor_strategy.resolve_anchor(anchor, new_text)

        if new_offset is None:
            logger.warning(f"Could not find '{highlight_text[:30]}...' in new document, keeping original position")
            return glyph_block

        logger.debug(f"  Resolved to new_offset={new_offset} (delta={new_offset - (anchor.char_offset or 0)})")

        # Calculate new position using layout engine
        try:
            new_x, new_y = self.layout_engine.offset_to_position(
                new_offset, new_text, new_origin, self.TEXT_WIDTH
            )
            logger.debug(f"  Layout engine: new_pos=({new_x:.1f}, {new_y:.1f}), origin={new_origin}")
        except Exception as e:
            logger.warning(f"Failed to calculate new position for highlight: {e}")
            return glyph_block

        # Calculate offset from old position
        x_offset = new_x - old_x
        y_offset = new_y - old_y

        logger.debug(f"  Calculated offset: ({x_offset:.1f}, {y_offset:.1f})")

        # Adjust all rectangles
        for rect in glyph_value.rectangles:
            rect.x += x_offset
            rect.y += y_offset

        logger.debug(
            f"Adjusted highlight '{highlight_text[:30]}...' by offset=({x_offset:.1f}, {y_offset:.1f}), "
            f"confidence={anchor.confidence:.2f}"
        )

        return glyph_block

    def paginate_content(
        self, blocks: list[ContentBlock]
    ) -> list[list[ContentBlock]]:
        """Split content blocks into pages based on line count.

        This method estimates how many lines each block will take and breaks
        content into pages that fit within the configured lines_per_page limit.

        Args:
            blocks: List of content blocks to paginate

        Returns:
            List of pages, where each page is a list of content blocks

        Note:
            - Headers near the bottom of a page start a new page
            - Blocks are never split mid-way
            - Empty content results in one empty page
        """
        if not blocks:
            # At least one empty page
            return [[]]

        pages: list[list[ContentBlock]] = []
        current_page: list[ContentBlock] = []
        current_lines = 0

        for block in blocks:
            block_lines = self.estimate_block_lines(block)

            # Check if header should start new page (avoid orphan headers)
            if block.type == BlockType.HEADER and current_page:
                remaining_space = self.layout.lines_per_page - current_lines
                if remaining_space < 10:  # Less than 10 lines remaining
                    pages.append(current_page)
                    current_page = []
                    current_lines = 0

            # Check if block fits on current page
            if current_lines + block_lines > self.layout.lines_per_page:
                # Block doesn't fit - either split it or start new page
                if self.layout.allow_paragraph_splitting and block.type == BlockType.PARAGRAPH:
                    # Split paragraph across pages
                    lines_available = self.layout.lines_per_page - current_lines
                    chars_per_line = max(1, int(self.TEXT_WIDTH / self.CHAR_WIDTH))

                    # Estimate chars that fit on current page
                    chars_for_current = lines_available * chars_per_line

                    if chars_for_current > 0 and len(block.text) > chars_for_current:
                        # Split the text (try to split at word boundary)
                        split_point = chars_for_current
                        # Try to find a space near the split point
                        space_before = block.text.rfind(' ', 0, split_point)
                        if space_before > chars_for_current * 0.8:  # Within 20% of target
                            split_point = space_before

                        # Create two blocks from the split
                        current_text = block.text[:split_point].rstrip()
                        remaining_text = block.text[split_point:].lstrip()

                        if current_text:
                            current_block = ContentBlock(
                                type=block.type,
                                level=block.level,
                                text=current_text,
                                formatting=block.formatting,  # Note: formatting may not be accurate across split
                            )
                            current_page.append(current_block)

                        if current_page:
                            pages.append(current_page)

                        # Start new page with remaining text
                        if remaining_text:
                            next_block = ContentBlock(
                                type=block.type,
                                level=block.level,
                                text=remaining_text,
                                formatting=[],  # Formatting lost across split for now
                            )
                            current_page = [next_block]
                            current_lines = self.estimate_block_lines(next_block)
                        else:
                            current_page = []
                            current_lines = 0
                    else:
                        # Not enough room to split meaningfully, start new page
                        if current_page:
                            pages.append(current_page)
                        current_page = [block]
                        current_lines = block_lines
                else:
                    # Atomic block placement (default behavior)
                    if current_page:
                        pages.append(current_page)
                    current_page = [block]
                    current_lines = block_lines
            else:
                current_page.append(block)
                current_lines += block_lines

        # Don't forget the last page
        if current_page:
            pages.append(current_page)

        logger.info(
            f"Paginated {len(blocks)} blocks into {len(pages)} page(s), "
            f"target lines per page: {self.layout.lines_per_page}"
        )
        for i, page_blocks in enumerate(pages, 1):
            total_lines = sum(self.estimate_block_lines(block) for block in page_blocks)
            logger.info(f"  Page {i}: {len(page_blocks)} blocks, {total_lines} estimated lines")

        return pages if pages else [[]]

    def estimate_block_lines(self, block: ContentBlock) -> int:
        """Estimate how many lines a content block will occupy.

        Args:
            block: Content block to estimate

        Returns:
            Estimated number of lines
        """
        if block.type == BlockType.HORIZONTAL_RULE:
            return 2

        # Calculate based on text length and actual text width
        # Use TEXT_WIDTH instead of page margins for accurate pagination
        chars_per_line = max(1, int(self.TEXT_WIDTH / self.char_width))

        # Account for list item bullet
        text = block.text
        if block.type == BlockType.LIST_ITEM:
            text = f"• {text}"

        # Calculate wrapped lines (+1 accounts for partial last line)
        text_lines = max(1, len(text) // chars_per_line + 1)

        # No extra spacing for paragraphs (spacing handled by blank lines in markdown)
        if block.type == BlockType.HEADER:
            result = text_lines + 1  # Extra space after header for readability
        elif block.type == BlockType.PARAGRAPH:
            result = text_lines  # No extra spacing
        elif block.type == BlockType.CODE_BLOCK:
            # Code blocks: count actual newlines
            result = text.count("\n") + 2
        else:
            result = text_lines

        return result

    def blocks_to_text_items(
        self, blocks: list[ContentBlock]
    ) -> tuple[list[TextItem], list[TextBlock]]:
        """Convert content blocks to positioned text items.

        Each block is positioned on the page based on the running Y position
        and the configured margins.

        Note: Uses TEXT_POS_Y constant (94.0) for Y positioning to match
        the coordinate system used by RootTextBlock in rmscene. This ensures
        consistency between text generation and extraction for annotation
        preservation.

        Args:
            blocks: Content blocks for a single page

        Returns:
            Tuple of (text_items, text_blocks) where text_blocks include Y-coordinates
            for annotation mapping
        """
        items: list[TextItem] = []
        text_blocks: list[TextBlock] = []
        y_position = float(self.TEXT_POS_Y)  # Use rmscene constant, not margin_top

        for block in blocks:
            if block.type == BlockType.HORIZONTAL_RULE:
                # Skip horizontal rules (not rendered as text in Phase 1)
                y_position += self.line_height * 2
                continue

            x_position = float(self.layout.margin_left)
            width = float(
                self.page_width - self.layout.margin_left - self.layout.margin_right
            )

            # Prepare text with list bullet if needed
            text = block.text
            if block.type == BlockType.LIST_ITEM:
                # Add bullet and indentation for lists
                indent = 20 * block.level
                x_position += indent
                width -= indent
                text = f"• {text}"

            # Create text item
            y_start = y_position
            items.append(
                TextItem(
                    text=text,
                    x=x_position,
                    y=y_position,
                    width=width,
                    formatting=block.formatting,
                )
            )

            # Update Y position for next block
            lines = self.estimate_block_lines(block)
            y_position += lines * self.line_height
            y_end = y_position

            # Create text block for annotation mapping
            text_blocks.append(
                TextBlock(
                    content=text,
                    y_start=y_start,
                    y_end=y_end,
                    block_type=block.type.name.lower(),
                )
            )

        return items, text_blocks

    def generate_rm_file(self, page: RemarkablePage) -> bytes:
        """Generate binary .rm file content with custom text width.

        If the page has annotation_blocks AND original_rm_path, this does a
        round-trip modification preserving the original scene tree structure.
        Otherwise, creates a new document from scratch.

        Args:
            page: RemarkablePage with positioned text items

        Returns:
            Binary .rm file content

        Note:
            Uses custom scene tree construction to set text width to 750px,
            which displays at 1.0x zoom on the Paper Pro (vs 0.8x with the
            default 936px width from simple_text_document).
            Inline formatting (bold/italic) is preserved in the text but
            not visually rendered due to rmscene/reMarkable limitations.
        """
        # If we have annotations and original file, do round-trip modification
        if (hasattr(page, 'annotation_blocks') and page.annotation_blocks and
            hasattr(page, 'original_rm_path') and page.original_rm_path):
            return self._generate_rm_file_roundtrip(page)

        # Otherwise create from scratch (no annotations to preserve)
        return self._generate_rm_file_from_scratch(page)

    def _generate_rm_file_roundtrip(self, page: RemarkablePage) -> bytes:
        """Modify existing .rm file preserving scene tree structure.

        This preserves the original scene tree structure (TreeNodes, SceneGroups,
        SceneInfo, etc.) which is critical for annotations to display correctly.
        Only modifies the text content and annotation positions.

        Args:
            page: RemarkablePage with annotation_blocks and original_rm_path

        Returns:
            Binary .rm file content with preserved structure
        """
        # Read all blocks from original file
        with open(page.original_rm_path, 'rb') as f:
            blocks = list(rmscene.read_blocks(f))

        # Prepare new text content
        combined_text = "\n".join(item.text for item in page.text_items)
        if not combined_text.strip():
            combined_text = " "

        # Build index of original annotation blocks by block_id for matching
        original_annotation_ids = set()
        for block in blocks:
            block_type = type(block).__name__
            if block_type in ['SceneLineItemBlock', 'SceneGlyphItemBlock']:
                if hasattr(block, 'item') and hasattr(block.item, 'item_id'):
                    original_annotation_ids.add(block.item.item_id)

        # Build index of adjusted annotations by item_id
        adjusted_by_id = {}
        for adj_block in page.annotation_blocks:
            if hasattr(adj_block, 'item') and hasattr(adj_block.item, 'item_id'):
                adjusted_by_id[adj_block.item.item_id] = adj_block

        # Modify blocks in place
        modified_blocks = []
        annotation_count = 0

        for block in blocks:
            block_type = type(block).__name__

            # Replace text content in RootTextBlock
            if block_type == 'RootTextBlock':
                # Build styles dictionary with newline markers (format code 10)
                # See docs/RMSCENE_NEWLINE_WORKAROUND.md for details
                styles = {
                    CrdtId(0, 0): LwwValue(
                        timestamp=CrdtId(1, 15), value=si.ParagraphStyle.PLAIN
                    )
                }

                # Add format code 10 (newline marker) for each \n character
                for i, char in enumerate(combined_text):
                    if char == '\n':
                        styles[CrdtId(0, i)] = LwwValue(
                            timestamp=CrdtId(1, 15), value=10  # Format code 10 = newline
                        )

                # Create new RootTextBlock with updated text but same structure
                modified_block = RootTextBlock(
                    block_id=block.block_id,
                    value=si.Text(
                        items=CrdtSequence([
                            CrdtSequenceItem(
                                item_id=CrdtId(1, 16),
                                left_id=CrdtId(0, 0),
                                right_id=CrdtId(0, 0),
                                deleted_length=0,
                                value=combined_text,
                            )
                        ]),
                        styles=styles,  # Now includes newline markers
                        pos_x=block.value.pos_x,
                        pos_y=block.value.pos_y,
                        width=block.value.width,
                    ),
                )
                modified_blocks.append(modified_block)
                logger.debug(f"Replaced text content in RootTextBlock ({len(combined_text)} chars, {combined_text.count(chr(10))} newlines)")

            # Replace annotation blocks with adjusted versions
            elif block_type in ['SceneLineItemBlock', 'SceneGlyphItemBlock']:
                # Try to find adjusted version by item_id
                if hasattr(block, 'item') and hasattr(block.item, 'item_id'):
                    item_id = block.item.item_id
                    if item_id in adjusted_by_id:
                        modified_blocks.append(adjusted_by_id[item_id])
                        annotation_count += 1
                    else:
                        # No adjusted version, keep original
                        modified_blocks.append(block)
                        annotation_count += 1
                else:
                    # Can't match, keep original
                    modified_blocks.append(block)
                    annotation_count += 1

            # Update PageInfoBlock with new text stats
            elif block_type == 'PageInfoBlock':
                modified_block = PageInfoBlock(
                    loads_count=block.loads_count,
                    merges_count=block.merges_count,
                    text_chars_count=len(combined_text) + 1,
                    text_lines_count=combined_text.count("\n") + 1,
                )
                modified_blocks.append(modified_block)

            # Keep all other blocks (scene tree, groups, etc.) unchanged
            else:
                modified_blocks.append(block)

        # Serialize to binary
        buffer = io.BytesIO()
        rmscene.write_blocks(buffer, modified_blocks)
        rm_bytes = buffer.getvalue()

        logger.info(
            f"Generated .rm file via round-trip: {len(rm_bytes)} bytes, "
            f"{len(modified_blocks)} blocks ({annotation_count} annotations, preserved scene tree)"
        )

        return rm_bytes

    def _generate_rm_file_from_scratch(self, page: RemarkablePage) -> bytes:
        """Create new .rm file from scratch (original generate_rm_file logic).

        Used when there are no annotations to preserve.
        """
        # Combine all text items into a single text block
        combined_text = "\n".join(item.text for item in page.text_items)

        if not combined_text.strip():
            combined_text = " "  # At least one space for empty pages

        # Build styles dictionary with newline markers (format code 10)
        # See docs/RMSCENE_NEWLINE_WORKAROUND.md for details
        styles = {
            CrdtId(0, 0): LwwValue(
                timestamp=CrdtId(1, 15), value=si.ParagraphStyle.PLAIN
            )
        }

        # Add format code 10 (newline marker) for each \n character
        # This is a workaround for rmscene not yet supporting ParagraphStyle.NEWLINE
        for i, char in enumerate(combined_text):
            if char == '\n':
                # Use raw int 10 since rmscene doesn't have NEWLINE enum value yet
                styles[CrdtId(0, i)] = LwwValue(
                    timestamp=CrdtId(1, 15), value=10  # Format code 10 = newline
                )

        # Generate blocks manually with custom text width
        author_uuid = uuid4()

        blocks = [
            AuthorIdsBlock(author_uuids={1: author_uuid}),
            MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
            PageInfoBlock(
                loads_count=1,
                merges_count=0,
                text_chars_count=len(combined_text) + 1,
                text_lines_count=combined_text.count("\n") + 1,
            ),
            SceneTreeBlock(
                tree_id=CrdtId(0, 11),
                node_id=CrdtId(0, 0),
                is_update=True,
                parent_id=CrdtId(0, 1),
            ),
            RootTextBlock(
                block_id=CrdtId(0, 0),
                value=si.Text(
                    items=CrdtSequence(
                        [
                            CrdtSequenceItem(
                                item_id=CrdtId(1, 16),
                                left_id=CrdtId(0, 0),
                                right_id=CrdtId(0, 0),
                                deleted_length=0,
                                value=combined_text,
                            )
                        ]
                    ),
                    styles=styles,  # Now includes newline markers at format code 10
                    pos_x=self.TEXT_POS_X,
                    pos_y=self.TEXT_POS_Y,
                    width=self.TEXT_WIDTH,
                ),
            ),
            TreeNodeBlock(
                si.Group(
                    node_id=CrdtId(0, 1),
                )
            ),
            TreeNodeBlock(
                si.Group(
                    node_id=CrdtId(0, 11),
                    label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
                )
            ),
            SceneGroupItemBlock(
                parent_id=CrdtId(0, 1),
                item=CrdtSequenceItem(
                    item_id=CrdtId(0, 13),
                    left_id=CrdtId(0, 0),
                    right_id=CrdtId(0, 0),
                    deleted_length=0,
                    value=CrdtId(0, 11),
                ),
            ),
        ]

        # Add preserved annotations (strokes and highlights) as rmscene blocks
        if hasattr(page, 'annotation_blocks') and page.annotation_blocks:
            blocks.extend(page.annotation_blocks)
            logger.debug(f"Added {len(page.annotation_blocks)} preserved annotation blocks to .rm file")

        # Serialize to binary format
        buffer = io.BytesIO()
        rmscene.write_blocks(buffer, blocks)
        rm_bytes = buffer.getvalue()

        logger.debug(
            f"Generated .rm file: {len(rm_bytes)} bytes, "
            f"{len(page.text_items)} text items, "
            f"{len(combined_text)} characters"
        )

        return rm_bytes

