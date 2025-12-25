"""Coordinate transformation utilities for annotation relocation.

This module provides pure, reusable utilities for transforming annotation
coordinates when document content changes. The utilities are decoupled
from rmscene internals and operate on abstract types.

Architecture:
    Handlers (highlight_handler, stroke_handler) own their relocation logic
    and know their specific block types. They use these utilities for the
    coordinate math, keeping the math testable and reusable.

    transform/
    ├── types.py        - Pure data types (Position, Rectangle, etc.)
    ├── delta.py        - Delta calculation utilities
    ├── rectangles.py   - Rectangle manipulation utilities
    └── anchor.py       - Anchor resolution utilities

Usage:
    from rock_paper_sync.transform import (
        # Types
        Position,
        PositionDelta,
        Rectangle,
        TextSpan,
        AnchorResolution,
        RelocationResult,
        # Delta calculation
        calculate_relocation_delta,
        calculate_simple_y_delta,
        # Rectangle manipulation
        apply_delta_to_rectangles,
        detect_reflow,
        rebuild_for_reflow,
        # Anchor resolution
        resolve_anchor,
        resolve_by_relative_position,
    )

    # Example: Calculate delta for highlight relocation
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

    # Example: Apply delta to rectangles
    new_rects = apply_delta_to_rectangles(old_rects, delta)

    # Example: Resolve anchor in new text
    result = resolve_anchor(
        anchor_text="important concept",
        old_offset=150,
        old_text=old_doc,
        new_text=new_doc,
    )
"""

# Types
# Anchor resolution
from .anchor import (
    find_all_occurrences,
    resolve_anchor,
    resolve_by_relative_position,
)

# Delta calculation
from .delta import (
    calculate_relocation_delta,
    calculate_simple_y_delta,
    estimate_line_delta,
)

# Rectangle manipulation
from .rectangles import (
    apply_delta_to_rectangles,
    clamp_rectangles_to_page,
    detect_reflow,
    merge_adjacent_rectangles,
    rebuild_for_reflow,
)
from .types import (
    AnchorResolution,
    Position,
    PositionDelta,
    Rectangle,
    RelocationResult,
    TextSpan,
    rectangles_from_tuples,
    rectangles_to_tuples,
)

__all__ = [
    # Types
    "Position",
    "PositionDelta",
    "Rectangle",
    "TextSpan",
    "AnchorResolution",
    "RelocationResult",
    "rectangles_from_tuples",
    "rectangles_to_tuples",
    # Delta calculation
    "calculate_relocation_delta",
    "calculate_simple_y_delta",
    "estimate_line_delta",
    # Rectangle manipulation
    "apply_delta_to_rectangles",
    "detect_reflow",
    "rebuild_for_reflow",
    "merge_adjacent_rectangles",
    "clamp_rectangles_to_page",
    # Anchor resolution
    "resolve_anchor",
    "resolve_by_relative_position",
    "find_all_occurrences",
]
