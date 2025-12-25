#!/usr/bin/env python3
"""Unified reMarkable file inspector for coordinate transformation analysis.

This tool provides flexible inspection of reMarkable v6 .rm files for debugging
annotation reanchoring and coordinate transformation issues.

Usage:
    # Quick summary of a single file
    uv run tools/analysis/rm_inspector.py file.rm

    # Summary of all .rm files in a directory
    uv run tools/analysis/rm_inspector.py path/to/rm_files/

    # Specific analysis modes
    uv run tools/analysis/rm_inspector.py file.rm --mode annotations
    uv run tools/analysis/rm_inspector.py file.rm --mode anchors
    uv run tools/analysis/rm_inspector.py file.rm --mode scene-graph

Modes:
    summary      - Quick overview: annotation counts, coordinate ranges, block types (default)
    annotations  - Detailed annotation info: strokes with bboxes, highlights with text
    anchors      - TreeNodeBlock anchor_origin_x/y, anchor_type, anchor_threshold
    coords       - Native coordinate ranges (X/Y min/max) per parent_id
    baselines    - Map parent_id to anchor positions from TreeNodeBlocks
    blocks       - List all block types with counts
    text         - Extract text blocks with positions
    structure    - Show document structure (layers, parents, children)
    scene-graph  - Debug scene graph with all blocks in file order
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import rmscene
from rmscene.scene_stream import (
    RootTextBlock,
    SceneGroupItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)


def get_rm_files(path: Path) -> list[Path]:
    """Get .rm files from path (file or directory)."""
    if path.is_file():
        return [path]
    elif path.is_dir():
        rm_files = sorted(path.glob("*.rm"))
        if not rm_files:
            print(f"No .rm files found in {path}", file=sys.stderr)
            sys.exit(1)
        return rm_files
    else:
        print(f"Error: {path} is not a file or directory", file=sys.stderr)
        sys.exit(1)


def analyze_summary(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
    """Quick summary: annotation counts, coordinate ranges, block types.

    This is the default mode for getting a quick overview of a .rm file.
    """
    from rock_paper_sync.annotations import read_annotations

    with rm_file.open("rb") as f:
        blocks = list(rmscene.read_blocks(f))

    annotations = read_annotations(rm_file)

    lines = []
    lines.append(f"=== {rm_file.name} ===")

    # Block type counts
    block_counts = defaultdict(int)
    for block in blocks:
        block_counts[type(block).__name__] += 1

    lines.append(f"Blocks: {len(blocks)} total")
    key_blocks = ["TreeNodeBlock", "SceneGroupItemBlock", "SceneLineItemBlock", "GlyphItemBlock"]
    for bt in key_blocks:
        if bt in block_counts:
            # Shorten names for readability
            short_name = bt.replace("Block", "").replace("Item", "")
            lines.append(f"  {short_name}: {block_counts[bt]}")

    # Annotation summary
    strokes = [a for a in annotations if a.stroke]
    highlights = [a for a in annotations if a.highlight]

    lines.append(f"\nAnnotations: {len(annotations)} total")
    lines.append(f"  Strokes: {len(strokes)}")
    lines.append(f"  Highlights: {len(highlights)}")

    # Coordinate ranges
    if annotations:
        all_x = []
        all_y = []
        for ann in annotations:
            bbox = ann.bounding_box
            if bbox:
                all_x.extend([bbox.x, bbox.x + bbox.w])
                all_y.extend([bbox.y, bbox.y + bbox.h])

        if all_x:
            lines.append(f"\nCoordinate ranges:")
            lines.append(f"  X: [{min(all_x):.0f}, {max(all_x):.0f}]")
            lines.append(f"  Y: [{min(all_y):.0f}, {max(all_y):.0f}]")

            # Check for margin annotations (x < 0)
            margin_count = sum(1 for a in annotations if a.bounding_box and a.bounding_box.x < 0)
            if margin_count:
                lines.append(f"  Margin annotations (x<0): {margin_count}")

    # Show brief bbox list for strokes
    if strokes and verbose:
        lines.append("\nStroke bboxes:")
        for i, s in enumerate(strokes):
            bb = s.stroke.bounding_box
            lines.append(f"  [{i}] ({bb.x:.0f}, {bb.y:.0f}) {bb.w:.0f}x{bb.h:.0f}")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_annotations(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
    """Detailed annotation info: strokes with bboxes, highlights with text."""
    from rock_paper_sync.annotations import read_annotations

    annotations = read_annotations(rm_file)

    lines = []
    lines.append(f"=== Annotations in {rm_file.name} ===")
    lines.append(f"Total: {len(annotations)}\n")

    strokes = [a for a in annotations if a.stroke]
    highlights = [a for a in annotations if a.highlight]

    if strokes:
        lines.append(f"Strokes ({len(strokes)}):")
        for i, ann in enumerate(strokes):
            s = ann.stroke
            bb = s.bounding_box
            parent = f"parent={ann.parent_id}" if ann.parent_id else "no parent"
            lines.append(f"  [{i}] bbox=({bb.x:.1f}, {bb.y:.1f}, {bb.w:.1f}, {bb.h:.1f})")
            lines.append(f"       {len(s.points)} points, color={s.color}, tool={s.tool}, {parent}")
            if verbose and s.points:
                # Show first/last point
                p0, p1 = s.points[0], s.points[-1]
                lines.append(f"       first=({p0.x:.1f}, {p0.y:.1f}), last=({p1.x:.1f}, {p1.y:.1f})")

    if highlights:
        lines.append(f"\nHighlights ({len(highlights)}):")
        for i, ann in enumerate(highlights):
            h = ann.highlight
            text_preview = h.text[:50] + "..." if len(h.text) > 50 else h.text
            parent = f"parent={ann.parent_id}" if ann.parent_id else "no parent"
            lines.append(f"  [{i}] text='{text_preview}'")
            lines.append(f"       {len(h.rectangles)} rect(s), color={h.color}, {parent}")
            if h.rectangles:
                r = h.rectangles[0]
                lines.append(f"       first rect=({r.x:.1f}, {r.y:.1f}, {r.w:.1f}, {r.h:.1f})")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_anchors(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
    """Analyze TreeNodeBlock anchoring information.

    Shows anchor_origin_x, anchor_origin_y, anchor_type, anchor_threshold
    for each TreeNodeBlock to understand coordinate anchoring.
    """
    with rm_file.open("rb") as f:
        blocks = list(rmscene.read_blocks(f))

    tree_nodes = [b for b in blocks if isinstance(b, TreeNodeBlock)]

    lines = []
    lines.append("=== TreeNodeBlock Anchoring Analysis ===")
    lines.append(f"File: {rm_file}")
    lines.append(f"Total TreeNodeBlocks: {len(tree_nodes)}\n")

    for i, block in enumerate(tree_nodes):
        if not hasattr(block, "group"):
            continue

        group = block.group
        lines.append(f"TreeNode {i}:")
        lines.append(f"  node_id: {group.node_id}")

        if hasattr(group, "anchor_id") and group.anchor_id:
            anchor_id = (
                group.anchor_id.value if hasattr(group.anchor_id, "value") else group.anchor_id
            )
            lines.append(f"  anchor_id: {anchor_id}")

        if hasattr(group, "anchor_origin_x") and group.anchor_origin_x:
            val = (
                group.anchor_origin_x.value
                if hasattr(group.anchor_origin_x, "value")
                else group.anchor_origin_x
            )
            lines.append(f"  anchor_origin_x: {val}")

        if hasattr(group, "anchor_origin_y") and group.anchor_origin_y:
            val = (
                group.anchor_origin_y.value
                if hasattr(group.anchor_origin_y, "value")
                else group.anchor_origin_y
            )
            lines.append(f"  anchor_origin_y: {val}")
        else:
            lines.append("  anchor_origin_y: NOT FOUND")

        if hasattr(group, "anchor_type") and group.anchor_type:
            val = (
                group.anchor_type.value
                if hasattr(group.anchor_type, "value")
                else group.anchor_type
            )
            lines.append(f"  anchor_type: {val}")

        if hasattr(group, "anchor_threshold") and group.anchor_threshold:
            val = (
                group.anchor_threshold.value
                if hasattr(group.anchor_threshold, "value")
                else group.anchor_threshold
            )
            lines.append(f"  anchor_threshold: {val}")

        lines.append("")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_coords(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
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

    all_x: list[float] = []
    all_y: list[float] = []
    by_parent: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"x": [], "y": []})

    for ann in annotations:
        if not ann.stroke:
            continue

        for point in ann.stroke.points:
            all_x.append(point.x)
            all_y.append(point.y)

            if ann.parent_id:
                by_parent[str(ann.parent_id)]["x"].append(point.x)
                by_parent[str(ann.parent_id)]["y"].append(point.y)

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
            y_sign = (
                "positive"
                if all(y >= 0 for y in coords["y"])
                else "negative" if all(y < 0 for y in coords["y"]) else "mixed"
            )
            lines.append(f"    Y sign: {y_sign}")
    else:
        lines.append("No stroke coordinates found")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_baselines(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
    """Map parent IDs to anchor positions from TreeNodeBlocks.

    Extracts the parent_id → (anchor_origin_x, anchor_origin_y) mapping
    needed for coordinate transformation.
    """
    with rm_file.open("rb") as f:
        blocks = list(rmscene.read_blocks(f))

    lines = []
    lines.append("=== Parent Baseline Mapping ===")
    lines.append(f"File: {rm_file}\n")

    parent_to_anchor: dict[str, tuple[float | None, float | None]] = {}

    for block in blocks:
        if not isinstance(block, TreeNodeBlock):
            continue
        if not hasattr(block, "group"):
            continue

        node_id = block.group.node_id
        anchor_x = None
        anchor_y = None

        if hasattr(block.group, "anchor_origin_x") and block.group.anchor_origin_x:
            anchor_x = (
                block.group.anchor_origin_x.value
                if hasattr(block.group.anchor_origin_x, "value")
                else block.group.anchor_origin_x
            )

        if hasattr(block.group, "anchor_origin_y") and block.group.anchor_origin_y:
            anchor_y = (
                block.group.anchor_origin_y.value
                if hasattr(block.group.anchor_origin_y, "value")
                else block.group.anchor_origin_y
            )

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


def analyze_blocks(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
    """List all block types with counts and basic info.

    Shows the structure of the .rm file by listing block types,
    useful for understanding file format.
    """
    with rm_file.open("rb") as f:
        blocks = list(rmscene.read_blocks(f))

    block_counts: dict[str, int] = defaultdict(int)
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


def analyze_text(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
    """Extract text blocks with positions.

    Shows RootTextBlock and any text content for understanding
    text positioning and coordinate origins.
    """
    with rm_file.open("rb") as f:
        blocks = list(rmscene.read_blocks(f))

    lines = []
    lines.append("=== Text Block Analysis ===")
    lines.append(f"File: {rm_file}\n")

    for i, block in enumerate(blocks):
        if isinstance(block, RootTextBlock):
            lines.append(f"RootTextBlock {i}:")
            if hasattr(block, "value"):
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


def analyze_structure(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
    """Show document structure (layers, parents, children).

    Analyzes the hierarchical structure of scene items to understand
    parent-child relationships and layer organization.
    """
    with rm_file.open("rb") as f:
        blocks = list(rmscene.read_blocks(f))

    lines = []
    lines.append("=== Document Structure ===")
    lines.append(f"File: {rm_file}\n")

    # Find all scene items
    groups = [b for b in blocks if isinstance(b, SceneGroupItemBlock)]
    lines.append(f"SceneGroupItemBlocks: {len(groups)}")

    for i, group in enumerate(groups[:10]):  # Show first 10
        lines.append(f"  Group {i}:")
        if hasattr(group, "value"):
            lines.append(f"    node_id: {group.value.node_id}")

    tree_nodes = [b for b in blocks if isinstance(b, TreeNodeBlock)]
    lines.append(f"\nTreeNodeBlocks: {len(tree_nodes)}")

    output = "\n".join(lines)
    if output_file:
        output_file.write_text(output)
        print(f"Output written to {output_file}")
    else:
        print(output)


def analyze_scene_graph(rm_file: Path, output_file: Path | None = None, verbose: bool = False):
    """Debug scene graph structure with all blocks in order.

    Shows blocks in file order to debug parent-child relationships
    and identify issues with SceneGroupItemBlock references.
    """
    with rm_file.open("rb") as f:
        blocks = list(rmscene.read_blocks(f))

    lines = []
    lines.append("=== Scene Graph Debug ===")
    lines.append(f"File: {rm_file}")
    lines.append(f"Total blocks: {len(blocks)}\n")

    # Track defined nodes (TreeNodeBlock node_ids)
    defined_nodes: set[str] = set()
    # Track referenced parent_ids in SceneGroupItemBlocks
    referenced_parents: list[tuple[str, bool]] = []

    for i, block in enumerate(blocks):
        block_type = type(block).__name__

        if block_type == "AuthorIdsBlock":
            author_count = len(block.author_uuids) if hasattr(block, "author_uuids") else "?"
            lines.append(f"[{i:2d}] {block_type} (authors: {author_count})")
        elif block_type == "SceneTreeBlock":
            lines.append(
                f"[{i:2d}] {block_type}: tree_id={block.tree_id}, "
                f"node_id={block.node_id}, parent_id={block.parent_id}"
            )
        elif block_type == "TreeNodeBlock":
            if hasattr(block, "group") and block.group:
                node_id = block.group.node_id
                defined_nodes.add(str(node_id))
                label = (
                    block.group.label.value
                    if hasattr(block.group, "label") and block.group.label
                    else ""
                )
                anchor = (
                    block.group.anchor_id.value
                    if hasattr(block.group, "anchor_id") and block.group.anchor_id
                    else None
                )
                author = f"author={node_id.part1}" if node_id.part1 != 0 else ""
                lines.append(f"[{i:2d}] {block_type}: node_id={node_id} {author}")
                if label:
                    lines.append(f"      label='{label}'")
                if anchor:
                    lines.append(f"      anchor={anchor}")
        elif block_type == "SceneGroupItemBlock":
            parent_id = block.parent_id
            value = block.item.value
            item_id = block.item.item_id
            left_id = block.item.left_id
            right_id = block.item.right_id

            # Check if parent exists
            parent_exists = str(parent_id) in defined_nodes
            parent_status = "✓" if parent_exists else "✗ MISSING"

            referenced_parents.append((str(parent_id), parent_exists))

            lines.append(f"[{i:2d}] {block_type}:")
            lines.append(f"      parent_id={parent_id} {parent_status}")
            lines.append(f"      value={value} (links to TreeNode)")
            lines.append(f"      item_id={item_id}")
            lines.append(f"      left_id={left_id}, right_id={right_id}")
        elif "Line" in block_type or "Glyph" in block_type:
            parent = getattr(block, "parent_id", "?")
            lines.append(f"[{i:2d}] {block_type}: parent_id={parent}")
        elif block_type in ["MigrationInfoBlock", "PageInfoBlock", "RootTextBlock", "SceneInfo"]:
            lines.append(f"[{i:2d}] {block_type}")
        else:
            lines.append(f"[{i:2d}] {block_type}")

    # Summary
    lines.append("\n=== Summary ===")
    lines.append(f"Defined TreeNodeBlocks: {len(defined_nodes)}")
    lines.append(f"  {', '.join(sorted(defined_nodes))}")

    missing_parents = [p for p, exists in referenced_parents if not exists]
    if missing_parents:
        lines.append(f"\n⚠️ SceneGroupItemBlocks with MISSING parent_id:")
        for p in set(missing_parents):
            lines.append(f"  - {p}")
    else:
        lines.append("\n✓ All SceneGroupItemBlock parent_ids exist")

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
        epilog=__doc__,
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to .rm file or directory containing .rm files",
    )
    parser.add_argument(
        "--mode",
        "-m",
        default="summary",
        choices=[
            "summary",
            "annotations",
            "anchors",
            "coords",
            "baselines",
            "blocks",
            "text",
            "structure",
            "scene-graph",
        ],
        help="Analysis mode to run (default: summary)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Optional output file (default: print to stdout)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show additional details",
    )

    args = parser.parse_args()

    if not args.path.exists():
        print(f"Error: Path not found: {args.path}", file=sys.stderr)
        return 1

    modes = {
        "summary": analyze_summary,
        "annotations": analyze_annotations,
        "anchors": analyze_anchors,
        "coords": analyze_coords,
        "baselines": analyze_baselines,
        "blocks": analyze_blocks,
        "text": analyze_text,
        "structure": analyze_structure,
        "scene-graph": analyze_scene_graph,
    }

    rm_files = get_rm_files(args.path)

    for rm_file in rm_files:
        modes[args.mode](rm_file, args.output, args.verbose)
        if len(rm_files) > 1:
            print()  # Blank line between files

    return 0


if __name__ == "__main__":
    sys.exit(main())
