"""CRDT utilities for annotation handling.

Provides stateless functions for CRDT operations:
- ID generation (with incrementing counter)
- Block cloning (TreeNodeBlock, SceneTreeBlock, SceneGroupItemBlock)
- Anchor offset updates

Previously these were methods of CrdtService class. Now module-level functions
with explicit counter state, making the stateless operations more obvious.

Usage:
    # Create counter for ID generation
    counter = CrdtIdCounter(base_id=100)

    # Generate IDs
    node_id = generate_id(counter)  # CrdtId(2, 100)
    node_id2 = generate_id(counter)  # CrdtId(2, 101)

    # Clone blocks with updates
    new_tree_node = clone_tree_node_with_anchor(tree_node, new_offset=42)
    new_bundle = prepare_bundle_for_page(bundle, layer_id)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rmscene import (
    CrdtId,
    LwwValue,
    SceneGroupItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)
from rmscene.crdt_sequence import CrdtSequenceItem
from rmscene.scene_items import Group

if TYPE_CHECKING:
    from ..scene_adapter.bundle import StrokeBundle

logger = logging.getLogger(__name__)

# Default author ID for user-created content
USER_AUTHOR_ID = 2

# Default layer ID (system layer 1)
DEFAULT_LAYER_ID = CrdtId(0, 11)


class CrdtIdCounter:
    """Mutable counter for CRDT ID generation.

    Simple wrapper around an integer that can be incremented.
    Used by module functions to generate sequential CRDT IDs.

    Attributes:
        value: Current counter value
        base_id: Original starting value (for reset)
        author_id: Author ID for generated CrdtIds (default: 2 = user)

    Example:
        counter = CrdtIdCounter(base_id=100)
        id1 = generate_id(counter)  # CrdtId(2, 100), counter.value=101
        id2 = generate_id(counter)  # CrdtId(2, 101), counter.value=102
    """

    def __init__(self, base_id: int = 100, author_id: int = USER_AUTHOR_ID):
        """Initialize counter.

        Args:
            base_id: Starting value for counter
            author_id: Author ID for generated CrdtIds
        """
        self.base_id = base_id
        self.value = base_id
        self.author_id = author_id

    def reset(self, base_id: int | None = None) -> None:
        """Reset the counter.

        Args:
            base_id: New base value, or None to reset to original base_id
        """
        if base_id is not None:
            self.base_id = base_id
        self.value = self.base_id
        logger.debug(f"Reset CRDT counter to {self.value}")


def generate_id(counter: CrdtIdCounter) -> CrdtId:
    """Generate the next available CRDT ID.

    Returns a CrdtId with:
    - part1 = counter.author_id (default: 2 for user content)
    - part2 = incrementing counter value

    Args:
        counter: Mutable counter that will be incremented

    Returns:
        New CrdtId
    """
    crdt_id = CrdtId(counter.author_id, counter.value)
    counter.value += 1
    logger.debug(f"Generated CRDT ID: {crdt_id.part1}:{crdt_id.part2}")
    return crdt_id


def clone_tree_node_with_anchor(
    tree_node: TreeNodeBlock,
    new_anchor_offset: int,
    anchor_author_id: int | None = None,
) -> TreeNodeBlock:
    """Clone a TreeNodeBlock with a new anchor offset.

    Creates a new TreeNodeBlock with the same structure but
    with anchor_id pointing to the new character offset.

    Args:
        tree_node: Original TreeNodeBlock
        new_anchor_offset: New character offset for the anchor
        anchor_author_id: Author ID for anchor (default: 1)

    Returns:
        New TreeNodeBlock with updated anchor
    """
    if anchor_author_id is None:
        anchor_author_id = 1  # Anchors typically use author ID 1

    new_anchor_id = CrdtId(anchor_author_id, new_anchor_offset)

    new_group = Group(
        node_id=tree_node.group.node_id,
        label=tree_node.group.label,
        visible=tree_node.group.visible,
        anchor_id=LwwValue(
            timestamp=tree_node.group.anchor_id.timestamp,
            value=new_anchor_id,
        ),
        anchor_type=tree_node.group.anchor_type,
        anchor_threshold=tree_node.group.anchor_threshold,
        anchor_origin_x=tree_node.group.anchor_origin_x,
    )

    new_tree_node = TreeNodeBlock(group=new_group)

    logger.debug(
        f"Cloned TreeNodeBlock with new anchor: "
        f"{tree_node.group.anchor_id.value} -> {new_anchor_id}"
    )
    return new_tree_node


def create_scene_tree_block(
    node_id: CrdtId,
    parent_id: CrdtId | None = None,
) -> SceneTreeBlock:
    """Create a new SceneTreeBlock declaring a node.

    Args:
        node_id: The node being declared
        parent_id: Parent layer (default: 0:11)

    Returns:
        New SceneTreeBlock
    """
    if parent_id is None:
        parent_id = DEFAULT_LAYER_ID

    return SceneTreeBlock(
        tree_id=node_id,
        node_id=CrdtId(0, 0),
        is_update=True,
        parent_id=parent_id,
    )


def create_scene_group_item_block(
    item_id: CrdtId,
    value: CrdtId,
    parent_id: CrdtId | None = None,
) -> SceneGroupItemBlock:
    """Create a new SceneGroupItemBlock linking to a layer.

    Args:
        item_id: ID for this item in the sequence
        value: The node ID being linked
        parent_id: Parent layer (default: 0:11)

    Returns:
        New SceneGroupItemBlock
    """
    if parent_id is None:
        parent_id = DEFAULT_LAYER_ID

    return SceneGroupItemBlock(
        parent_id=parent_id,
        item=CrdtSequenceItem(
            item_id=item_id,
            left_id=CrdtId(0, 0),
            right_id=CrdtId(0, 0),
            deleted_length=0,
            value=value,
        ),
    )


def prepare_bundle_for_page(
    bundle: StrokeBundle,
    layer_id: CrdtId | None = None,
) -> StrokeBundle:
    """Prepare a StrokeBundle for injection into a new page.

    Creates fresh SceneTreeBlock and SceneGroupItemBlock with
    reset CRDT neighbors, ready for injection.

    Args:
        bundle: The StrokeBundle to prepare
        layer_id: Target layer (default: 0:11)

    Returns:
        New StrokeBundle ready for injection
    """
    from ..scene_adapter.bundle import StrokeBundle as StrokeBundleClass

    if layer_id is None:
        layer_id = DEFAULT_LAYER_ID

    # Create fresh SceneTreeBlock
    new_scene_tree = create_scene_tree_block(bundle.node_id, layer_id)

    # Create fresh SceneGroupItemBlock
    old_sgi = bundle.scene_group_item
    if old_sgi:
        new_scene_group_item = create_scene_group_item_block(
            item_id=old_sgi.item.item_id,
            value=bundle.node_id,
            parent_id=layer_id,
        )
    else:
        new_scene_group_item = None

    return StrokeBundleClass(
        node_id=bundle.node_id,
        tree_node=bundle.tree_node,
        scene_tree=new_scene_tree,
        scene_group_item=new_scene_group_item,
        strokes=bundle.strokes,
    )


def reanchor_bundle(
    bundle: StrokeBundle,
    new_anchor_offset: int,
) -> StrokeBundle:
    """Create a new bundle with updated anchor offset.

    Combines tree node cloning with bundle preparation.

    Args:
        bundle: Original StrokeBundle
        new_anchor_offset: New character offset

    Returns:
        New StrokeBundle with updated anchor
    """
    from ..scene_adapter.bundle import StrokeBundle as StrokeBundleClass
    from ..scene_adapter.translator import is_sentinel_anchor

    if not bundle.tree_node:
        return bundle

    # Preserve sentinel anchors unchanged
    if is_sentinel_anchor(bundle.tree_node):
        return bundle

    new_tree_node = clone_tree_node_with_anchor(bundle.tree_node, new_anchor_offset)

    return StrokeBundleClass(
        node_id=bundle.node_id,
        tree_node=new_tree_node,
        scene_tree=bundle.scene_tree,
        scene_group_item=bundle.scene_group_item,
        strokes=bundle.strokes,
    )


# Backwards compatibility: CrdtService class as thin wrapper
class CrdtService:
    """Deprecated: Use module functions with CrdtIdCounter instead.

    This class is kept for backwards compatibility with existing code.
    New code should use the module functions directly.
    """

    def __init__(self, base_id: int = 100, author_id: int = USER_AUTHOR_ID):
        """Initialize service (deprecated)."""
        self._counter = CrdtIdCounter(base_id=base_id, author_id=author_id)

    def generate_id(self) -> CrdtId:
        """Generate ID (deprecated - use generate_id(counter) instead)."""
        return generate_id(self._counter)

    def reset(self, base_id: int | None = None) -> None:
        """Reset counter (deprecated)."""
        self._counter.reset(base_id)

    def clone_tree_node_with_anchor(
        self,
        tree_node: TreeNodeBlock,
        new_anchor_offset: int,
        anchor_author_id: int | None = None,
    ) -> TreeNodeBlock:
        """Clone tree node (deprecated - use module function instead)."""
        return clone_tree_node_with_anchor(tree_node, new_anchor_offset, anchor_author_id)

    def create_scene_tree_block(
        self, node_id: CrdtId, parent_id: CrdtId | None = None
    ) -> SceneTreeBlock:
        """Create scene tree block (deprecated - use module function instead)."""
        return create_scene_tree_block(node_id, parent_id)

    def create_scene_group_item_block(
        self, item_id: CrdtId, value: CrdtId, parent_id: CrdtId | None = None
    ) -> SceneGroupItemBlock:
        """Create scene group item (deprecated - use module function instead)."""
        return create_scene_group_item_block(item_id, value, parent_id)

    def prepare_bundle_for_page(
        self, bundle: StrokeBundle, layer_id: CrdtId | None = None
    ) -> StrokeBundle:
        """Prepare bundle (deprecated - use module function instead)."""
        return prepare_bundle_for_page(bundle, layer_id)

    def reanchor_bundle(self, bundle: StrokeBundle, new_anchor_offset: int) -> StrokeBundle:
        """Reanchor bundle (deprecated - use module function instead)."""
        return reanchor_bundle(bundle, new_anchor_offset)
