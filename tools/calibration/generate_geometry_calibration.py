#!/usr/bin/env python3
"""Generate geometry calibration .rm file with ruler strokes.

Creates an .rm file with:
1. Border strokes at the edge of the visible area
2. Ruler marks at known physical intervals (inches)
3. Grid lines for visual verification

The user can compare these marks to a physical ruler to validate
coordinate mapping accuracy.

Usage:
    uv run python tools/calibration/generate_geometry_calibration.py

    # Output to specific file:
    uv run python tools/calibration/generate_geometry_calibration.py -o calibration_geometry
"""

from __future__ import annotations

import argparse
import io
import uuid
from pathlib import Path

import rmscene
from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtId, CrdtSequence, CrdtSequenceItem
from rmscene.scene_stream import (
    AuthorIdsBlock,
    MigrationInfoBlock,
    PageInfoBlock,
    RootTextBlock,
    SceneGroupItemBlock,
    SceneInfo,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)
from rmscene.tagged_block_common import LwwValue

# reMarkable Paper Pro Move - Physical Device Specs
# Source: reMarkable website
PHYSICAL_WIDTH = 954    # pixels (horizontal in portrait)
PHYSICAL_HEIGHT = 1696  # pixels (vertical in portrait)
PHYSICAL_PPI = 264      # Physical screen resolution
# Physical size: 91.8mm (W) × 163.2mm (H)

# Document Coordinate System (inherited from reMarkable 2)
# All .rm files use reMarkable 2's coordinate system regardless of target device
PAGE_WIDTH = 1404   # doc pixels (from reMarkable 2: 1404×1872 @ 226 DPI)
PAGE_HEIGHT = 1872  # doc pixels
TEXT_WIDTH = 750.0
TEXT_POS_X = -375.0  # Centered
TEXT_POS_Y = 94.0

# CRITICAL: Document DPI is ALWAYS 226 (reMarkable 2's resolution)
# This was empirically validated with physical ruler measurements
REMARKABLE_2_PPI = 226  # Document coordinate system DPI
DOC_PPI = REMARKABLE_2_PPI  # Use this for all physical measurements

# Rendering on Paper Pro Move:
# - Scale factor: 264 / 226 = 1.168 physical pixels per doc pixel
# - Viewport shows ~1443 doc pixels vertically (1696 physical - ~253 for UI chrome)
# - Content below y≈1443 is off-screen but exists in document
VIEWPORT_HEIGHT = 1443  # Empirically measured visible doc pixels


def doc_pixels_per_inch() -> float:
    """Document pixels per physical inch (always 226 DPI from reMarkable 2)."""
    return DOC_PPI


def create_line_stroke(
    x1: float, y1: float, x2: float, y2: float, node_id: CrdtId, num_points: int = 5, width: int = 5
) -> si.Line:
    """Create a Line stroke between two points.

    Args:
        x1, y1: Starting coordinates
        x2, y2: Ending coordinates
        node_id: CRDT node ID for this stroke
        num_points: Number of points to interpolate (more = smoother)
        width: Stroke width in pixels (default 5 for visibility)

    Returns:
        si.Line object
    """
    points = []
    for i in range(num_points):
        t = i / (num_points - 1) if num_points > 1 else 0
        x = x1 + t * (x2 - x1)
        y = y1 + t * (y2 - y1)
        # Stroke parameters: speed, direction, width, pressure
        point = si.Point(
            x=x,
            y=y,
            speed=0,
            direction=0,
            width=width,  # Line width (must be integer)
            pressure=128,  # Pressure (0-255)
        )
        points.append(point)

    return si.Line(
        color=si.PenColor.BLACK,
        tool=si.Pen.FINELINER_2,  # Clean, precise lines
        points=points,
        thickness_scale=2.0,
        starting_length=0.0,
    )


class CrdtIdGenerator:
    """Generate sequential CRDT IDs."""

    def __init__(self, start: int = 100):
        self.current = start

    def next(self) -> CrdtId:
        """Get next CRDT ID."""
        cid = CrdtId(1, self.current)
        self.current += 1
        return cid


def generate_ruler_strokes(id_gen: CrdtIdGenerator) -> list:
    """Generate stroke blocks for ruler marks.

    Returns:
        List of rmscene SceneLineItemBlock blocks
    """
    blocks = []

    # Helper to add a stroke
    def add_stroke(x1: float, y1: float, x2: float, y2: float):
        stroke_id = id_gen.next()
        line = create_line_stroke(x1, y1, x2, y2, stroke_id)

        # Add stroke directly to Layer 1 (minimal approach - no TreeNodeBlock needed)
        blocks.append(
            SceneLineItemBlock(
                parent_id=CrdtId(0, 11),  # Layer 1
                item=CrdtSequenceItem(
                    item_id=id_gen.next(),
                    left_id=CrdtId(0, 0),
                    right_id=CrdtId(0, 0),
                    deleted_length=0,
                    value=line,  # The Line object, not the stroke_id
                ),
            )
        )

    # Coordinate system: (0, 0) at top-center, +Y down, ±X from center
    # Note: PAGE_HEIGHT (1872) is logical document height, not physical visible area
    # Physical viewport is smaller - adjusting to match actual visible bounds

    # Physical viewport bounds (iterating to find actual limits)
    left = -400
    right = 400
    top = 10  # Closer to physical top
    bottom = 1400  # Physical bottom is much higher than logical 1872

    # Border rectangle
    # Top edge
    add_stroke(left, top, right, top)
    # Bottom edge
    add_stroke(left, bottom, right, bottom)
    # Left edge
    add_stroke(left, top, left, bottom)
    # Right edge
    add_stroke(right, top, right, bottom)

    # Physical ruler calibration (1 cm intervals)
    # 1 cm = 0.393701 inches
    # Using DOC_PPI = 226 (reMarkable 2's DPI) for accurate physical measurements
    cm_to_doc_pixels = 0.393701 * DOC_PPI  # ~89.0 doc pixels per cm

    # Start 1 cm below top border
    ruler_start_y = top + cm_to_doc_pixels  # 10 + ~88 = ~98
    ruler_x = -300  # Place on left side
    tick_length_long = 60  # Main tick marks
    tick_length_short = 30  # Half cm marks

    # Draw 71.8mm (7.18 cm) ruler with 1 cm intervals
    ruler_length_cm = 7.18  # 71.8mm
    num_cm_marks = 8  # 0, 1, 2, 3, 4, 5, 6, 7 cm marks

    for i in range(num_cm_marks):
        y_pos = ruler_start_y + (i * cm_to_doc_pixels)
        # Draw tick mark
        add_stroke(ruler_x, y_pos, ruler_x + tick_length_long, y_pos)

        # Draw half-cm marks between (except after the last one)
        if i < num_cm_marks - 1:
            y_half = y_pos + (cm_to_doc_pixels / 2)
            add_stroke(ruler_x, y_half, ruler_x + tick_length_short, y_half)

    # Draw vertical ruler line extending to exactly 71.8mm
    ruler_end_y = ruler_start_y + (ruler_length_cm * cm_to_doc_pixels)
    add_stroke(ruler_x, ruler_start_y, ruler_x, ruler_end_y)

    return blocks


def generate_rm_file(output_path: Path) -> None:
    """Generate .rm file with ruler strokes."""
    id_gen = CrdtIdGenerator(start=100)

    # Minimal text content (just the marker)
    text_content = "Geometry Calibration\n\nRuler marks show physical inch boundaries.\n\ne"

    # Build text styles with newline markers
    styles = {CrdtId(0, 0): LwwValue(timestamp=CrdtId(1, 15), value=si.ParagraphStyle.PLAIN)}
    for i, char in enumerate(text_content):
        if char == "\n":
            styles[CrdtId(0, i)] = LwwValue(timestamp=CrdtId(1, 15), value=10)

    # Build blocks for a minimal .rm file
    blocks = [
        # Header blocks
        AuthorIdsBlock(author_uuids={1: uuid.UUID('00000000-0000-0000-0000-000000000001')}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(
            loads_count=1,
            merges_count=0,
            text_chars_count=len(text_content) + 1,
            text_lines_count=text_content.count("\n") + 1,
        ),
        # Scene info with paper size (testing coordinate system)
        SceneInfo(
            current_layer=LwwValue(timestamp=CrdtId(1, 2), value=CrdtId(0, 11)),
            background_visible=LwwValue(timestamp=CrdtId(1, 3), value=True),
            root_document_visible=LwwValue(timestamp=CrdtId(1, 4), value=True),
            paper_size=(PHYSICAL_WIDTH, PHYSICAL_HEIGHT),  # Paper Pro Move: 954 x 1696
        ),
        # Scene tree structure
        SceneTreeBlock(
            tree_id=CrdtId(0, 11),
            node_id=CrdtId(0, 0),
            is_update=True,
            parent_id=CrdtId(0, 1),
        ),
        # Root text block
        RootTextBlock(
            block_id=CrdtId(0, 0),
            value=si.Text(
                items=CrdtSequence(
                    [
                        CrdtSequenceItem(
                            item_id=CrdtId(1, 16),
                            left_id=CrdtId(0, 0),
                            right_id=CrdtId(0, 0),
                            deleted_length=0,
                            value=text_content,
                        )
                    ]
                ),
                styles=styles,
                pos_x=TEXT_POS_X,
                pos_y=TEXT_POS_Y,
                width=TEXT_WIDTH,
            ),
        ),
        # Root group
        TreeNodeBlock(si.Group(node_id=CrdtId(0, 1))),
        # Layer 1
        TreeNodeBlock(
            si.Group(
                node_id=CrdtId(0, 11),
                label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
            )
        ),
        # Add layer to root
        SceneGroupItemBlock(
            parent_id=CrdtId(0, 1),
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=CrdtId(0, 11),
            ),
        ),
    ]

    # Add ruler strokes
    stroke_blocks = generate_ruler_strokes(id_gen)
    blocks.extend(stroke_blocks)

    # Write to .rm file
    buffer = io.BytesIO()
    rmscene.write_blocks(buffer, blocks)
    rm_bytes = buffer.getvalue()

    rm_path = output_path.with_suffix('.rm')
    rm_path.parent.mkdir(parents=True, exist_ok=True)
    rm_path.write_bytes(rm_bytes)
    print(f"Generated: {rm_path} ({len(rm_bytes)} bytes, {len(stroke_blocks)} stroke blocks)")


def generate_calibration_markdown() -> str:
    """Generate markdown content for geometry calibration document."""
    lines = [
        "# Geometry Calibration",
        "",
        "This document has ruler strokes at known physical positions.",
        "",
        "## Coordinate System",
        "",
        f"- Document canvas: {PAGE_WIDTH} × {PAGE_HEIGHT} doc pixels (reMarkable 2 format)",
        f"- Document DPI: {DOC_PPI} (reMarkable 2's resolution)",
        f"- Physical device: Paper Pro Move",
        f"  - Screen: {PHYSICAL_WIDTH} × {PHYSICAL_HEIGHT} pixels at {PHYSICAL_PPI} PPI",
        f"  - Scale: {PHYSICAL_PPI / DOC_PPI:.3f}× ({PHYSICAL_PPI}/{DOC_PPI})",
        f"- Viewport: ~{VIEWPORT_HEIGHT} doc pixels visible (after UI chrome)",
        "",
        "## Ruler Reference",
        "",
        "The strokes in the .rm file show:",
        "- Border at visible viewport edges",
        "- 71.8mm vertical ruler with 1cm tick marks",
        "- Calibrated using empirical measurements with physical ruler",
        "",
        "## Coordinate System Discovery",
        "",
        f"Document coordinates use reMarkable 2's {DOC_PPI} DPI regardless of target device.",
        f"When rendered on Paper Pro Move ({PHYSICAL_PPI} PPI), coordinates are scaled",
        f"{PHYSICAL_PPI / DOC_PPI:.3f}× to match the physical screen.",
        "",
        f"Content below y≈{VIEWPORT_HEIGHT} is off-screen but exists in the document.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate geometry calibration files")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(__file__).parent.parent.parent
        / "tests"
        / "record_replay"
        / "fixtures"
        / "calibration_geometry",
        help="Output file path (without extension)",
    )

    args = parser.parse_args()

    # Generate markdown
    md_path = args.output.with_suffix('.md')
    md_content = generate_calibration_markdown()
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_content)
    print(f"Generated: {md_path}")

    # Generate .rm file with strokes
    generate_rm_file(args.output)


if __name__ == "__main__":
    main()
