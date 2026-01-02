"""
Annotation extraction for reMarkable documents.

This module handles reading annotations (strokes and highlights) from reMarkable .rm files.

Key Concepts:
    - **Strokes**: Hand-drawn annotations (lines, sketches) with point coordinates
    - **Highlights**: Text selections with bounding rectangles
    - **Position mapping**: Annotations are mapped to Y-coordinates on the page

Position Coordinate System:
    reMarkable uses a coordinate system where:
    - (0, 0) is typically near the center of the page
    - Positive Y goes downward
    - Text blocks have pos_x, pos_y, and width
    - Page height ~1872 points (A4 at 226 DPI)

Example:
    >>> annotations = read_annotations("document.rm")
    >>> for ann in annotations:
    ...     if ann.type == AnnotationType.STROKE:
    ...         print(f"Stroke at y={ann.stroke.center_y()}")
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import BinaryIO

import rmscene as rm
from rmscene.tagged_block_common import CrdtId


class AnnotationType(Enum):
    """Type of annotation."""

    STROKE = "stroke"  # Hand-drawn lines/sketches
    HIGHLIGHT = "highlight"  # Text highlights


@dataclass
class Point:
    """A point in 2D space with optional metadata."""

    x: float
    y: float
    pressure: float | None = None
    width: int | None = None
    speed: float | None = None


@dataclass
class Rectangle:
    """A rectangular region on the page."""

    x: float
    y: float
    w: float
    h: float

    def center_y(self) -> float:
        """Get the vertical center of the rectangle."""
        return self.y + self.h / 2

    def contains_point(self, x: float, y: float) -> bool:
        """Check if a point is within this rectangle."""
        return self.x <= x <= self.x + self.w and self.y <= y <= self.y + self.h


@dataclass
class Stroke:
    """A hand-drawn stroke annotation.

    Attributes:
        points: List of points forming the stroke path
        color: Pen color code (0=black, 1=grey, 2=white, 3=yellow, etc.)
        tool: Pen tool type (ballpoint, fineliner, highlighter, etc.)
        thickness: Stroke thickness scale
        bounding_box: Axis-aligned bounding box containing all points
    """

    points: list[Point]
    color: int
    tool: int
    thickness: float
    bounding_box: Rectangle = field(init=False)

    def __post_init__(self):
        """Calculate bounding box from points."""
        if not self.points:
            self.bounding_box = Rectangle(0, 0, 0, 0)
        else:
            xs = [p.x for p in self.points]
            ys = [p.y for p in self.points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            self.bounding_box = Rectangle(min_x, min_y, max_x - min_x, max_y - min_y)

    def center_y(self) -> float:
        """Get the vertical center of the stroke."""
        return self.bounding_box.center_y()


@dataclass
class StrokeData:
    """Canonical stroke data for document model, clustering, and rendering.

    This is the unified representation for stroke data used by:
    - DocumentModel/DocumentAnnotation for annotation storage
    - ClusteringStrategy implementations for spatial grouping
    - OCR rendering for image generation

    Uses Point objects for full fidelity with the original stroke data.

    For clustering, only bounding_box is required. Other fields are optional
    and default to sensible values when not provided.

    Attributes:
        bounding_box: Axis-aligned bounding box as (x, y, w, h) - REQUIRED
        points: Stroke points as Point objects
        color: Stroke color code (0=black, 1=grey, 2=white, etc.)
        tool: Pen tool type (ballpoint, fineliner, etc.)
        thickness: Stroke thickness scale
        timestamps: Optional per-point timestamps (for future visual model)
    """

    bounding_box: tuple[float, float, float, float]  # (x, y, w, h) - required
    points: list[Point] = field(default_factory=list)
    color: int = 0
    tool: int = 0
    thickness: float = 2.0
    timestamps: list[float] | None = None  # For future clustering models

    @property
    def center(self) -> tuple[float, float]:
        """Get bounding box center (x, y) for clustering."""
        x, y, w, h = self.bounding_box
        return (x + w / 2, y + h / 2)

    @classmethod
    def from_stroke(cls, stroke: Stroke) -> "StrokeData":
        """Create StrokeData from a Stroke object."""
        bbox = stroke.bounding_box
        return cls(
            bounding_box=(bbox.x, bbox.y, bbox.w, bbox.h),
            points=list(stroke.points),  # Copy Point objects directly
            color=stroke.color,
            tool=stroke.tool,
            thickness=stroke.thickness,
        )

    @classmethod
    def from_points_and_metadata(
        cls,
        points: list[Point],
        color: int = 0,
        tool: int = 0,
        thickness: float = 2.0,
    ) -> "StrokeData":
        """Create StrokeData from points, computing bounding box automatically."""
        if not points:
            return cls(
                bounding_box=(0, 0, 0, 0), points=[], color=color, tool=tool, thickness=thickness
            )

        xs = [p.x for p in points]
        ys = [p.y for p in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        bbox = (min_x, min_y, max_x - min_x, max_y - min_y)

        return cls(
            bounding_box=bbox,
            points=points,
            color=color,
            tool=tool,
            thickness=thickness,
        )


@dataclass
class Highlight:
    """A text highlight annotation.

    Attributes:
        text: The highlighted text content
        color: Highlight color code
        rectangles: List of rectangles covering the highlight (can span multiple lines)
    """

    text: str
    color: int
    rectangles: list[Rectangle]

    def center_y(self) -> float:
        """Get the average vertical center of all rectangles."""
        if not self.rectangles:
            return 0.0
        return sum(r.center_y() for r in self.rectangles) / len(self.rectangles)


@dataclass
class Annotation:
    """A generic annotation that can be either a stroke or highlight.

    Attributes:
        type: The type of annotation
        stroke: Stroke data (if type is STROKE)
        highlight: Highlight data (if type is HIGHLIGHT)
        layer_id: Optional layer/group ID for organization
        parent_id: Optional parent CRDT ID for coordinate space detection
            - CrdtId(0, 11): Absolute coordinates (root layer)
            - Other values: Text-relative coordinates
        annotation_id: Unique identifier for this annotation
    """

    type: AnnotationType
    stroke: Stroke | None = None
    highlight: Highlight | None = None
    layer_id: str | None = None
    parent_id: CrdtId | None = None
    annotation_id: str = field(default_factory=lambda: str(id(None)))  # Unique ID per instance

    def center_y(self) -> float:
        """Get the vertical center position of this annotation."""
        if self.type == AnnotationType.STROKE and self.stroke:
            return self.stroke.center_y()
        elif self.type == AnnotationType.HIGHLIGHT and self.highlight:
            return self.highlight.center_y()
        return 0.0

    @property
    def bounding_box(self) -> Rectangle | None:
        """Get bounding box for this annotation."""
        if self.stroke:
            return self.stroke.bounding_box
        elif self.highlight and self.highlight.rectangles:
            # Return the combined bounding box of all highlight rectangles
            if not self.highlight.rectangles:
                return None
            rects = self.highlight.rectangles
            return Rectangle(
                x=min(r.x for r in rects),
                y=min(r.y for r in rects),
                w=max(r.x + r.w for r in rects) - min(r.x for r in rects),
                h=max(r.y + r.h for r in rects) - min(r.y for r in rects),
            )
        return None


@dataclass
class TextBlock:
    """A block of text content with position information.

    This represents text generated from markdown (headings, paragraphs, lists, etc.)
    that we want to associate with annotations.

    Attributes:
        content: The text content
        y_start: Starting Y coordinate
        y_end: Ending Y coordinate
        block_type: Type of block (heading, paragraph, list_item, etc.)
        markdown_line: Optional line number in original markdown
        page_index: Which page this block is on (for cross-page annotation tracking)
        char_start: Character offset where this block starts in the full page text
        char_end: Character offset where this block ends in the full page text
    """

    content: str
    y_start: float
    y_end: float
    block_type: str
    markdown_line: int | None = None
    page_index: int = 0  # For cross-page annotation tracking
    char_start: int | None = None  # Offset in full page text
    char_end: int | None = None  # Offset in full page text

    def contains_y(self, y: float) -> bool:
        """Check if a Y coordinate falls within this text block."""
        return self.y_start <= y <= self.y_end


def read_annotations(file_path: Path | str | BinaryIO) -> list[Annotation]:
    """Read all annotations from a reMarkable .rm file.

    Args:
        file_path: Path to .rm file or open binary file object

    Returns:
        List of Annotation objects (strokes and highlights)

    Example:
        >>> annotations = read_annotations("document.rm")
        >>> print(f"Found {len(annotations)} annotations")
        >>> for ann in annotations:
        ...     if ann.type == AnnotationType.STROKE:
        ...         print(f"Stroke at y={ann.stroke.center_y()}")
    """
    annotations = []

    # Open file if path provided
    if isinstance(file_path, Path | str):
        with open(file_path, "rb") as f:
            return read_annotations(f)

    # Read blocks from file
    blocks = list(rm.read_blocks(file_path))

    # Extract strokes (hand-drawn annotations)
    for block in blocks:
        if "Line" in type(block).__name__:
            line = block.item.value

            # Skip if line is None or has no points
            if line is None or not hasattr(line, "points") or line.points is None:
                continue

            # Convert rmscene points to our Point objects
            points = [
                Point(
                    x=p.x,
                    y=p.y,
                    pressure=p.pressure if hasattr(p, "pressure") else 100,
                    width=p.width if hasattr(p, "width") else 16,
                    speed=p.speed if hasattr(p, "speed") else 0,
                )
                for p in line.points
            ]

            # Skip strokes with no points
            if not points:
                continue

            stroke = Stroke(
                points=points,
                color=line.color.value if hasattr(line.color, "value") else line.color,
                tool=line.tool.value if hasattr(line.tool, "value") else line.tool,
                thickness=line.thickness_scale if hasattr(line, "thickness_scale") else 2.0,
            )

            # Extract parent_id for coordinate space detection
            parent_id = block.parent_id if hasattr(block, "parent_id") else None

            annotations.append(
                Annotation(type=AnnotationType.STROKE, stroke=stroke, parent_id=parent_id)
            )

    # Extract highlights (text selections)
    for block in blocks:
        if "Glyph" in type(block).__name__:
            glyph = block.item.value

            # Skip if glyph is None or has no rectangles
            if glyph is None or not hasattr(glyph, "rectangles") or glyph.rectangles is None:
                continue

            # Convert rectangles
            rectangles = [Rectangle(x=r.x, y=r.y, w=r.w, h=r.h) for r in glyph.rectangles]

            # Skip if no valid rectangles
            if not rectangles:
                continue

            highlight = Highlight(
                text=glyph.text if hasattr(glyph, "text") and glyph.text else "",
                color=glyph.color.value if hasattr(glyph.color, "value") else glyph.color,
                rectangles=rectangles,
            )

            # Extract parent_id for coordinate space detection
            parent_id = block.parent_id if hasattr(block, "parent_id") else None

            annotations.append(
                Annotation(type=AnnotationType.HIGHLIGHT, highlight=highlight, parent_id=parent_id)
            )

    return annotations


# Re-export WordWrapLayoutEngine from the layout module for backwards compatibility
# The canonical implementation is now in rock_paper_sync.layout.engine

__all_layout__ = ["WordWrapLayoutEngine"]
