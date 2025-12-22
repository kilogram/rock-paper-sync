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
    HeuristicTextAnchor,
    TextBlock,
)
from .annotations.document_model import (
    AnchorContext,
    ContextResolver,
    DocumentAnnotation,
    DocumentModel,
    PageProjection,
)
from .config import LayoutConfig as AppLayoutConfig
from .coordinate_transformer import (
    END_OF_DOC_ANCHOR_MARKER,
)
from .layout import DeviceGeometry, WordWrapLayoutEngine
from .layout.device import DEFAULT_DEVICE
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
class PageAnnotationContext:
    """Unified annotation context for a page.

    Replaces the previous 4 parallel dicts (same_page_annotations,
    cross_page_annotations, page_rm_paths, moved_out_ids) with a single
    context object per page.

    Attributes:
        annotations: All annotation blocks routed to this page
        tree_nodes: List of (TreeNodeBlock, stroke_y, SceneGroupItemBlock, SceneTreeBlock) tuples for cross-page strokes
        source_rm_path: Original .rm file path (for roundtrip preservation)
        exclude_ids: Annotation IDs that moved OUT of this page (exclude from roundtrip)
        exclude_tree_node_ids: TreeNodeBlock node_ids that moved OUT (exclude from roundtrip)
        has_same_page: True if any annotations stayed on their original page
    """

    annotations: list = field(default_factory=list)
    tree_nodes: list = field(
        default_factory=list
    )  # List of (TreeNodeBlock, stroke_y, SceneGroupItemBlock, SceneTreeBlock) tuples
    source_rm_path: Path | None = None
    exclude_ids: set = field(default_factory=set)
    exclude_tree_node_ids: set = field(default_factory=set)
    has_same_page: bool = False


@dataclass
class RemarkablePage:
    """A single page in a reMarkable document.

    Attributes:
        uuid: Unique page identifier
        text_items: List of positioned text items on this page
        text_blocks: List of text blocks with position info (for annotation mapping)
        annotation_context: Unified annotation state for this page
        content_blocks: Original parsed content blocks (for text extraction)
    """

    uuid: str
    text_items: list[TextItem] = field(default_factory=list)
    text_blocks: list[TextBlock] = field(default_factory=list)
    annotation_context: PageAnnotationContext | None = None
    content_blocks: list = field(default_factory=list)


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

    Device geometry parameters are provided via a DeviceGeometry instance,
    which encapsulates all device-specific layout values. For backward
    compatibility, the default geometry (Paper Pro) is used if not specified.

    Attributes:
        layout: Page layout configuration
        geometry: Device geometry (page dimensions, typography, etc.)
        page_width: Page width in pixels (from geometry)
        page_height: Page height in pixels (from geometry)
        line_height: Pixels per line (from geometry)
        char_width: Pixels per character (from geometry)
    """

    def __init__(
        self,
        layout_config: AppLayoutConfig,
        geometry: DeviceGeometry | None = None,
    ) -> None:
        """Initialize generator with layout settings.

        Args:
            layout_config: Page layout configuration
            geometry: Device geometry (uses DEFAULT_DEVICE if not provided)
        """
        self.layout = layout_config
        self.geometry = geometry or DEFAULT_DEVICE

        # Derive dimensions from geometry
        self.page_width = self.geometry.page_width
        self.page_height = self.geometry.page_height
        self.line_height = self.geometry.line_height
        self.char_width = self.geometry.char_width

        # Initialize layout engine for text positioning
        # Use proportional font metrics for accurate highlight positioning
        # The device uses Noto Sans (proportional font), not monospace
        self.layout_engine = WordWrapLayoutEngine.from_geometry(
            self.geometry,
            use_font_metrics=True,  # Enable Noto Sans font metrics for accuracy
        )

        # Context resolver for annotation migration (V2 architecture)
        self._context_resolver = ContextResolver(
            context_window=50,
            fuzzy_threshold=0.8,
        )

        # Keep HeuristicTextAnchor for direct use (e.g., highlight adjustment)
        self.text_anchor_strategy = HeuristicTextAnchor(context_window=50, fuzzy_threshold=0.8)

        logger.info(
            "RemarkableGenerator initialized with DocumentModel-based annotation preservation"
        )

    def _get_handler_for_block(self, block):
        """Get the appropriate handler for an annotation block.

        Args:
            block: Raw rmscene annotation block

        Returns:
            HighlightHandler for Glyph blocks, StrokeHandler for Line blocks, or None
        """
        # Check block type name first (matches original behavior)
        block_type = type(block).__name__
        if "Glyph" in block_type:
            return self._highlight_handler
        if "Line" in block_type:
            return self._stroke_handler

        # Fallback: check item.value type
        if hasattr(block, "item") and hasattr(block.item, "value"):
            value_type = type(block.item.value).__name__
            if "Glyph" in value_type:
                return self._highlight_handler
            if "Line" in value_type:
                return self._stroke_handler

        return None

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

        Uses DocumentModel as the authoritative source for pagination and
        annotation migration (V2 architecture).

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

        # Create DocumentModel from content blocks (V2 architecture)
        new_model = DocumentModel.from_content_blocks(md_doc.content, self.geometry)

        # If we have existing .rm files, load old model and migrate annotations
        if existing_rm_files:
            valid_rm_files = [p for p in existing_rm_files if p and p.exists()]
            if valid_rm_files:
                old_model = DocumentModel.from_rm_files(valid_rm_files, self.geometry)
                if old_model.annotations:
                    new_model, report = old_model.migrate_annotations_to(new_model)
                    logger.info(
                        f"Migrated {len(report.migrations)} annotations, "
                        f"{len(report.orphans)} orphaned (success rate: {report.success_rate:.1%})"
                    )

        # Project to pages - DocumentModel is authoritative for pagination
        projections = new_model.project_to_pages(existing_page_uuids, self.layout_engine)

        # Build UUID -> .rm path mapping for source file tracking
        uuid_to_rm_path: dict[str, Path] = {}
        if existing_rm_files and existing_page_uuids:
            for uuid, rm_path in zip(existing_page_uuids, existing_rm_files):
                if rm_path and rm_path.exists():
                    uuid_to_rm_path[uuid] = rm_path

        # Generate RemarkablePages from PageProjections
        pages: list[RemarkablePage] = []
        for projection in projections:
            # Convert content blocks to text items
            text_items, text_blocks = self.blocks_to_text_items(projection.content_blocks)

            page = RemarkablePage(
                uuid=projection.page_uuid,
                text_items=text_items,
                text_blocks=text_blocks,
            )

            # Add annotations from projection
            if projection.annotations:
                self._apply_annotations_to_page(page, projection, uuid_to_rm_path)

            pages.append(page)

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

    def _apply_annotations_to_page(
        self,
        page: RemarkablePage,
        projection: PageProjection,
        uuid_to_rm_path: dict[str, Path],
    ) -> None:
        """Apply annotations from PageProjection to RemarkablePage.

        Converts DocumentAnnotation objects to rmscene blocks and adjusts
        coordinates for the target page.

        Args:
            page: Target RemarkablePage to add annotations to
            projection: PageProjection containing annotations and page text
            uuid_to_rm_path: Mapping of page UUIDs to source .rm file paths
        """
        # Initialize annotation context if needed
        if page.annotation_context is None:
            page.annotation_context = PageAnnotationContext()

        ctx = page.annotation_context
        page_text = projection.page_text
        new_origin = (self.geometry.text_pos_x, self.geometry.text_pos_y)

        # Collect annotation IDs that are being applied to this page
        projection_annotation_ids: set[CrdtId] = set()
        for doc_anno in projection.annotations:
            block = doc_anno.original_rm_block
            if block and hasattr(block, "item") and hasattr(block.item, "item_id"):
                projection_annotation_ids.add(block.item.item_id)

        for doc_anno in projection.annotations:
            block = doc_anno.original_rm_block
            if block is None:
                logger.warning(f"Annotation {doc_anno.annotation_id} has no original block")
                continue

            block_type = type(block).__name__

            if "Glyph" in block_type:
                # Highlight - adjust coordinates for this page
                adjusted_block = self._adjust_highlight_for_projection(
                    block,
                    page_text,
                    new_origin,
                    doc_anno.anchor_context,
                )
                if adjusted_block:
                    ctx.annotations.append(adjusted_block)

            elif "Line" in block_type:
                # Stroke - keep original coordinates (relative to text anchor)
                ctx.annotations.append(block)

                # Handle TreeNodeBlock for strokes
                if doc_anno.original_tree_node:
                    # Calculate target char offset in page text
                    target_offset = self._calculate_annotation_page_offset(
                        doc_anno.anchor_context, page_text
                    )
                    # Store ORIGINAL TreeNodeBlock, SceneGroupItemBlock, and SceneTreeBlock - roundtrip code will reanchor/inject them
                    # (Don't reanchor here to avoid double-reanchoring)
                    node_id = (
                        doc_anno.original_tree_node.group.node_id
                        if hasattr(doc_anno.original_tree_node, "group")
                        and doc_anno.original_tree_node.group
                        else None
                    )
                    logger.debug(
                        f"Adding to ctx.tree_nodes for page {projection.page_index}: "
                        f"node_id={node_id}, scene_tree_block={'YES' if doc_anno.original_scene_tree_block else 'MISSING'}, "
                        f"scene_group_item={'YES' if doc_anno.original_scene_group_item else 'MISSING'}"
                    )
                    ctx.tree_nodes.append(
                        (
                            doc_anno.original_tree_node,
                            target_offset,
                            doc_anno.original_scene_group_item,
                            doc_anno.original_scene_tree_block,
                        )
                    )

            else:
                # Unknown type - keep as-is
                ctx.annotations.append(block)

        # Set source .rm path and populate exclude_ids
        if projection.page_uuid in uuid_to_rm_path:
            source_rm_path = uuid_to_rm_path[projection.page_uuid]
            ctx.source_rm_path = source_rm_path

            # Extract annotation IDs from the source .rm file
            # Annotations NOT in projection_annotation_ids have moved to other pages
            if source_rm_path.exists():
                try:
                    with open(source_rm_path, "rb") as f:
                        source_blocks = list(rmscene.read_blocks(f))

                    source_annotation_ids: set[CrdtId] = set()
                    source_tree_node_ids: set[CrdtId] = set()

                    for block in source_blocks:
                        # Collect annotation item IDs
                        if hasattr(block, "item") and hasattr(block.item, "item_id"):
                            source_annotation_ids.add(block.item.item_id)

                        # Collect user TreeNodeBlock node IDs (part1 == 2)
                        # System nodes (0:1, 0:11) should never be excluded
                        block_type = type(block).__name__
                        if block_type == "TreeNodeBlock":
                            if hasattr(block, "group") and block.group:
                                node_id = block.group.node_id
                                if node_id and node_id.part1 == 2:  # Only user nodes
                                    source_tree_node_ids.add(node_id)

                    # Annotations in source but not in projection have moved to other pages
                    ctx.exclude_ids = source_annotation_ids - projection_annotation_ids

                    # TreeNodeBlocks for cross-page strokes should also be excluded
                    projection_tree_node_ids = {
                        tn.group.node_id
                        for tn, _, _, _ in ctx.tree_nodes
                        if hasattr(tn, "group") and tn.group
                    }
                    ctx.exclude_tree_node_ids = source_tree_node_ids - projection_tree_node_ids

                    if ctx.exclude_ids:
                        logger.debug(
                            f"Excluding {len(ctx.exclude_ids)} annotations that moved to other pages"
                        )
                    if ctx.exclude_tree_node_ids:
                        logger.debug(
                            f"Excluding {len(ctx.exclude_tree_node_ids)} TreeNodeBlocks that moved to other pages"
                        )

                except Exception as e:
                    logger.warning(f"Failed to read source .rm file for exclude_ids: {e}")

        if ctx.annotations:
            ctx.has_same_page = True
            logger.debug(
                f"Applied {len(ctx.annotations)} annotations to page {projection.page_index}"
            )

    def _adjust_highlight_for_projection(
        self,
        block,
        page_text: str,
        new_origin: tuple[float, float],
        anchor_context: AnchorContext,
    ):
        """Adjust highlight rectangles for a target page.

        Uses the anchor context to find where the highlighted text is in the
        page text, then recalculates rectangle positions.

        For cross-page movement (V2 architecture), we recalculate positions
        from scratch based on the target page layout rather than using deltas.

        Args:
            block: SceneGlyphItemBlock containing highlight
            page_text: Text content of the target page
            new_origin: (x, y) origin of the page text
            anchor_context: AnchorContext with highlight position info

        Returns:
            Adjusted block or None if highlight can't be placed
        """
        from rmscene import scene_items as si

        if not hasattr(block.item, "value"):
            return block

        glyph_value = block.item.value
        highlight_text = getattr(glyph_value, "text", "") or ""

        if not highlight_text:
            return block

        # Find where this highlight is in the page text
        offset = page_text.find(highlight_text)
        if offset == -1:
            # Try anchor context text
            offset = page_text.find(anchor_context.text_content)
            if offset == -1:
                logger.warning(f"Could not find highlight '{highlight_text[:30]}...' in page")
                return None

        # Get original rectangles for shape preservation
        if not hasattr(glyph_value, "rectangles") or not glyph_value.rectangles:
            return block

        original_rects = glyph_value.rectangles
        original_height = original_rects[0].h if original_rects else self.geometry.line_height

        # Calculate new highlight rectangles using layout engine
        end_offset = offset + len(highlight_text)
        new_rects = self.layout_engine.calculate_highlight_rectangles(
            offset, end_offset, page_text, new_origin, self.geometry.text_width
        )

        if not new_rects:
            logger.warning(
                f"Failed to calculate rectangles for highlight '{highlight_text[:30]}...'"
            )
            return block

        # Replace rectangles with newly calculated ones (preserving original height)
        glyph_value.rectangles.clear()
        for x, y, w, h in new_rects:
            glyph_value.rectangles.append(si.Rectangle(x, y, w, original_height))

        # Update offset fields
        glyph_value.start = offset
        glyph_value.length = len(highlight_text)

        if hasattr(block, "extra_value_data") and block.extra_value_data:
            # Update CRDT anchor if needed
            crdt_base_id = 16  # Default
            block.extra_value_data = update_glyph_extra_value_data(
                block.extra_value_data, offset, len(highlight_text), crdt_base_id
            )

        logger.debug(
            f"Placed highlight '{highlight_text[:20]}...' at offset={offset}, "
            f"{len(new_rects)} rect(s)"
        )

        return block

    def _calculate_annotation_page_offset(
        self,
        anchor_context: AnchorContext,
        page_text: str,
    ) -> int:
        """Calculate character offset for an annotation in page text.

        Args:
            anchor_context: AnchorContext with annotation position info
            page_text: Text content of the target page

        Returns:
            Character offset in page text, or 0 if not found
        """
        # Try to find the anchor's text in the page
        pos = page_text.find(anchor_context.text_content)
        if pos != -1:
            return pos

        # Try diff anchor
        if anchor_context.diff_anchor:
            span = anchor_context.diff_anchor.resolve_in(page_text)
            if span:
                return span[0]

        # Fallback: Use Y position hint to approximate anchor
        # This handles strokes that moved to different pages where their
        # original anchor text no longer exists
        if anchor_context.y_position_hint is not None:
            from rock_paper_sync.layout import LayoutContext, TextAreaConfig

            # Create layout context for this page
            layout_ctx = LayoutContext.from_text(
                page_text,
                use_font_metrics=True,
                config=TextAreaConfig(
                    text_width=self.geometry.text_width,
                    text_pos_x=self.geometry.text_pos_x,
                    text_pos_y=self.geometry.text_pos_y,
                ),
            )

            # Convert Y position to approximate offset
            offset = layout_ctx.position_to_offset(0, anchor_context.y_position_hint)
            offset = max(0, min(offset, len(page_text) - 1))

            logger.debug(
                f"Using Y-position fallback for stroke anchor: y={anchor_context.y_position_hint:.1f} "
                f"-> offset={offset}"
            )
            return offset

        logger.warning("Failed to resolve annotation anchor, defaulting to offset=0")
        return 0

    def _match_rm_files_to_pages(
        self, existing_rm_files: list[Path | None], pages: list[RemarkablePage]
    ) -> list[Path | None]:
        """Match existing .rm files to new pages by content similarity.

        This fixes the cross-page anchor bug where .rm files are passed in
        OLD document order (sorted by UUID) but need to be matched to NEW
        document pages by content.

        Uses Jaccard similarity on word sets to find the best match for each
        new page. Each .rm file can only be used once (greedy matching).

        Args:
            existing_rm_files: List of .rm file paths in original order
            pages: List of newly generated pages

        Returns:
            Reordered list where matched_rm_files[i] is the .rm file for pages[i]
        """
        if not existing_rm_files or not pages:
            return existing_rm_files

        # Extract text from each .rm file
        rm_texts: list[tuple[Path | None, str]] = []
        for rm_path in existing_rm_files:
            if rm_path and Path(rm_path).exists():
                _, _, page_text = self._extract_text_blocks_from_rm(rm_path)
                rm_texts.append((rm_path, page_text))
            else:
                rm_texts.append((rm_path, ""))

        # Get text from each new page
        page_texts = []
        for page in pages:
            page_text = "\n".join(tb.content for tb in page.text_blocks)
            page_texts.append(page_text)

        # Match each new page to the best .rm file using Jaccard similarity
        matched_rm_files: list[Path | None] = [None] * len(pages)
        used_rm_indices: set[int] = set()

        for page_idx, page_text in enumerate(page_texts):
            if not page_text.strip():
                continue

            page_words = set(page_text.lower().split())
            if not page_words:
                continue

            best_rm_idx = None
            best_score = 0.0

            for rm_idx, (rm_path, rm_text) in enumerate(rm_texts):
                if rm_idx in used_rm_indices:
                    continue
                if not rm_text.strip():
                    continue

                rm_words = set(rm_text.lower().split())
                if not rm_words:
                    continue

                # Jaccard similarity
                intersection = len(page_words & rm_words)
                union = len(page_words | rm_words)
                score = intersection / union if union > 0 else 0.0

                if score > best_score:
                    best_score = score
                    best_rm_idx = rm_idx

            # Require minimum similarity threshold
            if best_rm_idx is not None and best_score > 0.3:
                matched_rm_files[page_idx] = rm_texts[best_rm_idx][0]
                used_rm_indices.add(best_rm_idx)
                logger.debug(
                    f"Matched new page {page_idx} to .rm file {best_rm_idx} "
                    f"(similarity={best_score:.2f})"
                )

        # Log any unmatched .rm files (may contain orphaned annotations)
        unmatched_rm = [
            i for i in range(len(rm_texts)) if i not in used_rm_indices and rm_texts[i][1].strip()
        ]
        if unmatched_rm:
            logger.warning(
                f"{len(unmatched_rm)} .rm file(s) with content couldn't be matched to new pages"
            )

        return matched_rm_files

    def _preserve_annotations_with_document_model(
        self,
        pages: list[RemarkablePage],
        existing_rm_files: list[Path | None],
    ) -> None:
        """Preserve annotations using DocumentModel-based migration (V2 architecture).

        This method replaces the old AnnotationPreserver with a cleaner flow:
        1. Build DocumentModel from existing .rm files (extracts annotations)
        2. Build DocumentModel from new pages (content only)
        3. Migrate annotations using AnchorContext-based resolution
        4. Adjust annotation blocks to new positions
        5. Assign adjusted annotations to pages via PageAnnotationContext

        Args:
            pages: List of newly generated pages (will be modified in-place)
            existing_rm_files: List of .rm file paths matching old pages
        """
        from .annotations.document_model import Paragraph as DocParagraph

        # Filter to only existing files
        valid_rm_files = [p for p in existing_rm_files if p and Path(p).exists()]
        if not valid_rm_files:
            logger.debug("No valid .rm files for annotation preservation")
            return

        # Build document model from existing .rm files
        old_model = DocumentModel.from_rm_files(valid_rm_files, self.geometry)
        if not old_model.annotations:
            logger.debug("No annotations found in existing .rm files")
            return

        logger.info(f"Extracted {len(old_model.annotations)} annotations from existing .rm files")

        # Build document model from new pages
        new_paragraphs = []
        for page in pages:
            for tb in page.text_blocks:
                para = DocParagraph(
                    content=tb.content,
                    paragraph_type="paragraph",
                    char_start=tb.char_start or 0,
                    char_end=tb.char_end or len(tb.content),
                )
                new_paragraphs.append(para)

        new_model = DocumentModel.from_paragraphs(new_paragraphs, self.geometry)

        # Text origin for block adjustment
        new_origin = (self.geometry.text_pos_x, self.geometry.text_pos_y)

        # Migrate annotations from old to new
        migrated_model, report = old_model.migrate_annotations_to(new_model)

        logger.info(
            f"Migration report: {len(report.migrations)} migrated, {len(report.orphans)} orphaned "
            f"(success rate: {report.success_rate:.1%}, avg confidence: {report.average_confidence:.2f})"
        )

        # Route annotations to pages by finding which page contains the annotation's text
        # (Don't use project_to_pages() because it uses a different pagination algorithm)
        page_texts = ["\n".join(tb.content for tb in page.text_blocks) for page in pages]

        # For each annotation, find which page contains it
        annotations_by_page: dict[int, list[DocumentAnnotation]] = {
            i: [] for i in range(len(pages))
        }

        for doc_anno in migrated_model.annotations:
            anchor = doc_anno.anchor_context

            # Find which page contains this annotation's text
            target_page = None
            for page_idx, page_text in enumerate(page_texts):
                if anchor.text_content in page_text:
                    target_page = page_idx
                    break

            # Fallback: fuzzy match
            if target_page is None:
                import difflib

                best_page = None
                best_score = 0.0
                for page_idx, page_text in enumerate(page_texts):
                    matcher = difflib.SequenceMatcher(None, anchor.text_content, page_text)
                    match = matcher.find_longest_match(
                        0, len(anchor.text_content), 0, len(page_text)
                    )
                    score = match.size / len(anchor.text_content) if anchor.text_content else 0
                    if score > best_score:
                        best_score = score
                        best_page = page_idx

                if best_page is not None and best_score > 0.5:
                    target_page = best_page
                    logger.debug(
                        f"Fuzzy matched '{anchor.text_content[:30]}...' to page {target_page} (score={best_score:.2f})"
                    )

            if target_page is not None:
                annotations_by_page[target_page].append(doc_anno)
                logger.debug(
                    f"Routed {doc_anno.annotation_type} '{anchor.text_content[:30]}...' to page {target_page}"
                )
            else:
                logger.warning(
                    f"Could not find page for {doc_anno.annotation_type} '{anchor.text_content[:30]}...'"
                )

        # Assign annotations to pages via annotation_context
        for page_idx, page in enumerate(pages):
            page_annotations = annotations_by_page.get(page_idx, [])
            if not page_annotations:
                continue

            # Create annotation context for this page
            ctx = PageAnnotationContext()

            # Get PAGE-level text for this page (not document-level)
            page_text = page_texts[page_idx]

            # Process each annotation - ADJUST blocks to new positions
            for doc_anno in page_annotations:
                if not doc_anno.original_rm_block:
                    continue

                block = doc_anno.original_rm_block
                block_type = type(block).__name__

                # Adjust highlight (Glyph) blocks
                if "Glyph" in block_type:
                    # For highlight adjustment, we need to use PAGE-level text
                    # not document-level text, because rectangles are page-relative.
                    #
                    # Strategy: Find the highlighted text in the page and adjust
                    # relative to the PAGE origin, not document origin.
                    adjusted_block = self._adjust_glyph_for_page(
                        block,
                        page_text,
                        new_origin,
                        doc_anno.anchor_context,
                    )
                    ctx.annotations.append(adjusted_block)

                # Adjust stroke (Line) blocks
                elif "Line" in block_type:
                    # Strokes keep their original coordinates (relative to text anchor)
                    # The TreeNodeBlock anchor handles positioning
                    ctx.annotations.append(block)

                    # Handle TreeNodeBlock for strokes
                    if doc_anno.original_tree_node:
                        # Calculate target char offset in page text
                        target_offset = self._calculate_annotation_offset(doc_anno, page)
                        ctx.tree_nodes.append(
                            (
                                doc_anno.original_tree_node,
                                target_offset,
                                doc_anno.original_scene_group_item,
                            )
                        )

                else:
                    # Unknown type - keep as-is
                    ctx.annotations.append(block)

            # Determine source path for roundtrip
            # Use the first matching .rm file for this page
            for rm_path in existing_rm_files:
                if rm_path and rm_path.exists():
                    ctx.source_rm_path = rm_path
                    break

            if ctx.annotations:
                ctx.has_same_page = True

            page.annotation_context = ctx

            if ctx.annotations:
                logger.debug(
                    f"Page {page_idx}: assigned {len(ctx.annotations)} annotations, "
                    f"{len(ctx.tree_nodes)} TreeNodeBlocks"
                )

    def _calculate_annotation_offset(
        self,
        doc_anno: DocumentAnnotation,
        page: RemarkablePage,
    ) -> int:
        """Calculate character offset for an annotation on a page.

        Uses the anchor context to find the best position in the page text.
        """
        # Get page text
        page_text = "\n".join(tb.content for tb in page.text_blocks)

        # Try to find the annotation's text in the page
        anchor = doc_anno.anchor_context
        pos = page_text.find(anchor.text_content)
        if pos != -1:
            return pos

        # Fallback: use paragraph index
        if anchor.paragraph_index is not None:
            offset = 0
            for i, tb in enumerate(page.text_blocks):
                if i >= anchor.paragraph_index:
                    return offset
                offset += len(tb.content) + 1  # +1 for newline

        # Default to 0
        return 0

    def _adjust_glyph_for_page(
        self,
        glyph_block,
        page_text: str,
        page_origin: tuple[float, float],
        anchor_context: AnchorContext,
    ):
        """Adjust highlight rectangles for a target page.

        Unlike _adjust_glyph_with_content_anchoring which uses document-level
        text and calculates deltas, this method recalculates positions from
        scratch for the target page. This is necessary when highlights move
        cross-page, as the coordinate system changes.

        Args:
            glyph_block: SceneGlyphItemBlock containing highlight rectangles
            page_text: Text content of the target page
            page_origin: (x, y) origin of text on target page
            anchor_context: AnchorContext with resolved position info

        Returns:
            Modified glyph_block with rectangles positioned for target page
        """
        # Get highlight text and rectangles
        if not hasattr(glyph_block.item, "value"):
            return glyph_block

        glyph_value = glyph_block.item.value
        highlight_text = getattr(glyph_value, "text", "") or ""

        if not highlight_text or not hasattr(glyph_value, "rectangles"):
            return glyph_block

        # Find the highlighted text in the page
        # Try exact match first, then fuzzy match
        text_offset = page_text.find(highlight_text)

        if text_offset == -1:
            # Try anchor context text content
            text_offset = page_text.find(anchor_context.text_content)

        if text_offset == -1:
            # Fuzzy match - find closest match
            import difflib

            matcher = difflib.SequenceMatcher(None, highlight_text, page_text)
            match = matcher.find_longest_match(0, len(highlight_text), 0, len(page_text))
            if match.size >= len(highlight_text) * 0.6:
                text_offset = match.b
            else:
                logger.warning(
                    f"Could not find '{highlight_text[:30]}...' in page (page has {len(page_text)} chars), "
                    f"anchor_context.text_content='{anchor_context.text_content[:50]}...'"
                )
                logger.debug(f"Page text snippet: '{page_text[:200]}...'")
                return glyph_block

        # Calculate new rectangles using layout engine
        end_offset = text_offset + len(highlight_text)
        new_rects = self.layout_engine.calculate_highlight_rectangles(
            text_offset,
            end_offset,
            page_text,
            page_origin,
            self.geometry.text_width,
        )

        if not new_rects:
            logger.warning(f"Failed to calculate rectangles for '{highlight_text[:30]}...'")
            return glyph_block

        # Preserve original rectangle properties where possible
        original_height = (
            glyph_value.rectangles[0].h if glyph_value.rectangles else self.geometry.line_height
        )

        # Update rectangles
        glyph_value.rectangles.clear()
        for x, y, w, _ in new_rects:
            glyph_value.rectangles.append(si.Rectangle(x, y, w, original_height))

        # Update start field for older firmware
        glyph_value.start = text_offset

        # Update text field if needed
        new_text = page_text[text_offset:end_offset]
        if new_text:
            glyph_value.text = new_text
            glyph_value.length = len(new_text)

        # Update extra_value_data for CRDT anchoring (firmware 3.6+)
        if hasattr(glyph_block, "extra_value_data") and glyph_block.extra_value_data:
            glyph_block.extra_value_data = update_glyph_extra_value_data(
                glyph_block.extra_value_data, text_offset, len(highlight_text), crdt_base_id=16
            )

        logger.debug(
            f"Adjusted highlight '{highlight_text[:30]}...' to offset {text_offset} on page"
        )

        return glyph_block

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
            text_origin_y = self.geometry.text_pos_y  # Default to constant
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
                    from .layout import LayoutContext, TextAreaConfig

                    layout_ctx = LayoutContext.from_text(
                        full_text,
                        use_font_metrics=True,
                        config=TextAreaConfig(
                            text_width=self.geometry.text_width,
                            text_pos_x=self.geometry.text_pos_x,
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
                                    char_start=para_start,
                                    char_end=para_end,
                                )
                            )

            return text_blocks, text_origin_y, full_text

        except Exception as e:
            logger.warning(f"Failed to extract text blocks from {rm_file_path}: {e}")
            return [], self.geometry.text_pos_y, ""

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
                old_offset, old_text, old_origin, self.geometry.text_width
            )
            new_x_model, new_y_model = self.layout_engine.offset_to_position(
                new_offset, new_text, new_origin, self.geometry.text_width
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
            new_offset, new_end_offset, new_text, new_origin, self.geometry.text_width
        )
        new_rect_count = len(new_rects)

        if new_rect_count != old_rect_count:
            # REFLOW CASE: Highlight now spans different number of lines
            # Use delta-based positioning for accuracy (font metric errors cancel out)
            logger.debug(f"  Reflow detected: {old_rect_count} rect(s) → {new_rect_count} rect(s)")

            # Preserve original rectangle properties
            original_rect = glyph_value.rectangles[0] if glyph_value.rectangles else None
            original_height = original_rect.h if original_rect else self.geometry.line_height

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
            # - X: Either at line start (self.geometry.text_pos_x) or relative position within line
            # - Y: Previous line Y + original_height (highlight rectangles are contiguous)
            #
            # We detect line-start by checking if layout X is close to text origin
            line_start_x = new_origin[0]  # self.geometry.text_pos_x
            tolerance = 10.0  # Allow small deviation

            for i, (x, y, w, _) in enumerate(new_rects[1:], start=1):
                # Check if this rectangle starts at line beginning
                is_line_start = abs(x - line_start_x) < tolerance

                if is_line_start:
                    # Rectangle at line start: use self.geometry.text_pos_x directly
                    # This avoids font metric errors in X calculation
                    rect_x = self.geometry.text_pos_x
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

    def _reanchor_tree_node_for_cross_page(
        self,
        tree_node,
        target_char_offset: int,
        target_page: "RemarkablePage",
    ):
        """Recalculate TreeNodeBlock anchor_id for cross-page stroke movement.

        When a stroke moves to a different page, its TreeNodeBlock anchor_id
        needs to point to text on the NEW page, not the old page. This method
        sets the anchor to the pre-calculated character offset.

        The anchor_id.value is CrdtId(part1, part2) where:
        - part1: Author/origin ID (typically 1 for text-anchored, 0 for sentinel)
        - part2: Character offset into the RootTextBlock text (NOT combined with CRDT base)

        Special case: Margin notes use sentinel anchor_id with part1=0 and
        part2=END_OF_DOC_ANCHOR_MARKER. These must be preserved unchanged.

        Args:
            tree_node: Original TreeNodeBlock
            target_char_offset: Pre-calculated character offset for target page
            target_page: Target page to anchor to

        Returns:
            New TreeNodeBlock with recalculated anchor_id
        """
        if not hasattr(tree_node, "group") or not tree_node.group:
            return tree_node

        g = tree_node.group
        if not hasattr(g, "anchor_id") or not g.anchor_id or not g.anchor_id.value:
            return tree_node

        old_anchor = g.anchor_id
        old_offset = old_anchor.value.part2

        # Check for sentinel anchor (margin notes, non-text-anchored strokes)
        # These have anchor_id.part1 = 0 and part2 = END_OF_DOC_ANCHOR_MARKER
        # They should be preserved unchanged - Y positioning comes from stroke coords
        # NOTE: Check the ORIGINAL anchor, not the target offset!
        if old_offset == END_OF_DOC_ANCHOR_MARKER:
            logger.debug(
                f"Preserving sentinel anchor for cross-page TreeNodeBlock {g.node_id} "
                f"(margin note or non-text-anchored stroke)"
            )
            return tree_node

        # Create new anchor_id with the pre-calculated offset
        # The anchor's part2 is simply the character offset into the RootTextBlock text,
        # NOT combined with crdt_base_id (that's for CRDT sequence items, not text anchors)
        new_anchor_value = CrdtId(old_anchor.value.part1, target_char_offset)
        new_anchor_lww = LwwValue(timestamp=old_anchor.timestamp, value=new_anchor_value)

        # Create new Group with updated anchor_id
        new_group = replace(g, anchor_id=new_anchor_lww)

        # Create new TreeNodeBlock with updated group
        new_block = replace(tree_node, group=new_group)

        logger.debug(
            f"Reanchored cross-page TreeNodeBlock {g.node_id}: "
            f"anchor_id {old_offset} -> {target_char_offset}"
        )

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
        y_position = float(self.geometry.text_pos_y)  # Track Y for annotation mapping

        for block in blocks:
            block_lines = self.estimate_block_lines(block)
            block.page_y_start = y_position  # Set Y position for annotation mapping
            block.page_index = len(pages)  # Track which page this block is on

            # Check if header should start new page (avoid orphan headers)
            if block.type == BlockType.HEADER and current_page:
                remaining_space = self.geometry.lines_per_page - current_lines
                if remaining_space < 10:  # Less than 10 lines remaining
                    pages.append(current_page)
                    current_page = []
                    current_lines = 0
                    y_position = float(self.geometry.text_pos_y)
                    block.page_y_start = y_position
                    block.page_index = len(pages)  # Update to new page

            # Check if block fits on current page
            if current_lines + block_lines > self.geometry.lines_per_page:
                # Block doesn't fit on current page
                is_paragraph = block.type == BlockType.PARAGRAPH
                is_oversized = block_lines > self.geometry.lines_per_page
                should_split = is_paragraph and (
                    self.layout.allow_paragraph_splitting or is_oversized
                )

                if should_split:
                    # Split paragraph using layout engine
                    # Only fill remaining page space when splitting is explicitly allowed
                    # For forced oversized splits, start on a new page
                    if self.layout.allow_paragraph_splitting:
                        remaining_lines = self.geometry.lines_per_page - current_lines
                        chunks = self.layout_engine.split_for_pages(
                            block.text,
                            self.geometry.lines_per_page,
                            first_chunk_lines=remaining_lines,
                        )
                    else:
                        # Forced split (oversized) - start on new page with full-page chunks
                        if current_page:
                            pages.append(current_page)
                        current_page = []
                        current_lines = 0
                        y_position = float(self.geometry.text_pos_y)
                        chunks = self.layout_engine.split_for_pages(
                            block.text, self.geometry.lines_per_page
                        )

                    for i, chunk_text in enumerate(chunks):
                        chunk_lines = len(
                            self.layout_engine.calculate_line_breaks(
                                chunk_text, self.geometry.text_width
                            )
                        )

                        # Start new page after first chunk (first chunk fits by design)
                        if i > 0:
                            if current_page:
                                pages.append(current_page)
                            current_page = []
                            current_lines = 0
                            y_position = float(self.geometry.text_pos_y)

                        chunk_block = ContentBlock(
                            type=block.type,
                            level=block.level,
                            text=chunk_text,
                            formatting=block.formatting if i == 0 else [],
                            page_index=len(pages),  # Track which page this chunk is on
                        )
                        current_page.append(chunk_block)
                        current_lines += chunk_lines
                        y_position += chunk_lines * self.line_height
                else:
                    # Atomic block placement - start new page
                    if current_page:
                        pages.append(current_page)
                    current_page = [block]
                    current_lines = block_lines
                    y_position = float(self.geometry.text_pos_y) + block_lines * self.line_height
                    block.page_y_start = float(self.geometry.text_pos_y)
                    block.page_index = len(pages)  # Update to new page
            else:
                current_page.append(block)
                current_lines += block_lines
                y_position += block_lines * self.line_height

        # Don't forget the last page
        if current_page:
            pages.append(current_page)

        logger.info(
            f"Paginated {len(blocks)} blocks into {len(pages)} page(s), "
            f"target lines per page: {self.geometry.lines_per_page}"
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
        line_breaks = self.layout_engine.calculate_line_breaks(text, self.geometry.text_width)
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

        Note: Uses self.geometry.text_pos_y constant (94.0) for Y positioning to match
        the coordinate system used by RootTextBlock in rmscene. This ensures
        consistency between text generation and extraction for annotation
        preservation.

        Args:
            blocks: Content blocks for a single page

        Returns:
            Tuple of (text_items, text_blocks) where text_blocks include Y-coordinates
            for annotation mapping
        """
        from .layout import LayoutContext, TextAreaConfig

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
            config=TextAreaConfig(
                text_width=self.geometry.text_width,
                text_pos_x=self.geometry.text_pos_x,
                text_pos_y=self.geometry.text_pos_y,
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
                    page_index=block.page_index if block.page_index is not None else 0,
                    char_start=current_offset,
                    char_end=para_end,
                )
            )

            current_offset = para_end + 1  # +1 for newline

        return items, text_blocks

    def _reorder_blocks_for_device(self, blocks: list) -> list:
        """Reorder blocks to match device-expected format.

        The reMarkable device requires strict block ordering:
        1. Header blocks (AuthorIdsBlock, MigrationInfoBlock, PageInfoBlock, SceneInfo)
        2. All SceneTreeBlocks
        3. RootTextBlock
        4. All TreeNodeBlocks
        5. All SceneGroupItemBlocks
        6. All annotation blocks (SceneLineItemBlock, SceneGlyphItemBlock)

        This function takes a list of blocks in any order and returns them
        in the correct order for device compatibility.
        """
        # Categorize blocks by type
        header_blocks = []  # AuthorIds, MigrationInfo, PageInfo, SceneInfo
        scene_tree_blocks = []
        root_text_block = None
        tree_node_blocks = []
        scene_group_item_blocks = []
        annotation_blocks = []  # SceneLineItemBlock, SceneGlyphItemBlock

        for block in blocks:
            block_type = type(block).__name__

            if block_type in ["AuthorIdsBlock", "MigrationInfoBlock", "PageInfoBlock", "SceneInfo"]:
                header_blocks.append(block)
            elif block_type == "SceneTreeBlock":
                scene_tree_blocks.append(block)
            elif block_type == "RootTextBlock":
                root_text_block = block
            elif block_type == "TreeNodeBlock":
                tree_node_blocks.append(block)
            elif block_type == "SceneGroupItemBlock":
                scene_group_item_blocks.append(block)
            elif block_type in ["SceneLineItemBlock", "SceneGlyphItemBlock"]:
                annotation_blocks.append(block)
            else:
                # Unknown block type - add to header (preserves order)
                header_blocks.append(block)

        # Reconstruct in correct order
        result = []
        result.extend(header_blocks)
        result.extend(scene_tree_blocks)
        if root_text_block:
            result.append(root_text_block)
        result.extend(tree_node_blocks)
        result.extend(scene_group_item_blocks)
        result.extend(annotation_blocks)

        return result

    def generate_rm_file(self, page: RemarkablePage) -> bytes:
        """Generate binary .rm file content with custom text width.

        Uses annotation_context to determine generation strategy:
        - If context has source_rm_path: roundtrip to preserve scene tree
        - Otherwise: create from scratch

        Args:
            page: RemarkablePage with positioned text items

        Returns:
            Binary .rm file content

        Note:
            Uses custom scene tree construction to set text width to 750px,
            which displays at 1.0x zoom on the Paper Pro (vs 0.8x with the
            default 936px width from simple_text_document).
        """
        ctx = page.annotation_context

        # Roundtrip if we have a source file (for annotation preservation)
        if ctx and ctx.source_rm_path and ctx.source_rm_path.exists():
            return self._generate_rm_file_roundtrip(page)

        # Create from scratch (no annotations or no source file)
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
            page: RemarkablePage with annotation_context

        Returns:
            Binary .rm file content with preserved structure
        """
        ctx = page.annotation_context

        # Read all blocks from original file
        with open(str(ctx.source_rm_path), "rb") as f:
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

        # Get IDs of annotations that moved OUT of this page (should be excluded)
        if ctx.exclude_ids:
            logger.debug(f"Excluding {len(ctx.exclude_ids)} annotations that moved to other pages")

        # Build index of adjusted annotations by item_id
        # Also track which are from the same file (can be matched) vs cross-page (must inject)
        adjusted_by_id = {}
        cross_page_to_inject = []
        for adj_block in ctx.annotations:
            if hasattr(adj_block, "item") and hasattr(adj_block.item, "item_id"):
                item_id = adj_block.item.item_id
                if item_id in original_annotation_ids:
                    # Same file - can match by ID
                    adjusted_by_id[item_id] = adj_block
                else:
                    # Cross-page - needs injection
                    cross_page_to_inject.append(adj_block)
            else:
                # No item_id - inject as cross-page
                cross_page_to_inject.append(adj_block)

        if cross_page_to_inject:
            logger.debug(f"Cross-page annotations to inject: {len(cross_page_to_inject)}")

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
                    # Skip annotations that moved to other pages
                    if item_id in ctx.exclude_ids:
                        logger.debug(f"Excluding annotation {item_id} (moved to another page)")
                        continue
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
                # Skip TreeNodeBlocks that moved to other pages
                node_id = block.group.node_id if hasattr(block, "group") and block.group else None
                if node_id and node_id in ctx.exclude_tree_node_ids:
                    logger.debug(f"Excluding TreeNodeBlock {node_id} (moved to another page)")
                    continue

                # Also skip if this TreeNodeBlock will be injected as cross-page
                # (to avoid duplicates with different anchor values)
                cross_page_node_ids = {
                    tn.group.node_id for tn, _, _, _ in ctx.tree_nodes if tn.group
                }
                if node_id and node_id in cross_page_node_ids:
                    logger.debug(
                        f"Skipping TreeNodeBlock {node_id} (will be injected as cross-page)"
                    )
                    continue

                if anchor_offset_delta != 0:
                    modified_block = self._update_tree_node_anchor(block, anchor_offset_delta)
                    modified_blocks.append(modified_block)
                else:
                    modified_blocks.append(block)

            # Filter SceneTreeBlock for excluded tree node IDs
            elif block_type == "SceneTreeBlock":
                # Skip SceneTreeBlocks for nodes that moved to other pages
                tree_id = block.tree_id if hasattr(block, "tree_id") else None
                if tree_id and tree_id in ctx.exclude_tree_node_ids:
                    logger.debug(f"Excluding SceneTreeBlock {tree_id} (moved to another page)")
                    continue
                # Also skip if this will be injected as cross-page
                cross_page_node_ids = {
                    tn.group.node_id for tn, _, _, _ in ctx.tree_nodes if tn.group
                }
                if tree_id and tree_id in cross_page_node_ids:
                    logger.debug(
                        f"Skipping SceneTreeBlock {tree_id} (will be injected as cross-page)"
                    )
                    continue
                modified_blocks.append(block)

            # Filter SceneGroupItemBlock for excluded tree node IDs
            elif block_type == "SceneGroupItemBlock":
                # Skip SceneGroupItemBlocks for nodes that moved to other pages
                value_id = (
                    block.item.value
                    if hasattr(block, "item") and hasattr(block.item, "value")
                    else None
                )
                if value_id and value_id in ctx.exclude_tree_node_ids:
                    logger.debug(
                        f"Excluding SceneGroupItemBlock {value_id} (moved to another page)"
                    )
                    continue
                # Also skip if this will be injected as cross-page
                cross_page_node_ids = {
                    tn.group.node_id for tn, _, _, _ in ctx.tree_nodes if tn.group
                }
                if value_id and value_id in cross_page_node_ids:
                    logger.debug(
                        f"Skipping SceneGroupItemBlock {value_id} (will be injected as cross-page)"
                    )
                    continue
                modified_blocks.append(block)

            # Keep all other blocks unchanged
            else:
                modified_blocks.append(block)

        # Inject cross-page TreeNodeBlocks FIRST (strokes reference them via parent_id)
        # Reanchor them to point to correct text positions on this page
        if ctx.tree_nodes:
            logger.warning(f"INJECTING {len(ctx.tree_nodes)} TreeNodeBlocks to page")
            for tree_node, target_char_offset, scene_group_item, scene_tree_block in ctx.tree_nodes:
                reanchored_node = self._reanchor_tree_node_for_cross_page(
                    tree_node, target_char_offset, page
                )
                modified_blocks.append(reanchored_node)

                if hasattr(tree_node, "group") and tree_node.group:
                    node_id = tree_node.group.node_id

                    # Inject SceneTreeBlock to declare this node in the scene tree
                    # This MUST come before SceneGroupItemBlock that references it
                    new_scene_tree_block = SceneTreeBlock(
                        tree_id=node_id,
                        node_id=CrdtId(0, 0),
                        is_update=True,
                        parent_id=CrdtId(0, 11),  # Layer 1
                    )
                    modified_blocks.append(new_scene_tree_block)
                    logger.debug(f"Injected SceneTreeBlock for TreeNode {node_id}")

                    # Create a NEW SceneGroupItemBlock to link TreeNodeBlock to scene graph
                    # The original scene_group_item has left_id/right_id referencing nodes
                    # from the source page that don't exist here. Reset them to (0,0).
                    if scene_group_item:
                        new_scene_group_item = SceneGroupItemBlock(
                            parent_id=CrdtId(0, 11),  # Layer 1
                            item=CrdtSequenceItem(
                                item_id=scene_group_item.item.item_id,  # Keep original ID
                                left_id=CrdtId(0, 0),  # Reset - no left neighbor
                                right_id=CrdtId(0, 0),  # Reset - no right neighbor
                                deleted_length=0,
                                value=node_id,  # The TreeNodeBlock we're linking
                            ),
                        )
                        modified_blocks.append(new_scene_group_item)
                        logger.debug(f"Injected SceneGroupItemBlock for TreeNode {node_id}")
            logger.info(
                f"Injected {len(ctx.tree_nodes)} cross-page TreeNodeBlocks (reanchored) with SceneTreeBlocks and SceneGroupItemBlocks"
            )
        else:
            logger.warning("NO TreeNodeBlocks to inject (ctx.tree_nodes is empty)")

        # Inject cross-page annotations that couldn't be matched by item_id
        if cross_page_to_inject:
            for inj_block in cross_page_to_inject:
                modified_blocks.append(inj_block)
                annotation_count += 1
            logger.info(f"Injected {len(cross_page_to_inject)} cross-page annotations")

        # Reorder blocks for device compatibility
        # Device requires: SceneTreeBlocks → RootTextBlock → TreeNodeBlocks → SceneGroupItemBlocks → Strokes
        modified_blocks = self._reorder_blocks_for_device(modified_blocks)

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
                    pos_x=self.geometry.text_pos_x,
                    pos_y=self.geometry.text_pos_y,
                    width=self.geometry.text_width,
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

        # Add preserved annotations (strokes and highlights) from context
        ctx = page.annotation_context
        if ctx and ctx.annotations:
            if ctx.tree_nodes:
                logger.warning(f"FROM-SCRATCH: Injecting {len(ctx.tree_nodes)} TreeNodeBlocks")

                for tree_node, target_offset, scene_group_item, scene_tree_block in ctx.tree_nodes:
                    # Reanchor TreeNodeBlock for this page
                    reanchored_node = self._reanchor_tree_node_for_cross_page(
                        tree_node, target_offset, page
                    )
                    blocks.append(reanchored_node)

                    if hasattr(tree_node, "group") and tree_node.group:
                        node_id = tree_node.group.node_id

                        # SceneTreeBlock to declare this node in the scene tree
                        new_scene_tree_block = SceneTreeBlock(
                            tree_id=node_id,
                            node_id=CrdtId(0, 0),
                            is_update=True,
                            parent_id=CrdtId(0, 11),  # Layer 1
                        )
                        blocks.append(new_scene_tree_block)
                        logger.debug(
                            f"FROM-SCRATCH: Prepared SceneTreeBlock for TreeNode {node_id}"
                        )

                        # SceneGroupItemBlock to link TreeNodeBlock to scene graph
                        if scene_group_item:
                            new_scene_group_item = SceneGroupItemBlock(
                                parent_id=CrdtId(0, 11),  # Layer 1
                                item=CrdtSequenceItem(
                                    item_id=scene_group_item.item.item_id,  # Keep original ID
                                    left_id=CrdtId(0, 0),  # Reset - no left neighbor
                                    right_id=CrdtId(0, 0),  # Reset - no right neighbor
                                    deleted_length=0,
                                    value=node_id,  # The TreeNodeBlock we're linking
                                ),
                            )
                            blocks.append(new_scene_group_item)
                            logger.debug(
                                f"FROM-SCRATCH: Prepared SceneGroupItemBlock for TreeNode {node_id}"
                            )

            # Add the annotation blocks (strokes, highlights)
            blocks.extend(ctx.annotations)
            logger.debug(f"Added {len(ctx.annotations)} preserved annotation blocks to .rm file")

        # Reorder blocks for device compatibility
        # Device requires: SceneTreeBlocks → RootTextBlock → TreeNodeBlocks → SceneGroupItemBlocks → Strokes
        blocks = self._reorder_blocks_for_device(blocks)

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
