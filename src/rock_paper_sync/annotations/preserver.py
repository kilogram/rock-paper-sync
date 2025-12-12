"""Annotation preservation for reMarkable document regeneration.

This module provides the AnnotationPreserver class that handles extracting,
routing, and repositioning annotations when documents are regenerated.

The preservation process has four phases:

1. **Extraction**: Read text blocks and annotations from existing .rm files
2. **Mapping**: Build document-level position mappings using content similarity
3. **Routing**: Route each annotation to its target page based on content mapping
4. **Context Assignment**: Build PageAnnotationContext for each page

Example:
    preserver = AnnotationPreserver(geometry, layout_engine)
    preserver.preserve(pages, existing_rm_files)

    # Pages now have annotation_context set
    for page in pages:
        print(f"Page has {len(page.annotation_context.annotations)} annotations")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import rmscene

from rock_paper_sync.annotations import TextBlock, calculate_position_mapping
from rock_paper_sync.annotations.handlers.highlight_handler import HighlightHandler
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.coordinate_transformer import END_OF_DOC_ANCHOR_MARKER

if TYPE_CHECKING:
    from rock_paper_sync.generator import PageAnnotationContext, RemarkablePage
    from rock_paper_sync.layout import DeviceGeometry, WordWrapLayoutEngine

logger = logging.getLogger(__name__)


@dataclass
class OldPageData:
    """Snapshot of an old page's state for annotation preservation.

    Encapsulates all data extracted from an existing .rm file needed
    for annotation routing and position adjustment.

    Attributes:
        rm_file_path: Path to the source .rm file
        text_blocks: Text blocks with Y positions (page_index set)
        text_origin_y: Y-coordinate of text origin from RootTextBlock
        page_text: Full text content for content-based anchoring
        annotation_blocks: Raw rmscene annotation blocks (Line/Glyph)
        tree_nodes_by_id: Mapping of node_id -> TreeNodeBlock for stroke anchoring
        scene_group_items_by_tree_node_id: Mapping of TreeNode ID -> SceneGroupItemBlock
        scene_tree_blocks_by_tree_id: Mapping of tree_id -> SceneTreeBlock
        all_blocks: All rmscene blocks from the file (for reference)
    """

    rm_file_path: Path
    text_blocks: list[TextBlock]
    text_origin_y: float
    page_text: str
    annotation_blocks: list[Any]
    tree_nodes_by_id: dict[Any, Any] = field(default_factory=dict)
    scene_group_items_by_tree_node_id: dict[Any, Any] = field(default_factory=dict)
    scene_tree_blocks_by_tree_id: dict[Any, Any] = field(default_factory=dict)
    all_blocks: list[Any] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """Result of routing an annotation to its target page.

    Attributes:
        annotation_block: The raw rmscene annotation block
        source_page_idx: Original page index
        target_page_idx: Destination page index
        is_cross_page: True if annotation moves between pages
        adjusted_block: Block with position adjustments applied
        tree_node: TreeNodeBlock for strokes (if cross-page)
        scene_group_item: SceneGroupItemBlock that links TreeNodeBlock to scene graph
        scene_tree_block: SceneTreeBlock that declares TreeNodeBlock in scene tree
        target_char_offset: Character offset for TreeNodeBlock anchor
    """

    annotation_block: Any
    source_page_idx: int
    target_page_idx: int
    is_cross_page: bool
    adjusted_block: Any
    tree_node: Any | None = None
    scene_group_item: Any | None = None
    scene_tree_block: Any | None = None
    target_char_offset: int | None = None


class AnnotationPreserver:
    """Preserves annotations when regenerating reMarkable documents.

    Orchestrates extracting annotations from existing .rm files, routing
    them to new pages based on content movement, and adjusting coordinates.

    The class delegates type-specific operations to HighlightHandler and
    StrokeHandler, using their get_position() and relocate() methods.

    Attributes:
        geometry: Device geometry for layout calculations
        layout_engine: WordWrap engine for position calculations
        highlight_handler: Handler for Glyph blocks
        stroke_handler: Handler for Line blocks
    """

    def __init__(
        self,
        geometry: DeviceGeometry,
        layout_engine: WordWrapLayoutEngine,
        highlight_handler: HighlightHandler | None = None,
        stroke_handler: StrokeHandler | None = None,
    ) -> None:
        """Initialize preserver with layout dependencies.

        Args:
            geometry: Device geometry for coordinate calculations
            layout_engine: Layout engine for position-to-offset conversion
            highlight_handler: Custom highlight handler (default: new instance)
            stroke_handler: Custom stroke handler (default: new instance)
        """
        self.geometry = geometry
        self.layout_engine = layout_engine
        self.highlight_handler = highlight_handler or HighlightHandler()
        self.stroke_handler = stroke_handler or StrokeHandler()

    # =========================================================================
    # Public API
    # =========================================================================

    def preserve(
        self,
        pages: list[RemarkablePage],
        existing_rm_files: list[Path | None],
    ) -> None:
        """Preserve annotations from existing .rm files into new pages.

        This is the main entry point. Modifies pages in-place by setting
        their annotation_context attribute.

        Args:
            pages: List of newly generated pages (will be modified)
            existing_rm_files: List of .rm file paths matching old pages
        """
        # Phase 1: Extraction
        old_page_data_list, all_old_text_blocks = self.extract_all_old_pages(existing_rm_files)

        if not all_old_text_blocks:
            logger.warning("No text blocks to map, skipping annotation preservation")
            return

        # Build combined new text blocks
        all_new_text_blocks = self._collect_new_text_blocks(pages)

        if not all_new_text_blocks:
            logger.warning("No new text blocks, skipping annotation preservation")
            return

        logger.debug(
            f"Document-level: {len(all_old_text_blocks)} old blocks, "
            f"{len(all_new_text_blocks)} new blocks"
        )

        # Phase 2: Mapping
        position_mapping = self.build_position_mapping(all_old_text_blocks, all_new_text_blocks)

        # Phase 3: Routing
        routing_decisions = self.route_all_annotations(
            old_page_data_list,
            pages,
            position_mapping,
            all_new_text_blocks,
        )

        if not routing_decisions:
            logger.debug("No annotations found in any old page")
            return

        logger.info(f"Routed {len(routing_decisions)} annotations for cross-page handling")

        # Phase 4: Context Assignment
        contexts = self.build_page_contexts(
            routing_decisions,
            len(pages),
            existing_rm_files,
            old_page_data_list,
        )

        self.assign_contexts_to_pages(pages, contexts, existing_rm_files)

    # =========================================================================
    # Phase 1: Extraction
    # =========================================================================

    def extract_old_page_data(
        self,
        rm_file_path: Path,
        page_index: int,
        text_block_extractor: Any,
    ) -> OldPageData | None:
        """Extract text blocks and annotations from an existing .rm file.

        Args:
            rm_file_path: Path to .rm file
            page_index: Page index to set on extracted TextBlocks
            text_block_extractor: Callable to extract text blocks (from generator)

        Returns:
            OldPageData with extracted information, or None if extraction fails
        """
        try:
            # Use the generator's extraction method for consistency
            text_blocks, text_origin_y, page_text = text_block_extractor(rm_file_path)

            # Set page_index on each text block
            for tb in text_blocks:
                tb.page_index = page_index

            # Read all blocks for annotations
            with open(rm_file_path, "rb") as f:
                all_blocks = list(rmscene.read_blocks(f))

            # Build TreeNodeBlock lookup
            tree_nodes_by_id = {}
            scene_group_items_by_tree_node_id = {}
            scene_tree_blocks_by_tree_id = {}
            for block in all_blocks:
                if type(block).__name__ == "TreeNodeBlock":
                    if hasattr(block, "group") and block.group:
                        node_id = block.group.node_id
                        if node_id:
                            tree_nodes_by_id[node_id] = block

                elif type(block).__name__ == "SceneGroupItemBlock":
                    # Maps TreeNodeBlock IDs to their SceneGroupItemBlocks
                    # SceneGroupItemBlock.value points to the TreeNodeBlock
                    if hasattr(block, "value") and block.value:
                        tree_node_id = block.value
                        scene_group_items_by_tree_node_id[tree_node_id] = block

                elif type(block).__name__ == "SceneTreeBlock":
                    # Maps tree_id to SceneTreeBlock
                    # SceneTreeBlock declares a TreeNodeBlock in the scene tree
                    if hasattr(block, "tree_id") and block.tree_id:
                        scene_tree_blocks_by_tree_id[block.tree_id] = block

            # Extract annotation blocks
            annotation_blocks = [
                block
                for block in all_blocks
                if "Line" in type(block).__name__ or "Glyph" in type(block).__name__
            ]

            logger.debug(
                f"Page {page_index}: {len(annotation_blocks)} annotations, "
                f"{len(tree_nodes_by_id)} TreeNodeBlocks"
            )

            return OldPageData(
                rm_file_path=rm_file_path,
                text_blocks=text_blocks,
                text_origin_y=text_origin_y,
                page_text=page_text,
                annotation_blocks=annotation_blocks,
                tree_nodes_by_id=tree_nodes_by_id,
                scene_group_items_by_tree_node_id=scene_group_items_by_tree_node_id,
                scene_tree_blocks_by_tree_id=scene_tree_blocks_by_tree_id,
                all_blocks=all_blocks,
            )

        except Exception as e:
            logger.warning(f"Failed to extract data from {rm_file_path}: {e}")
            return None

    def extract_all_old_pages(
        self,
        existing_rm_files: list[Path | None],
        text_block_extractor: Any = None,
    ) -> tuple[list[OldPageData | None], list[TextBlock]]:
        """Extract data from all old pages.

        Args:
            existing_rm_files: List of .rm file paths (or None)
            text_block_extractor: Callable to extract text blocks

        Returns:
            Tuple of (per-page data list, combined text blocks)
        """
        # Import here to avoid circular dependency

        old_page_data_list: list[OldPageData | None] = []
        all_old_text_blocks: list[TextBlock] = []

        for page_idx, rm_file_path in enumerate(existing_rm_files):
            if rm_file_path and Path(rm_file_path).exists():
                page_data = self._extract_page_data_internal(rm_file_path, page_idx)
                if page_data:
                    old_page_data_list.append(page_data)
                    all_old_text_blocks.extend(page_data.text_blocks)
                else:
                    old_page_data_list.append(None)
            else:
                old_page_data_list.append(None)

        return old_page_data_list, all_old_text_blocks

    def _extract_page_data_internal(
        self, rm_file_path: Path, page_index: int
    ) -> OldPageData | None:
        """Internal extraction without external extractor dependency."""
        from rock_paper_sync.layout import LayoutContext, TextAreaConfig

        try:
            with open(rm_file_path, "rb") as f:
                all_blocks = list(rmscene.read_blocks(f))

            text_blocks = []
            text_origin_y = self.geometry.text_pos_y
            full_text = ""

            # Find RootTextBlock
            for block in all_blocks:
                if "RootText" in type(block).__name__:
                    text_data = block.value
                    text_origin_y = text_data.pos_y

                    # Extract text from CrdtSequence
                    text_parts = []
                    for item in text_data.items.sequence_items():
                        if hasattr(item, "value") and isinstance(item.value, str):
                            text_parts.append(item.value)

                    full_text = "".join(text_parts)
                    paragraphs = full_text.split("\n")

                    # Create layout context for Y positions
                    layout_ctx = LayoutContext.from_text(
                        full_text,
                        use_font_metrics=True,
                        config=TextAreaConfig(
                            text_width=self.geometry.text_width,
                            text_pos_x=self.geometry.text_pos_x,
                            text_pos_y=text_data.pos_y,
                        ),
                    )

                    current_offset = 0
                    for paragraph in paragraphs:
                        if paragraph.strip():
                            para_start = full_text.find(paragraph, current_offset)
                            if para_start == -1:
                                para_start = current_offset
                            para_end = para_start + len(paragraph)
                            current_offset = para_end + 1

                            _, y_start = layout_ctx.offset_to_position(para_start)
                            _, y_end = layout_ctx.offset_to_position(para_end)
                            y_end += layout_ctx.line_height

                            text_blocks.append(
                                TextBlock(
                                    content=paragraph,
                                    y_start=y_start,
                                    y_end=y_end,
                                    block_type="paragraph",
                                    page_index=page_index,
                                    char_start=para_start,
                                    char_end=para_end,
                                )
                            )

            # Build TreeNodeBlock lookup
            tree_nodes_by_id = {}
            scene_group_items_by_tree_node_id = {}
            scene_tree_blocks_by_tree_id = {}
            for block in all_blocks:
                if type(block).__name__ == "TreeNodeBlock":
                    if hasattr(block, "group") and block.group:
                        node_id = block.group.node_id
                        if node_id:
                            tree_nodes_by_id[node_id] = block

                elif type(block).__name__ == "SceneGroupItemBlock":
                    # Maps TreeNodeBlock IDs to their SceneGroupItemBlocks
                    # SceneGroupItemBlock.value points to the TreeNodeBlock
                    if hasattr(block, "value") and block.value:
                        tree_node_id = block.value
                        scene_group_items_by_tree_node_id[tree_node_id] = block

                elif type(block).__name__ == "SceneTreeBlock":
                    # Maps tree_id to SceneTreeBlock
                    # SceneTreeBlock declares a TreeNodeBlock in the scene tree
                    if hasattr(block, "tree_id") and block.tree_id:
                        scene_tree_blocks_by_tree_id[block.tree_id] = block

            # Extract annotation blocks
            annotation_blocks = [
                block
                for block in all_blocks
                if "Line" in type(block).__name__ or "Glyph" in type(block).__name__
            ]

            return OldPageData(
                rm_file_path=rm_file_path,
                text_blocks=text_blocks,
                text_origin_y=text_origin_y,
                page_text=full_text,
                annotation_blocks=annotation_blocks,
                tree_nodes_by_id=tree_nodes_by_id,
                scene_group_items_by_tree_node_id=scene_group_items_by_tree_node_id,
                scene_tree_blocks_by_tree_id=scene_tree_blocks_by_tree_id,
                all_blocks=all_blocks,
            )

        except Exception as e:
            logger.warning(f"Failed to extract data from {rm_file_path}: {e}")
            return None

    # =========================================================================
    # Phase 2: Mapping
    # =========================================================================

    def build_position_mapping(
        self,
        old_text_blocks: list[TextBlock],
        new_text_blocks: list[TextBlock],
    ) -> dict[int, int]:
        """Build document-level mapping from old block indices to new ones.

        Args:
            old_text_blocks: Combined old text blocks (with page_index set)
            new_text_blocks: Combined new text blocks (with page_index set)

        Returns:
            Dict mapping old_block_idx -> new_block_idx
        """
        return calculate_position_mapping(old_text_blocks, new_text_blocks)

    # =========================================================================
    # Phase 3: Routing
    # =========================================================================

    def route_all_annotations(
        self,
        old_page_data_list: list[OldPageData | None],
        new_pages: list[RemarkablePage],
        position_mapping: dict[int, int],
        all_new_text_blocks: list[TextBlock],
    ) -> list[RoutingDecision]:
        """Route all annotations from all old pages.

        Args:
            old_page_data_list: Per-page extracted data
            new_pages: List of new pages
            position_mapping: Document-level mapping
            all_new_text_blocks: Combined new text blocks

        Returns:
            List of all routing decisions
        """
        decisions = []
        new_text_origin_y = self.geometry.text_pos_y

        for source_page_idx, page_data in enumerate(old_page_data_list):
            if page_data is None:
                continue

            # Calculate document offset for this page
            doc_offset = sum(
                len(old_page_data_list[i].text_blocks) if old_page_data_list[i] else 0
                for i in range(source_page_idx)
            )

            for anno_block in page_data.annotation_blocks:
                decision = self._route_single_annotation(
                    anno_block,
                    source_page_idx,
                    page_data,
                    new_pages,
                    position_mapping,
                    all_new_text_blocks,
                    doc_offset,
                    new_text_origin_y,
                    old_page_data_list,
                )
                decisions.append(decision)

        return decisions

    def _route_single_annotation(
        self,
        anno_block: Any,
        source_page_idx: int,
        page_data: OldPageData,
        new_pages: list[RemarkablePage],
        position_mapping: dict[int, int],
        all_new_text_blocks: list[TextBlock],
        doc_offset: int,
        new_text_origin_y: float,
        old_page_data_list: list[OldPageData | None],
    ) -> RoutingDecision:
        """Route a single annotation to its target page."""
        from rock_paper_sync.generator import get_crdt_base_id_from_rm

        handler = self._get_handler_for_block(anno_block)
        old_text_blocks = page_data.text_blocks
        old_text_origin_y = page_data.text_origin_y

        # Get annotation position using handler
        anno_center_y = None
        if handler:
            position = handler.get_position(anno_block, old_text_origin_y)
            anno_center_y = position[1] if position else None

        # For strokes (Line blocks), prefer anchor-based routing over Y position
        # Strokes use dual-anchor coordinates which can give misleading Y positions
        # The TreeNodeBlock anchor_id points to the actual text character offset
        nearest_old_idx = None
        if "Line" in type(anno_block).__name__:
            parent_id = getattr(anno_block, "parent_id", None)
            if parent_id and parent_id in page_data.tree_nodes_by_id:
                tree_node = page_data.tree_nodes_by_id[parent_id]
                if (
                    hasattr(tree_node, "group")
                    and tree_node.group
                    and hasattr(tree_node.group, "anchor_id")
                    and tree_node.group.anchor_id
                ):
                    anchor_val = tree_node.group.anchor_id.value
                    if hasattr(anchor_val, "part2"):
                        anchor_offset = anchor_val.part2
                        # Skip sentinel anchors - use Y fallback for those
                        if anchor_offset != END_OF_DOC_ANCHOR_MARKER:
                            # Find which text block contains this anchor offset
                            # Use actual char_start/char_end - should always be available
                            for local_idx, tb in enumerate(old_text_blocks):
                                if tb.char_start is not None and tb.char_end is not None:
                                    if tb.char_start <= anchor_offset <= tb.char_end:
                                        nearest_old_idx = doc_offset + local_idx
                                        logger.debug(
                                            f"Stroke anchor-based routing: "
                                            f"anchor={anchor_offset} -> text_block={nearest_old_idx}"
                                        )
                                        break
                                else:
                                    # This shouldn't happen - char offsets should be populated
                                    logger.warning(
                                        f"TextBlock missing char_start/char_end at index "
                                        f"{local_idx} during routing, falling back"
                                    )
                                    cumulative = sum(
                                        len(old_text_blocks[i].content) + 1
                                        for i in range(local_idx)
                                    )
                                    block_end = cumulative + len(tb.content)
                                    if cumulative <= anchor_offset <= block_end:
                                        nearest_old_idx = doc_offset + local_idx
                                        break

        # Fall back to Y-position matching if anchor-based routing didn't work
        if nearest_old_idx is None and anno_center_y is not None:
            nearest_old_idx = self._find_nearest_text_block(
                anno_center_y, old_text_blocks, doc_offset
            )

        # Find target page
        if nearest_old_idx is not None and nearest_old_idx in position_mapping:
            new_block_idx = position_mapping[nearest_old_idx]
            new_text_block = all_new_text_blocks[new_block_idx]
            target_page_idx = new_text_block.page_index
            if "Line" in type(anno_block).__name__:
                print(
                    f"[DEBUG] Routing via position_mapping: nearest_old_idx={nearest_old_idx}, new_block_idx={new_block_idx}, target_page_idx={target_page_idx}"
                )
        elif anno_center_y is None:
            target_page_idx = min(source_page_idx, len(new_pages) - 1)
            if "Line" in type(anno_block).__name__:
                print(
                    f"[DEBUG] Routing fallback (no Y): source_page_idx={source_page_idx}, target_page_idx={target_page_idx}"
                )
        else:
            target_page_idx = self._find_nearest_page_with_content(source_page_idx, new_pages)
            if "Line" in type(anno_block).__name__:
                print(
                    f"[DEBUG] Routing fallback (nearest page): source_page_idx={source_page_idx}, target_page_idx={target_page_idx}"
                )

        target_page_idx = max(0, min(target_page_idx, len(new_pages) - 1))
        is_cross_page = source_page_idx != target_page_idx

        if "Line" in type(anno_block).__name__:
            old_text_len = len(page_data.page_text) if page_data.page_text else 0
            new_page_text_len = sum(len(b.content) for b in new_pages[target_page_idx].text_blocks)
            print(
                f"[DEBUG] Stroke routing: source={source_page_idx}, target={target_page_idx}, is_cross_page={is_cross_page}, old_len={old_text_len}, new_len={new_page_text_len}"
            )

        # Adjust annotation position
        crdt_base_id = get_crdt_base_id_from_rm(page_data.rm_file_path)
        if handler:
            new_page_text = "\n".join(
                block.content for block in new_pages[target_page_idx].text_blocks
            )
            adjusted_block = handler.relocate(
                anno_block,
                page_data.page_text,
                new_page_text,
                (self.geometry.text_pos_x, old_text_origin_y),
                (self.geometry.text_pos_x, new_text_origin_y),
                self.layout_engine,
                self.geometry,
                crdt_base_id,
            )
        else:
            adjusted_block = anno_block

        # Handle TreeNodeBlock for strokes
        # We need to recalculate anchor offsets if:
        # 1. The stroke moved to a different page (is_cross_page), OR
        # 2. The stroke stays on the same page but the page text changed
        tree_node = None
        scene_group_item = None
        scene_tree_block = None
        target_char_offset = None
        if "Line" in type(anno_block).__name__:
            parent_id = getattr(anno_block, "parent_id", None)
            if parent_id and parent_id in page_data.tree_nodes_by_id:
                tree_node = page_data.tree_nodes_by_id[parent_id]
                # Get SceneGroupItemBlock and SceneTreeBlock for this tree node
                if hasattr(tree_node, "group") and tree_node.group:
                    node_id = tree_node.group.node_id
                    scene_group_item = page_data.scene_group_items_by_tree_node_id.get(node_id)
                    scene_tree_block = page_data.scene_tree_blocks_by_tree_id.get(node_id)
                # Check if page text changed (compare lengths as proxy)
                old_text_len = len(page_data.page_text) if page_data.page_text else 0
                new_page_text = "\n".join(
                    block.content for block in new_pages[target_page_idx].text_blocks
                )
                new_text_len = len(new_page_text)
                text_changed = old_text_len != new_text_len

                if is_cross_page or text_changed:
                    target_char_offset = self._calculate_tree_node_offset(
                        tree_node,
                        old_text_blocks,
                        new_pages[target_page_idx],
                        position_mapping,
                        doc_offset,
                        source_page_idx,
                        anno_center_y,
                        old_page_data_list,
                        adjusted_block,
                        new_text_origin_y,
                        all_new_text_blocks,
                        target_page_idx,
                    )
                    if text_changed and not is_cross_page:
                        print(
                            f"[DEBUG] Same-page reanchor: old_len={old_text_len}, "
                            f"new_len={new_text_len}, offset={target_char_offset}"
                        )

        return RoutingDecision(
            annotation_block=anno_block,
            source_page_idx=source_page_idx,
            target_page_idx=target_page_idx,
            is_cross_page=is_cross_page,
            adjusted_block=adjusted_block,
            tree_node=tree_node,
            scene_group_item=scene_group_item,
            scene_tree_block=scene_tree_block,
            target_char_offset=target_char_offset,
        )

    # =========================================================================
    # Phase 4: Context Assignment
    # =========================================================================

    def build_page_contexts(
        self,
        routing_decisions: list[RoutingDecision],
        num_pages: int,
        existing_rm_files: list[Path | None],
        old_page_data_list: list[OldPageData | None],
    ) -> list[PageAnnotationContext]:
        """Build PageAnnotationContext for each page from routing decisions."""
        from rock_paper_sync.generator import PageAnnotationContext

        contexts = [PageAnnotationContext() for _ in range(num_pages)]

        # Track TreeNodeBlock usage for exclusion logic
        tree_node_cross_page: dict[tuple[int, Any], set] = {}
        tree_node_same_page: dict[tuple[int, Any], set] = {}

        for decision in routing_decisions:
            ctx = contexts[decision.target_page_idx]
            ctx.annotations.append(decision.adjusted_block)

            if decision.is_cross_page:
                # Track moved-out annotations for exclusion
                # Clamp source_page_idx to valid context range
                source_idx = min(decision.source_page_idx, num_pages - 1)
                if (
                    hasattr(decision.annotation_block, "item")
                    and hasattr(decision.annotation_block.item, "item_id")
                    and source_idx >= 0
                ):
                    contexts[source_idx].exclude_ids.add(decision.annotation_block.item.item_id)

                # Add TreeNodeBlock for cross-page strokes
                if decision.tree_node and decision.target_char_offset is not None:
                    # Check if already added
                    existing_node_ids = [
                        tn.group.node_id
                        for tn, _, _, _ in ctx.tree_nodes
                        if hasattr(tn, "group") and tn.group
                    ]
                    parent_id = getattr(decision.annotation_block, "parent_id", None)
                    if parent_id not in existing_node_ids:
                        ctx.tree_nodes.append(
                            (
                                decision.tree_node,
                                decision.target_char_offset,
                                decision.scene_group_item,
                                decision.scene_tree_block,
                            )
                        )

                # Track for exclusion
                if decision.tree_node and hasattr(decision.tree_node, "group"):
                    node_id = decision.tree_node.group.node_id
                    key = (decision.source_page_idx, node_id)
                    if key not in tree_node_cross_page:
                        tree_node_cross_page[key] = set()
                    tree_node_cross_page[key].add(id(decision.annotation_block))
            else:
                # Same page
                source_idx = decision.source_page_idx
                old_data = (
                    old_page_data_list[source_idx] if source_idx < len(old_page_data_list) else None
                )
                if old_data:
                    ctx.source_rm_path = old_data.rm_file_path
                ctx.has_same_page = True

                # Handle same-page strokes that need reanchoring (text changed)
                if decision.tree_node and decision.target_char_offset is not None:
                    # Add to tree_nodes for reanchoring, just like cross-page
                    existing_node_ids = [
                        tn.group.node_id
                        for tn, _, _, _ in ctx.tree_nodes
                        if hasattr(tn, "group") and tn.group
                    ]
                    parent_id = getattr(decision.annotation_block, "parent_id", None)
                    if parent_id not in existing_node_ids:
                        ctx.tree_nodes.append(
                            (
                                decision.tree_node,
                                decision.target_char_offset,
                                decision.scene_group_item,
                                decision.scene_tree_block,
                            )
                        )
                        # Mark for exclusion from original file (will be reinjected with new anchor)
                        if hasattr(decision.tree_node, "group") and decision.tree_node.group:
                            node_id = decision.tree_node.group.node_id
                            ctx.exclude_tree_node_ids.add(node_id)

                # Track same-page strokes for exclusion logic
                if "Line" in type(decision.annotation_block).__name__:
                    parent_id = getattr(decision.annotation_block, "parent_id", None)
                    if parent_id and old_data and parent_id in old_data.tree_nodes_by_id:
                        tree_node = old_data.tree_nodes_by_id[parent_id]
                        if hasattr(tree_node, "group") and tree_node.group:
                            node_id = tree_node.group.node_id
                            # Only track if source page is in valid range for contexts
                            if source_idx < num_pages:
                                key = (source_idx, node_id)
                                if key not in tree_node_same_page:
                                    tree_node_same_page[key] = set()
                                tree_node_same_page[key].add(id(decision.annotation_block))

        # Compute TreeNodeBlock exclusions
        for key, cross_strokes in tree_node_cross_page.items():
            page_idx, node_id = key
            same_strokes = tree_node_same_page.get(key, set())
            if not same_strokes:
                # Only exclude if page_idx is in valid range
                if page_idx < num_pages:
                    contexts[page_idx].exclude_tree_node_ids.add(node_id)
                    logger.debug(
                        f"Page {page_idx}: TreeNodeBlock {node_id} excluded "
                        f"({len(cross_strokes)} moved, 0 stayed)"
                    )

        return contexts

    def assign_contexts_to_pages(
        self,
        pages: list[RemarkablePage],
        contexts: list[PageAnnotationContext],
        existing_rm_files: list[Path | None],
    ) -> None:
        """Assign annotation contexts to pages."""
        for page_idx, page in enumerate(pages):
            ctx = contexts[page_idx]

            # Set source for roundtrip if needed
            if not ctx.source_rm_path and ctx.exclude_ids:
                if page_idx < len(existing_rm_files) and existing_rm_files[page_idx]:
                    ctx.source_rm_path = existing_rm_files[page_idx]

            if ctx.annotations and not ctx.has_same_page:
                if page_idx < len(existing_rm_files) and existing_rm_files[page_idx]:
                    ctx.source_rm_path = existing_rm_files[page_idx]

            page.annotation_context = ctx

            # Log summary
            if ctx.annotations or ctx.exclude_ids:
                same_count = len(ctx.annotations) if ctx.has_same_page else 0
                cross_count = len(ctx.annotations) - same_count
                mode = "roundtrip" if ctx.source_rm_path else "fresh"
                logger.info(
                    f"Page {page_idx}: {same_count} same-page + {cross_count} cross-page "
                    f"annotations, {len(ctx.exclude_ids)} moved out ({mode})"
                )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_handler_for_block(self, block: Any) -> HighlightHandler | StrokeHandler | None:
        """Get appropriate handler for an annotation block type."""
        block_type = type(block).__name__
        if "Glyph" in block_type:
            return self.highlight_handler
        if "Line" in block_type:
            return self.stroke_handler

        if hasattr(block, "item") and hasattr(block.item, "value"):
            value_type = type(block.item.value).__name__
            if "Glyph" in value_type:
                return self.highlight_handler
            if "Line" in value_type:
                return self.stroke_handler

        return None

    def _find_nearest_text_block(
        self,
        anno_center_y: float,
        text_blocks: list[TextBlock],
        doc_offset: int,
    ) -> int | None:
        """Find index of nearest text block to annotation Y position."""
        nearest_idx = None
        min_distance = float("inf")

        for local_idx, tb in enumerate(text_blocks):
            block_center_y = (tb.y_start + tb.y_end) / 2
            distance = abs(anno_center_y - block_center_y)
            if distance < min_distance:
                min_distance = distance
                nearest_idx = doc_offset + local_idx

        return nearest_idx

    def _find_nearest_page_with_content(
        self, source_page_idx: int, pages: list[RemarkablePage]
    ) -> int:
        """Find nearest page with text content."""
        if not pages:
            return 0

        # Clamp source_page_idx to valid range
        source_page_idx = max(0, min(source_page_idx, len(pages) - 1))

        for offset in range(len(pages)):
            # Try forward
            forward_idx = source_page_idx + offset
            if forward_idx < len(pages):
                if pages[forward_idx].text_blocks:
                    return forward_idx
            # Try backward
            backward_idx = source_page_idx - offset
            if 0 <= backward_idx < len(pages):
                if pages[backward_idx].text_blocks:
                    return backward_idx

        return 0

    def _calculate_tree_node_offset(
        self,
        tree_node: Any,
        old_text_blocks: list[TextBlock],
        target_page: RemarkablePage,
        position_mapping: dict[int, int],
        doc_offset: int,
        source_page_idx: int,
        nearest_old_idx: int | None,
        old_page_data_list: list[OldPageData | None],
        adjusted_block: Any,
        new_text_origin_y: float,
        all_new_text_blocks: list[TextBlock],
        target_page_idx: int,
    ) -> int:
        """Calculate target character offset for TreeNodeBlock anchor.

        For margin notes and other non-text-anchored strokes, the anchor_id
        uses a sentinel value (END_OF_DOC_ANCHOR_MARKER with part1=0) which
        should be preserved unchanged during cross-page migration.
        """
        print(f"[DEBUG] _calculate_tree_node_offset called, target_page_idx={target_page_idx}")
        target_char_offset = 0

        # Get original anchor
        original_anchor = None
        anchor_part1 = None
        if (
            hasattr(tree_node, "group")
            and tree_node.group
            and hasattr(tree_node.group, "anchor_id")
            and tree_node.group.anchor_id
        ):
            anchor_val = tree_node.group.anchor_id.value
            if hasattr(anchor_val, "part2"):
                original_anchor = anchor_val.part2
                anchor_part1 = anchor_val.part1
            else:
                original_anchor = anchor_val

        # Check for sentinel anchor (margin notes, non-text-anchored strokes)
        # These have anchor_id.part1 = 0 and part2 = END_OF_DOC_ANCHOR_MARKER
        # They should be preserved unchanged - Y positioning comes from stroke coords
        if original_anchor == END_OF_DOC_ANCHOR_MARKER and anchor_part1 == 0:
            logger.debug(
                "Preserving sentinel anchor for TreeNodeBlock "
                "(margin note or non-text-anchored stroke)"
            )
            return END_OF_DOC_ANCHOR_MARKER

        # Find which old text block the anchor pointed to
        # Use actual char_start/char_end offsets from TextBlock to correctly handle
        # empty paragraphs and gaps in the full page text
        anchor_old_idx = None
        intra_block_offset = 0
        if original_anchor is not None and old_text_blocks:
            for local_idx, tb in enumerate(old_text_blocks):
                # Use actual character offsets - these should always be available
                if tb.char_start is not None and tb.char_end is not None:
                    if tb.char_start <= original_anchor <= tb.char_end:
                        anchor_old_idx = doc_offset + local_idx
                        intra_block_offset = original_anchor - tb.char_start
                        break
                else:
                    # This shouldn't happen - char_start/char_end should be populated
                    logger.warning(
                        f"TextBlock missing char_start/char_end at index {local_idx}, "
                        f"falling back to cumulative calculation"
                    )
                    cumulative = 0
                    for i, old_tb in enumerate(old_text_blocks[:local_idx]):
                        cumulative += len(old_tb.content) + 1
                    block_end = cumulative + len(tb.content)
                    if cumulative <= original_anchor <= block_end:
                        anchor_old_idx = doc_offset + local_idx
                        intra_block_offset = original_anchor - cumulative
                        break

        # Use anchor-based mapping if available
        mapping_idx = anchor_old_idx if anchor_old_idx is not None else nearest_old_idx

        if mapping_idx is not None and mapping_idx in position_mapping:
            target_doc_idx = position_mapping[mapping_idx]

            # Calculate starting doc index for target page
            target_page_start_idx = 0
            for tb in all_new_text_blocks:
                if tb.page_index == target_page_idx:
                    break
                target_page_start_idx += 1

            # Convert document-level index to page-local index
            target_page_local_idx = target_doc_idx - target_page_start_idx

            # Calculate character offset using actual char_start from target block
            if 0 <= target_page_local_idx < len(target_page.text_blocks):
                target_block = target_page.text_blocks[target_page_local_idx]
                # Use actual char_start - should always be available
                if target_block.char_start is not None:
                    target_block_len = len(target_block.content)
                    clamped_offset = min(intra_block_offset, target_block_len)
                    target_char_offset = target_block.char_start + clamped_offset
                    print(
                        f"[DEBUG] TreeNode anchor: original={original_anchor}, "
                        f"intra_offset={intra_block_offset}, target.char_start="
                        f"{target_block.char_start}, clamped={clamped_offset}, "
                        f"result={target_char_offset}, target_page_idx={target_page_idx}"
                    )
                else:
                    # This shouldn't happen - char_start should be populated
                    logger.warning(
                        f"Target TextBlock missing char_start at page-local index "
                        f"{target_page_local_idx}, falling back to cumulative calculation"
                    )
                    for i in range(target_page_local_idx):
                        if i < len(target_page.text_blocks):
                            target_char_offset += len(target_page.text_blocks[i].content)
                            if i < len(target_page.text_blocks) - 1:
                                target_char_offset += 1
                    target_block_len = len(target_block.content)
                    clamped_offset = min(intra_block_offset, target_block_len)
                    target_char_offset += clamped_offset
        else:
            # Fallback: use Y position
            handler = self._get_handler_for_block(adjusted_block)
            if handler and target_page.text_blocks:
                stroke_y = handler.get_position(adjusted_block, new_text_origin_y)
                if stroke_y:
                    best_idx = 0
                    best_distance = float("inf")
                    for idx, tb in enumerate(target_page.text_blocks):
                        block_y = (tb.y_start + tb.y_end) / 2
                        distance = abs(stroke_y[1] - block_y)
                        if distance < best_distance:
                            best_distance = distance
                            best_idx = idx

                    # Use char_start - should always be available
                    best_block = target_page.text_blocks[best_idx]
                    if best_block.char_start is not None:
                        target_char_offset = best_block.char_start
                    else:
                        # This shouldn't happen - char_start should be populated
                        logger.warning(
                            f"Best-match TextBlock missing char_start at index {best_idx}, "
                            f"falling back to cumulative calculation"
                        )
                        for i in range(best_idx):
                            target_char_offset += len(target_page.text_blocks[i].content)
                            if i < len(target_page.text_blocks) - 1:
                                target_char_offset += 1

        return target_char_offset

    def _collect_new_text_blocks(self, pages: list[RemarkablePage]) -> list[TextBlock]:
        """Collect all text blocks from new pages."""
        all_blocks = []
        for page in pages:
            all_blocks.extend(page.text_blocks)
        return all_blocks
