"""CRDT-aware Stroke representation.

This module provides the new Stroke class that knows its own CRDT structure,
replacing the older Stroke/StrokeData classes in core_types.py.

A stroke in the reMarkable v6 CRDT format requires FOUR interdependent blocks:
1. SceneTreeBlock - Declares the node exists in the scene tree
2. TreeNodeBlock - Defines the node and its text anchor
3. SceneGroupItemBlock - Links the node to its parent layer
4. SceneLineItemBlock - Contains the actual stroke data (points, color, etc.)

This class encapsulates a single SceneLineItemBlock with references to its
parent CRDT blocks for proper serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rmscene import CrdtId

if TYPE_CHECKING:
    from rmscene.scene_items import (
        SceneGroupItemBlock,
        SceneLineItemBlock,
        SceneTreeBlock,
        TreeNodeBlock,
    )

    from .scene_graph import SceneGraphIndex


@dataclass
class Stroke:
    """A single stroke that knows its CRDT structure.

    Each Stroke encapsulates one SceneLineItemBlock with references to the
    parent CRDT blocks (TreeNode, SceneTree, SceneGroupItem) needed for
    proper serialization.

    Multiple Strokes may share the same parent blocks (same node_id).
    When serializing a collection of Strokes, parent blocks should be
    deduplicated - see StrokeCluster.to_rm_blocks().

    Attributes:
        line_block: The SceneLineItemBlock containing point data
        node_id: The CRDT ID linking this stroke to its parent blocks
        tree_node: Reference to parent TreeNodeBlock (shared with siblings)
        scene_tree: Reference to parent SceneTreeBlock (shared with siblings)
        scene_group_item: Reference to parent SceneGroupItemBlock (shared with siblings)

    Example:
        # Extract strokes from an rm file
        strokes = Stroke.all_from_rm_file(rm_path)

        # Each stroke knows its parent CRDT structure
        for stroke in strokes:
            print(f"Stroke {stroke.node_id} at y={stroke.center_y:.1f}")
            print(f"  Anchor offset: {stroke.anchor_offset}")
    """

    line_block: SceneLineItemBlock
    node_id: CrdtId
    tree_node: TreeNodeBlock | None = None
    scene_tree: SceneTreeBlock | None = None
    scene_group_item: SceneGroupItemBlock | None = None

    # Cached bounding box (computed lazily)
    _bounding_box: tuple[float, float, float, float] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def _line_value(self):
        """Access the Line value inside the SceneLineItemBlock."""
        return self.line_block.item.value

    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        """Get axis-aligned bounding box (x, y, w, h)."""
        if self._bounding_box is None:
            points = self._line_value.points
            if not points:
                self._bounding_box = (0.0, 0.0, 0.0, 0.0)
            else:
                xs = [p.x for p in points]
                ys = [p.y for p in points]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                self._bounding_box = (min_x, min_y, max_x - min_x, max_y - min_y)
        return self._bounding_box

    @property
    def center_y(self) -> float:
        """Get vertical center of the stroke."""
        x, y, w, h = self.bounding_box
        return y + h / 2

    @property
    def center(self) -> tuple[float, float]:
        """Get center point (x, y) for clustering."""
        x, y, w, h = self.bounding_box
        return (x + w / 2, y + h / 2)

    @property
    def color(self) -> int:
        """Get stroke color code."""
        return self._line_value.color.value

    @property
    def tool(self) -> int:
        """Get pen tool type."""
        return self._line_value.tool.value

    @property
    def thickness(self) -> float:
        """Get stroke thickness scale."""
        return self._line_value.thickness_scale

    @property
    def points(self) -> list[tuple[float, float, float]]:
        """Get points as (x, y, pressure) tuples."""
        return [
            (p.x, p.y, p.pressure if hasattr(p, "pressure") else 0.0)
            for p in self._line_value.points
        ]

    @property
    def anchor_offset(self) -> int | None:
        """Get the text anchor offset from the TreeNodeBlock."""
        if self.tree_node and self.tree_node.group.anchor_id:
            anchor_val = self.tree_node.group.anchor_id.value
            return anchor_val.part2 if isinstance(anchor_val, CrdtId) else None
        return None

    @property
    def is_complete(self) -> bool:
        """Check if all required CRDT blocks are present."""
        return (
            self.tree_node is not None
            and self.scene_tree is not None
            and self.scene_group_item is not None
        )

    def to_rm_blocks(self) -> list:
        """Serialize this stroke's CRDT blocks.

        Returns blocks in correct order for serialization:
        1. SceneTreeBlock (declaration)
        2. TreeNodeBlock (anchor)
        3. SceneGroupItemBlock (layer link)
        4. SceneLineItemBlock (stroke data)

        Note: When serializing multiple strokes that share parent blocks,
        use StrokeCluster.to_rm_blocks() to deduplicate parent blocks.
        """
        blocks = []
        if self.scene_tree:
            blocks.append(self.scene_tree)
        if self.tree_node:
            blocks.append(self.tree_node)
        if self.scene_group_item:
            blocks.append(self.scene_group_item)
        blocks.append(self.line_block)
        return blocks

    @classmethod
    def all_from_scene_index(cls, index: SceneGraphIndex) -> list[Stroke]:
        """Extract all Strokes from a SceneGraphIndex.

        Each SceneLineItemBlock becomes one Stroke, with references to its
        parent blocks (TreeNode, SceneTree, SceneGroupItem).

        Args:
            index: SceneGraphIndex containing parsed blocks

        Returns:
            List of Stroke objects with CRDT structure attached
        """
        from .scene_graph import is_user_node

        strokes = []

        for line_block in index.strokes:
            parent_id = line_block.parent_id
            if not is_user_node(parent_id):
                continue

            stroke = cls(
                line_block=line_block,
                node_id=parent_id,
                tree_node=index.tree_nodes.get(parent_id),
                scene_tree=index.scene_trees.get(parent_id),
                scene_group_item=index.scene_group_items.get(parent_id),
            )
            strokes.append(stroke)

        return strokes

    @classmethod
    def all_from_rm_file(cls, rm_path) -> list[Stroke]:
        """Extract all Strokes from an .rm file.

        Args:
            rm_path: Path to .rm file

        Returns:
            List of Stroke objects with CRDT structure attached
        """
        from .scene_graph import SceneGraphIndex

        index = SceneGraphIndex.from_file(rm_path)
        return cls.all_from_scene_index(index)
