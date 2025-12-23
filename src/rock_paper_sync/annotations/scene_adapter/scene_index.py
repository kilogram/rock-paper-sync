"""Scene graph indexing and validation utilities.

This module provides:
- SceneGraphIndex: Fast O(1) lookups for scene graph blocks
- Validation functions to check scene graph integrity
- Helper functions for CrdtId manipulation

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
    1. No duplicate TreeNodeBlocks (same node_id appears multiple times)
    2. Every SceneGroupItemBlock.value has a corresponding TreeNodeBlock
    3. Every user-created TreeNodeBlock has a corresponding SceneTreeBlock
    4. All parent_id references resolve to existing nodes
    5. All stroke parent_ids reference existing TreeNodeBlocks
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    try:
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    except Exception as e:
        errors.append(
            ValidationError(
                error_type="READ_ERROR",
                message=f"Failed to parse .rm bytes: {e}",
            )
        )
        return SceneGraphValidationResult(errors=errors, warnings=warnings)

    # Validation 0: Check for duplicate TreeNodeBlocks (before building index)
    # This catches bugs where the same TreeNodeBlock is injected multiple times
    tree_node_occurrences: dict[CrdtId, int] = {}
    for block in blocks:
        if isinstance(block, TreeNodeBlock):
            node_id = block.group.node_id
            tree_node_occurrences[node_id] = tree_node_occurrences.get(node_id, 0) + 1

    for node_id, count in tree_node_occurrences.items():
        if count > 1:
            errors.append(
                ValidationError(
                    error_type="DUPLICATE_TREE_NODE",
                    message=f"TreeNodeBlock {format_crdt_id(node_id)} appears {count} times (should be 1)",
                    node_id=node_id,
                    details={"count": count},
                )
            )

    # Now build the index for remaining validations
    index = SceneGraphIndex.from_blocks(blocks)

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
        return validate_scene_graph(f.read())
