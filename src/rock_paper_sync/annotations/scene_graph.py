"""Scene graph utilities for rmscene blocks.

This module provides indexing, bundling, and validation for the scene graph
structure required by reMarkable devices.

The reMarkable v6 CRDT scene graph has this structure:
    SceneTreeBlock → TreeNodeBlock → SceneGroupItemBlock → SceneLineItemBlock
       (declares)      (anchors)        (links to layer)      (stroke data)

Key relationships:
- Every user-created TreeNodeBlock MUST have a SceneTreeBlock declaring it
- Every SceneGroupItemBlock.value MUST reference an existing TreeNodeBlock
- Every SceneGroupItemBlock.parent_id MUST reference an existing node (usually 0:11)
- Every SceneLineItemBlock.parent_id MUST reference an existing TreeNodeBlock

Author IDs (CrdtId.part1):
- 0: System-created (implicit nodes like root, layers)
- 1: Generator-created (text blocks, formatting)
- 2: User-created (annotations, strokes, highlights)
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import rmscene
from rmscene import (
    CrdtId,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)

# =============================================================================
# CrdtId Helpers
# =============================================================================


def is_system_node(node_id: CrdtId) -> bool:
    """Check if this is a system node (part1 == 0)."""
    return node_id.part1 == 0


def is_user_node(node_id: CrdtId) -> bool:
    """Check if this is a user-created node (part1 == 2)."""
    return node_id.part1 == 2


def format_crdt_id(node_id: CrdtId) -> str:
    """Format CrdtId for display."""
    return f"{node_id.part1}:{node_id.part2}"


# Well-known system node IDs
SYSTEM_ROOT = CrdtId(0, 1)
SYSTEM_LAYER_1 = CrdtId(0, 11)
SYSTEM_LAYER_1_GROUP = CrdtId(0, 13)

KNOWN_SYSTEM_NODES = frozenset({SYSTEM_ROOT, SYSTEM_LAYER_1, SYSTEM_LAYER_1_GROUP})


# =============================================================================
# StrokeBundle - Groups all blocks for a single stroke
# =============================================================================


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

    This is a data container that simplifies the work of StrokeHandler/AnnotationPreserver.
    Those units own the mutation logic; StrokeBundle just ensures blocks are grouped.

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


# =============================================================================
# Scene Graph Index
# =============================================================================


@dataclass
class SceneGraphIndex:
    """Index of scene graph blocks for fast lookup.

    Provides O(1) lookups for:
    - TreeNodeBlocks by node_id
    - SceneTreeBlocks by tree_id
    - SceneGroupItemBlocks by value (the TreeNodeBlock they link to)
    """

    tree_nodes: dict[CrdtId, TreeNodeBlock] = field(default_factory=dict)
    scene_trees: dict[CrdtId, SceneTreeBlock] = field(default_factory=dict)
    scene_group_items: dict[CrdtId, SceneGroupItemBlock] = field(default_factory=dict)
    strokes: list[SceneLineItemBlock] = field(default_factory=list)
    all_node_ids: set[CrdtId] = field(default_factory=set)

    @classmethod
    def from_blocks(cls, blocks: list[Any]) -> SceneGraphIndex:
        """Build index from list of rmscene blocks."""
        index = cls()

        # Add known system nodes
        index.all_node_ids.update(KNOWN_SYSTEM_NODES)

        for block in blocks:
            if isinstance(block, TreeNodeBlock):
                node_id = block.group.node_id
                index.tree_nodes[node_id] = block
                index.all_node_ids.add(node_id)

            elif isinstance(block, SceneTreeBlock):
                index.scene_trees[block.tree_id] = block

            elif isinstance(block, SceneGroupItemBlock):
                value = block.item.value
                index.scene_group_items[value] = block

            elif isinstance(block, SceneLineItemBlock):
                index.strokes.append(block)

        return index

    @classmethod
    def from_bytes(cls, rm_bytes: bytes) -> SceneGraphIndex:
        """Build index from .rm file bytes."""
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        return cls.from_blocks(blocks)

    @classmethod
    def from_file(cls, path: Path) -> SceneGraphIndex:
        """Build index from .rm file path."""
        with path.open("rb") as f:
            blocks = list(rmscene.read_blocks(f))
        return cls.from_blocks(blocks)

    def node_exists(self, node_id: CrdtId) -> bool:
        """Check if a node ID exists (including system nodes)."""
        return node_id in self.all_node_ids


# =============================================================================
# Validation
# =============================================================================


@dataclass
class ValidationError:
    """A validation error found in scene graph structure."""

    error_type: str
    message: str
    node_id: CrdtId | None = None
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        if self.node_id:
            return f"[{self.error_type}] {format_crdt_id(self.node_id)}: {self.message}"
        return f"[{self.error_type}] {self.message}"


@dataclass
class SceneGraphValidationResult:
    """Result of scene graph validation."""

    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    index: SceneGraphIndex | None = None

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def tree_node_count(self) -> int:
        return len(self.index.tree_nodes) if self.index else 0

    @property
    def scene_tree_count(self) -> int:
        return len(self.index.scene_trees) if self.index else 0

    @property
    def scene_group_item_count(self) -> int:
        return len(self.index.scene_group_items) if self.index else 0

    @property
    def stroke_count(self) -> int:
        return len(self.index.strokes) if self.index else 0

    def __str__(self) -> str:
        status = "PASS" if self.is_valid else "FAIL"
        lines = [
            f"Scene Graph Validation: {status}",
            f"  TreeNodeBlocks: {self.tree_node_count}",
            f"  SceneTreeBlocks: {self.scene_tree_count}",
            f"  SceneGroupItemBlocks: {self.scene_group_item_count}",
            f"  Strokes: {self.stroke_count}",
        ]
        if self.errors:
            lines.append(f"  Errors ({len(self.errors)}):")
            for error in self.errors:
                lines.append(f"    - {error}")
        if self.warnings:
            lines.append(f"  Warnings ({len(self.warnings)}):")
            for warning in self.warnings:
                lines.append(f"    - {warning}")
        return "\n".join(lines)


def validate_scene_graph(rm_bytes: bytes) -> SceneGraphValidationResult:
    """Validate scene graph structure for device compatibility.

    Checks:
    1. Every SceneGroupItemBlock.value has a corresponding TreeNodeBlock
    2. Every user-created TreeNodeBlock has a corresponding SceneTreeBlock
    3. All parent_id references resolve to existing nodes
    4. All stroke parent_ids reference existing TreeNodeBlocks
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    try:
        index = SceneGraphIndex.from_bytes(rm_bytes)
    except Exception as e:
        errors.append(
            ValidationError(
                error_type="READ_ERROR",
                message=f"Failed to parse .rm bytes: {e}",
            )
        )
        return SceneGraphValidationResult(errors=errors, warnings=warnings)

    # Validation 1: Every SceneGroupItemBlock.value must have a TreeNodeBlock
    for value, sgi in index.scene_group_items.items():
        if not index.node_exists(value):
            errors.append(
                ValidationError(
                    error_type="ORPHANED_SCENE_GROUP_ITEM",
                    message=f"SceneGroupItemBlock.value={format_crdt_id(value)} has no TreeNodeBlock",
                    node_id=value,
                    details={"parent_id": format_crdt_id(sgi.parent_id)},
                )
            )

    # Validation 2: Every SceneGroupItemBlock.parent_id must exist
    for value, sgi in index.scene_group_items.items():
        if not index.node_exists(sgi.parent_id):
            errors.append(
                ValidationError(
                    error_type="MISSING_PARENT",
                    message=f"SceneGroupItemBlock.parent_id={format_crdt_id(sgi.parent_id)} not found",
                    node_id=value,
                    details={"parent_id": format_crdt_id(sgi.parent_id)},
                )
            )

    # Validation 3: Every user-created TreeNodeBlock must have a SceneTreeBlock
    for node_id in index.tree_nodes:
        if is_user_node(node_id) and node_id not in index.scene_trees:
            errors.append(
                ValidationError(
                    error_type="UNDECLARED_TREE_NODE",
                    message=f"TreeNodeBlock {format_crdt_id(node_id)} has no SceneTreeBlock",
                    node_id=node_id,
                )
            )

    # Validation 4: Every stroke parent_id must reference an existing TreeNodeBlock
    for stroke in index.strokes:
        if not index.node_exists(stroke.parent_id):
            errors.append(
                ValidationError(
                    error_type="ORPHANED_STROKE",
                    message=f"Stroke parent_id={format_crdt_id(stroke.parent_id)} not found",
                    details={"parent_id": format_crdt_id(stroke.parent_id)},
                )
            )

    # Warning: User TreeNodeBlocks without SceneGroupItemBlock
    for node_id in index.tree_nodes:
        if is_user_node(node_id) and node_id not in index.scene_group_items:
            warnings.append(
                ValidationError(
                    error_type="UNLINKED_TREE_NODE",
                    message=f"TreeNodeBlock {format_crdt_id(node_id)} has no SceneGroupItemBlock",
                    node_id=node_id,
                )
            )

    return SceneGraphValidationResult(
        errors=errors,
        warnings=warnings,
        index=index,
    )


def validate_scene_graph_file(rm_path: Path) -> SceneGraphValidationResult:
    """Validate scene graph from a file path."""
    with rm_path.open("rb") as f:
        return validate_scene_graph(f.read(), str(rm_path))
