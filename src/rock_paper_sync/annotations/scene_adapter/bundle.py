"""StrokeBundle - Groups all blocks required for a complete stroke.

A stroke in the reMarkable v6 CRDT format requires FOUR interdependent blocks:

    SceneTreeBlock → TreeNodeBlock → SceneGroupItemBlock → SceneLineItemBlock
       (declares)      (anchors)        (links to layer)      (stroke data)

When migrating strokes between pages, ALL FOUR blocks must move together.
Missing any block causes the device to fail silently or show errors like
"Unable to find node with id=X:Y".

This module provides the StrokeBundle dataclass that groups these blocks
as an atomic unit for migration operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rmscene import (
    CrdtId,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)

if TYPE_CHECKING:
    from .scene_index import SceneGraphIndex, ValidationError


def format_crdt_id(node_id: CrdtId) -> str:
    """Format CrdtId for display."""
    return f"{node_id.part1}:{node_id.part2}"


def is_user_node(node_id: CrdtId) -> bool:
    """Check if this is a user-created node (part1 == 2)."""
    return node_id.part1 == 2


@dataclass
class StrokeBundle:
    """Groups all blocks required for a complete stroke.

    A stroke in the reMarkable v6 CRDT format requires FOUR interdependent blocks:

    1. SceneTreeBlock - Declares the node exists in the scene tree
    2. TreeNodeBlock - Defines the node and its text anchor
    3. SceneGroupItemBlock - Links the node to its parent layer (usually 0:11)
    4. SceneLineItemBlock - Contains the actual stroke data (points, color, etc.)

    When migrating strokes between pages, ALL FOUR blocks must move together.
    Missing any block causes the device to fail silently or show errors like
    "Unable to find node with id=X:Y".

    This is a data container for stroke block grouping.
    Used by generator._apply_annotations_to_page() for atomic stroke migration.

    Example:
        index = SceneGraphIndex.from_file(rm_path)
        bundles = StrokeBundle.from_index(index)

        for bundle in bundles:
            if bundle.is_complete:
                # Safe to migrate - all 4 blocks present
                target_blocks.extend(bundle.to_raw_blocks())
    """

    node_id: CrdtId
    tree_node: TreeNodeBlock | None = None
    scene_tree: SceneTreeBlock | None = None
    scene_group_item: SceneGroupItemBlock | None = None
    strokes: list[SceneLineItemBlock] = field(default_factory=list)

    @property
    def anchor_offset(self) -> int | None:
        """Get the text anchor offset from the TreeNodeBlock."""
        if self.tree_node and self.tree_node.group.anchor_id:
            anchor_val = self.tree_node.group.anchor_id.value
            return anchor_val.part2 if isinstance(anchor_val, CrdtId) else None
        return None

    @property
    def is_complete(self) -> bool:
        """Check if all 4 required block types are present."""
        return (
            self.tree_node is not None
            and self.scene_tree is not None
            and self.scene_group_item is not None
            and len(self.strokes) > 0
        )

    @property
    def missing_blocks(self) -> list[str]:
        """List which block types are missing."""
        missing = []
        if self.tree_node is None:
            missing.append("TreeNodeBlock")
        if self.scene_tree is None:
            missing.append("SceneTreeBlock")
        if self.scene_group_item is None:
            missing.append("SceneGroupItemBlock")
        if not self.strokes:
            missing.append("SceneLineItemBlock")
        return missing

    def to_raw_blocks(self) -> list[Any]:
        """Get all blocks for writing.

        Returns blocks in the correct order for serialization:
        1. SceneTreeBlock (declaration)
        2. TreeNodeBlock (anchor)
        3. SceneGroupItemBlock (layer link)
        4. SceneLineItemBlock(s) (stroke data)
        """
        blocks: list[Any] = []
        if self.scene_tree:
            blocks.append(self.scene_tree)
        if self.tree_node:
            blocks.append(self.tree_node)
        if self.scene_group_item:
            blocks.append(self.scene_group_item)
        blocks.extend(self.strokes)
        return blocks

    @classmethod
    def from_index(cls, index: SceneGraphIndex) -> list[StrokeBundle]:
        """Extract all StrokeBundles from a SceneGraphIndex.

        Groups blocks by their node_id to form complete bundles.
        Only returns bundles for user-created nodes (part1 == 2).
        """
        bundles: dict[tuple[int, int], StrokeBundle] = {}

        def key(node_id: CrdtId) -> tuple[int, int]:
            return (node_id.part1, node_id.part2)

        # Start with user TreeNodeBlocks as the primary keys
        for node_id, tree_node in index.tree_nodes.items():
            if is_user_node(node_id):
                k = key(node_id)
                if k not in bundles:
                    bundles[k] = cls(node_id=node_id)
                bundles[k].tree_node = tree_node

        # Add SceneTreeBlocks
        for tree_id, scene_tree in index.scene_trees.items():
            if is_user_node(tree_id):
                k = key(tree_id)
                if k not in bundles:
                    bundles[k] = cls(node_id=tree_id)
                bundles[k].scene_tree = scene_tree

        # Add SceneGroupItemBlocks (value is the TreeNodeBlock they link to)
        for value, sgi in index.scene_group_items.items():
            if is_user_node(value):
                k = key(value)
                if k not in bundles:
                    bundles[k] = cls(node_id=value)
                bundles[k].scene_group_item = sgi

        # Add strokes (parent_id references the TreeNodeBlock)
        for stroke in index.strokes:
            if is_user_node(stroke.parent_id):
                k = key(stroke.parent_id)
                if k not in bundles:
                    bundles[k] = cls(node_id=stroke.parent_id)
                bundles[k].strokes.append(stroke)

        return list(bundles.values())

    def validate(self) -> list[ValidationError]:
        """Validate this bundle's integrity."""
        from .scene_index import ValidationError

        errors = []

        if not self.tree_node:
            errors.append(
                ValidationError(
                    error_type="MISSING_TREE_NODE",
                    message="StrokeBundle missing TreeNodeBlock",
                    node_id=self.node_id,
                )
            )

        if not self.scene_tree:
            errors.append(
                ValidationError(
                    error_type="MISSING_SCENE_TREE",
                    message="StrokeBundle missing SceneTreeBlock declaration",
                    node_id=self.node_id,
                )
            )

        if not self.scene_group_item:
            errors.append(
                ValidationError(
                    error_type="MISSING_SCENE_GROUP_ITEM",
                    message="StrokeBundle missing SceneGroupItemBlock (not linked to layer)",
                    node_id=self.node_id,
                )
            )

        if not self.strokes:
            errors.append(
                ValidationError(
                    error_type="MISSING_STROKE_DATA",
                    message="StrokeBundle has no SceneLineItemBlock stroke data",
                    node_id=self.node_id,
                )
            )

        return errors

    def __str__(self) -> str:
        status = (
            "COMPLETE" if self.is_complete else f"INCOMPLETE ({', '.join(self.missing_blocks)})"
        )
        anchor_str = (
            f"anchor={self.anchor_offset}" if self.anchor_offset is not None else "no anchor"
        )
        return f"StrokeBundle({format_crdt_id(self.node_id)}, {anchor_str}, {len(self.strokes)} strokes, {status})"
