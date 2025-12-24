"""reMarkable document generator with rmscene integration.

This module converts parsed markdown documents into reMarkable v6 format files.
It handles pagination, text positioning, and generates binary .rm files using
the rmscene library.
"""

import logging
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from pathlib import Path

import rmscene
from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtId
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
from .annotations.domain import (
    HighlightPlacement,
    PageTransformPlan,
    StrokePlacement,
)
from .annotations.merging import AnnotationMerger, MergeContext
from .annotations.scene_adapter import (
    PageTransformExecutor,
    StrokeBundle,
)
from .config import LayoutConfig as AppLayoutConfig
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
        new_model = DocumentModel.from_content_blocks(
            md_doc.content,
            self.geometry,
            allow_paragraph_splitting=self.layout.allow_paragraph_splitting,
        )

        # If we have existing .rm files, load old model and migrate annotations
        if existing_rm_files:
            valid_rm_files = [p for p in existing_rm_files if p and p.exists()]
            if valid_rm_files:
                old_model = DocumentModel.from_rm_files(valid_rm_files, self.geometry)
                logger.info(
                    f"generate_document: old_model has {len(old_model.annotations)} annotations"
                )
                if old_model.annotations:
                    merger = AnnotationMerger(resolver=ContextResolver())
                    context = MergeContext(old_model=old_model, new_model=new_model)
                    result = merger.merge(context)
                    new_model = result.merged_model
                    report = result.report
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
            logger.info(
                f"Page {projection.page_index}: projection.annotations has {len(projection.annotations) if projection.annotations else 0} items"
            )
            if projection.annotations:
                self._apply_annotations_to_page(page, projection, uuid_to_rm_path)

            # Always set source_rm_path if available (for preserving unreplaced strokes)
            if projection.page_uuid in uuid_to_rm_path:
                if page.annotation_context is None:
                    page.annotation_context = PageAnnotationContext()
                if page.annotation_context.source_rm_path is None:
                    page.annotation_context.source_rm_path = uuid_to_rm_path[projection.page_uuid]

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
                # Highlight - use HighlightHandler.relocate for delta-based positioning
                # This preserves original rectangle precision while correctly shifting positions
                from rock_paper_sync.annotations.handlers.highlight_handler import HighlightHandler

                # Get old text from source page using source_page_idx
                source_page_idx = doc_anno.source_page_idx
                old_text = ""
                old_origin = new_origin  # Default to same origin

                # Find source .rm file from uuid_to_rm_path using page index
                # The uuid_to_rm_path maps page UUIDs to .rm files
                if source_page_idx is not None and uuid_to_rm_path:
                    # Get the rm_path for the source page (UUIDs are ordered by page)
                    rm_paths = list(uuid_to_rm_path.values())
                    if source_page_idx < len(rm_paths):
                        source_rm_path = rm_paths[source_page_idx]
                        try:
                            _, old_origin_y, old_text = self._extract_text_blocks_from_rm(
                                source_rm_path
                            )
                            old_origin = (self.geometry.text_pos_x, old_origin_y)
                        except Exception as e:
                            logger.warning(f"Could not get old text from {source_rm_path}: {e}")

                if old_text:
                    # Use HighlightHandler.relocate for delta-based positioning
                    handler = HighlightHandler()
                    adjusted_block = handler.relocate(
                        block,
                        old_text,
                        page_text,
                        old_origin,
                        new_origin,
                        self.layout_engine,
                        self.geometry,
                        crdt_base_id=16,  # Default CRDT base
                    )
                else:
                    # Fallback to simple projection when no old text available
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
                    # Each stroke uses its own anchor to preserve relative X positions
                    target_offset = self._calculate_annotation_page_offset(
                        doc_anno.anchor_context, page_text
                    )

                    # Store ORIGINAL TreeNodeBlock, SceneGroupItemBlock, and SceneTreeBlock - roundtrip code will reanchor/inject them
                    node_id = (
                        doc_anno.original_tree_node.group.node_id
                        if hasattr(doc_anno.original_tree_node, "group")
                        and doc_anno.original_tree_node.group
                        else None
                    )
                    logger.debug(
                        f"Adding to ctx.tree_nodes for page {projection.page_index}: "
                        f"node_id={node_id}, target_offset={target_offset}, "
                        f"cluster={doc_anno.cluster_id or 'none'}"
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
        # Use context disambiguation if there are multiple occurrences
        offset = self._find_highlight_with_context(highlight_text, page_text, anchor_context)

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

        # IMPORTANT: Our layout engine may produce different X positions than the device
        # because text wrapping can differ between font renderers.
        # Strategy: Use calculated Y (to handle paragraph insertion), but preserve
        # original X offset from origin when the text width is similar.
        orig_rect = original_rects[0]
        orig_x_offset = orig_rect.x - new_origin[0]  # X offset from origin

        # Check if original was near line start (within ~10px of origin)
        orig_at_line_start = abs(orig_x_offset) < 15

        # Replace rectangles with calculated Y but potentially preserved X
        glyph_value.rectangles.clear()
        for i, (calc_x, calc_y, calc_w, calc_h) in enumerate(new_rects):
            if i < len(original_rects):
                orig_r = original_rects[i]
                # Preserve original X if it was meaningful (not a full recalculation)
                # Use original X when original was at line start and calculated is similar,
                # or when original X offset was near zero (highlight at line start)
                if orig_at_line_start:
                    # Original was at line start - preserve that relationship
                    # Find where this line starts and position there
                    final_x = new_origin[0]  # At origin = line start
                else:
                    # Original had meaningful X offset - preserve it
                    # This handles cases where our wrapping differs from device
                    final_x = orig_r.x
                final_y = calc_y  # Always use calculated Y for pagination
                glyph_value.rectangles.append(
                    si.Rectangle(final_x, final_y, calc_w, original_height)
                )
            else:
                # Extra rectangles (multiline highlight expanded) - use calculated
                glyph_value.rectangles.append(si.Rectangle(calc_x, calc_y, calc_w, original_height))

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

    def _find_highlight_with_context(
        self,
        highlight_text: str,
        page_text: str,
        anchor_context: AnchorContext,
    ) -> int:
        """Find highlight text in page using edit-resilient anchoring.

        Uses multiple strategies in order of preference:
        1. DiffAnchor - finds stable text before/after the highlight
        2. Context similarity - fuzzy match on context_before/context_after
        3. Simple find - first occurrence (fallback)

        Args:
            highlight_text: The highlighted text to find
            page_text: Full page text content
            anchor_context: AnchorContext with diff_anchor and context windows

        Returns:
            Character offset of highlight, or -1 if not found
        """
        import difflib

        # Strategy 1: Use DiffAnchor if available (most reliable for edits)
        if anchor_context.diff_anchor:
            span = anchor_context.diff_anchor.resolve_in(page_text)
            if span:
                logger.debug(
                    f"Found highlight '{highlight_text[:20]}...' via diff_anchor at {span[0]}"
                )
                return span[0]

        # Strategy 2: Find all occurrences and disambiguate
        candidates: list[int] = []
        start = 0
        while True:
            pos = page_text.find(highlight_text, start)
            if pos == -1:
                break
            candidates.append(pos)
            start = pos + 1

        if not candidates:
            # Try anchor context text as fallback
            start = 0
            while True:
                pos = page_text.find(anchor_context.text_content, start)
                if pos == -1:
                    break
                candidates.append(pos)
                start = pos + 1

        if not candidates:
            return -1

        if len(candidates) == 1:
            return candidates[0]

        # Multiple occurrences - disambiguate using context similarity
        best_score = 0.0
        best_offset = candidates[0]

        for offset in candidates:
            # Get context around this occurrence
            ctx_len = 50
            before = page_text[max(0, offset - ctx_len) : offset]
            after = page_text[offset + len(highlight_text) : offset + len(highlight_text) + ctx_len]

            # Compare with anchor context using fuzzy matching
            before_ratio = difflib.SequenceMatcher(
                None, before, anchor_context.context_before
            ).ratio()
            after_ratio = difflib.SequenceMatcher(None, after, anchor_context.context_after).ratio()
            score = (before_ratio + after_ratio) / 2

            if score > best_score:
                best_score = score
                best_offset = offset

        logger.debug(
            f"Disambiguated highlight '{highlight_text[:20]}...' "
            f"from {len(candidates)} candidates, score={best_score:.2f}"
        )

        return best_offset

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
        merger = AnnotationMerger(resolver=ContextResolver())
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)
        migrated_model = result.merged_model
        report = result.report

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

    def paginate_content(self, blocks: list[ContentBlock]) -> list[list[ContentBlock]]:
        """Split content blocks into pages based on line count.

        This method uses ContentPaginator for consistent pagination logic shared
        with DocumentModel. It also sets page_index and page_y_start on each
        block for annotation mapping.

        Args:
            blocks: List of content blocks to paginate

        Returns:
            List of pages, where each page is a list of content blocks

        Note:
            - Headers near the bottom of a page start a new page
            - Oversized paragraphs (>1 page) are always split
            - Empty content results in one empty page
        """
        from rock_paper_sync.layout import ContentPaginator

        paginator = ContentPaginator(
            layout_engine=self.layout_engine,
            lines_per_page=self.geometry.lines_per_page,
            allow_paragraph_splitting=self.layout.allow_paragraph_splitting,
        )
        pages = paginator.paginate(blocks)

        # Post-process: set page_index and page_y_start on each block
        for page_idx, page_blocks in enumerate(pages):
            y_position = float(self.geometry.text_pos_y)
            for block in page_blocks:
                block.page_index = page_idx
                block.page_y_start = y_position
                block_lines = self.estimate_block_lines(block)
                y_position += block_lines * self.line_height

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

    def _build_transform_plan(self, page: RemarkablePage) -> PageTransformPlan:
        """Build a PageTransformPlan from a RemarkablePage.

        Converts the legacy PageAnnotationContext into the new domain types:
        - StrokeBundles for stroke groups (TreeNodeBlock + strokes)
        - StrokePlacements with anchor offsets
        - HighlightPlacements for highlight blocks

        Args:
            page: RemarkablePage with text_items and annotation_context

        Returns:
            PageTransformPlan ready for executor
        """
        # Build page text from text items
        page_text = "\n".join(item.text for item in page.text_items)
        if not page_text.strip():
            page_text = " "

        ctx = page.annotation_context
        stroke_placements: list[StrokePlacement] = []
        highlight_placements: list[HighlightPlacement] = []

        if ctx:
            # Build StrokeBundles from ctx.tree_nodes
            # Each entry: (TreeNodeBlock, target_offset, SceneGroupItemBlock, SceneTreeBlock)
            # We need to find matching strokes from ctx.annotations by parent_id

            # Index strokes by parent_id
            strokes_by_parent: dict[CrdtId, list] = {}
            highlight_blocks: list = []

            for block in ctx.annotations:
                block_type = type(block).__name__
                if block_type == "SceneLineItemBlock":
                    parent_id = block.parent_id
                    if parent_id not in strokes_by_parent:
                        strokes_by_parent[parent_id] = []
                    strokes_by_parent[parent_id].append(block)
                elif block_type == "SceneGlyphItemBlock":
                    highlight_blocks.append(block)

            # Build StrokeBundles from tree_nodes + matching strokes
            seen_node_ids: set[CrdtId] = set()
            for tree_node, target_offset, scene_group_item, scene_tree_block in ctx.tree_nodes:
                if not hasattr(tree_node, "group") or not tree_node.group:
                    continue

                node_id = tree_node.group.node_id

                # Deduplicate - multiple strokes may reference same TreeNodeBlock
                if node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node_id)

                # Get strokes for this TreeNodeBlock
                strokes = strokes_by_parent.get(node_id, [])

                # Create StrokeBundle
                bundle = StrokeBundle(
                    node_id=node_id,
                    tree_node=tree_node,
                    scene_tree=scene_tree_block,
                    scene_group_item=scene_group_item,
                    strokes=strokes,
                )

                # Create StrokePlacement
                # Clamp offset to valid range - offset may have been calculated for
                # a different page_text representation during migration
                clamped_offset = max(0, min(target_offset, len(page_text)))
                placement = StrokePlacement(
                    opaque_handle=bundle,
                    anchor_char_offset=clamped_offset,
                )
                stroke_placements.append(placement)

            # Create HighlightPlacements
            for block in highlight_blocks:
                placement = HighlightPlacement(
                    opaque_handle=block,
                    start_offset=0,  # Will be computed by executor
                    end_offset=0,
                )
                highlight_placements.append(placement)

        return PageTransformPlan(
            page_uuid=page.uuid,
            page_text=page_text,
            stroke_placements=stroke_placements,
            highlight_placements=highlight_placements,
            source_rm_path=ctx.source_rm_path if ctx else None,
        )

    def generate_rm_file(self, page: RemarkablePage) -> bytes:
        """Generate binary .rm file content with custom text width.

        Uses PageTransformExecutor for unified .rm generation.
        This is the SINGLE code path for all .rm file generation.

        Args:
            page: RemarkablePage with positioned text items

        Returns:
            Binary .rm file content

        Note:
            Uses custom scene tree construction to set text width to 750px,
            which displays at 1.0x zoom on the Paper Pro (vs 0.8x with the
            default 936px width from simple_text_document).
        """
        # Build transformation plan from page data
        plan = self._build_transform_plan(page)

        # Execute plan to generate .rm bytes
        executor = PageTransformExecutor(self.geometry)
        rm_bytes = executor.execute(plan)

        ctx = page.annotation_context
        if ctx and ctx.source_rm_path:
            logger.info(
                f"Generated .rm file via executor: {len(rm_bytes)} bytes "
                f"({len(plan.stroke_placements)} strokes, {len(plan.highlight_placements)} highlights)"
            )
        else:
            logger.debug(f"Generated .rm file from scratch: {len(rm_bytes)} bytes")

        return rm_bytes
