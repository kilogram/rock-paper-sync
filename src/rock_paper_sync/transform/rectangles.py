"""Rectangle manipulation utilities for highlight relocation.

This module provides pure functions for manipulating highlight rectangles
when annotations need to be relocated. Handles both simple delta application
and complex reflow scenarios where text wraps differently.

Design principles:
- Pure functions (no side effects, no rmscene imports)
- Operate on abstract Rectangle type
- Preserve pixel-perfect positions where possible
- Handle edge cases (reflow, line breaks) gracefully

Usage:
    from rock_paper_sync.transform import (
        apply_delta_to_rectangles,
        rebuild_for_reflow,
        Rectangle,
        PositionDelta,
    )

    # Simple case: move all rectangles by delta
    new_rects = apply_delta_to_rectangles(old_rects, delta)

    # Reflow case: text wraps to different number of lines
    new_rects = rebuild_for_reflow(
        original_first_rect=old_rects[0],
        layout_rects=new_layout_rects,
        delta=delta,
        text_origin_x=100.0,
    )
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .types import PositionDelta, Rectangle

if TYPE_CHECKING:
    from rock_paper_sync.layout import WordWrapLayoutEngine


def apply_delta_to_rectangles(
    rectangles: Sequence[Rectangle],
    delta: PositionDelta,
) -> list[Rectangle]:
    """Apply position delta to a list of rectangles.

    Simple case where highlight stays on same number of lines.
    Just shifts all rectangles by the delta amount.

    Args:
        rectangles: Original highlight rectangles
        delta: Position delta to apply

    Returns:
        New list of rectangles with delta applied
    """
    return [rect.offset_by(delta) for rect in rectangles]


def detect_reflow(
    old_rect_count: int,
    new_span_start: int,
    new_span_end: int,
    new_text: str,
    new_origin: tuple[float, float],
    text_width: float,
    layout_engine: WordWrapLayoutEngine,
) -> tuple[bool, list[tuple[float, float, float, float]]]:
    """Detect if text span reflows to different number of lines.

    When text content changes, a highlight might wrap to more or fewer
    lines. This detects that case and returns the new layout rectangles.

    Args:
        old_rect_count: Number of rectangles in original highlight
        new_span_start: Start offset of highlight in new text
        new_span_end: End offset of highlight in new text
        new_text: Document text after modification
        new_origin: Text origin (x, y) in new document
        text_width: Available text width on page
        layout_engine: Layout engine for rectangle calculation

    Returns:
        (reflow_detected, new_layout_rectangles)
        - reflow_detected: True if line count changed
        - new_layout_rectangles: Layout-calculated rectangles if reflowed
    """
    new_rects = layout_engine.calculate_highlight_rectangles(
        new_span_start,
        new_span_end,
        new_text,
        new_origin,
        text_width,
    )

    if len(new_rects) != old_rect_count:
        return (True, new_rects)
    return (False, [])


def rebuild_for_reflow(
    original_first_rect: Rectangle,
    layout_rects: Sequence[tuple[float, float, float, float]],
    delta: PositionDelta,
    text_origin_x: float,
    line_start_tolerance: float = 10.0,
) -> list[Rectangle]:
    """Rebuild rectangles when highlight reflows to different line count.

    When text reflows (wraps to more or fewer lines), we need to rebuild
    the highlight rectangles. Strategy:

    1. First rectangle: Apply delta to preserve pixel-perfect position
       from device capture. This maintains the exact X position and height
       that the user created.

    2. Subsequent rectangles: Use geometry-based positioning. Lines that
       start at the text origin get X from layout. Lines that continue
       from previous get relative X positioning.

    Args:
        original_first_rect: First rectangle from original highlight
        layout_rects: New rectangles from layout engine (x, y, w, h tuples)
        delta: Position delta for the first rectangle
        text_origin_x: X position where text lines start
        line_start_tolerance: How close to origin counts as "line start"

    Returns:
        New list of rectangles preserving device-native appearance
    """
    if not layout_rects:
        return []

    # First rectangle uses delta to preserve pixel-perfect position
    first_layout_x, first_layout_y, first_w, first_h = layout_rects[0]
    first_rect = Rectangle(
        x=original_first_rect.x + delta.dx,
        y=original_first_rect.y + delta.dy,
        width=first_w,
        height=original_first_rect.height,  # Preserve original height
    )

    result = [first_rect]

    # Subsequent rectangles use geometry-based positioning
    for i, (x, y, w, h) in enumerate(layout_rects[1:], start=1):
        is_line_start = abs(x - text_origin_x) < line_start_tolerance

        if is_line_start:
            # Full line: start at text origin
            rect_x = text_origin_x
        else:
            # Continuation: maintain relative X from first rect
            rel_x = x - first_layout_x
            rect_x = first_rect.x + rel_x

        # Y position: stack below first rect using original height
        rect_y = first_rect.y + i * original_first_rect.height

        result.append(
            Rectangle(
                x=rect_x,
                y=rect_y,
                width=w,
                height=original_first_rect.height,
            )
        )

    return result


def merge_adjacent_rectangles(
    rectangles: Sequence[Rectangle],
    y_tolerance: float = 5.0,
) -> list[Rectangle]:
    """Merge rectangles that are on the same line.

    Sometimes highlights have multiple rectangles on the same line
    (e.g., around inline formatting). This merges them for cleaner
    visual appearance.

    Args:
        rectangles: List of rectangles to merge
        y_tolerance: How close Y values must be to count as same line

    Returns:
        Merged rectangles (one per line)
    """
    if not rectangles:
        return []

    # Sort by Y then X
    sorted_rects = sorted(rectangles, key=lambda r: (r.y, r.x))

    result: list[Rectangle] = []
    current = sorted_rects[0]

    for rect in sorted_rects[1:]:
        if abs(rect.y - current.y) <= y_tolerance:
            # Same line: extend current rectangle
            new_x = min(current.x, rect.x)
            new_right = max(current.x + current.width, rect.x + rect.width)
            current = Rectangle(
                x=new_x,
                y=current.y,
                width=new_right - new_x,
                height=current.height,
            )
        else:
            # Different line: save current, start new
            result.append(current)
            current = rect

    result.append(current)
    return result


def clamp_rectangles_to_page(
    rectangles: Sequence[Rectangle],
    page_width: float,
    page_height: float,
    margin: float = 0.0,
) -> list[Rectangle]:
    """Clamp rectangles to stay within page bounds.

    Ensures rectangles don't extend past page edges, which could
    cause rendering issues on the device.

    Args:
        rectangles: Rectangles to clamp
        page_width: Page width in pixels
        page_height: Page height in pixels
        margin: Minimum margin from page edge

    Returns:
        Clamped rectangles
    """
    result = []
    for rect in rectangles:
        x = max(margin, min(rect.x, page_width - margin - rect.width))
        y = max(margin, min(rect.y, page_height - margin - rect.height))
        width = min(rect.width, page_width - margin - x)
        height = min(rect.height, page_height - margin - y)

        if width > 0 and height > 0:
            result.append(Rectangle(x=x, y=y, width=width, height=height))

    return result
