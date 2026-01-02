#!/usr/bin/env python3
"""Visualize strokes from .rm files with cluster bounding boxes.

Uses the same coordinate transformation logic as the generator to ensure
consistent positioning via the shared ParentAnchorResolver class.

Usage:
    uv run tools/analysis/stroke_visualizer.py <rm_file> [--output <svg_file>] [--threshold <px>]

Examples:
    uv run tools/analysis/stroke_visualizer.py tests/record_replay/testdata/stroke_reanchor/phases/phase_2_phase_2/rm_files/*.rm
    uv run tools/analysis/stroke_visualizer.py file.rm --output /tmp/strokes.svg --threshold 80
"""

import argparse
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import rmscene

from rock_paper_sync.annotations.common.spatial import cluster_bboxes_kdtree
from rock_paper_sync.coordinates import (
    AnchorResolver as ParentAnchorResolver,
    is_root_layer,
)

# is_text_relative is inverse of is_root_layer
def is_text_relative(parent_id):
    return not is_root_layer(parent_id)


def extract_strokes(rm_path: Path) -> tuple[list[dict], float]:
    """Extract stroke data from .rm file using proper per-parent coordinate transformation.

    Uses ParentAnchorResolver to ensure the same coordinate transformation logic
    as the generator for consistent positioning.

    Returns:
        Tuple of (strokes, text_origin_y)
    """
    strokes = []

    # Create ParentAnchorResolver - handles all per-parent anchor extraction
    anchor_resolver = ParentAnchorResolver.from_rm_file(rm_path)

    # Read blocks for stroke extraction
    with open(rm_path, "rb") as f:
        blocks = list(rmscene.read_blocks(f))

    # Extract strokes with per-parent anchoring
    for block in blocks:
        if type(block).__name__ == "SceneLineItemBlock":
            item = block.item
            if hasattr(item, "value") and item.value is not None:
                line = item.value
                if hasattr(line, "points") and line.points:
                    parent_id = getattr(block, "parent_id", None)

                    # Transform points using per-parent anchors via resolver
                    abs_points = []
                    for p in line.points:
                        abs_x, abs_y = anchor_resolver.to_absolute(p.x, p.y, parent_id)
                        abs_points.append((abs_x, abs_y))

                    abs_xs = [p[0] for p in abs_points]
                    abs_ys = [p[1] for p in abs_points]

                    strokes.append({
                        "idx": len(strokes),
                        "points": abs_points,
                        "bbox": (min(abs_xs), min(abs_ys),
                                max(abs_xs) - min(abs_xs),
                                max(abs_ys) - min(abs_ys)),
                        "center": ((min(abs_xs) + max(abs_xs)) / 2,
                                  (min(abs_ys) + max(abs_ys)) / 2),
                        "parent_id": str(parent_id),
                        "is_text_relative": is_text_relative(parent_id),
                    })

    # Get text origin Y from the resolver's layout context config
    text_origin_y = anchor_resolver.layout_context.config.text_pos_y

    return strokes, text_origin_y


def get_cluster_bbox(strokes: list[dict], cluster_indices: list[int]) -> tuple[float, float, float, float]:
    """Get bounding box for a cluster of strokes."""
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")

    for idx in cluster_indices:
        x, y, w, h = strokes[idx]["bbox"]
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x + w)
        max_y = max(max_y, y + h)

    return (min_x, min_y, max_x - min_x, max_y - min_y)


# Cluster colors (colorblind-friendly palette)
CLUSTER_COLORS = [
    "#E69F00",  # Orange
    "#56B4E9",  # Sky blue
    "#009E73",  # Green
    "#F0E442",  # Yellow
    "#0072B2",  # Blue
    "#D55E00",  # Vermillion
    "#CC79A7",  # Pink
    "#999999",  # Gray
]


def generate_svg(
    strokes: list[dict],
    clusters: list[list[int]],
    title: str = "Stroke Visualization",
    show_indices: bool = True,
    show_cluster_boxes: bool = True,
) -> str:
    """Generate SVG visualization of strokes with cluster coloring."""
    if not strokes:
        return "<svg></svg>"

    # Calculate bounds
    all_points = []
    for s in strokes:
        all_points.extend(s["points"])

    if not all_points:
        return "<svg></svg>"

    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    # Add padding
    padding = 50
    width = max_x - min_x + 2 * padding
    height = max_y - min_y + 2 * padding

    # Scale to reasonable SVG size (max 1200px wide)
    scale = min(1.0, 1200 / width) if width > 0 else 1.0
    svg_width = width * scale
    svg_height = height * scale

    # Build stroke-to-cluster mapping
    stroke_to_cluster = {}
    for cluster_idx, cluster in enumerate(clusters):
        for stroke_idx in cluster:
            stroke_to_cluster[stroke_idx] = cluster_idx

    svg_parts = [
        f'<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width:.0f}" height="{svg_height:.0f}" '
        f'viewBox="{min_x - padding} {min_y - padding} {width} {height}">',
        f'  <rect x="{min_x - padding}" y="{min_y - padding}" width="{width}" height="{height}" fill="white"/>',
        f'  <title>{title}</title>',
        "",
        "  <!-- Grid lines -->",
        f'  <line x1="{min_x - padding}" y1="0" x2="{max_x + padding}" y2="0" stroke="#ddd" stroke-width="0.5"/>',
        f'  <line x1="0" y1="{min_y - padding}" x2="0" y2="{max_y + padding}" stroke="#ddd" stroke-width="0.5"/>',
        f'  <text x="5" y="-5" font-size="10" fill="#999">Y=0</text>',
        "",
    ]

    # Draw cluster bounding boxes first (behind strokes)
    if show_cluster_boxes:
        svg_parts.append("  <!-- Cluster bounding boxes -->")
        for cluster_idx, cluster in enumerate(clusters):
            color = CLUSTER_COLORS[cluster_idx % len(CLUSTER_COLORS)]
            x, y, w, h = get_cluster_bbox(strokes, cluster)
            svg_parts.append(
                f'  <rect x="{x - 5}" y="{y - 5}" width="{w + 10}" height="{h + 10}" '
                f'fill="{color}" fill-opacity="0.1" stroke="{color}" stroke-width="2" stroke-dasharray="5,5"/>'
            )
            # Cluster label
            svg_parts.append(
                f'  <text x="{x - 5}" y="{y - 10}" font-size="12" fill="{color}" font-weight="bold">'
                f'Cluster {cluster_idx} ({len(cluster)} strokes)</text>'
            )
        svg_parts.append("")

    # Draw strokes
    svg_parts.append("  <!-- Strokes -->")
    for stroke in strokes:
        cluster_idx = stroke_to_cluster.get(stroke["idx"], 0)
        color = CLUSTER_COLORS[cluster_idx % len(CLUSTER_COLORS)]

        # Draw stroke path
        points = stroke["points"]
        if len(points) >= 2:
            path_data = f"M {points[0][0]} {points[0][1]}"
            for px, py in points[1:]:
                path_data += f" L {px} {py}"
            svg_parts.append(
                f'  <path d="{path_data}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
            )
        elif len(points) == 1:
            # Single point - draw as circle
            svg_parts.append(
                f'  <circle cx="{points[0][0]}" cy="{points[0][1]}" r="3" fill="{color}"/>'
            )

        # Draw stroke center and index
        if show_indices:
            cx, cy = stroke["center"]
            svg_parts.append(
                f'  <circle cx="{cx}" cy="{cy}" r="4" fill="white" stroke="{color}" stroke-width="1"/>'
            )
            svg_parts.append(
                f'  <text x="{cx + 6}" y="{cy + 3}" font-size="8" fill="#333">{stroke["idx"]}</text>'
            )

    # Legend
    svg_parts.append("")
    svg_parts.append("  <!-- Legend -->")
    legend_y = min_y - padding + 20
    for cluster_idx, cluster in enumerate(clusters):
        color = CLUSTER_COLORS[cluster_idx % len(CLUSTER_COLORS)]
        svg_parts.append(
            f'  <rect x="{max_x - 100}" y="{legend_y}" width="15" height="15" fill="{color}"/>'
        )
        svg_parts.append(
            f'  <text x="{max_x - 80}" y="{legend_y + 12}" font-size="10">Cluster {cluster_idx} ({len(cluster)})</text>'
        )
        legend_y += 20

    svg_parts.append("</svg>")

    return "\n".join(svg_parts)


def main():
    parser = argparse.ArgumentParser(description="Visualize strokes from .rm files")
    parser.add_argument("rm_files", nargs="+", type=Path, help="Path(s) to .rm file(s)")
    parser.add_argument("--output", "-o", type=Path, help="Output SVG path (default: /tmp/<filename>.svg)")
    parser.add_argument("--threshold", "-t", type=float, default=80.0, help="Clustering distance threshold (default: 80)")
    parser.add_argument("--no-indices", action="store_true", help="Hide stroke indices")
    parser.add_argument("--no-boxes", action="store_true", help="Hide cluster bounding boxes")

    args = parser.parse_args()

    for rm_path in args.rm_files:
        if not rm_path.exists():
            print(f"Error: {rm_path} does not exist")
            continue

        print(f"Processing: {rm_path}")

        # Extract strokes using proper coordinate transformation
        strokes, text_origin_y = extract_strokes(rm_path)
        print(f"  Found {len(strokes)} strokes (text_origin_y={text_origin_y})")

        if not strokes:
            print("  No strokes to visualize")
            continue

        # Cluster strokes using the same function as generator
        bboxes = [s["bbox"] for s in strokes]
        index_clusters = cluster_bboxes_kdtree(bboxes, args.threshold)
        print(f"  Clustered into {len(index_clusters)} cluster(s) (threshold: {args.threshold}px)")

        for i, cluster in enumerate(index_clusters):
            bbox = get_cluster_bbox(strokes, cluster)
            print(f"    Cluster {i}: {len(cluster)} strokes, bbox=({bbox[0]:.1f}, {bbox[1]:.1f}, {bbox[2]:.1f}x{bbox[3]:.1f})")

        # Generate SVG
        svg = generate_svg(
            strokes,
            index_clusters,
            title=f"{rm_path.name} - {len(index_clusters)} clusters",
            show_indices=not args.no_indices,
            show_cluster_boxes=not args.no_boxes,
        )

        # Output path
        if args.output:
            output_path = args.output
        else:
            output_path = Path("/tmp") / f"{rm_path.stem}.svg"

        output_path.write_text(svg)
        print(f"  SVG saved to: {output_path}")


if __name__ == "__main__":
    main()
