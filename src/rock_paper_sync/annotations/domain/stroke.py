"""CRDT-aware Stroke representation.

This module provides a rich Stroke class that combines:
- Point data (for rendering/clustering)
- CRDT block references (for device serialization)

Key distinction from other stroke types:
- core_types.Stroke: Point-based only, no CRDT awareness
- core_types.StrokeData: Lightweight, for clustering/rendering
- scene_adapter.StrokeBundle: CRDT blocks only, no point data
- THIS Stroke: Full fidelity - points + CRDT context

Usage:
    # Create from SceneGraphIndex (extracts both point data and CRDT refs)
    strokes = Stroke.from_scene_index(index)

    # Access point data for rendering
    for point in stroke.points:
        draw_point(point.x, point.y, point.pressure)

    # Access CRDT refs for serialization
    for block in stroke.bundle.to_raw_blocks():
        writer.write_block(block)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rmscene import CrdtId, SceneLineItemBlock

from ..core.types import Point

if TYPE_CHECKING:
    from ..core.types import StrokeData
    from ..scene_adapter.bundle import StrokeBundle
    from ..scene_adapter.scene_index import SceneGraphIndex


@dataclass
class Stroke:
    """A single stroke with full CRDT context.

    Combines point data (for rendering/clustering) with CRDT block references
    (for device serialization). This is the "rich" stroke type.

    Each stroke belongs to exactly one StrokeBundle (CRDT serialization unit).
    Multiple strokes may share the same TreeNodeBlock if drawn in quick succession.

    Attributes:
        stroke_id: Unique identifier from the SceneLineItemBlock
        points: Stroke path as Point objects (x, y, pressure, width, speed)
        bounding_box: Axis-aligned bounding box as (x, y, w, h)
        color: Pen color code (0=black, 1=grey, 2=white, 3=yellow, etc.)
        tool: Pen tool type (ballpoint, fineliner, highlighter, etc.)
        thickness: Stroke thickness scale

        tree_node_id: CrdtId of the TreeNodeBlock this stroke belongs to
        line_block: The raw SceneLineItemBlock for serialization
        bundle: Reference to the containing StrokeBundle (all 4 CRDT blocks)
    """

    # Stroke data
    stroke_id: CrdtId
    points: list[Point]
    bounding_box: tuple[float, float, float, float]  # (x, y, w, h)
    color: int
    tool: int
    thickness: float

    # CRDT context
    tree_node_id: CrdtId
    line_block: SceneLineItemBlock
    bundle: StrokeBundle | None = field(default=None, repr=False)

    @property
    def center(self) -> tuple[float, float]:
        """Get the center point of the stroke's bounding box."""
        x, y, w, h = self.bounding_box
        return (x + w / 2, y + h / 2)

    @property
    def center_y(self) -> float:
        """Get the vertical center of the stroke."""
        return self.center[1]

    @property
    def anchor_offset(self) -> int | None:
        """Get the text anchor offset from the parent TreeNodeBlock."""
        if self.bundle and self.bundle.tree_node:
            return self.bundle.anchor_offset
        return None

    @classmethod
    def from_line_block(
        cls,
        line_block: SceneLineItemBlock,
        bundle: StrokeBundle | None = None,
    ) -> Stroke:
        """Create a Stroke from a SceneLineItemBlock.

        Extracts point data and metadata from the raw rmscene block.

        Args:
            line_block: The SceneLineItemBlock containing stroke data
            bundle: Optional reference to the containing StrokeBundle
        """
        # Extract points from the line block
        points: list[Point] = []
        item = line_block.item
        if hasattr(item, "value") and hasattr(item.value, "points"):
            for pt in item.value.points:
                points.append(
                    Point(
                        x=pt.x,
                        y=pt.y,
                        pressure=getattr(pt, "pressure", 1.0),
                        width=getattr(pt, "width", 2.0),
                        speed=getattr(pt, "speed", 0.0),
                    )
                )

        # Calculate bounding box from points
        if points:
            xs = [p.x for p in points]
            ys = [p.y for p in points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            bbox = (min_x, min_y, max_x - min_x, max_y - min_y)
        else:
            bbox = (0.0, 0.0, 0.0, 0.0)

        # Extract color, tool, thickness from the line item
        color = 0
        tool = 0
        thickness = 2.0
        if hasattr(item, "value"):
            val = item.value
            if hasattr(val, "color"):
                color = val.color.value if hasattr(val.color, "value") else int(val.color)
            if hasattr(val, "tool"):
                tool = val.tool.value if hasattr(val.tool, "value") else int(val.tool)
            if hasattr(val, "thickness_scale"):
                thickness = val.thickness_scale

        return cls(
            stroke_id=line_block.item.item_id,
            points=points,
            bounding_box=bbox,
            color=color,
            tool=tool,
            thickness=thickness,
            tree_node_id=line_block.parent_id,
            line_block=line_block,
            bundle=bundle,
        )

    @classmethod
    def from_scene_index(cls, index: SceneGraphIndex) -> list[Stroke]:
        """Extract all Strokes from a SceneGraphIndex.

        Creates Stroke objects with full CRDT context by:
        1. Building StrokeBundles from the index
        2. Creating Stroke objects from each line block
        3. Linking strokes to their bundles

        Args:
            index: SceneGraphIndex containing all blocks from an .rm file

        Returns:
            List of Stroke objects with bundle references
        """
        from ..scene_adapter.bundle import StrokeBundle

        # Build bundles first
        bundles = StrokeBundle.from_index(index)

        # Create lookup by node_id
        bundle_by_node: dict[tuple[int, int], StrokeBundle] = {}
        for bundle in bundles:
            key = (bundle.node_id.part1, bundle.node_id.part2)
            bundle_by_node[key] = bundle

        # Create strokes from line blocks
        strokes: list[Stroke] = []
        for bundle in bundles:
            for line_block in bundle.strokes:
                stroke = cls.from_line_block(line_block, bundle=bundle)
                strokes.append(stroke)

        return strokes

    def to_stroke_data(self) -> StrokeData:
        """Convert to lightweight StrokeData for clustering/rendering.

        Returns a StrokeData object that can be used by clustering algorithms
        and rendering code without the full CRDT context.
        """
        from ..core.types import StrokeData

        return StrokeData(
            bounding_box=self.bounding_box,
            points=self.points,
            color=self.color,
            tool=self.tool,
            thickness=self.thickness,
        )

    def __str__(self) -> str:
        from ..scene_adapter.bundle import format_crdt_id

        anchor_str = f"anchor={self.anchor_offset}" if self.anchor_offset else "no anchor"
        return (
            f"Stroke({format_crdt_id(self.stroke_id)}, "
            f"parent={format_crdt_id(self.tree_node_id)}, "
            f"{len(self.points)} points, {anchor_str})"
        )
