"""Translator between domain annotations and rmscene blocks.

This module is the ONLY place that converts between:
- Domain concepts (DomainAnnotation, AnchorContext, StrokeData)
- rmscene blocks (SceneLineItemBlock, TreeNodeBlock, etc.)

All other code should use the translator to cross the layer boundary.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import rmscene
from rmscene import CrdtId

from rock_paper_sync.coordinate_transformer import END_OF_DOC_ANCHOR_MARKER

from .block_registry import BlockKind, classify_block
from .bundle import StrokeBundle
from .scene_index import SceneGraphIndex

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def extract_unknown_blocks(blocks: list[Any]) -> list[Any]:
    """Extract all unknown blocks from a list of rmscene blocks.

    Unknown blocks are preserved verbatim through roundtrip.
    This protects future pen types, annotation styles, etc.

    Args:
        blocks: List of rmscene blocks

    Returns:
        List of blocks classified as UNKNOWN
    """
    return [b for b in blocks if classify_block(b) == BlockKind.UNKNOWN]


def extract_annotation_blocks(blocks: list[Any]) -> tuple[list[Any], list[Any]]:
    """Extract stroke and highlight blocks from a list of rmscene blocks.

    Args:
        blocks: List of rmscene blocks

    Returns:
        (stroke_blocks, highlight_blocks) tuple
    """
    strokes = []
    highlights = []

    for block in blocks:
        kind = classify_block(block)
        if kind == BlockKind.STROKE:
            strokes.append(block)
        elif kind == BlockKind.HIGHLIGHT:
            highlights.append(block)

    return strokes, highlights


def build_stroke_bundles(blocks: list[Any]) -> list[StrokeBundle]:
    """Build complete StrokeBundles from a list of rmscene blocks.

    Groups related blocks (TreeNodeBlock, SceneTreeBlock, SceneGroupItemBlock,
    SceneLineItemBlock) by their node_id to form complete bundles.

    Args:
        blocks: List of rmscene blocks

    Returns:
        List of StrokeBundles (may be incomplete if blocks are missing)
    """
    index = SceneGraphIndex.from_blocks(blocks)
    return StrokeBundle.from_index(index)


def get_anchor_offset_from_tree_node(tree_node: Any) -> int | None:
    """Extract the anchor character offset from a TreeNodeBlock.

    Args:
        tree_node: A TreeNodeBlock

    Returns:
        Character offset, or None if not present or is sentinel
    """
    if not tree_node:
        return None
    if not hasattr(tree_node, "group") or not tree_node.group:
        return None
    if not tree_node.group.anchor_id:
        return None

    anchor_val = tree_node.group.anchor_id.value
    if not isinstance(anchor_val, CrdtId):
        return None

    # Check for sentinel (end-of-document marker)
    if anchor_val.part2 == END_OF_DOC_ANCHOR_MARKER:
        return None

    return anchor_val.part2


def is_sentinel_anchor(tree_node: Any) -> bool:
    """Check if a TreeNodeBlock has a sentinel (end-of-document) anchor.

    Sentinel anchors are used for margin notes that aren't tied to
    specific text positions.
    """
    if not tree_node:
        return False
    if not hasattr(tree_node, "group") or not tree_node.group:
        return False
    if not tree_node.group.anchor_id:
        return False

    anchor_val = tree_node.group.anchor_id.value
    if not isinstance(anchor_val, CrdtId):
        return False

    return anchor_val.part2 == END_OF_DOC_ANCHOR_MARKER


class SceneTranslator:
    """Bidirectional translation between domain and rmscene.

    This class encapsulates all knowledge of rmscene block structure.
    Domain code uses this to:
    - Extract annotations from .rm files
    - Convert annotations back to blocks for injection
    """

    def extract_from_file(
        self,
        rm_path: Path,
    ) -> tuple[list[StrokeBundle], list[Any], list[Any]]:
        """Extract all data from an .rm file.

        Returns:
            (stroke_bundles, highlight_blocks, unknown_blocks) tuple

            - stroke_bundles: Complete StrokeBundles for each stroke group
            - highlight_blocks: Raw highlight blocks (SceneGlyphItemBlock)
            - unknown_blocks: Unknown blocks to preserve verbatim
        """
        with open(rm_path, "rb") as f:
            blocks = list(rmscene.read_blocks(f))

        # Build stroke bundles
        stroke_bundles = build_stroke_bundles(blocks)

        # Extract highlights
        _, highlight_blocks = extract_annotation_blocks(blocks)

        # Extract unknown blocks
        unknown_blocks = extract_unknown_blocks(blocks)

        return stroke_bundles, highlight_blocks, unknown_blocks

    def extract_from_bytes(
        self,
        rm_bytes: bytes,
    ) -> tuple[list[StrokeBundle], list[Any], list[Any]]:
        """Extract all data from .rm bytes.

        Returns:
            (stroke_bundles, highlight_blocks, unknown_blocks) tuple
        """
        import io

        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))

        stroke_bundles = build_stroke_bundles(blocks)
        _, highlight_blocks = extract_annotation_blocks(blocks)
        unknown_blocks = extract_unknown_blocks(blocks)

        return stroke_bundles, highlight_blocks, unknown_blocks

    def reanchor_bundle(
        self,
        bundle: StrokeBundle,
        new_anchor_offset: int,
        author_id: int = 1,
    ) -> StrokeBundle:
        """Create a new bundle with updated anchor offset.

        This creates new TreeNodeBlock with the anchor_id pointing to
        the new character offset. Other blocks are preserved as-is.

        Args:
            bundle: The original StrokeBundle
            new_anchor_offset: New character offset for the anchor
            author_id: Author ID for the new anchor CrdtId

        Returns:
            New StrokeBundle with updated TreeNodeBlock
        """
        if not bundle.tree_node:
            return bundle

        # Check for sentinel anchor - preserve it unchanged
        if is_sentinel_anchor(bundle.tree_node):
            return bundle

        # Create updated TreeNodeBlock with new anchor
        old_tree_node = bundle.tree_node
        new_anchor_id = CrdtId(author_id, new_anchor_offset)

        # Deep copy the group with new anchor
        from rmscene import LwwValue
        from rmscene.scene_items import Group

        new_group = Group(
            node_id=old_tree_node.group.node_id,
            label=old_tree_node.group.label,
            visible=old_tree_node.group.visible,
            anchor_id=LwwValue(
                timestamp=old_tree_node.group.anchor_id.timestamp,
                value=new_anchor_id,
            ),
            anchor_type=old_tree_node.group.anchor_type,
            anchor_threshold=old_tree_node.group.anchor_threshold,
            anchor_origin_x=old_tree_node.group.anchor_origin_x,
        )

        from rmscene import TreeNodeBlock

        new_tree_node = TreeNodeBlock(
            group=new_group,
        )

        return StrokeBundle(
            node_id=bundle.node_id,
            tree_node=new_tree_node,
            scene_tree=bundle.scene_tree,
            scene_group_item=bundle.scene_group_item,
            strokes=bundle.strokes,
        )

    def prepare_bundle_for_injection(
        self,
        bundle: StrokeBundle,
        layer_id: CrdtId | None = None,
    ) -> StrokeBundle:
        """Prepare a bundle for injection into a new page.

        When moving a stroke to a new page, we need to:
        1. Create a fresh SceneTreeBlock declaring the node
        2. Create a fresh SceneGroupItemBlock with reset CRDT neighbors

        Args:
            bundle: The StrokeBundle to prepare
            layer_id: The layer to link to (default: 0:11)

        Returns:
            New StrokeBundle ready for injection
        """
        from rmscene import SceneGroupItemBlock, SceneTreeBlock
        from rmscene.crdt_sequence import CrdtSequenceItem

        if layer_id is None:
            layer_id = CrdtId(0, 11)

        # Create fresh SceneTreeBlock declaring this node
        new_scene_tree = SceneTreeBlock(
            tree_id=bundle.node_id,
            node_id=CrdtId(0, 0),
            is_update=True,
            parent_id=layer_id,
        )

        # Create fresh SceneGroupItemBlock with reset neighbors
        old_sgi = bundle.scene_group_item
        if old_sgi:
            new_scene_group_item = SceneGroupItemBlock(
                parent_id=layer_id,
                item=CrdtSequenceItem(
                    item_id=old_sgi.item.item_id,
                    left_id=CrdtId(0, 0),
                    right_id=CrdtId(0, 0),
                    deleted_length=0,
                    value=bundle.node_id,
                ),
            )
        else:
            new_scene_group_item = None

        return StrokeBundle(
            node_id=bundle.node_id,
            tree_node=bundle.tree_node,
            scene_tree=new_scene_tree,
            scene_group_item=new_scene_group_item,
            strokes=bundle.strokes,
        )
