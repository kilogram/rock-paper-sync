"""Pure data types for coordinate transformation.

These types are decoupled from rmscene - they represent abstract
geometric concepts like positions, deltas, and bounding boxes.

Design principles:
- Immutable (frozen dataclasses)
- No external dependencies
- Rich methods for common operations
- Fully testable in isolation

Usage:
    from rock_paper_sync.transform import Position, PositionDelta, Rectangle

    pos = Position(100.0, 200.0)
    delta = PositionDelta(0.0, 57.0)  # One line down
    new_pos = pos.offset_by(delta)

    rect = Rectangle(100.0, 200.0, 150.0, 20.0)
    moved_rect = rect.offset_by(delta)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    """An (x, y) position in page coordinates.

    Represents a point on the page in absolute pixel coordinates.
    """

    x: float
    y: float

    def offset_by(self, delta: PositionDelta) -> Position:
        """Return a new Position offset by the given delta."""
        return Position(self.x + delta.dx, self.y + delta.dy)

    def distance_to(self, other: Position) -> float:
        """Calculate Euclidean distance to another position."""
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def __iter__(self):
        """Allow unpacking as (x, y)."""
        yield self.x
        yield self.y


@dataclass(frozen=True)
class PositionDelta:
    """A translation vector (dx, dy).

    Represents the difference between two positions, used to move
    annotations when content shifts.
    """

    dx: float
    dy: float

    @classmethod
    def between(cls, old: Position, new: Position) -> PositionDelta:
        """Create a delta representing movement from old to new position."""
        return cls(dx=new.x - old.x, dy=new.y - old.y)

    @classmethod
    def zero(cls) -> PositionDelta:
        """Create a zero delta (no movement)."""
        return cls(dx=0.0, dy=0.0)

    @property
    def magnitude(self) -> float:
        """Length of the delta vector."""
        return (self.dx**2 + self.dy**2) ** 0.5

    def __add__(self, other: PositionDelta) -> PositionDelta:
        """Combine two deltas."""
        return PositionDelta(self.dx + other.dx, self.dy + other.dy)

    def __iter__(self):
        """Allow unpacking as (dx, dy)."""
        yield self.dx
        yield self.dy


@dataclass(frozen=True)
class Rectangle:
    """A bounding rectangle.

    Represents a rectangular region on the page, used for highlight
    rectangles and stroke bounding boxes.
    """

    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Position:
        """Center point of the rectangle."""
        return Position(self.x + self.width / 2, self.y + self.height / 2)

    @property
    def top_left(self) -> Position:
        """Top-left corner."""
        return Position(self.x, self.y)

    @property
    def bottom_right(self) -> Position:
        """Bottom-right corner."""
        return Position(self.x + self.width, self.y + self.height)

    def offset_by(self, delta: PositionDelta) -> Rectangle:
        """Return a new Rectangle offset by the given delta."""
        return Rectangle(
            x=self.x + delta.dx,
            y=self.y + delta.dy,
            width=self.width,
            height=self.height,
        )

    def contains(self, pos: Position) -> bool:
        """Check if position is inside the rectangle."""
        return self.x <= pos.x <= self.x + self.width and self.y <= pos.y <= self.y + self.height

    def intersects(self, other: Rectangle) -> bool:
        """Check if rectangles overlap."""
        return not (
            self.x + self.width < other.x
            or other.x + other.width < self.x
            or self.y + self.height < other.y
            or other.y + other.height < self.y
        )

    @classmethod
    def from_tuple(cls, t: tuple[float, float, float, float]) -> Rectangle:
        """Create from (x, y, width, height) tuple."""
        return cls(x=t[0], y=t[1], width=t[2], height=t[3])

    def to_tuple(self) -> tuple[float, float, float, float]:
        """Convert to (x, y, width, height) tuple."""
        return (self.x, self.y, self.width, self.height)


@dataclass(frozen=True)
class TextSpan:
    """A span of text identified by character offsets.

    Represents a contiguous range of characters in a document,
    used for anchoring annotations to text content.
    """

    start: int
    end: int

    @property
    def length(self) -> int:
        """Number of characters in the span."""
        return self.end - self.start

    def offset_by(self, delta: int) -> TextSpan:
        """Return a new TextSpan shifted by delta characters."""
        return TextSpan(self.start + delta, self.end + delta)

    def contains_offset(self, offset: int) -> bool:
        """Check if character offset is within this span."""
        return self.start <= offset < self.end

    def extract_from(self, text: str) -> str:
        """Extract the text content of this span."""
        return text[self.start : self.end]

    def __iter__(self):
        """Allow unpacking as (start, end)."""
        yield self.start
        yield self.end


@dataclass(frozen=True)
class AnchorResolution:
    """Result of resolving an anchor in new text.

    When text changes, we need to find where an annotation's anchor
    (its attachment point) has moved to. This captures the result
    of that resolution.
    """

    old_offset: int
    new_offset: int
    confidence: float  # 0.0 to 1.0, how confident we are in the match
    match_type: str  # "exact", "fuzzy", "context", "fallback"

    @property
    def offset_delta(self) -> int:
        """Character offset change."""
        return self.new_offset - self.old_offset


@dataclass(frozen=True)
class RelocationResult:
    """Result of relocating an annotation.

    Captures all information about how an annotation was moved,
    useful for logging and debugging.
    """

    anchor_resolution: AnchorResolution
    position_delta: PositionDelta
    reflow_detected: bool
    new_rectangles: tuple[Rectangle, ...] | None = None

    @property
    def new_offset(self) -> int:
        """New character offset after relocation."""
        return self.anchor_resolution.new_offset

    @property
    def confidence(self) -> float:
        """Confidence in the relocation."""
        return self.anchor_resolution.confidence


def rectangles_from_tuples(
    tuples: Sequence[tuple[float, float, float, float]],
) -> list[Rectangle]:
    """Convert sequence of (x, y, w, h) tuples to Rectangle list."""
    return [Rectangle.from_tuple(t) for t in tuples]


def rectangles_to_tuples(
    rectangles: Sequence[Rectangle],
) -> list[tuple[float, float, float, float]]:
    """Convert sequence of Rectangles to (x, y, w, h) tuple list."""
    return [r.to_tuple() for r in rectangles]
