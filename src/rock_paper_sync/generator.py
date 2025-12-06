"""reMarkable document generator with rmscene integration.

This module converts parsed markdown documents into reMarkable v6 format files.
It handles pagination, text positioning, and generates binary .rm files using
the rmscene library.
"""

import io
import logging
import time
import uuid as uuid_module
from dataclasses import dataclass, field, replace
from pathlib import Path
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
)
from .config import LayoutConfig as AppLayoutConfig
from .coordinate_transformer import (
    END_OF_DOC_ANCHOR_MARKER,
    ParentAnchorResolver,
    apply_y_offset_to_block,
    get_annotation_center_y,
)
from .layout import WordWrapLayoutEngine
from .layout.constants import (
    CHAR_WIDTH,
    LINE_HEIGHT,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    TEXT_POS_X,
    TEXT_POS_Y,
    TEXT_WIDTH,
)
from .parser import BlockType, ContentBlock, MarkdownDocument, TextFormat

logger = logging.getLogger("rock_paper_sync.generator")


# =============================================================================
# CRDT Anchor Encoding/Decoding for extra_value_data
# =============================================================================
# In reMarkable firmware 3.6+, highlights store their text anchor position
# in extra_value_data as a CrdtId. The format is:
#   Field 7: CrdtId(author_id, base_id + char_offset)
# Where base_id comes from the RootTextBlock's CrdtSequenceItem.item_id.part2
#
# This allows us to update highlight positions when text shifts.
# =============================================================================


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a varint starting at pos, return (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a varint."""
    result = []
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _decode_crdt_id(data: bytes, pos: int) -> tuple[tuple[int, int], int]:
    """Decode a CrdtId (two varints) starting at pos."""
    part1, pos = _decode_varint(data, pos)
    part2, pos = _decode_varint(data, pos)
    return (part1, part2), pos


def _encode_crdt_id(part1: int, part2: int) -> bytes:
    """Encode a CrdtId as two varints."""
    return _encode_varint(part1) + _encode_varint(part2)


def update_glyph_extra_value_data(
    extra_data: bytes, new_char_offset: int, highlight_length: int, crdt_base_id: int = 16
) -> bytes:
    """Update the character offset anchors in extra_value_data.

    The extra_value_data contains tagged fields for text anchoring:
    - Field 15 (tag 0x7F): Start CrdtId (m_firstId) - first char of highlight
    - Field 17 (tag 0x8F): Fixed prefix (0x01 0x01) + end position varint (m_lastId)

    The device reads m_firstId from Field 15 and m_lastId end position from
    the varint after the fixed prefix in Field 17. Both must be updated.

    Format discovered from device firmware 3.6+ behavior:
    - 7f [author_varint] [start_pos_varint]
    - 8f 01 01 [end_pos_varint]
    - [remaining fields...]

    Args:
        extra_data: Original extra_value_data bytes
        new_char_offset: New character offset (start) in the text
        highlight_length: Length of the highlighted text
        crdt_base_id: Base ID from RootTextBlock (usually 16)

    Returns:
        Updated extra_value_data with new start and end positions
    """
    if len(extra_data) < 3:
        logger.debug("extra_value_data too short to contain anchor CrdtId")
        return extra_data

    # Verify this is Field 15 with CrdtId type (tag 0x7F)
    if extra_data[0] != 0x7F:
        logger.debug(f"Expected tag 0x7F, got 0x{extra_data[0]:02x}")
        return extra_data

    # Decode Field 15: Start CrdtId (m_firstId)
    old_start_crdt, pos_after_field15 = _decode_crdt_id(extra_data, 1)
    author_id = old_start_crdt[0]

    # Check for Field 17 (tag 0x8F)
    if pos_after_field15 >= len(extra_data) or extra_data[pos_after_field15] != 0x8F:
        logger.debug(
            f"Expected tag 0x8F at pos {pos_after_field15}, "
            f"got 0x{extra_data[pos_after_field15]:02x if pos_after_field15 < len(extra_data) else 'EOF'}"
        )
        return extra_data

    # Field 17 has a fixed prefix of 0x01 0x01, then the end position as varint
    # Verify the fixed prefix
    field17_start = pos_after_field15 + 1  # Skip the 0x8F tag
    if field17_start + 2 >= len(extra_data):
        logger.debug("Field 17 too short for fixed prefix")
        return extra_data

    if extra_data[field17_start] != 0x01 or extra_data[field17_start + 1] != 0x01:
        logger.debug(
            f"Expected Field 17 prefix 01 01, got "
            f"{extra_data[field17_start]:02x} {extra_data[field17_start + 1]:02x}"
        )
        return extra_data

    # Decode the end position varint after the fixed prefix
    end_pos_start = field17_start + 2
    old_end_pos, pos_after_end = _decode_varint(extra_data, end_pos_start)

    # Calculate new positions
    new_start_part2 = crdt_base_id + new_char_offset
    # End position is exclusive (start + length), not inclusive (start + length - 1)
    new_end_pos = crdt_base_id + new_char_offset + highlight_length

    # Encode new start CrdtId
    new_start_bytes = _encode_crdt_id(author_id, new_start_part2)

    # Encode new end position varint
    new_end_bytes = _encode_varint(new_end_pos)

    # Reconstruct:
    # Field15 tag + start CrdtId + Field17 tag + fixed prefix + end varint + rest
    new_extra = (
        bytes([0x7F])
        + new_start_bytes
        + bytes([0x8F, 0x01, 0x01])
        + new_end_bytes
        + extra_data[pos_after_end:]
    )

    old_start_offset = old_start_crdt[1] - crdt_base_id
    old_end_offset = old_end_pos - crdt_base_id

    logger.debug(
        f"Updated extra_value_data: start CrdtId ({author_id}, {old_start_crdt[1]})->({author_id}, {new_start_part2}) "
        f"[char {old_start_offset}->{new_char_offset}], "
        f"end pos {old_end_pos}->{new_end_pos} [char {old_end_offset}->{new_char_offset + highlight_length}]"
    )

    return new_extra


def get_crdt_base_id_from_rm(rm_file_path: Path) -> int:
    """Extract CRDT base ID from RootTextBlock in .rm file.

    The base ID is the item_id.part2 of the first CrdtSequenceItem in the
    RootTextBlock's text items.

    Args:
        rm_file_path: Path to .rm file

    Returns:
        Base ID (typically 16), or default 16 if not found
    """
    try:
        with open(rm_file_path, "rb") as f:
            for block in rmscene.read_blocks(f):
                if type(block).__name__ == "RootTextBlock":
                    for item in block.value.items.sequence_items():
                        return item.item_id.part2
    except Exception as e:
        logger.warning(f"Failed to get CRDT base ID from {rm_file_path}: {e}")

    return 16  # Default base ID


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
    original_rm_path: Path | None = None


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

    Layout constants are imported from rock_paper_sync.layout.constants,
    which is the single source of truth for all layout-related values.

    Attributes:
        layout: Page layout configuration
        page_width: Page width in pixels (1404 for reMarkable Paper Pro)
        page_height: Page height in pixels (1872 for reMarkable Paper Pro)
        line_height: Pixels per line (57px, calibrated from device)
        char_width: Pixels per character (15px, measured from device)
    """

    def __init__(self, layout_config: AppLayoutConfig) -> None:
        """Initialize generator with layout settings.

        Args:
            layout_config: Page layout configuration
        """
        self.layout = layout_config
        self.page_width = PAGE_WIDTH
        self.page_height = PAGE_HEIGHT
        self.line_height = LINE_HEIGHT
        self.char_width = CHAR_WIDTH

        # Initialize annotation adjustment strategies (Phase 1)
        self.text_anchor_strategy = HeuristicTextAnchor(context_window=50, fuzzy_threshold=0.8)
        # Use proportional font metrics for accurate highlight positioning
        # The device uses Noto Sans (proportional font), not monospace
        self.layout_engine = WordWrapLayoutEngine(
            text_width=TEXT_WIDTH,
            avg_char_width=CHAR_WIDTH,  # Fallback if font metrics unavailable
            line_height=LINE_HEIGHT,
            use_font_metrics=True,  # Enable Noto Sans font metrics for accuracy
        )

        logger.info(
            "RemarkableGenerator initialized with rmscene integration and Phase 1 annotation anchoring"
        )

    def _build_text_styles(self, text: str) -> dict:
        """Build rmscene styles dictionary with newline markers.

        Creates a styles dictionary for rmscene Text blocks with format code 10
        (newline marker) for each \\n character. This is a workaround for rmscene
        not yet supporting ParagraphStyle.NEWLINE.

        See docs/RMSCENE_NEWLINE_WORKAROUND.md for details.

        Args:
            text: Text content to build styles for

        Returns:
            Dictionary mapping CrdtId positions to LwwValue styles
        """
        styles = {CrdtId(0, 0): LwwValue(timestamp=CrdtId(1, 15), value=si.ParagraphStyle.PLAIN)}

        # Add format code 10 (newline marker) for each \n character
        for i, char in enumerate(text):
            if char == "\n":
                styles[CrdtId(0, i)] = LwwValue(
                    timestamp=CrdtId(1, 15),
                    value=10,  # Format code 10 = newline
                )

        return styles

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
                RemarkablePage(uuid=page_uuid, text_items=text_items, text_blocks=text_blocks)
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
        old_full_text = "\n\n".join(old_full_text_parts)  # Join pages with double newline

        # Compute FULL DOCUMENT text from all new pages
        new_full_text_parts = []
        for page in pages:
            page_text = "\n".join(block.content for block in page.text_blocks)
            new_full_text_parts.append(page_text)
        new_full_text = "\n\n".join(new_full_text_parts)  # Join pages with double newline

        logger.debug(
            f"Full document text: old={len(old_full_text)} chars, new={len(new_full_text)} chars"
        )

        for i, (page, rm_file_path) in enumerate(zip(pages, existing_rm_files)):
            if rm_file_path is None or not Path(rm_file_path).exists():
                logger.debug(f"Page {i}: No existing .rm file to preserve annotations from")
                continue

            try:
                # Read all blocks from existing file using rmscene
                with open(rm_file_path, "rb") as f:
                    existing_blocks = list(rmscene.read_blocks(f))

                # Extract annotation blocks (Lines and Glyphs)
                annotation_blocks = [
                    block
                    for block in existing_blocks
                    if "Line" in type(block).__name__ or "Glyph" in type(block).__name__
                ]

                if not annotation_blocks:
                    logger.debug(f"Page {i}: No annotation blocks found in {rm_file_path}")
                    continue

                # Extract old text blocks for spatial positioning (per-page)
                old_text_blocks, old_text_origin_y, _ = self._extract_text_blocks_from_rm(
                    rm_file_path
                )
                new_text_blocks = page.text_blocks
                new_text_origin_y = TEXT_POS_Y  # New documents use TEXT_POS_Y constant

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

                # Get CRDT base ID for updating highlight anchors (firmware 3.6+)
                crdt_base_id = get_crdt_base_id_from_rm(rm_file_path)

                # Adjust annotation blocks based on text repositioning
                # - Glyph blocks (highlights): content anchoring with delta-based X/Y
                # - Line blocks (strokes): cluster by proximity, anchor to paragraph
                adjusted_blocks = []

                # Separate highlights from strokes
                glyph_blocks = [b for b in annotation_blocks if "Glyph" in type(b).__name__]
                stroke_blocks = [b for b in annotation_blocks if "Line" in type(b).__name__]

                # Process highlights individually (content anchoring)
                for block in glyph_blocks:
                    try:
                        adjusted_block = self._adjust_annotation_block_position(
                            block,
                            old_text,
                            new_text,
                            old_text_blocks,
                            new_text_blocks,
                            position_map,
                            old_text_origin_y,
                            new_text_origin_y,
                            crdt_base_id,
                        )
                        adjusted_blocks.append(adjusted_block)
                    except Exception as e:
                        logger.warning(f"Failed to adjust highlight block: {e}")
                        adjusted_blocks.append(block)

                # Process strokes by cluster (context-aware anchoring)
                if stroke_blocks:
                    # Create ParentAnchorResolver from the existing blocks for per-parent
                    # coordinate transformation. This ensures strokes with different parent_ids
                    # are positioned correctly in absolute coordinate space.
                    anchor_resolver = ParentAnchorResolver.from_blocks(existing_blocks)

                    stroke_clusters = self._cluster_stroke_blocks(stroke_blocks, anchor_resolver)

                    # Separate implicit paragraphs (below all text) from regular clusters
                    # Implicit paragraphs should all move together as one unit
                    implicit_clusters = []
                    regular_clusters = []

                    for cluster in stroke_clusters:
                        # Calculate cluster center to check if implicit
                        centers = []
                        for block in cluster:
                            center = self._get_stroke_center(block, anchor_resolver)
                            if center:
                                centers.append(center[1])
                        if centers:
                            cluster_center_y = sum(centers) / len(centers)
                            if self._is_implicit_paragraph(cluster_center_y, old_text_blocks):
                                implicit_clusters.append(cluster)
                            else:
                                regular_clusters.append(cluster)
                        else:
                            regular_clusters.append(cluster)

                    # Merge all implicit paragraph clusters into one
                    if implicit_clusters:
                        merged_implicit = []
                        for cluster in implicit_clusters:
                            merged_implicit.extend(cluster)
                        if merged_implicit:
                            regular_clusters.append(merged_implicit)
                            logger.debug(
                                f"Merged {len(implicit_clusters)} implicit paragraph cluster(s) "
                                f"into 1 ({len(merged_implicit)} strokes)"
                            )

                    # With anchor_id updates in _generate_rm_file_roundtrip(), strokes
                    # are automatically positioned correctly because their TreeNodeBlock
                    # anchor_ids now point to the correct text offsets.
                    # We do NOT need to apply Y deltas to native coordinates.
                    # See docs/STROKE_ANCHORING.md for details.
                    for cluster in regular_clusters:
                        adjusted_blocks.extend(cluster)
                        logger.debug(
                            f"Preserved {len(cluster)} stroke(s) in cluster (anchor_ids will be updated)"
                        )

                # Store the adjusted rmscene blocks AND original file path
                page.annotation_blocks = adjusted_blocks
                page.original_rm_path = rm_file_path

                logger.info(
                    f"Page {i}: Preserved and adjusted {len(adjusted_blocks)} annotation blocks from {rm_file_path}"
                )

            except Exception as e:
                logger.warning(f"Page {i}: Failed to preserve annotations from {rm_file_path}: {e}")

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
            with open(rm_file_path, "rb") as f:
                blocks = list(rmscene.read_blocks(f))

            text_blocks = []
            text_origin_y = TEXT_POS_Y  # Default to constant
            full_text = ""

            # Find RootTextBlock to get text content and position
            for block in blocks:
                if "RootText" in type(block).__name__:
                    text_data = block.value
                    text_origin_y = text_data.pos_y  # Capture the actual text origin

                    # Extract actual text from CrdtSequence
                    # The text is in the 'value' field of each CrdtSequenceItem
                    text_parts = []
                    for item in text_data.items.sequence_items():
                        if hasattr(item, "value") and isinstance(item.value, str):
                            text_parts.append(item.value)

                    # Full text for content anchoring (join without splitting first)
                    full_text = "".join(text_parts)

                    # Split into paragraphs for TextBlock creation
                    paragraphs = full_text.split("\n")

                    # Create TextBlock for each paragraph with Y positions from layout engine
                    # Use WordWrapLayoutEngine for consistent line break calculation
                    from .layout import LayoutConfig as LayoutCfg
                    from .layout import LayoutContext

                    layout_ctx = LayoutContext.from_text(
                        full_text,
                        use_font_metrics=True,
                        config=LayoutCfg(
                            text_width=TEXT_WIDTH,
                            text_pos_x=TEXT_POS_X,
                            text_pos_y=text_data.pos_y,
                        ),
                    )

                    # Track position in full text to map paragraphs to offsets
                    current_offset = 0
                    for paragraph in paragraphs:
                        if paragraph.strip():
                            # Find paragraph start/end in full text
                            para_start = full_text.find(paragraph, current_offset)
                            if para_start == -1:
                                para_start = current_offset
                            para_end = para_start + len(paragraph)
                            current_offset = para_end + 1  # +1 for newline

                            # Get Y positions from layout engine
                            _, y_start = layout_ctx.offset_to_position(para_start)
                            _, y_end = layout_ctx.offset_to_position(para_end)
                            # Add one line height to y_end since offset_to_position
                            # gives the TOP of the line containing that character
                            y_end += layout_ctx.line_height

                            text_blocks.append(
                                TextBlock(
                                    content=paragraph,
                                    y_start=y_start,
                                    y_end=y_end,
                                    block_type="paragraph",
                                )
                            )

            return text_blocks, text_origin_y, full_text

        except Exception as e:
            logger.warning(f"Failed to extract text blocks from {rm_file_path}: {e}")
            return [], TEXT_POS_Y, ""

    def _adjust_annotation_block_position(
        self,
        block,
        old_text: str,
        new_text: str,
        old_text_blocks: list[TextBlock],
        new_text_blocks: list[TextBlock],
        position_map: dict[int, int],
        old_text_origin_y: float,
        new_text_origin_y: float,
        crdt_base_id: int = 16,
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
            crdt_base_id: Base ID from RootTextBlock for CRDT offset calculation

        Returns:
            Modified block with adjusted coordinates
        """
        # Check if this is a Glyph (highlight) - use content anchoring
        if "Glyph" in type(block).__name__:
            return self._adjust_glyph_with_content_anchoring(
                block,
                old_text,
                new_text,
                (TEXT_POS_X, old_text_origin_y),
                (TEXT_POS_X, new_text_origin_y),
                crdt_base_id,
            )

        # For Lines (strokes), use spatial Y-only approach
        # Get the annotation's center Y position in ABSOLUTE coordinates
        center_y_absolute = get_annotation_center_y(block, old_text_origin_y)

        if center_y_absolute is None:
            logger.debug("Cannot determine annotation center Y, keeping original position")
            return block

        # Find the nearest old text block to this annotation (both in absolute coords)
        nearest_old_idx = None
        min_distance = float("inf")

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
        old_block_y = (
            old_text_blocks[nearest_old_idx].y_start + old_text_blocks[nearest_old_idx].y_end
        ) / 2
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
        new_origin: tuple[float, float],
        crdt_base_id: int = 16,
    ):
        """Re-render highlight rectangles using content anchoring.

        Uses a delta-based approach to preserve pixel-perfect rectangle positions:
        1. Find where highlighted text was in old document (anchor)
        2. Resolve where that text is in new document (new_offset)
        3. Calculate position delta using SAME layout model for both
        4. Apply delta to original pixel-perfect rectangles
        5. Update CRDT anchor in extra_value_data for firmware 3.6+

        This approach preserves the original Qt-rendered rectangle precision
        while correctly shifting highlights when text moves.

        Args:
            glyph_block: SceneGlyphItemBlock containing highlight rectangles
            old_text: Full text of old document
            new_text: Full text of new document
            old_origin: (x, y) origin of old text block
            new_origin: (x, y) origin of new text block
            crdt_base_id: Base ID from RootTextBlock for CRDT offset calculation

        Returns:
            Modified glyph_block with adjusted rectangles and CRDT anchor
        """
        # Extract highlighted text
        if not hasattr(glyph_block.item, "value"):
            logger.warning("Glyph block has no value, keeping original position")
            return glyph_block

        glyph_value = glyph_block.item.value
        if not hasattr(glyph_value, "text") or not glyph_value.text:
            logger.warning("Glyph has no text content, keeping original position")
            return glyph_block

        highlight_text = glyph_value.text

        # Need rectangles to adjust
        if not hasattr(glyph_value, "rectangles") or not glyph_value.rectangles:
            logger.warning("Glyph has no rectangles, keeping original position")
            return glyph_block

        # Get old position (average of rectangles) for anchor finding
        old_x = sum(r.x for r in glyph_value.rectangles) / len(glyph_value.rectangles)
        old_y = sum(r.y for r in glyph_value.rectangles) / len(glyph_value.rectangles)

        # Find anchor in old document
        anchor = self.text_anchor_strategy.find_anchor(highlight_text, old_text, (old_x, old_y))

        logger.debug(
            f"Highlight '{highlight_text[:30]}...': old_pos=({old_x:.1f}, {old_y:.1f}), "
            f"old_offset={anchor.char_offset}, confidence={anchor.confidence:.2f}"
        )

        if anchor.confidence < 0.5:
            logger.warning(
                f"Low confidence anchor ({anchor.confidence:.2f}) for '{highlight_text[:30]}...', "
                f"keeping original position"
            )
            return glyph_block

        # Resolve anchor in new document
        new_offset = self.text_anchor_strategy.resolve_anchor(anchor, new_text)

        if new_offset is None:
            logger.warning(
                f"Could not find '{highlight_text[:30]}...' in new document, keeping original position"
            )
            return glyph_block

        old_offset = anchor.char_offset
        if old_offset is None:
            logger.warning("Anchor has no char_offset, keeping original position")
            return glyph_block

        logger.debug(
            f"  Resolved: old_offset={old_offset} -> new_offset={new_offset} "
            f"(delta={new_offset - old_offset})"
        )

        # DELTA-BASED APPROACH: Calculate positions using SAME layout model
        # This makes model inaccuracies cancel out
        try:
            old_x_model, old_y_model = self.layout_engine.offset_to_position(
                old_offset, old_text, old_origin, TEXT_WIDTH
            )
            new_x_model, new_y_model = self.layout_engine.offset_to_position(
                new_offset, new_text, new_origin, TEXT_WIDTH
            )
        except Exception as e:
            logger.warning(f"Failed to calculate positions for highlight: {e}")
            return glyph_block

        # Calculate delta between model positions (errors cancel out)
        x_delta = new_x_model - old_x_model
        y_delta = new_y_model - old_y_model

        logger.debug(
            f"  Model positions: old=({old_x_model:.1f}, {old_y_model:.1f}), "
            f"new=({new_x_model:.1f}, {new_y_model:.1f})"
        )
        logger.debug(f"  Delta: ({x_delta:.1f}, {y_delta:.1f})")

        # REFLOW DETECTION: Check if highlight now spans different number of lines
        # When text reflows, we need to recalculate rectangles from scratch
        old_rect_count = len(glyph_value.rectangles)
        new_end_offset = new_offset + len(highlight_text)
        new_rects = self.layout_engine.calculate_highlight_rectangles(
            new_offset, new_end_offset, new_text, new_origin, TEXT_WIDTH
        )
        new_rect_count = len(new_rects)

        if new_rect_count != old_rect_count:
            # REFLOW CASE: Highlight now spans different number of lines
            # Use delta-based positioning for accuracy (font metric errors cancel out)
            logger.debug(f"  Reflow detected: {old_rect_count} rect(s) → {new_rect_count} rect(s)")

            # Preserve original rectangle properties
            original_rect = glyph_value.rectangles[0] if glyph_value.rectangles else None
            original_height = original_rect.h if original_rect else LINE_HEIGHT

            # Strategy: Apply delta to first rect, then use known geometry for others
            # This preserves pixel-perfect positioning from the original highlight
            # while correctly handling multi-line splits

            # Get layout-calculated positions for reference
            first_new_x, first_new_y, first_new_w, _ = new_rects[0]

            # Calculate first rectangle using delta approach (preserves accuracy)
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

            # For additional rectangles, use KNOWN GEOMETRY instead of layout relative offsets
            # Relative offsets from layout engine have font metric scaling errors
            #
            # Key insight: Each subsequent line's rectangle has:
            # - X: Either at line start (TEXT_POS_X) or relative position within line
            # - Y: Previous line Y + original_height (highlight rectangles are contiguous)
            #
            # We detect line-start by checking if layout X is close to text origin
            line_start_x = new_origin[0]  # TEXT_POS_X
            tolerance = 10.0  # Allow small deviation

            for i, (x, y, w, _) in enumerate(new_rects[1:], start=1):
                # Check if this rectangle starts at line beginning
                is_line_start = abs(x - line_start_x) < tolerance

                if is_line_start:
                    # Rectangle at line start: use TEXT_POS_X directly
                    # This avoids font metric errors in X calculation
                    rect_x = TEXT_POS_X
                else:
                    # Mid-line continuation: use relative offset (rare case)
                    rel_x = x - first_new_x
                    rect_x = first_rect_x + rel_x

                # Y position: each line is original_height below previous
                # Device uses highlight height as line spacing (~44px), not LINE_HEIGHT
                rect_y = first_rect_y + i * original_height

                glyph_value.rectangles.append(si.Rectangle(rect_x, rect_y, w, original_height))
                logger.debug(
                    f"  rect[{i}]: x={rect_x:.1f}, y={rect_y:.1f}, w={w:.1f} (line_start={is_line_start})"
                )

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
        # This is the critical fix: the device uses CrdtId in extra_value_data
        # to anchor highlights to character positions in the text
        if hasattr(glyph_block, "extra_value_data") and glyph_block.extra_value_data:
            glyph_block.extra_value_data = update_glyph_extra_value_data(
                glyph_block.extra_value_data, new_offset, len(highlight_text), crdt_base_id
            )

        logger.debug(
            f"Adjusted highlight '{highlight_text[:30]}...' by delta=({x_delta:.1f}, {y_delta:.1f}), "
            f"offset={old_offset}->{new_offset}, confidence={anchor.confidence:.2f}"
        )

        return glyph_block

    # =========================================================================
    # Stroke Clustering and Re-Anchoring
    # =========================================================================

    def _get_stroke_center(
        self, block, anchor_resolver: ParentAnchorResolver
    ) -> tuple[float, float] | None:
        """Extract center (X, Y) coordinates from stroke block in absolute coordinates.

        Uses per-parent anchor positions for correct coordinate transformation.

        Args:
            block: rmscene SceneLineItemBlock
            anchor_resolver: ParentAnchorResolver for per-parent coordinate transformation

        Returns:
            Tuple of (center_x, center_y) in absolute coordinates, or None if invalid
        """
        try:
            if not hasattr(block, "item") or not hasattr(block.item, "value"):
                return None

            value = block.item.value

            if "Line" not in type(value).__name__:
                return None

            if not hasattr(value, "points") or not value.points:
                return None

            # Calculate native center
            xs = [p.x for p in value.points if hasattr(p, "x")]
            ys = [p.y for p in value.points if hasattr(p, "y")]

            if not xs or not ys:
                return None

            native_center_x = sum(xs) / len(xs)
            native_center_y = sum(ys) / len(ys)

            # Get parent_id for per-parent anchor lookup
            parent_id = getattr(block, "parent_id", None)

            # Transform to absolute using per-parent anchors
            absolute_x, absolute_y = anchor_resolver.to_absolute(
                native_center_x, native_center_y, parent_id
            )

            return (absolute_x, absolute_y)

        except Exception as e:
            logger.warning(f"Failed to get stroke center: {e}")
            return None

    def _get_stroke_bbox(
        self, block, anchor_resolver: ParentAnchorResolver
    ) -> tuple[float, float, float, float] | None:
        """Extract bounding box from stroke block in absolute coordinates.

        Uses per-parent anchor positions for correct coordinate transformation.

        Args:
            block: rmscene SceneLineItemBlock
            anchor_resolver: ParentAnchorResolver for per-parent coordinate transformation

        Returns:
            Tuple of (x, y, width, height) in absolute coordinates, or None if invalid
        """
        try:
            if not hasattr(block, "item") or not hasattr(block.item, "value"):
                return None

            value = block.item.value

            if "Line" not in type(value).__name__:
                return None

            if not hasattr(value, "points") or not value.points:
                return None

            # Calculate bounding box from points
            xs = [p.x for p in value.points if hasattr(p, "x")]
            ys = [p.y for p in value.points if hasattr(p, "y")]

            if not xs or not ys:
                return None

            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            # Get parent_id for per-parent anchor lookup
            parent_id = getattr(block, "parent_id", None)

            # Transform top-left corner to absolute using per-parent anchors
            abs_x, abs_y = anchor_resolver.to_absolute(min_x, min_y, parent_id)

            width = max_x - min_x
            height = max_y - min_y

            # Ensure minimum dimensions (for single-point strokes)
            width = max(width, 1.0)
            height = max(height, 1.0)

            return (abs_x, abs_y, width, height)

        except Exception as e:
            logger.warning(f"Failed to get stroke bbox: {e}")
            return None

    def _cluster_stroke_blocks(
        self,
        stroke_blocks: list,
        anchor_resolver: ParentAnchorResolver,
        distance_threshold: float = 80.0,
    ) -> list[list]:
        """Cluster stroke blocks using efficient KDTree-based spatial indexing.

        Uses transitive chaining via connected components: if stroke A is near B,
        and B is near C, all three cluster together even if A and C are far apart.
        This properly handles multi-line handwritten notes.

        Uses per-parent anchor positions for correct coordinate transformation,
        ensuring strokes with different parent_ids are placed correctly in
        absolute coordinate space before clustering.

        Args:
            stroke_blocks: List of rmscene Line blocks
            anchor_resolver: ParentAnchorResolver for per-parent coordinate transformation
            distance_threshold: Max distance between bbox centers to cluster
                              (default: 80px, captures multi-line handwriting)

        Returns:
            List of clusters, where each cluster is a list of stroke blocks
        """
        from .annotations.common.spatial import cluster_bboxes_kdtree

        if not stroke_blocks:
            return []

        # Extract bounding boxes from stroke blocks using per-parent anchors
        bboxes = []
        valid_blocks = []

        for block in stroke_blocks:
            bbox = self._get_stroke_bbox(block, anchor_resolver)
            if bbox is not None:
                bboxes.append(bbox)
                valid_blocks.append(block)

        if not valid_blocks:
            return []

        # Use efficient KDTree clustering with transitive chaining
        index_clusters = cluster_bboxes_kdtree(bboxes, distance_threshold)

        # Convert index clusters to block clusters
        clusters = [[valid_blocks[idx] for idx in indices] for indices in index_clusters]

        logger.debug(
            f"Clustered {len(valid_blocks)} strokes into {len(clusters)} cluster(s) "
            f"(distance threshold: {distance_threshold}px)"
        )
        return clusters

    def _is_implicit_paragraph(
        self,
        cluster_center_y: float,
        text_blocks: list[TextBlock],
        gap_threshold: float | None = None,
    ) -> bool:
        """Detect if stroke cluster is below all text (implicit handwritten paragraph).

        Strokes below text with a gap > LINE_HEIGHT are treated as an implicit
        paragraph that only moves when total text content expands.

        Args:
            cluster_center_y: Center Y of the stroke cluster
            text_blocks: Text blocks from the document
            gap_threshold: Minimum gap to consider implicit (default: LINE_HEIGHT)

        Returns:
            True if cluster is an implicit paragraph below all text
        """
        if gap_threshold is None:
            gap_threshold = LINE_HEIGHT

        if not text_blocks:
            return True  # No text = everything is "implicit"

        # Find the bottom of all text
        last_text_y = max(tb.y_end for tb in text_blocks)

        # Check if cluster is below text with sufficient gap
        gap = cluster_center_y - last_text_y
        is_implicit = gap > gap_threshold

        if is_implicit:
            logger.debug(
                f"Stroke cluster at y={cluster_center_y:.1f} is implicit paragraph "
                f"(gap={gap:.1f} > threshold={gap_threshold:.1f})"
            )

        return is_implicit

    def _calculate_stroke_cluster_delta(
        self,
        cluster: list,
        anchor_resolver: ParentAnchorResolver,
        old_text_blocks: list[TextBlock],
        new_text_blocks: list[TextBlock],
        position_map: dict[int, int],
    ) -> float:
        """Calculate Y delta for a stroke cluster based on anchor paragraph movement.

        Uses delta-based positioning like highlights: calculates delta between
        old and new paragraph positions using same model, so errors cancel out.

        For implicit paragraphs (below all text with gap), calculates delta
        based on total text expansion.

        Args:
            cluster: List of stroke blocks in this cluster
            anchor_resolver: ParentAnchorResolver for per-parent coordinate transformation
            old_text_blocks: Text blocks from old document
            new_text_blocks: Text blocks from new document
            position_map: Mapping from old to new block indices

        Returns:
            Y offset to apply to all strokes in cluster
        """
        if not cluster:
            return 0.0

        # Calculate cluster center Y (average of all stroke centers)
        centers = []
        for block in cluster:
            center = self._get_stroke_center(block, anchor_resolver)
            if center:
                centers.append(center[1])  # Y coordinate

        if not centers:
            return 0.0

        cluster_center_y = sum(centers) / len(centers)

        # Check for implicit paragraph (below all text with gap)
        if self._is_implicit_paragraph(cluster_center_y, old_text_blocks):
            # Calculate delta based on total text expansion
            if not old_text_blocks or not new_text_blocks:
                return 0.0

            old_text_end = max(tb.y_end for tb in old_text_blocks)
            new_text_end = max(tb.y_end for tb in new_text_blocks)
            y_delta = new_text_end - old_text_end

            logger.debug(
                f"Implicit paragraph: text end moved {old_text_end:.1f} -> {new_text_end:.1f}, "
                f"delta={y_delta:.1f}"
            )
            return y_delta

        # Normal case: anchor to nearest paragraph
        if not old_text_blocks:
            return 0.0

        # Find containing or nearest old paragraph
        # Priority 1: Paragraph that CONTAINS the stroke (y_start <= stroke_y <= y_end)
        # Priority 2: Nearest by center-to-center distance (fallback for margin notes)
        nearest_old_idx = None

        # First, check if stroke is within any paragraph's Y range
        for idx, tb in enumerate(old_text_blocks):
            if tb.y_start <= cluster_center_y <= tb.y_end:
                nearest_old_idx = idx
                logger.debug(
                    f"Stroke at y={cluster_center_y:.1f} is within para {idx} "
                    f"(y={tb.y_start:.1f}-{tb.y_end:.1f})"
                )
                break

        # Fallback: find nearest by center distance (for margin notes beside text)
        if nearest_old_idx is None:
            min_distance = float("inf")
            for idx, tb in enumerate(old_text_blocks):
                block_center_y = (tb.y_start + tb.y_end) / 2
                distance = abs(cluster_center_y - block_center_y)
                if distance < min_distance:
                    min_distance = distance
                    nearest_old_idx = idx

        if nearest_old_idx is None:
            return 0.0

        # Map to new paragraph
        new_idx = position_map.get(nearest_old_idx)
        if new_idx is None or new_idx >= len(new_text_blocks):
            logger.debug(
                f"No mapping for old paragraph {nearest_old_idx}, keeping cluster position"
            )
            return 0.0

        # Calculate Y delta (same model for old/new → errors cancel)
        old_para_y = (
            old_text_blocks[nearest_old_idx].y_start + old_text_blocks[nearest_old_idx].y_end
        ) / 2
        new_para_y = (new_text_blocks[new_idx].y_start + new_text_blocks[new_idx].y_end) / 2
        y_delta = new_para_y - old_para_y

        logger.debug(
            f"Stroke cluster at y={cluster_center_y:.1f} anchors to para {nearest_old_idx}, "
            f"delta={y_delta:.1f}"
        )

        return y_delta

    # =========================================================================
    # TreeNodeBlock Anchor Update
    # =========================================================================

    def _compute_anchor_offset_delta(self, old_text: str, new_text: str) -> int:
        """Compute the character offset delta between old and new text.

        This determines how much to adjust TreeNodeBlock anchor_ids when text
        changes. The anchor_id.part2 is a character offset into the text content.
        When text is inserted before anchor points, the offset must increase.

        Uses a simple heuristic: finds where old text content appears in new text
        to determine the insertion offset.

        Args:
            old_text: Original text content from RootTextBlock
            new_text: New text content to be written

        Returns:
            Offset delta to add to anchor_id values (positive = text inserted)
        """
        if not old_text or not new_text:
            return 0

        # Simple case: text length changed
        len_delta = len(new_text) - len(old_text)
        if len_delta == 0:
            return 0

        # Try to find where old text starts in new text
        # This handles the common case of text prepended at the beginning
        # Use first 100 chars of old text as a signature
        signature_len = min(100, len(old_text))
        signature = old_text[:signature_len]

        if signature in new_text:
            insertion_offset = new_text.find(signature)
            if insertion_offset >= 0:
                logger.debug(
                    f"Anchor offset delta: found old text at position {insertion_offset} "
                    f"(text grew by {len_delta} chars)"
                )
                return insertion_offset

        # Fallback: assume text was prepended (delta = length difference)
        # This is correct when new content is added at the beginning
        if len_delta > 0:
            logger.debug(
                f"Anchor offset delta: using length delta {len_delta} (signature not found)"
            )
            return len_delta

        # Text was shortened - more complex case, use 0 for now
        logger.debug("Anchor offset delta: text shortened, using 0")
        return 0

    def _update_tree_node_anchor(self, block, offset_delta: int):
        """Create a new TreeNodeBlock with updated anchor_id offset.

        The anchor_id.value.part2 is a character offset into the text content.
        When text is inserted before the anchor point, the offset must be
        increased to maintain the correct text reference.

        Args:
            block: Original TreeNodeBlock
            offset_delta: Amount to add to anchor_id.part2

        Returns:
            New TreeNodeBlock with updated anchor_id (or original if no anchor)
        """
        if not hasattr(block, "group") or not block.group:
            return block

        g = block.group
        if not hasattr(g, "anchor_id") or not g.anchor_id or not g.anchor_id.value:
            return block

        old_anchor = g.anchor_id
        old_offset = old_anchor.value.part2

        # Don't modify end-of-document marker
        if old_offset == END_OF_DOC_ANCHOR_MARKER:
            return block

        # Create new anchor_id with updated offset
        new_offset = old_offset + offset_delta
        new_anchor_value = CrdtId(old_anchor.value.part1, new_offset)
        new_anchor_lww = LwwValue(timestamp=old_anchor.timestamp, value=new_anchor_value)

        # Create new Group with updated anchor_id
        new_group = replace(g, anchor_id=new_anchor_lww)

        # Create new TreeNodeBlock with updated group
        new_block = replace(block, group=new_group)

        logger.debug(f"Updated TreeNodeBlock {g.node_id} anchor_id: {old_offset} -> {new_offset}")

        return new_block

    def paginate_content(self, blocks: list[ContentBlock]) -> list[list[ContentBlock]]:
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
        y_position = float(TEXT_POS_Y)  # Track Y for annotation mapping

        for block in blocks:
            block_lines = self.estimate_block_lines(block)
            block.page_y_start = y_position  # Set Y position for annotation mapping

            # Check if header should start new page (avoid orphan headers)
            if block.type == BlockType.HEADER and current_page:
                remaining_space = self.layout.lines_per_page - current_lines
                if remaining_space < 10:  # Less than 10 lines remaining
                    pages.append(current_page)
                    current_page = []
                    current_lines = 0
                    y_position = float(TEXT_POS_Y)
                    block.page_y_start = y_position

            # Check if block fits on current page
            if current_lines + block_lines > self.layout.lines_per_page:
                # Block doesn't fit - either split it or start new page
                if self.layout.allow_paragraph_splitting and block.type == BlockType.PARAGRAPH:
                    # Split paragraph across pages using layout engine for accurate split point
                    lines_available = self.layout.lines_per_page - current_lines

                    # Use layout engine to find exact character offset for split
                    line_breaks = self.layout_engine.calculate_line_breaks(block.text, TEXT_WIDTH)

                    # Find character offset at end of lines_available lines
                    if lines_available > 0 and lines_available < len(line_breaks):
                        split_point = line_breaks[lines_available]
                    else:
                        # Fallback: can't split meaningfully
                        split_point = 0

                    if split_point > 0 and split_point < len(block.text):
                        # Try to split at word boundary (find space before split point)
                        space_before = block.text.rfind(" ", 0, split_point)
                        if space_before > split_point * 0.8:  # Within 20% of target
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
                            y_position = float(TEXT_POS_Y) + current_lines * self.line_height
                        else:
                            current_page = []
                            current_lines = 0
                            y_position = float(TEXT_POS_Y)
                    else:
                        # Not enough room to split meaningfully, start new page
                        if current_page:
                            pages.append(current_page)
                        current_page = [block]
                        current_lines = block_lines
                        y_position = float(TEXT_POS_Y) + block_lines * self.line_height
                        block.page_y_start = float(TEXT_POS_Y)
                else:
                    # Atomic block placement (default behavior)
                    if current_page:
                        pages.append(current_page)
                    current_page = [block]
                    current_lines = block_lines
                    y_position = float(TEXT_POS_Y) + block_lines * self.line_height
                    block.page_y_start = float(TEXT_POS_Y)
            else:
                current_page.append(block)
                current_lines += block_lines
                y_position += block_lines * self.line_height

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

        Uses WordWrapLayoutEngine with font metrics for accurate line counting.
        This ensures pagination matches actual text rendering, preventing text
        from spilling beyond page boundaries.

        Args:
            block: Content block to estimate

        Returns:
            Estimated number of lines
        """
        if block.type == BlockType.HORIZONTAL_RULE:
            return 2

        # Account for list item bullet
        text = block.text
        if block.type == BlockType.LIST_ITEM:
            text = f"• {text}"

        # Use layout engine with font metrics for accurate line counting
        # This matches the actual rendering in blocks_to_text_items()
        line_breaks = self.layout_engine.calculate_line_breaks(text, TEXT_WIDTH)
        text_lines = len(line_breaks)

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
        and the configured margins. Uses WordWrapLayoutEngine for consistent
        line break calculation that matches _extract_text_blocks_from_rm().

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
        from .layout import LayoutConfig as LayoutCfg
        from .layout import LayoutContext

        items: list[TextItem] = []
        text_blocks: list[TextBlock] = []

        # Build full text to use layout engine for consistent positioning
        # This matches _extract_text_blocks_from_rm() behavior
        full_text_parts = []
        for block in blocks:
            if block.type == BlockType.HORIZONTAL_RULE:
                full_text_parts.append("")  # Placeholder for HR
            else:
                text = block.text
                if block.type == BlockType.LIST_ITEM:
                    text = f"• {text}"
                full_text_parts.append(text)

        full_text = "\n".join(full_text_parts)

        # Create layout context for consistent Y positioning
        layout_ctx = LayoutContext.from_text(
            full_text,
            use_font_metrics=True,
            config=LayoutCfg(
                text_width=TEXT_WIDTH,
                text_pos_x=TEXT_POS_X,
                text_pos_y=TEXT_POS_Y,
            ),
        )

        # Track position in full text
        current_offset = 0
        for block in blocks:
            if block.type == BlockType.HORIZONTAL_RULE:
                # Skip horizontal rules (not rendered as text in Phase 1)
                current_offset += 1  # +1 for newline
                continue

            x_position = float(self.layout.margin_left)
            width = float(self.page_width - self.layout.margin_left - self.layout.margin_right)

            # Prepare text with list bullet if needed
            text = block.text
            if block.type == BlockType.LIST_ITEM:
                indent = 20 * block.level
                x_position += indent
                width -= indent
                text = f"• {text}"

            # Get Y positions from layout engine (consistent with extraction)
            _, y_start = layout_ctx.offset_to_position(current_offset)
            para_end = current_offset + len(text)
            _, y_end = layout_ctx.offset_to_position(para_end)
            y_end += layout_ctx.line_height  # Add line height for bottom of last line

            # Create text item
            items.append(
                TextItem(
                    text=text,
                    x=x_position,
                    y=y_start,
                    width=width,
                    formatting=block.formatting,
                )
            )

            # Create text block for annotation mapping
            text_blocks.append(
                TextBlock(
                    content=text,
                    y_start=y_start,
                    y_end=y_end,
                    block_type=block.type.name.lower(),
                )
            )

            current_offset = para_end + 1  # +1 for newline

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
        if (
            hasattr(page, "annotation_blocks")
            and page.annotation_blocks
            and hasattr(page, "original_rm_path")
            and page.original_rm_path
        ):
            return self._generate_rm_file_roundtrip(page)

        # Otherwise create from scratch (no annotations to preserve)
        return self._generate_rm_file_from_scratch(page)

    def _generate_rm_file_roundtrip(self, page: RemarkablePage) -> bytes:
        """Modify existing .rm file preserving scene tree structure.

        This preserves the original scene tree structure (TreeNodes, SceneGroups,
        SceneInfo, etc.) which is critical for annotations to display correctly.
        Only modifies the text content and annotation positions.

        IMPORTANT: When text changes, TreeNodeBlock anchor_ids must be updated
        to track the original text content. The anchor_id.value.part2 is a
        character offset into the text. If text is inserted before an anchor,
        the offset must be adjusted. See docs/STROKE_ANCHORING.md for details.

        Args:
            page: RemarkablePage with annotation_blocks and original_rm_path

        Returns:
            Binary .rm file content with preserved structure
        """
        # Read all blocks from original file
        with open(str(page.original_rm_path), "rb") as f:
            blocks = list(rmscene.read_blocks(f))

        # Prepare new text content
        combined_text = "\n".join(item.text for item in page.text_items)
        if not combined_text.strip():
            combined_text = " "

        # Extract old text from original RootTextBlock for anchor_id adjustment
        old_text = ""
        for block in blocks:
            if type(block).__name__ == "RootTextBlock":
                for item in block.value.items.sequence_items():
                    if hasattr(item, "value") and isinstance(item.value, str):
                        old_text += item.value
                break

        # Compute anchor offset delta for TreeNodeBlock anchor_id updates
        # This handles the case where text is inserted before anchor points
        anchor_offset_delta = self._compute_anchor_offset_delta(old_text, combined_text)

        # Build index of original annotation blocks by block_id for matching
        original_annotation_ids = set()
        for block in blocks:
            block_type = type(block).__name__
            if block_type in ["SceneLineItemBlock", "SceneGlyphItemBlock"]:
                if hasattr(block, "item") and hasattr(block.item, "item_id"):
                    original_annotation_ids.add(block.item.item_id)

        # Build index of adjusted annotations by item_id
        adjusted_by_id = {}
        for adj_block in page.annotation_blocks:
            if hasattr(adj_block, "item") and hasattr(adj_block.item, "item_id"):
                adjusted_by_id[adj_block.item.item_id] = adj_block

        # Modify blocks in place
        modified_blocks = []
        annotation_count = 0

        for block in blocks:
            block_type = type(block).__name__

            # Replace text content in RootTextBlock
            if block_type == "RootTextBlock":
                # Build styles dictionary with newline markers
                styles = self._build_text_styles(combined_text)

                # Create new RootTextBlock with updated text but same structure
                modified_block = RootTextBlock(
                    block_id=block.block_id,
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
                        styles=styles,  # Now includes newline markers
                        pos_x=block.value.pos_x,
                        pos_y=block.value.pos_y,
                        width=block.value.width,
                    ),
                )
                modified_blocks.append(modified_block)
                logger.debug(
                    f"Replaced text content in RootTextBlock ({len(combined_text)} chars, {combined_text.count(chr(10))} newlines)"
                )

            # Replace annotation blocks with adjusted versions
            elif block_type in ["SceneLineItemBlock", "SceneGlyphItemBlock"]:
                # Try to find adjusted version by item_id
                if hasattr(block, "item") and hasattr(block.item, "item_id"):
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
            elif block_type == "PageInfoBlock":
                modified_block = PageInfoBlock(
                    loads_count=block.loads_count,
                    merges_count=block.merges_count,
                    text_chars_count=len(combined_text) + 1,
                    text_lines_count=combined_text.count("\n") + 1,
                )
                modified_blocks.append(modified_block)

            # Update TreeNodeBlock anchor_ids to track text content
            elif block_type == "TreeNodeBlock":
                if anchor_offset_delta != 0:
                    modified_block = self._update_tree_node_anchor(block, anchor_offset_delta)
                    modified_blocks.append(modified_block)
                else:
                    modified_blocks.append(block)

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

        # Build styles dictionary with newline markers
        styles = self._build_text_styles(combined_text)

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
                    pos_x=TEXT_POS_X,
                    pos_y=TEXT_POS_Y,
                    width=TEXT_WIDTH,
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
        if hasattr(page, "annotation_blocks") and page.annotation_blocks:
            blocks.extend(page.annotation_blocks)
            logger.debug(
                f"Added {len(page.annotation_blocks)} preserved annotation blocks to .rm file"
            )

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
