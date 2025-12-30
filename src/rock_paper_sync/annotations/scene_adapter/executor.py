"""PageTransformExecutor - applies transformation intents to generate .rm files.

This is the SINGLE code path for generating .rm files. It implements the
"Preserve Unknown, Regenerate Known" strategy:

1. PARTITION: Load source .rm (if any), separate known from unknown blocks
2. REGENERATE: Build fresh structural blocks from page text
3. MIGRATE: Apply stroke/highlight placements with correct anchors
4. PRESERVE: Append unknown blocks verbatim
5. VALIDATE: Ensure scene graph integrity before returning

The executor hides ALL rmscene mechanics from callers. Callers express
intent through PageTransformPlan; the executor handles implementation.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import rmscene
from rmscene import CrdtId
from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtSequence, CrdtSequenceItem
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

from .block_registry import BlockKind, classify_block
from .bundle import StrokeBundle
from .scene_index import (
    SceneGraphIndex,
    validate_scene_graph,
)
from .translator import (
    END_OF_DOC_ANCHOR_MARKER,
    SceneTranslator,
)

if TYPE_CHECKING:
    from rock_paper_sync.annotations.domain.intents import (
        HighlightPlacement,
        PageTransformPlan,
        StrokePlacement,
    )
    from rock_paper_sync.layout import DeviceGeometry

logger = logging.getLogger(__name__)

# Base item ID for text CrdtSequenceItem.
# When generating RootTextBlock, we use item_id=CrdtId(1, TEXT_BASE_ITEM_ID).
# Anchor offsets must include this base to correctly map character positions
# to CrdtIds in build_anchor_pos().
TEXT_BASE_ITEM_ID = 16


@dataclass
class ExecutionContext:
    """Internal context for page generation.

    This holds intermediate state during execution.
    Not exposed to callers.
    """

    page_text: str
    source_blocks: list[Any] = field(default_factory=list)
    source_index: SceneGraphIndex | None = None

    # Old text for computing anchor deltas
    old_text: str = ""

    # Blocks partitioned by category
    known_blocks: dict[BlockKind, list[Any]] = field(default_factory=dict)
    unknown_blocks: list[Any] = field(default_factory=list)

    # Output blocks being built
    output_blocks: list[Any] = field(default_factory=list)

    # Tracking for deduplication
    injected_node_ids: set[tuple[int, int]] = field(default_factory=set)

    # Computed anchor delta (for relative stroke positioning)
    anchor_offset_delta: int = 0


class PageTransformExecutor:
    """Executes transformation plans to generate .rm bytes.

    This class encapsulates ALL rmscene block mechanics:
    - Structural block generation (AuthorIdsBlock, RootTextBlock, etc.)
    - TreeNodeBlock anchor updates
    - SceneTreeBlock declarations
    - SceneGroupItemBlock linking
    - Block ordering for device compatibility
    - Deduplication of shared parent nodes
    - Scene graph validation

    Callers never need to know about these mechanics.
    """

    def __init__(self, geometry: DeviceGeometry):
        """Initialize executor with device geometry.

        Args:
            geometry: Device geometry for text layout
        """
        self.geometry = geometry
        self.translator = SceneTranslator()

    def execute(self, plan: PageTransformPlan) -> bytes:
        """Generate .rm bytes from transformation plan.

        This is the SINGLE entry point for all .rm generation.
        No more roundtrip vs from-scratch split.

        Args:
            plan: Complete transformation plan for the page

        Returns:
            Binary .rm file contents

        Raises:
            ValueError: If scene graph validation fails
        """
        ctx = ExecutionContext(
            page_text=plan.page_text,
        )

        # Step 1: PARTITION - Load and partition source blocks
        if plan.source_rm_path and plan.source_rm_path.exists():
            self._load_and_partition(ctx, plan.source_rm_path)

        # Step 2: REGENERATE - Build structural blocks from source of truth
        self._regenerate_structural(ctx)

        # Step 3: MIGRATE - Apply stroke placements from plan
        # All strokes that should be on this page are in stroke_placements.
        # The DocumentModel routes ALL strokes - we regenerate fresh each run.
        for placement in plan.stroke_placements:
            self._apply_stroke_placement(ctx, placement)

        # NOTE: _preserve_unreplaced_strokes removed - if we're generating fresh,
        # all strokes should be in stroke_placements. Preserving from source file
        # caused duplication when strokes moved cross-page.

        # Step 4: MIGRATE - Apply highlight placements
        for placement in plan.highlight_placements:
            self._apply_highlight_placement(ctx, placement)

        # NOTE: _preserve_unreplaced_highlights removed - same as strokes

        # Step 5: PRESERVE - Append unknown blocks verbatim
        for unknown in plan.unknown_blocks:
            ctx.output_blocks.append(unknown.opaque_handle)

        # Also append unknown blocks from source file
        ctx.output_blocks.extend(ctx.unknown_blocks)

        # Step 6: REORDER - Ensure device-compatible block ordering
        ctx.output_blocks = self._reorder_blocks(ctx.output_blocks)

        # Step 7: SERIALIZE
        buffer = io.BytesIO()
        rmscene.write_blocks(buffer, ctx.output_blocks)
        rm_bytes = buffer.getvalue()

        # Step 8: VALIDATE - Ensure scene graph integrity
        validation = validate_scene_graph(rm_bytes)
        if not validation.is_valid:
            error_msgs = "\n".join(str(e) for e in validation.errors)
            raise ValueError(f"Generated invalid scene graph:\n{error_msgs}")

        return rm_bytes

    def _load_and_partition(self, ctx: ExecutionContext, source_path: Path) -> None:
        """Load source .rm and partition blocks by category.

        Known blocks go into ctx.known_blocks by category.
        Unknown blocks go into ctx.unknown_blocks (sacred, preserved verbatim).

        Also extracts old text for computing anchor deltas.
        """
        with open(source_path, "rb") as f:
            ctx.source_blocks = list(rmscene.read_blocks(f))

        ctx.source_index = SceneGraphIndex.from_blocks(ctx.source_blocks)

        for block in ctx.source_blocks:
            kind = classify_block(block)

            if kind == BlockKind.UNKNOWN:
                ctx.unknown_blocks.append(block)
            else:
                ctx.known_blocks.setdefault(kind, []).append(block)

            # Extract old text from RootTextBlock for anchor delta calculation
            if kind == BlockKind.ROOT_TEXT:
                for item in block.value.items.sequence_items():
                    if hasattr(item, "value") and isinstance(item.value, str):
                        ctx.old_text += item.value

        # Compute anchor offset delta for preserving relative stroke positions
        ctx.anchor_offset_delta = self._compute_anchor_delta(ctx.old_text, ctx.page_text)

    def _compute_anchor_delta(self, old_text: str, new_text: str) -> int:
        """Compute anchor offset delta between old and new text.

        This enables delta-based reanchoring, which preserves the relative
        spacing between multiple strokes in a cluster.

        The delta is computed by finding where the content shifted.
        For now, use a simple length-based delta.

        Args:
            old_text: Original page text
            new_text: New page text

        Returns:
            Delta to add to all anchor offsets
        """
        # Simple heuristic: if text was inserted at the beginning,
        # anchors need to shift by the difference
        # This is a simplified version - the full generator has more sophisticated logic
        if not old_text or not new_text:
            return 0

        # Find common prefix to detect where changes start
        common_prefix = 0
        for i, (old_char, new_char) in enumerate(zip(old_text, new_text)):
            if old_char != new_char:
                break
            common_prefix = i + 1

        # If content was inserted before the common prefix, delta is positive
        # If content was deleted, delta is negative
        len_diff = len(new_text) - len(old_text)

        # Only apply delta if changes occurred before the end of old text
        if common_prefix < len(old_text):
            return len_diff

        return 0

    def _regenerate_structural(self, ctx: ExecutionContext) -> None:
        """Regenerate structural blocks from page text.

        These blocks are always regenerated from the source of truth
        (the markdown content), never preserved from the source file.

        Includes:
        - Header blocks (AuthorIds, MigrationInfo, PageInfo)
        - Scene tree declaration for Layer 1
        - RootTextBlock with text content
        - System TreeNodeBlocks (0:1 root, 0:11 layer)
        - SceneGroupItemBlock linking layer to root
        """
        text = ctx.page_text or " "

        # Build text styles (single paragraph style based on first block type)
        styles = self._build_text_styles(ctx)

        structural_blocks = [
            # Header blocks
            AuthorIdsBlock(author_uuids={1: uuid4()}),
            MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
            PageInfoBlock(
                loads_count=1,
                merges_count=0,
                text_chars_count=len(text) + 1,
                text_lines_count=text.count("\n") + 1,
            ),
            # Scene tree root declaration (Layer 1)
            SceneTreeBlock(
                tree_id=CrdtId(0, 11),
                node_id=CrdtId(0, 0),
                is_update=True,
                parent_id=CrdtId(0, 1),
            ),
            # Text content
            RootTextBlock(
                block_id=CrdtId(0, 0),
                value=si.Text(
                    items=CrdtSequence(
                        [
                            CrdtSequenceItem(
                                item_id=CrdtId(1, TEXT_BASE_ITEM_ID),
                                left_id=CrdtId(0, 0),
                                right_id=CrdtId(0, 0),
                                deleted_length=0,
                                value=text,
                            )
                        ]
                    ),
                    styles=styles,
                    pos_x=self.geometry.text_pos_x,
                    pos_y=self.geometry.text_pos_y,
                    width=self.geometry.text_width,
                ),
            ),
            # System TreeNodeBlocks (required for valid scene graph)
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
            # SceneGroupItemBlock linking Layer 1 to root
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

        ctx.output_blocks.extend(structural_blocks)

    def _build_text_styles(self, ctx: ExecutionContext) -> dict:
        """Build rmscene styles dictionary.

        Creates a styles dictionary for rmscene Text blocks with a single
        paragraph style at position (0,0). The style is HEADING if the first
        line is a markdown heading, otherwise PLAIN.

        Device-native .rm files use only one style entry at (0,0) - they don't
        use format code 10 for newlines.

        Args:
            ctx: Execution context with page text

        Returns:
            Dictionary mapping CrdtId positions to LwwValue styles
        """
        # TODO: Support heading styles per-paragraph (see docs/TODO.md)
        return {CrdtId(0, 0): LwwValue(timestamp=CrdtId(1, 15), value=si.ParagraphStyle.PLAIN)}

    def _apply_stroke_placement(
        self,
        ctx: ExecutionContext,
        placement: StrokePlacement,
    ) -> None:
        """Apply a stroke placement to the output.

        Uses the pre-computed anchor_char_offset from the placement, which
        was computed by the DocumentModel. This handles both:
        - Same-page strokes (anchor adjusted for text changes)
        - Cross-page strokes (anchor computed for target page)

        Handles:
        - Deduplication of shared parent nodes
        - Scene graph block creation for injection
        - Sentinel anchor preservation
        """
        bundle: StrokeBundle = placement.opaque_handle

        if not bundle or not bundle.is_complete:
            logger.warning(f"Skipping incomplete bundle: {bundle}")
            return

        # Check for deduplication - don't inject same node_id twice
        node_key = (bundle.node_id.part1, bundle.node_id.part2)
        if node_key in ctx.injected_node_ids:
            return

        ctx.injected_node_ids.add(node_key)

        # Get original anchor offset
        original_anchor = bundle.anchor_offset

        # Check for sentinel anchor - preserve unchanged
        if original_anchor == END_OF_DOC_ANCHOR_MARKER or original_anchor is None:
            # Use the bundle as-is (sentinel anchors don't move)
            prepared = self.translator.prepare_bundle_for_injection(bundle)
            ctx.output_blocks.extend(prepared.to_raw_blocks())
            return

        # Use the pre-computed anchor from the placement
        # This was calculated by DocumentModel and accounts for:
        # - Text changes on same page
        # - Cross-page movement to new text positions
        new_anchor_offset = placement.anchor_char_offset

        # Ensure anchor stays in valid range
        if new_anchor_offset < 0:
            new_anchor_offset = 0
        if new_anchor_offset > len(ctx.page_text):
            new_anchor_offset = len(ctx.page_text)

        # Add base item ID to convert character offset to CrdtId part2.
        # The generated RootTextBlock uses item_id=CrdtId(1, TEXT_BASE_ITEM_ID),
        # so anchor_id must be CrdtId(1, TEXT_BASE_ITEM_ID + char_offset) to
        # correctly map to the paragraph Y position in build_anchor_pos().
        crdt_anchor_offset = new_anchor_offset + TEXT_BASE_ITEM_ID

        logger.info(
            f"Reanchoring stroke {bundle.node_id}: "
            f"original_anchor={original_anchor}, char_offset={new_anchor_offset}, "
            f"crdt_offset={crdt_anchor_offset}, page_text_len={len(ctx.page_text)}"
        )

        # Reanchor the bundle with the CRDT-adjusted offset
        reanchored = self.translator.reanchor_bundle(bundle, crdt_anchor_offset)

        # Prepare for injection (reset CRDT neighbors for new page)
        prepared = self.translator.prepare_bundle_for_injection(reanchored)
        ctx.output_blocks.extend(prepared.to_raw_blocks())

        logger.debug(
            f"Placed stroke {bundle.node_id}: anchor {original_anchor} -> {new_anchor_offset}"
        )

    def _apply_highlight_placement(
        self,
        ctx: ExecutionContext,
        placement: HighlightPlacement,
    ) -> None:
        """Apply a highlight placement to the output.

        For now, highlights are simpler than strokes - they don't have
        the complex scene graph dependencies.
        """
        # Highlight reanchoring handled by HighlightHandler.relocate()
        # which updates rectangle positions before placement
        if placement.opaque_handle:
            ctx.output_blocks.append(placement.opaque_handle)

    def _reorder_blocks(self, blocks: list[Any]) -> list[Any]:
        """Reorder blocks for device compatibility.

        The reMarkable device expects blocks in a specific order:
        1. Header blocks (AuthorIds, MigrationInfo, PageInfo)
        2. SceneTree declarations
        3. TreeNode definitions
        4. RootTextBlock
        5. SceneGroupItems and strokes
        """
        header_blocks = []
        scene_tree_blocks = []
        tree_node_blocks = []
        text_blocks = []
        other_blocks = []

        for block in blocks:
            kind = classify_block(block)

            if kind in (BlockKind.AUTHOR_IDS, BlockKind.MIGRATION_INFO, BlockKind.PAGE_INFO):
                header_blocks.append(block)
            elif kind == BlockKind.SCENE_TREE:
                scene_tree_blocks.append(block)
            elif kind == BlockKind.TREE_NODE:
                tree_node_blocks.append(block)
            elif kind == BlockKind.ROOT_TEXT:
                text_blocks.append(block)
            else:
                other_blocks.append(block)

        return header_blocks + scene_tree_blocks + tree_node_blocks + text_blocks + other_blocks
