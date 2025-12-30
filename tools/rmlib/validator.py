"""Validate .rm file structure for device compatibility.

This module validates that generated .rm files have valid structure:
- TreeNodeBlock anchors point to valid character positions in RootTextBlock
- Strokes have valid parent_ids referencing existing TreeNodeBlocks
- CRDT sequence ordering is preserved

Usage:
    from tools.rmlib.validator import validate_rm_file, ValidationError

    errors = validate_rm_file(rm_path, expected_page_text)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        sys.exit(1)

    # Or run as CLI:
    # uv run python -m tools.rmlib.validator file.rm --page-text "expected text"
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rmscene
from rmscene.scene_stream import RootTextBlock, TreeNodeBlock

# Sentinel anchor for margin notes / non-text-anchored strokes
# These anchors are intentionally very large and should not be validated
END_OF_DOC_ANCHOR_MARKER = 281474976710655  # 0xFFFFFFFFFFFF

# Base item ID for text CrdtSequenceItem
# CRDT anchor_id.part2 = TEXT_BASE_ITEM_ID + char_offset
# We need to subtract this when validating against page text length
TEXT_BASE_ITEM_ID = 16


@dataclass
class ValidationError:
    """A validation error found in an .rm file."""

    error_type: str
    message: str
    node_id: str | None = None
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        if self.node_id:
            return f"[{self.error_type}] {self.node_id}: {self.message}"
        return f"[{self.error_type}] {self.message}"


@dataclass
class ValidationResult:
    """Result of validating an .rm file."""

    rm_path: Path | str
    errors: list[ValidationError]
    warnings: list[ValidationError]
    tree_nodes_checked: int
    strokes_checked: int
    page_text_len: int | None

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def __str__(self) -> str:
        status = "PASS" if self.is_valid else "FAIL"
        lines = [
            f"Validation {status}: {self.rm_path}",
            f"  TreeNodes checked: {self.tree_nodes_checked}",
            f"  Strokes checked: {self.strokes_checked}",
            f"  Page text length: {self.page_text_len}",
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


def validate_rm_file(
    rm_path: Path | str,
    expected_page_text: str | None = None,
) -> ValidationResult:
    """Validate an .rm file for device compatibility.

    Args:
        rm_path: Path to the .rm file
        expected_page_text: Expected RootTextBlock text (for anchor validation)

    Returns:
        ValidationResult with errors and warnings
    """
    rm_path = Path(rm_path)
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []
    tree_nodes_checked = 0
    strokes_checked = 0
    page_text_len: int | None = None

    # Read the .rm file
    try:
        with rm_path.open("rb") as f:
            blocks = list(rmscene.read_blocks(f))
    except Exception as e:
        errors.append(
            ValidationError(
                error_type="READ_ERROR",
                message=f"Failed to read .rm file: {e}",
            )
        )
        return ValidationResult(
            rm_path=rm_path,
            errors=errors,
            warnings=warnings,
            tree_nodes_checked=0,
            strokes_checked=0,
            page_text_len=None,
        )

    # Extract RootTextBlock text
    root_text_blocks = [b for b in blocks if isinstance(b, RootTextBlock)]
    actual_page_text: str | None = None

    if root_text_blocks:
        rtb = root_text_blocks[0]
        if hasattr(rtb, "value") and hasattr(rtb.value, "items"):
            # Extract text from items - CrdtSequence.values() returns strings directly
            text_parts = []
            for item_val in rtb.value.items.values():
                if isinstance(item_val, str):
                    text_parts.append(item_val)
            actual_page_text = "".join(text_parts)
            page_text_len = len(actual_page_text)

    # Validate expected vs actual text
    if expected_page_text is not None and actual_page_text is not None:
        if actual_page_text != expected_page_text:
            warnings.append(
                ValidationError(
                    error_type="TEXT_MISMATCH",
                    message=(
                        f"Page text mismatch: "
                        f"expected {len(expected_page_text)} chars, "
                        f"got {len(actual_page_text)} chars"
                    ),
                    details={
                        "expected_len": len(expected_page_text),
                        "actual_len": len(actual_page_text),
                    },
                )
            )

    # Build map of node_ids
    tree_node_ids: set[str] = set()
    tree_nodes = [b for b in blocks if isinstance(b, TreeNodeBlock)]

    for block in tree_nodes:
        if not hasattr(block, "group") or not block.group:
            continue

        group = block.group
        node_id = _format_crdt_id(group.node_id)
        tree_node_ids.add(node_id)
        tree_nodes_checked += 1

        # Validate anchor_id if present
        if hasattr(group, "anchor_id") and group.anchor_id:
            anchor_val = group.anchor_id.value
            anchor_offset = anchor_val.part2 if hasattr(anchor_val, "part2") else anchor_val

            # Skip sentinel anchors (margin notes, non-text-anchored strokes)
            if anchor_offset == END_OF_DOC_ANCHOR_MARKER:
                continue

            # Check if anchor is within valid range
            # anchor_offset is CRDT part2 = TEXT_BASE_ITEM_ID + char_offset
            # Convert to char_offset for comparison with page_text_len
            char_offset = anchor_offset - TEXT_BASE_ITEM_ID
            if page_text_len is not None:
                if char_offset < 0:
                    errors.append(
                        ValidationError(
                            error_type="NEGATIVE_ANCHOR",
                            message=f"Anchor offset is negative: {char_offset}",
                            node_id=node_id,
                            details={"anchor": anchor_offset, "char_offset": char_offset},
                        )
                    )
                elif char_offset > page_text_len:
                    errors.append(
                        ValidationError(
                            error_type="ANCHOR_OUT_OF_RANGE",
                            message=(
                                f"Anchor {char_offset} > page text length "
                                f"{page_text_len}"
                            ),
                            node_id=node_id,
                            details={
                                "anchor": anchor_offset,
                                "char_offset": char_offset,
                                "page_text_len": page_text_len,
                            },
                        )
                    )
                elif char_offset > page_text_len * 0.9 and char_offset > 100:
                    # Warn about anchors near the end (might indicate wrong page)
                    warnings.append(
                        ValidationError(
                            error_type="ANCHOR_NEAR_END",
                            message=(
                                f"Anchor {char_offset} is near end of text "
                                f"({page_text_len})"
                            ),
                            node_id=node_id,
                            details={
                                "anchor": anchor_offset,
                                "char_offset": char_offset,
                                "page_text_len": page_text_len,
                            },
                        )
                    )

    # Count strokes (LineBlock patterns)
    for block in blocks:
        block_type = type(block).__name__
        if "Line" in block_type:
            strokes_checked += 1

    return ValidationResult(
        rm_path=rm_path,
        errors=errors,
        warnings=warnings,
        tree_nodes_checked=tree_nodes_checked,
        strokes_checked=strokes_checked,
        page_text_len=page_text_len,
    )


def validate_rm_bytes(
    rm_bytes: bytes,
    page_text_len: int | None = None,
    source_name: str = "<bytes>",
) -> ValidationResult:
    """Validate .rm content from bytes.

    Args:
        rm_bytes: The raw .rm file content
        page_text_len: Expected length of page text (for anchor validation)
        source_name: Name to use in error messages

    Returns:
        ValidationResult with errors and warnings
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []
    tree_nodes_checked = 0
    strokes_checked = 0
    actual_page_text_len: int | None = None

    # Read the blocks
    try:
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    except Exception as e:
        errors.append(
            ValidationError(
                error_type="READ_ERROR",
                message=f"Failed to parse .rm bytes: {e}",
            )
        )
        return ValidationResult(
            rm_path=source_name,
            errors=errors,
            warnings=warnings,
            tree_nodes_checked=0,
            strokes_checked=0,
            page_text_len=None,
        )

    # Extract text length from RootTextBlock
    root_text_blocks = [b for b in blocks if isinstance(b, RootTextBlock)]
    if root_text_blocks:
        rtb = root_text_blocks[0]
        if hasattr(rtb, "value") and hasattr(rtb.value, "items"):
            text_parts = []
            for item_val in rtb.value.items.values():
                if isinstance(item_val, str):
                    text_parts.append(item_val)
            actual_page_text_len = len("".join(text_parts))

    # Use actual or provided text length
    effective_text_len = actual_page_text_len or page_text_len

    # Validate TreeNodeBlocks
    tree_nodes = [b for b in blocks if isinstance(b, TreeNodeBlock)]
    for block in tree_nodes:
        if not hasattr(block, "group") or not block.group:
            continue

        group = block.group
        node_id = _format_crdt_id(group.node_id)
        tree_nodes_checked += 1

        # Only validate user-created annotations (author ID 2)
        if hasattr(group.node_id, "part1") and group.node_id.part1 != 2:
            continue

        # Validate anchor_id
        if hasattr(group, "anchor_id") and group.anchor_id:
            anchor_val = group.anchor_id.value
            if hasattr(anchor_val, "part2"):
                anchor_offset = anchor_val.part2
            else:
                anchor_offset = anchor_val

            # Skip sentinel anchors (margin notes, non-text-anchored strokes)
            if anchor_offset == END_OF_DOC_ANCHOR_MARKER:
                continue

            if effective_text_len is not None:
                # anchor_offset is CRDT part2 = TEXT_BASE_ITEM_ID + char_offset
                # Convert to char_offset for comparison with text length
                char_offset = anchor_offset - TEXT_BASE_ITEM_ID
                if char_offset < 0:
                    errors.append(
                        ValidationError(
                            error_type="NEGATIVE_ANCHOR",
                            message=f"Anchor offset is negative: {char_offset}",
                            node_id=node_id,
                        )
                    )
                elif char_offset > effective_text_len:
                    errors.append(
                        ValidationError(
                            error_type="ANCHOR_OUT_OF_RANGE",
                            message=(
                                f"Anchor {char_offset} > page text length "
                                f"{effective_text_len}"
                            ),
                            node_id=node_id,
                            details={
                                "anchor": anchor_offset,
                                "char_offset": char_offset,
                                "page_text_len": effective_text_len,
                            },
                        )
                    )

    # Count strokes
    for block in blocks:
        if "Line" in type(block).__name__:
            strokes_checked += 1

    return ValidationResult(
        rm_path=source_name,
        errors=errors,
        warnings=warnings,
        tree_nodes_checked=tree_nodes_checked,
        strokes_checked=strokes_checked,
        page_text_len=effective_text_len,
    )


def _format_crdt_id(crdt_id: Any) -> str:
    """Format a CrdtId for display."""
    if hasattr(crdt_id, "part1") and hasattr(crdt_id, "part2"):
        return f"{crdt_id.part1}:{crdt_id.part2}"
    return str(crdt_id)


@dataclass
class SceneGraphValidationResult:
    """Result of validating scene graph structure."""

    errors: list[ValidationError]
    warnings: list[ValidationError]
    tree_node_count: int
    scene_tree_count: int
    scene_group_item_count: int
    stroke_count: int

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

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


def validate_scene_graph(rm_bytes: bytes, source_name: str = "<bytes>") -> SceneGraphValidationResult:
    """Validate scene graph structure for device compatibility.

    Checks that all required block relationships are present:
    - Every SceneGroupItemBlock.value has a corresponding TreeNodeBlock
    - Every user-created TreeNodeBlock has a corresponding SceneTreeBlock
    - All parent_id references resolve to existing nodes
    - All stroke parent_ids reference existing TreeNodeBlocks

    Args:
        rm_bytes: The raw .rm file content
        source_name: Name to use in error messages

    Returns:
        SceneGraphValidationResult with errors and warnings
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    # Parse blocks
    try:
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    except Exception as e:
        errors.append(
            ValidationError(
                error_type="READ_ERROR",
                message=f"Failed to parse .rm bytes: {e}",
            )
        )
        return SceneGraphValidationResult(
            errors=errors,
            warnings=warnings,
            tree_node_count=0,
            scene_tree_count=0,
            scene_group_item_count=0,
            stroke_count=0,
        )

    # Build indices
    tree_node_ids: set[str] = set()  # All TreeNodeBlock node_ids
    user_tree_node_ids: set[str] = set()  # User-created TreeNodeBlocks (part1 == 2)
    scene_tree_ids: set[str] = set()  # SceneTreeBlock tree_ids (nodes declared in scene tree)
    scene_group_item_values: list[tuple[str, str]] = []  # (value, parent_id) for each SceneGroupItemBlock
    stroke_parent_ids: list[str] = []  # parent_ids from SceneLineItemBlocks

    tree_node_count = 0
    scene_tree_count = 0
    scene_group_item_count = 0
    stroke_count = 0

    for block in blocks:
        block_type = type(block).__name__

        if block_type == "TreeNodeBlock":
            if hasattr(block, "group") and block.group:
                node_id = block.group.node_id
                node_id_str = _format_crdt_id(node_id)
                tree_node_ids.add(node_id_str)
                tree_node_count += 1

                # Track user-created nodes (author ID part1 == 2)
                if hasattr(node_id, "part1") and node_id.part1 == 2:
                    user_tree_node_ids.add(node_id_str)

        elif block_type == "SceneTreeBlock":
            if hasattr(block, "tree_id") and block.tree_id:
                tree_id_str = _format_crdt_id(block.tree_id)
                scene_tree_ids.add(tree_id_str)
                scene_tree_count += 1

        elif block_type == "SceneGroupItemBlock":
            if hasattr(block, "item") and block.item:
                value_str = _format_crdt_id(block.item.value)
                parent_str = _format_crdt_id(block.parent_id)
                scene_group_item_values.append((value_str, parent_str))
                scene_group_item_count += 1

        elif "Line" in block_type:
            parent_id = getattr(block, "parent_id", None)
            if parent_id:
                stroke_parent_ids.append(_format_crdt_id(parent_id))
            stroke_count += 1

    # System node IDs (part1 == 0) are implicitly present in scene graph
    # 0:1 = root, 0:11 = Layer 1, 0:13 = Layer 1 scene group item
    def is_system_node(node_str: str) -> bool:
        """Check if a node ID is a system node (part1 == 0)."""
        if ":" in node_str:
            part1 = node_str.split(":")[0]
            return part1 == "0"
        return False

    # Add system nodes to the tree_node_ids set for validation purposes
    all_node_ids = tree_node_ids.copy()
    # Common system nodes that may be referenced but not explicitly defined
    system_nodes = {"0:1", "0:11", "0:13"}
    all_node_ids.update(system_nodes)

    # Validation 1: Every SceneGroupItemBlock.value must have a TreeNodeBlock
    # (except system nodes which are implicitly defined)
    for value_str, parent_str in scene_group_item_values:
        if value_str not in all_node_ids and not is_system_node(value_str):
            errors.append(
                ValidationError(
                    error_type="ORPHANED_SCENE_GROUP_ITEM",
                    message=f"SceneGroupItemBlock.value={value_str} has no corresponding TreeNodeBlock",
                    details={"value": value_str, "parent_id": parent_str},
                )
            )

    # Validation 2: Every SceneGroupItemBlock.parent_id must exist
    # (except system nodes which are implicitly defined)
    for value_str, parent_str in scene_group_item_values:
        if parent_str not in all_node_ids and not is_system_node(parent_str):
            errors.append(
                ValidationError(
                    error_type="MISSING_PARENT",
                    message=f"SceneGroupItemBlock.parent_id={parent_str} not found in TreeNodeBlocks",
                    details={"value": value_str, "parent_id": parent_str},
                )
            )

    # Validation 3: Every user-created TreeNodeBlock must have a SceneTreeBlock
    for user_node_id in user_tree_node_ids:
        if user_node_id not in scene_tree_ids:
            errors.append(
                ValidationError(
                    error_type="UNDECLARED_TREE_NODE",
                    message=f"TreeNodeBlock {user_node_id} has no SceneTreeBlock declaration",
                    node_id=user_node_id,
                    details={"node_id": user_node_id},
                )
            )

    # Validation 4: Every stroke parent_id must reference an existing TreeNodeBlock
    # (strokes should reference user TreeNodeBlocks, not system nodes)
    for parent_id_str in stroke_parent_ids:
        if parent_id_str not in all_node_ids:
            errors.append(
                ValidationError(
                    error_type="ORPHANED_STROKE",
                    message=f"Stroke parent_id={parent_id_str} not found in TreeNodeBlocks",
                    details={"parent_id": parent_id_str},
                )
            )

    # Warning: User TreeNodeBlocks without corresponding SceneGroupItemBlock
    scene_group_values_set = {v for v, _ in scene_group_item_values}
    for user_node_id in user_tree_node_ids:
        if user_node_id not in scene_group_values_set:
            warnings.append(
                ValidationError(
                    error_type="UNLINKED_TREE_NODE",
                    message=f"TreeNodeBlock {user_node_id} has no SceneGroupItemBlock linking it to layer",
                    node_id=user_node_id,
                )
            )

    return SceneGraphValidationResult(
        errors=errors,
        warnings=warnings,
        tree_node_count=tree_node_count,
        scene_tree_count=scene_tree_count,
        scene_group_item_count=scene_group_item_count,
        stroke_count=stroke_count,
    )


def validate_scene_graph_file(rm_path: Path) -> SceneGraphValidationResult:
    """Validate scene graph structure from a file path.

    Args:
        rm_path: Path to the .rm file

    Returns:
        SceneGraphValidationResult with errors and warnings
    """
    with rm_path.open("rb") as f:
        return validate_scene_graph(f.read(), str(rm_path))


# CLI interface
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Validate .rm file structure")
    parser.add_argument("rm_file", type=Path, help="Path to .rm file")
    parser.add_argument(
        "--page-text",
        type=str,
        help="Expected page text content",
    )
    parser.add_argument(
        "--page-text-file",
        type=Path,
        help="File containing expected page text",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all details including warnings",
    )

    args = parser.parse_args()

    if not args.rm_file.exists():
        print(f"Error: File not found: {args.rm_file}", file=sys.stderr)
        sys.exit(1)

    expected_text = None
    if args.page_text:
        expected_text = args.page_text
    elif args.page_text_file:
        expected_text = args.page_text_file.read_text()

    result = validate_rm_file(args.rm_file, expected_text)
    print(result)

    if not result.is_valid:
        sys.exit(1)
