"""Delta calculation utilities for annotation relocation.

This module provides pure functions for calculating position deltas
when annotations need to move due to content changes. The functions
operate on abstract types and don't know about rmscene internals.

Design principles:
- Pure functions (no side effects, no rmscene imports)
- Operate on abstract types (Position, TextSpan, etc.)
- Handlers adapt these to their specific block types
- Layout engine is passed in, not imported

Usage:
    from rock_paper_sync.transform import calculate_relocation_delta, TextSpan

    delta = calculate_relocation_delta(
        old_span=TextSpan(100, 120),
        new_offset=150,
        layout_engine=engine,
        text_width=1200.0,
        old_text=old_doc,
        new_text=new_doc,
        old_origin=(100.0, 100.0),
        new_origin=(100.0, 100.0),
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import Position, PositionDelta, TextSpan

if TYPE_CHECKING:
    from rock_paper_sync.layout import WordWrapLayoutEngine


def calculate_relocation_delta(
    old_span: TextSpan,
    new_offset: int,
    layout_engine: WordWrapLayoutEngine,
    text_width: float,
    old_text: str,
    new_text: str,
    old_origin: tuple[float, float],
    new_origin: tuple[float, float],
) -> PositionDelta:
    """Calculate position delta for relocating a text span.

    Uses the same layout engine with both old and new text so that
    any font metric approximations cancel out. This produces accurate
    deltas even when absolute positions have small errors.

    Args:
        old_span: Original text span (start, end offsets)
        new_offset: New character offset in new_text
        layout_engine: Layout engine for position calculations
        text_width: Available text width on page
        old_text: Document text before modification
        new_text: Document text after modification
        old_origin: Text origin (x, y) in old document
        new_origin: Text origin (x, y) in new document

    Returns:
        PositionDelta to apply to annotation coordinates
    """
    # Calculate old position using old text
    old_pos = layout_engine.offset_to_position(
        old_span.start,
        old_text,
        old_origin,
        text_width,
    )

    # Calculate new position using new text
    new_pos = layout_engine.offset_to_position(
        new_offset,
        new_text,
        new_origin,
        text_width,
    )

    return PositionDelta.between(
        Position(*old_pos),
        Position(*new_pos),
    )


def calculate_simple_y_delta(
    old_offset: int,
    new_offset: int,
    layout_engine: WordWrapLayoutEngine,
    text_width: float,
    old_text: str,
    new_text: str,
    origin: tuple[float, float],
) -> float:
    """Calculate Y-only delta for vertical repositioning.

    Simpler version when only Y movement matters (e.g., strokes
    that maintain their X position relative to anchor).

    Args:
        old_offset: Original character offset
        new_offset: New character offset
        layout_engine: Layout engine for position calculations
        text_width: Available text width on page
        old_text: Document text before modification
        new_text: Document text after modification
        origin: Text origin (x, y) - same for old and new

    Returns:
        Y delta in pixels (positive = down)
    """
    old_pos = layout_engine.offset_to_position(
        old_offset,
        old_text,
        origin,
        text_width,
    )

    new_pos = layout_engine.offset_to_position(
        new_offset,
        new_text,
        origin,
        text_width,
    )

    return new_pos[1] - old_pos[1]


def estimate_line_delta(
    old_offset: int,
    new_offset: int,
    old_text: str,
    new_text: str,
    line_height: float = 57.0,
) -> float:
    """Estimate Y delta based on line count change.

    Quick estimation without full layout calculation. Useful for
    validation or when layout engine isn't available.

    Args:
        old_offset: Original character offset
        new_offset: New character offset
        old_text: Document text before modification
        new_text: Document text after modification
        line_height: Approximate line height in pixels

    Returns:
        Estimated Y delta in pixels
    """
    old_lines = old_text[:old_offset].count("\n")
    new_lines = new_text[:new_offset].count("\n")
    return (new_lines - old_lines) * line_height
