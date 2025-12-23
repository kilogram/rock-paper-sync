"""Layout module for reMarkable document generation and annotation processing.

This module provides a single source of truth for device geometry and text
positioning logic used across the codebase. It consolidates:

- Device geometry (page dimensions, text area, typography)
- Word-wrap layout engine with font metrics support
- Layout context for annotation handlers
- Coordinate transformation constants

The key abstractions are:

- `DeviceGeometry`: Immutable device-specific layout parameters
- `LayoutContext`: Unified interface for annotation handlers

Usage:
    from rock_paper_sync.layout import (
        DeviceGeometry,
        PAPER_PRO,
        LayoutContext,
        WordWrapLayoutEngine,
    )

    # Use pre-defined device geometry
    geometry = PAPER_PRO
    print(f"Lines per page: {geometry.lines_per_page}")

    # Create layout engine from geometry
    engine = WordWrapLayoutEngine.from_geometry(geometry)

    # Create context for annotation processing
    context = LayoutContext.from_geometry(text_content, geometry)
    x, y = context.offset_to_position(char_offset)

For backward compatibility, LayoutConstants is still available but should
be considered deprecated for new code.
"""

# Backward compatibility - prefer DeviceGeometry for new code
from .constants import LayoutConstants
from .context import LayoutContext, TextAreaConfig
from .device import DEFAULT_DEVICE, PAPER_PRO_MOVE, DeviceGeometry
from .engine import WordWrapLayoutEngine
from .paginator import HEADER_ORPHAN_THRESHOLD_LINES, ContentPaginator

__all__ = [
    # Primary exports (use these for new code)
    "DeviceGeometry",
    "PAPER_PRO_MOVE",
    "DEFAULT_DEVICE",
    "LayoutContext",
    "TextAreaConfig",
    "WordWrapLayoutEngine",
    "ContentPaginator",
    "HEADER_ORPHAN_THRESHOLD_LINES",
    # Backward compatibility
    "LayoutConstants",
]
