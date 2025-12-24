"""CRDT Service - Centralized CRDT operations for annotation handling.

This service encapsulates all CRDT-related operations:
- ID generation (with incrementing counters)
- Block cloning (TreeNodeBlock, SceneTreeBlock, SceneGroupItemBlock)
- Anchor offset updates

Previously these operations were scattered across:
- generator.py (ID generation)
- translator.py (block cloning)
- handler implementations

Now all CRDT operations go through this service, making them:
- Testable (inject mock service)
- Consistent (single source of truth for ID generation)
- Documented (centralized CRDT knowledge)

Usage:
    # Create service with starting ID
    service = CrdtService(base_id=100)

    # Generate IDs
    node_id = service.generate_id()  # CrdtId(2, 100)
    node_id2 = service.generate_id()  # CrdtId(2, 101)

    # Clone blocks with updates
    new_tree_node = service.clone_tree_node_with_anchor(tree_node, new_offset=42)
    new_bundle = service.prepare_bundle_for_page(bundle, layer_id)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


@dataclass
class CrdtService:
    """Service for CRDT operations: ID generation and block cloning.

    Centralizes CRDT logic that was previously scattered across:
    - generator.py (ID generation)
    - translator.py (block cloning)
    - handler implementations

    The service maintains state (next_id counter) and provides
    methods for common CRDT operations.

    Attributes:
        base_id: Starting ID for generation (default: 100)
        author_id: Author ID for new blocks (default: 2 = user)
        next_id: Current counter for ID generation

    Example:
        service = CrdtService(base_id=100)

        # Generate sequential IDs
        id1 = service.generate_id()  # CrdtId(2, 100)
        id2 = service.generate_id()  # CrdtId(2, 101)

        # Clone with updated anchor
        new_tree_node = service.clone_tree_node_with_anchor(old_node, 42)
    """

    base_id: int = 100
    author_id: int = USER_AUTHOR_ID
    next_id: int = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the ID counter."""
        self.next_id = self.base_id

    def generate_id(self) -> CrdtId:
        """Generate the next available CRDT ID.

        Returns a CrdtId with:
        - part1 = author_id (default: 2 for user content)
        - part2 = incrementing counter

        Returns:
            New CrdtId
        """
        crdt_id = CrdtId(self.author_id, self.next_id)
        self.next_id += 1
        logger.debug(f"Generated CRDT ID: {crdt_id.part1}:{crdt_id.part2}")
        return crdt_id

    def reset(self, base_id: int | None = None) -> None:
        """Reset the ID counter.

        Args:
            base_id: New base ID, or None to use original base_id
        """
        if base_id is not None:
            self.base_id = base_id
        self.next_id = self.base_id
        logger.debug(f"Reset CRDT counter to {self.next_id}")

    def clone_tree_node_with_anchor(
        self,
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
        self,
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
        self,
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
        self,
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
        new_scene_tree = self.create_scene_tree_block(bundle.node_id, layer_id)

        # Create fresh SceneGroupItemBlock
        old_sgi = bundle.scene_group_item
        if old_sgi:
            new_scene_group_item = self.create_scene_group_item_block(
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
        self,
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

        new_tree_node = self.clone_tree_node_with_anchor(bundle.tree_node, new_anchor_offset)

        return StrokeBundleClass(
            node_id=bundle.node_id,
            tree_node=new_tree_node,
            scene_tree=bundle.scene_tree,
            scene_group_item=bundle.scene_group_item,
            strokes=bundle.strokes,
        )
