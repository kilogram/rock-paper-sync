#!/usr/bin/env python3
"""Unified reMarkable file inspector for coordinate transformation analysis.

This tool consolidates multiple analysis scripts into a single flexible interface
for LLM-driven investigation of reMarkable v6 file structures and coordinate systems.

Usage:
    # Analyze TreeNodeBlock anchoring
    python rm_inspector.py --mode anchors --rm-file path/to/file.rm

    # Explore coordinate patterns
    python rm_inspector.py --mode coords --rm-file path/to/file.rm

    # Inspect specific parent baselines
    python rm_inspector.py --mode baselines --rm-file path/to/file.rm

    # Dump all block types
    python rm_inspector.py --mode blocks --rm-file path/to/file.rm

    # Save output to file
    python rm_inspector.py --mode anchors --rm-file file.rm --output analysis.txt

Modes:
    anchors    - Analyze TreeNodeBlock anchor_origin_x/y, anchor_type, anchor_threshold
    coords     - Show native coordinate ranges (X/Y min/max) for annotations
    baselines  - Map parent_id to anchor positions from TreeNodeBlocks
    blocks     - List all block types with counts (RootTextBlock, TreeNodeBlock, etc.)
    text       - Extract text blocks with positions
    structure  - Show document structure (layers, parents, children)

The tool reads reMarkable v6 .rm files and extracts structural information
useful for understanding coordinate transformation and stroke anchoring.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import rmscene
from rmscene.scene_stream import (
    RootTextBlock,
    SceneGroupItemBlock,
    TreeNodeBlock,
)


def analyze_anchors(rm_file: Path, output_file: Path | None = None):
    """Analyze TreeNodeBlock anchoring information.

    Shows anchor_origin_x, anchor_origin_y, anchor_type, anchor_threshold
    for each TreeNodeBlock to understand coordinate anchoring.
    """
    with rm_file.open('rb') as f:
        blocks = list(rmscene.read_blocks(f))

    tree_nodes = [b for b in blocks if isinstance(b, TreeNodeBlock)]

    lines = []
    lines.append("=== TreeNodeBlock Anchoring Analysis ===")
    lines.append(f"File: {rm_file}")
    lines.append(f"Total TreeNodeBlocks: {len(tree_nodes)}\n")

    for i, block in enumerate(tree_nodes):
        if not hasattr(block, 'group'):
            continue

        group = block.group
        lines.append(f"TreeNode {i}:")
        lines.append(f"  node_id: {group.node_id}")

        if hasattr(group, 'anchor_id') and group.anchor_id:
            anchor_id = group.anchor_id.value if hasattr(group.anchor_id, 'value') else group.anchor_id
            lines.append(f"  anchor_id: {anchor_id}")

        if hasattr(group, 'anchor_origin_x') and group.anchor_origin_x:
            val = group.anchor_origin_x.value if hasattr(group.anchor_origin_x, 'value') else group.anchor_origin_x
            lines.append(f"  anchor_origin_x: {val}")

        if hasattr(group, 'anchor_origin_y') and group.anchor_origin_y:
            val = group.anchor_origin_y.value if hasattr(group.anchor_origin_y, 'value') else group.anchor_origin_y
            lines.append(f"  anchor_origin_y: {val}")
        else:
            lines.append("  anchor_origin_y: NOT FOUND")

        if hasattr(group, 'anchor_type') and group.anchor_type:
            val = group.anchor_type.value if hasattr(group.anchor_type, 'value') else group.anchor_type
            lines.append(f"  anchor_type: {val}")

        if hasattr(group, 'anchor_threshold') and group.anchor_threshold:
            val = group.anchor_threshold.value if hasattr(group.anchor_threshold, 'value') else group.anchor_threshold
            lines.append(f"  anchor_threshold: {val}")

        lines.append("")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_coords(rm_file: Path, output_file: Path | None = None):
    """Analyze native coordinate ranges for annotations.

    Shows min/max X and Y values across all stroke points to understand
    coordinate space and identify positive vs negative Y patterns.
    """
    from rock_paper_sync.annotations import read_annotations

    annotations = read_annotations(rm_file)

    lines = []
    lines.append("=== Coordinate Range Analysis ===")
    lines.append(f"File: {rm_file}")
    lines.append(f"Total annotations: {len(annotations)}\n")

    all_x = []
    all_y = []
    by_parent = defaultdict(lambda: {'x': [], 'y': []})

    for ann in annotations:
        if not ann.stroke:
            continue

        for point in ann.stroke.points:
            all_x.append(point.x)
            all_y.append(point.y)

            if ann.parent_id:
                by_parent[str(ann.parent_id)]['x'].append(point.x)
                by_parent[str(ann.parent_id)]['y'].append(point.y)

    if all_x:
        lines.append("Overall ranges:")
        lines.append(f"  X: [{min(all_x):.2f}, {max(all_x):.2f}]")
        lines.append(f"  Y: [{min(all_y):.2f}, {max(all_y):.2f}]")
        lines.append(f"  Positive Y count: {sum(1 for y in all_y if y >= 0)}")
        lines.append(f"  Negative Y count: {sum(1 for y in all_y if y < 0)}\n")

        lines.append("By parent_id:")
        for parent_id, coords in sorted(by_parent.items()):
            lines.append(f"  {parent_id}:")
            lines.append(f"    X: [{min(coords['x']):.2f}, {max(coords['x']):.2f}]")
            lines.append(f"    Y: [{min(coords['y']):.2f}, {max(coords['y']):.2f}]")
            lines.append(f"    Y sign: {'positive' if all(y >= 0 for y in coords['y']) else 'negative' if all(y < 0 for y in coords['y']) else 'mixed'}")
    else:
        lines.append("No stroke coordinates found")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_baselines(rm_file: Path, output_file: Path | None = None):
    """Map parent IDs to anchor positions from TreeNodeBlocks.

    Extracts the parent_id → (anchor_origin_x, anchor_origin_y) mapping
    needed for coordinate transformation.
    """
    with rm_file.open('rb') as f:
        blocks = list(rmscene.read_blocks(f))

    lines = []
    lines.append("=== Parent Baseline Mapping ===")
    lines.append(f"File: {rm_file}\n")

    parent_to_anchor = {}

    for block in blocks:
        if not isinstance(block, TreeNodeBlock):
            continue
        if not hasattr(block, 'group'):
            continue

        node_id = block.group.node_id
        anchor_x = None
        anchor_y = None

        if hasattr(block.group, 'anchor_origin_x') and block.group.anchor_origin_x:
            anchor_x = block.group.anchor_origin_x.value if hasattr(block.group.anchor_origin_x, 'value') else block.group.anchor_origin_x

        if hasattr(block.group, 'anchor_origin_y') and block.group.anchor_origin_y:
            anchor_y = block.group.anchor_origin_y.value if hasattr(block.group.anchor_origin_y, 'value') else block.group.anchor_origin_y

        if anchor_x is not None or anchor_y is not None:
            parent_to_anchor[str(node_id)] = (anchor_x, anchor_y)

    lines.append(f"Found {len(parent_to_anchor)} parent anchors:\n")
    for parent_id, (anchor_x, anchor_y) in sorted(parent_to_anchor.items()):
        lines.append(f"{parent_id}:")
        lines.append(f"  anchor_x: {anchor_x}")
        lines.append(f"  anchor_y: {anchor_y if anchor_y is not None else 'NOT FOUND'}")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_blocks(rm_file: Path, output_file: Path | None = None):
    """List all block types with counts and basic info.

    Shows the structure of the .rm file by listing block types,
    useful for understanding file format.
    """
    with rm_file.open('rb') as f:
        blocks = list(rmscene.read_blocks(f))

    block_counts = defaultdict(int)
    for block in blocks:
        block_counts[type(block).__name__] += 1

    lines = []
    lines.append("=== Block Type Analysis ===")
    lines.append(f"File: {rm_file}")
    lines.append(f"Total blocks: {len(blocks)}\n")

    for block_type, count in sorted(block_counts.items()):
        lines.append(f"{block_type}: {count}")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_text(rm_file: Path, output_file: Path | None = None):
    """Extract text blocks with positions.

    Shows RootTextBlock and any text content for understanding
    text positioning and coordinate origins.
    """
    with rm_file.open('rb') as f:
        blocks = list(rmscene.read_blocks(f))

    lines = []
    lines.append("=== Text Block Analysis ===")
    lines.append(f"File: {rm_file}\n")

    for i, block in enumerate(blocks):
        if isinstance(block, RootTextBlock):
            lines.append(f"RootTextBlock {i}:")
            if hasattr(block, 'value'):
                lines.append(f"  pos_x: {block.value.pos_x}")
                lines.append(f"  pos_y: {block.value.pos_y}")
                lines.append(f"  width: {block.value.width}")
            lines.append("")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_structure(rm_file: Path, output_file: Path | None = None):
    """Show document structure (layers, parents, children).

    Analyzes the hierarchical structure of scene items to understand
    parent-child relationships and layer organization.
    """
    with rm_file.open('rb') as f:
        blocks = list(rmscene.read_blocks(f))

    lines = []
    lines.append("=== Document Structure ===")
    lines.append(f"File: {rm_file}\n")

    # Find all scene items
    groups = [b for b in blocks if isinstance(b, SceneGroupItemBlock)]
    lines.append(f"SceneGroupItemBlocks: {len(groups)}")

    for i, group in enumerate(groups[:10]):  # Show first 10
        lines.append(f"  Group {i}:")
        if hasattr(group, 'value'):
            lines.append(f"    node_id: {group.value.node_id}")

    tree_nodes = [b for b in blocks if isinstance(b, TreeNodeBlock)]
    lines.append(f"\nTreeNodeBlocks: {len(tree_nodes)}")

    # Build parent-child map (for future use in extended analysis)
    for block in tree_nodes:
        if hasattr(block, 'group'):
            # Could extract parent info here if available
            pass

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def main():
    parser = argparse.ArgumentParser(
        description="Unified reMarkable file inspector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--mode',
        required=True,
        choices=['anchors', 'coords', 'baselines', 'blocks', 'text', 'structure'],
        help='Analysis mode to run'
    )
    parser.add_argument(
        '--rm-file',
        type=Path,
        required=True,
        help='Path to .rm file to analyze'
    )
    parser.add_argument(
        '--output',
        type=Path,
        help='Optional output file (default: print to stdout)'
    )

    args = parser.parse_args()

    if not args.rm_file.exists():
        print(f"Error: File not found: {args.rm_file}", file=sys.stderr)
        return 1

    modes = {
        'anchors': analyze_anchors,
        'coords': analyze_coords,
        'baselines': analyze_baselines,
        'blocks': analyze_blocks,
        'text': analyze_text,
        'structure': analyze_structure,
    }

    modes[args.mode](args.rm_file, args.output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
