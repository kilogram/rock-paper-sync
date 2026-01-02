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
from rmscene.crdt_sequence import CrdtId

from .annotations import (
    TextBlock,
)
from .annotations.document_model import (
    DocumentModel,
    PageProjection,
)
from .annotations.domain import (
    HighlightPlacement,
    PageTransformPlan,
    StrokePlacement,
)
from .annotations.scene_adapter import (
    PageTransformExecutor,
    StrokeBundle,
)
from .annotations.services.merger import AnnotationMerger, MergeContext
from .config import LayoutConfig as AppLayoutConfig
from .layout import DeviceGeometry, WordWrapLayoutEngine
from .layout.device import DEFAULT_DEVICE
from .parser import BlockType, ContentBlock, MarkdownDocument, TextFormat

logger = logging.getLogger("rock_paper_sync.generator")


# CRDT encoding/decoding functions moved to crdt_format.py


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

        # Fuzzy threshold for annotation migration (V2 architecture)
        self._fuzzy_threshold = 0.8

        # Initialize annotation handlers - they own their relocation logic
        from rock_paper_sync.annotations.handlers.highlight_handler import HighlightHandler
        from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler

        self._highlight_handler = HighlightHandler()
        self._stroke_handler = StrokeHandler()

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
                    merger = AnnotationMerger(fuzzy_threshold=self._fuzzy_threshold)
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

                # Use unified apply_to_page - returns AbsolutePosition with adjusted block
                result = self._highlight_handler.apply_to_page(
                    block,
                    page_text,
                    new_origin,
                    self.layout_engine,
                    self.geometry,
                    doc_anno.anchor_context,
                    old_text=old_text if old_text else None,
                    old_origin=old_origin if old_text else None,
                    crdt_base_id=16,
                )

                if result:
                    ctx.annotations.append(result.block)

            elif "Line" in block_type:
                # Stroke - returns CrdtRelativePosition with semantic offset
                result = self._stroke_handler.apply_to_page(
                    block,
                    page_text,
                    self.geometry,
                    doc_anno.anchor_context,
                    tree_node=doc_anno.original_tree_node,
                    scene_group_item=doc_anno.original_scene_group_item,
                    scene_tree_block=doc_anno.original_scene_tree_block,
                )

                if result:
                    ctx.annotations.append(result.block)

                    # Build tree_node_info tuple for later CRDT transformation
                    tree_node = result.tree_node
                    node_id = (
                        tree_node.group.node_id
                        if hasattr(tree_node, "group") and tree_node.group
                        else None
                    )
                    logger.debug(
                        f"Adding to ctx.tree_nodes for page {projection.page_index}: "
                        f"node_id={node_id}, target_offset={result.semantic_offset}, "
                        f"cluster={doc_anno.cluster_id or 'none'}"
                    )
                    tree_node_info = (
                        tree_node,
                        result.semantic_offset,
                        result.scene_group_item,
                        result.scene_tree_block,
                    )
                    ctx.tree_nodes.append(tree_node_info)

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

    def _extract_text_blocks_from_rm(
        self, rm_file_path: Path
    ) -> tuple[list[TextBlock], float, str]:
        """Extract text blocks, positions, and full text from an existing .rm file.

        Parses the .rm file to extract the RootTextBlock and creates TextBlock
        objects with Y-coordinates for each line of text. Also returns the text
        origin Y coordinate for coordinate space transformations and the full
        text content for annotation content anchoring (Phase 1).

        Delegates to RmFileExtractor for consolidated .rm reading.

        Args:
            rm_file_path: Path to existing .rm file

        Returns:
            Tuple of (text_blocks, text_origin_y, full_text) where:
            - text_blocks: List of TextBlock objects with position information
            - text_origin_y: Y-coordinate of the text origin (RootTextBlock.pos_y)
            - full_text: Full text content as a single string
        """
        from .rm_file_extractor import RmFileExtractor

        try:
            extractor = RmFileExtractor.from_path(rm_file_path)
            text_blocks = extractor.get_text_blocks(self.geometry)
            text_origin_y = extractor.text_origin.pos_y
            full_text = extractor.text_content
            return text_blocks, text_origin_y, full_text
        except Exception as e:
            logger.warning(f"Failed to extract text blocks from {rm_file_path}: {e}")
            return [], self.geometry.text_pos_y, ""

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
