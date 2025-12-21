"""Typed scene graph layer for rmscene blocks.

This module provides type-safe wrappers around rmscene blocks and validation
for the scene graph structure required by reMarkable devices.

The reMarkable v6 CRDT scene graph has this structure:
    SceneTreeBlock → TreeNodeBlock → SceneGroupItemBlock → SceneLineItemBlock
       (declares)      (anchors)        (links to layer)      (stroke data)

Key relationships:
- Every user-created TreeNodeBlock MUST have a SceneTreeBlock declaring it
- Every SceneGroupItemBlock.value MUST reference an existing TreeNodeBlock
- Every SceneGroupItemBlock.parent_id MUST reference an existing node (usually 0:11)
- Every SceneLineItemBlock.parent_id MUST reference an existing TreeNodeBlock

System nodes (part1 == 0):
- 0:1 - Root node
- 0:11 - Layer 1 (default layer for strokes)
- 0:13 - Layer 1 scene group

Author IDs:
- part1 == 0: System-created (implicit)
- part1 == 1: Device/generator-created (for text blocks)
- part1 == 2: User-created (annotations)
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

import rmscene

# =============================================================================
# CRDT ID Types
# =============================================================================


class AuthorId(IntEnum):
    """CRDT author IDs used in reMarkable files."""

    SYSTEM = 0  # System-defined (implicit nodes like root, layers)
    GENERATOR = 1  # Generator-created (text blocks, formatting)
    USER = 2  # User-created (annotations, strokes, highlights)


@dataclass(frozen=True)
class CrdtId:
    """Type-safe wrapper for rmscene CrdtId.

    A CRDT ID consists of:
    - part1: Author/namespace (0=system, 1=generator, 2=user)
    - part2: Unique sequence number within that namespace
    """

    part1: int
    part2: int

    @classmethod
    def from_rmscene(cls, crdt_id: Any) -> CrdtId | None:
        """Create from rmscene CrdtId object."""
        if crdt_id is None:
            return None
        if hasattr(crdt_id, "part1") and hasattr(crdt_id, "part2"):
            return cls(part1=crdt_id.part1, part2=crdt_id.part2)
        return None

    @property
    def is_system(self) -> bool:
        """Check if this is a system node (part1 == 0)."""
        return self.part1 == AuthorId.SYSTEM

    @property
    def is_user(self) -> bool:
        """Check if this is a user-created node (part1 == 2)."""
        return self.part1 == AuthorId.USER

    def __str__(self) -> str:
        return f"{self.part1}:{self.part2}"


# Well-known system node IDs
SYSTEM_ROOT = CrdtId(0, 1)
SYSTEM_LAYER_1 = CrdtId(0, 11)
SYSTEM_LAYER_1_GROUP = CrdtId(0, 13)

# Known system nodes for validation
KNOWN_SYSTEM_NODES = frozenset({SYSTEM_ROOT, SYSTEM_LAYER_1, SYSTEM_LAYER_1_GROUP})


# =============================================================================
# Typed Block Wrappers
# =============================================================================


@dataclass
class TypedTreeNodeBlock:
    """Type-safe wrapper for rmscene TreeNodeBlock.

    A TreeNodeBlock defines a node in the scene tree with:
    - node_id: Unique CRDT identifier
    - anchor_id: Optional text anchor position
    - anchor_type: How the node is anchored (character position, etc.)
    """

    node_id: CrdtId
    anchor_offset: int | None = None
    raw_block: Any = None  # Original rmscene block

    @classmethod
    def from_rmscene(cls, block: Any) -> TypedTreeNodeBlock | None:
        """Create from rmscene TreeNodeBlock."""
        if not hasattr(block, "group") or not block.group:
            return None

        group = block.group
        node_id = CrdtId.from_rmscene(group.node_id)
        if not node_id:
            return None

        # Extract anchor offset
        anchor_offset = None
        if hasattr(group, "anchor_id") and group.anchor_id:
            anchor_val = group.anchor_id.value
            if hasattr(anchor_val, "part2"):
                anchor_offset = anchor_val.part2
            else:
                anchor_offset = anchor_val

        return cls(
            node_id=node_id,
            anchor_offset=anchor_offset,
            raw_block=block,
        )


@dataclass
class TypedSceneTreeBlock:
    """Type-safe wrapper for rmscene SceneTreeBlock.

    A SceneTreeBlock declares a node exists in the scene tree.
    Every user-created TreeNodeBlock MUST have a corresponding SceneTreeBlock.
    """

    tree_id: CrdtId
    raw_block: Any = None

    @classmethod
    def from_rmscene(cls, block: Any) -> TypedSceneTreeBlock | None:
        """Create from rmscene SceneTreeBlock."""
        if not hasattr(block, "tree_id") or not block.tree_id:
            return None

        tree_id = CrdtId.from_rmscene(block.tree_id)
        if not tree_id:
            return None

        return cls(tree_id=tree_id, raw_block=block)


@dataclass
class TypedSceneGroupItemBlock:
    """Type-safe wrapper for rmscene SceneGroupItemBlock.

    A SceneGroupItemBlock links a TreeNodeBlock to its parent in the scene graph
    (typically Layer 1 for strokes).

    Fields:
    - value: CrdtId of the TreeNodeBlock this links
    - parent_id: CrdtId of the parent node (usually 0:11 for Layer 1)
    """

    value: CrdtId
    parent_id: CrdtId
    raw_block: Any = None

    @classmethod
    def from_rmscene(cls, block: Any) -> TypedSceneGroupItemBlock | None:
        """Create from rmscene SceneGroupItemBlock."""
        if not hasattr(block, "item") or not block.item:
            return None

        value = CrdtId.from_rmscene(block.item.value)
        parent_id = CrdtId.from_rmscene(block.parent_id)

        if not value or not parent_id:
            return None

        return cls(value=value, parent_id=parent_id, raw_block=block)


@dataclass
class TypedSceneLineItemBlock:
    """Type-safe wrapper for rmscene SceneLineItemBlock (strokes).

    Contains the stroke data and parent reference.
    """

    item_id: CrdtId | None
    parent_id: CrdtId | None
    raw_block: Any = None

    @classmethod
    def from_rmscene(cls, block: Any) -> TypedSceneLineItemBlock | None:
        """Create from rmscene SceneLineItemBlock."""
        item_id = None
        if hasattr(block, "item") and hasattr(block.item, "item_id"):
            item_id = CrdtId.from_rmscene(block.item.item_id)

        parent_id = CrdtId.from_rmscene(getattr(block, "parent_id", None))

        return cls(item_id=item_id, parent_id=parent_id, raw_block=block)


# =============================================================================
# StrokeBundle - Encapsulates all blocks for a single stroke
# =============================================================================


@dataclass
class StrokeBundle:
    """Encapsulates all blocks required for a complete stroke.

    A stroke in the reMarkable v6 CRDT format requires FOUR interdependent blocks:

    1. SceneTreeBlock - Declares the node exists in the scene tree
    2. TreeNodeBlock - Defines the node and its text anchor
    3. SceneGroupItemBlock - Links the node to its parent layer (usually 0:11)
    4. SceneLineItemBlock - Contains the actual stroke data (points, color, etc.)

    When migrating strokes between pages, ALL FOUR blocks must move together.
    Missing any block causes the device to fail silently or show errors like
    "Unable to find node with id=X:Y".

    This abstraction ensures atomic operations on strokes and prevents the
    orphaned block issues that caused the Phase 3 bug.

    Example usage:
        # Extract bundles from an .rm file
        index = SceneGraphIndex.from_file(rm_path)
        bundles = StrokeBundle.from_index(index)

        for bundle in bundles:
            print(f"Stroke {bundle.node_id}: anchor={bundle.anchor_offset}")
            if bundle.is_complete:
                print("  All 4 blocks present - safe to migrate")
            else:
                print(f"  Missing blocks: {bundle.missing_blocks}")

        # Get raw rmscene blocks for writing
        raw_blocks = bundle.to_raw_blocks()
    """

    node_id: CrdtId
    tree_node: TypedTreeNodeBlock | None = None
    scene_tree: TypedSceneTreeBlock | None = None
    scene_group_item: TypedSceneGroupItemBlock | None = None
    strokes: list[TypedSceneLineItemBlock] = field(default_factory=list)

    @property
    def anchor_offset(self) -> int | None:
        """Get the text anchor offset from the TreeNodeBlock."""
        if self.tree_node:
            return self.tree_node.anchor_offset
        return None

    @property
    def is_complete(self) -> bool:
        """Check if all 4 required block types are present.

        A complete bundle has:
        - TreeNodeBlock (defines the node)
        - SceneTreeBlock (declares in scene tree)
        - SceneGroupItemBlock (links to layer)
        - At least one SceneLineItemBlock (stroke data)
        """
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

    @property
    def has_tree_node(self) -> bool:
        return self.tree_node is not None

    @property
    def has_scene_tree(self) -> bool:
        return self.scene_tree is not None

    @property
    def has_scene_group_item(self) -> bool:
        return self.scene_group_item is not None

    @property
    def has_strokes(self) -> bool:
        return len(self.strokes) > 0

    def to_raw_blocks(self) -> list[Any]:
        """Get all raw rmscene blocks for writing.

        Returns blocks in the correct order for serialization:
        1. SceneTreeBlock (declaration)
        2. TreeNodeBlock (anchor)
        3. SceneGroupItemBlock (layer link)
        4. SceneLineItemBlock(s) (stroke data)
        """
        blocks = []
        if self.scene_tree and self.scene_tree.raw_block:
            blocks.append(self.scene_tree.raw_block)
        if self.tree_node and self.tree_node.raw_block:
            blocks.append(self.tree_node.raw_block)
        if self.scene_group_item and self.scene_group_item.raw_block:
            blocks.append(self.scene_group_item.raw_block)
        for stroke in self.strokes:
            if stroke.raw_block:
                blocks.append(stroke.raw_block)
        return blocks

    @classmethod
    def from_index(cls, index: SceneGraphIndex) -> list[StrokeBundle]:
        """Extract all StrokeBundles from a SceneGraphIndex.

        Groups blocks by their node_id to form complete bundles.
        Only returns bundles for user-created nodes (part1 == 2).
        """
        bundles: dict[CrdtId, StrokeBundle] = {}

        # Start with user TreeNodeBlocks as the primary keys
        for tree_node in index.user_tree_nodes:
            node_id = tree_node.node_id
            if node_id not in bundles:
                bundles[node_id] = cls(node_id=node_id)
            bundles[node_id].tree_node = tree_node

        # Add SceneTreeBlocks
        for tree_id, scene_tree in index.scene_trees.items():
            if tree_id.is_user:
                if tree_id not in bundles:
                    bundles[tree_id] = cls(node_id=tree_id)
                bundles[tree_id].scene_tree = scene_tree

        # Add SceneGroupItemBlocks (value is the TreeNodeBlock they link to)
        for value, sgi in index.scene_group_items.items():
            if value.is_user:
                if value not in bundles:
                    bundles[value] = cls(node_id=value)
                bundles[value].scene_group_item = sgi

        # Add strokes (parent_id references the TreeNodeBlock)
        for stroke in index.strokes:
            if stroke.parent_id and stroke.parent_id.is_user:
                if stroke.parent_id not in bundles:
                    bundles[stroke.parent_id] = cls(node_id=stroke.parent_id)
                bundles[stroke.parent_id].strokes.append(stroke)

        return list(bundles.values())

    def validate(self) -> list[ValidationError]:
        """Validate this bundle's integrity.

        Returns list of errors found.
        """
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
        return f"StrokeBundle({self.node_id}, {anchor_str}, {len(self.strokes)} strokes, {status})"


# =============================================================================
# Scene Graph Index
# =============================================================================


@dataclass
class SceneGraphIndex:
    """Index of all scene graph blocks for fast lookup.

    Provides O(1) lookups for:
    - TreeNodeBlocks by node_id
    - SceneTreeBlocks by tree_id
    - SceneGroupItemBlocks by value (the TreeNodeBlock they link to)
    """

    tree_nodes: dict[CrdtId, TypedTreeNodeBlock] = field(default_factory=dict)
    scene_trees: dict[CrdtId, TypedSceneTreeBlock] = field(default_factory=dict)
    scene_group_items: dict[CrdtId, TypedSceneGroupItemBlock] = field(default_factory=dict)
    strokes: list[TypedSceneLineItemBlock] = field(default_factory=list)

    # Convenience: all known node IDs (including system nodes)
    all_node_ids: set[CrdtId] = field(default_factory=set)

    @classmethod
    def from_blocks(cls, blocks: list[Any]) -> SceneGraphIndex:
        """Build index from list of rmscene blocks."""
        index = cls()

        # Add known system nodes
        for sys_node in KNOWN_SYSTEM_NODES:
            index.all_node_ids.add(sys_node)

        for block in blocks:
            block_type = type(block).__name__

            if block_type == "TreeNodeBlock":
                typed = TypedTreeNodeBlock.from_rmscene(block)
                if typed:
                    index.tree_nodes[typed.node_id] = typed
                    index.all_node_ids.add(typed.node_id)

            elif block_type == "SceneTreeBlock":
                typed = TypedSceneTreeBlock.from_rmscene(block)
                if typed:
                    index.scene_trees[typed.tree_id] = typed

            elif block_type == "SceneGroupItemBlock":
                typed = TypedSceneGroupItemBlock.from_rmscene(block)
                if typed:
                    index.scene_group_items[typed.value] = typed

            elif "Line" in block_type:
                typed = TypedSceneLineItemBlock.from_rmscene(block)
                if typed:
                    index.strokes.append(typed)

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

    def get_tree_node(self, node_id: CrdtId) -> TypedTreeNodeBlock | None:
        """Get TreeNodeBlock by ID."""
        return self.tree_nodes.get(node_id)

    def get_scene_tree(self, tree_id: CrdtId) -> TypedSceneTreeBlock | None:
        """Get SceneTreeBlock by ID."""
        return self.scene_trees.get(tree_id)

    def get_scene_group_item(self, value: CrdtId) -> TypedSceneGroupItemBlock | None:
        """Get SceneGroupItemBlock by the TreeNodeBlock it links to."""
        return self.scene_group_items.get(value)

    def node_exists(self, node_id: CrdtId) -> bool:
        """Check if a node ID exists (including system nodes)."""
        return node_id in self.all_node_ids

    @property
    def user_tree_nodes(self) -> Iterator[TypedTreeNodeBlock]:
        """Iterate over user-created TreeNodeBlocks (part1 == 2)."""
        for node in self.tree_nodes.values():
            if node.node_id.is_user:
                yield node


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
            return f"[{self.error_type}] {self.node_id}: {self.message}"
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


def validate_scene_graph(
    rm_bytes: bytes,
    source_name: str = "<bytes>",
) -> SceneGraphValidationResult:
    """Validate scene graph structure for device compatibility.

    Checks that all required block relationships are present:
    1. Every SceneGroupItemBlock.value has a corresponding TreeNodeBlock
    2. Every user-created TreeNodeBlock has a corresponding SceneTreeBlock
    3. All parent_id references resolve to existing nodes
    4. All stroke parent_ids reference existing TreeNodeBlocks

    Args:
        rm_bytes: The raw .rm file content
        source_name: Name to use in error messages

    Returns:
        SceneGraphValidationResult with errors, warnings, and the index
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    # Parse and index
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
                    message=f"SceneGroupItemBlock.value={value} has no corresponding TreeNodeBlock",
                    node_id=value,
                    details={"parent_id": str(sgi.parent_id)},
                )
            )

    # Validation 2: Every SceneGroupItemBlock.parent_id must exist
    for value, sgi in index.scene_group_items.items():
        if not index.node_exists(sgi.parent_id):
            errors.append(
                ValidationError(
                    error_type="MISSING_PARENT",
                    message=f"SceneGroupItemBlock.parent_id={sgi.parent_id} not found",
                    node_id=value,
                    details={"parent_id": str(sgi.parent_id)},
                )
            )

    # Validation 3: Every user-created TreeNodeBlock must have a SceneTreeBlock
    for node in index.user_tree_nodes:
        if node.node_id not in index.scene_trees:
            errors.append(
                ValidationError(
                    error_type="UNDECLARED_TREE_NODE",
                    message=f"TreeNodeBlock {node.node_id} has no SceneTreeBlock declaration",
                    node_id=node.node_id,
                )
            )

    # Validation 4: Every stroke parent_id must reference an existing TreeNodeBlock
    for stroke in index.strokes:
        if stroke.parent_id and not index.node_exists(stroke.parent_id):
            errors.append(
                ValidationError(
                    error_type="ORPHANED_STROKE",
                    message=f"Stroke parent_id={stroke.parent_id} not found in TreeNodeBlocks",
                    details={"parent_id": str(stroke.parent_id)},
                )
            )

    # Warning: User TreeNodeBlocks without corresponding SceneGroupItemBlock
    for node in index.user_tree_nodes:
        if node.node_id not in index.scene_group_items:
            warnings.append(
                ValidationError(
                    error_type="UNLINKED_TREE_NODE",
                    message=f"TreeNodeBlock {node.node_id} has no SceneGroupItemBlock linking it to layer",
                    node_id=node.node_id,
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
