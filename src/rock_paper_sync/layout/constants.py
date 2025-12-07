"""Layout constants for reMarkable document generation.

This module provides backward-compatible access to layout constants.
All values are now derived from DeviceGeometry, which is the single
source of truth for device-specific parameters.

For new code, prefer importing from device.py:

    from rock_paper_sync.layout.device import PAPER_PRO, DeviceGeometry

    # Access geometry properties directly
    geometry = PAPER_PRO
    print(geometry.page_width)  # 1404
    print(geometry.lines_per_page)  # 28

The module-level constants (PAGE_WIDTH, LINE_HEIGHT, etc.) are maintained
for backward compatibility but should be considered deprecated for new code.

Coordinate System
-----------------
reMarkable uses a coordinate system where:
- Origin (0, 0) is at the center-top of the page
- Positive Y goes downward
- Text area is centered horizontally with TEXT_POS_X = -TEXT_WIDTH/2

For text-relative annotations:
- Annotations are positioned relative to the RootTextBlock origin
- The origin is at (TEXT_POS_X, TEXT_POS_Y) = (-375.0, 94.0)

Calibration Notes
-----------------
These values were calibrated from reMarkable Paper Pro device analysis
on 2025-11-30. Key findings:

- LINE_HEIGHT of 57px matches actual device highlight positioning
- CHAR_WIDTH of 15px is average for Noto Sans at the default text size
- TEXT_WIDTH of 750px gives 1.0x display zoom on Paper Pro

See docs/RMSCENE_FINDINGS.md for detailed calibration methodology.
"""

from typing import Final

from .device import DEFAULT_DEVICE, PAPER_PRO_MOVE, DeviceGeometry

# Re-export for convenience
__all__ = [
    "DeviceGeometry",
    "PAPER_PRO_MOVE",
    "DEFAULT_DEVICE",
    "LayoutConstants",
    # Backward-compatible aliases
    "PAGE_WIDTH",
    "PAGE_HEIGHT",
    "TEXT_WIDTH",
    "TEXT_POS_X",
    "TEXT_POS_Y",
    "LINE_HEIGHT",
    "CHAR_WIDTH",
    "CHARS_PER_LINE",
    "BOTTOM_MARGIN",
    "LINES_PER_PAGE",
    "ROOT_LAYER_ID",
    "NEGATIVE_Y_OFFSET",
    "BASELINE_OFFSET",
    "RM_TEXT_BLOCK_LINE_HEIGHT",
]


class LayoutConstants:
    """Layout constants derived from default device geometry.

    This class provides class-level access to layout constants for
    backward compatibility. For new code, use DeviceGeometry directly.

    All values are in pixels unless otherwise noted.
    """

    # Reference to the device geometry
    _DEVICE: Final[DeviceGeometry] = DEFAULT_DEVICE

    # ==========================================================================
    # Page Dimensions
    # ==========================================================================

    #: Page width in pixels (reMarkable Paper Pro)
    PAGE_WIDTH: Final[int] = _DEVICE.page_width

    #: Page height in pixels (reMarkable Paper Pro)
    PAGE_HEIGHT: Final[int] = _DEVICE.page_height

    # ==========================================================================
    # Text Area Dimensions
    # ==========================================================================

    #: Text area width in pixels (optimized for 1.0x zoom on Paper Pro)
    TEXT_WIDTH: Final[float] = _DEVICE.text_width

    #: Text area X position (centered: -TEXT_WIDTH/2)
    TEXT_POS_X: Final[float] = _DEVICE.text_pos_x

    #: Text area Y position (top margin, ~2 lines)
    TEXT_POS_Y: Final[float] = _DEVICE.text_pos_y

    # ==========================================================================
    # Typography
    # ==========================================================================

    #: Line height in pixels (calibrated 2025-11-30 from device highlight analysis)
    LINE_HEIGHT: Final[float] = _DEVICE.line_height

    #: Average character width in pixels (Noto Sans at default size)
    #: Used as fallback when font metrics are unavailable
    CHAR_WIDTH: Final[float] = _DEVICE.char_width

    #: Approximate characters per line (TEXT_WIDTH / CHAR_WIDTH)
    CHARS_PER_LINE: Final[int] = _DEVICE.chars_per_line

    # ==========================================================================
    # Pagination
    # ==========================================================================

    #: Bottom margin in pixels (space below last line of text)
    BOTTOM_MARGIN: Final[float] = _DEVICE.bottom_margin

    #: Calculated lines per page (with 1 line safety margin)
    LINES_PER_PAGE: Final[int] = _DEVICE.lines_per_page

    # ==========================================================================
    # Coordinate Transformation
    # ==========================================================================

    #: Root layer ID for absolute coordinates
    ROOT_LAYER_ID: Final[tuple[int, int]] = _DEVICE.root_layer_id

    #: Y offset for negative-Y text-relative coordinates
    #: This accounts for baseline positioning in the device's coordinate system.
    NEGATIVE_Y_OFFSET: Final[float] = _DEVICE.negative_y_offset

    #: Baseline offset within a line (from top of line to text baseline)
    BASELINE_OFFSET: Final[float] = _DEVICE.baseline_offset

    # ==========================================================================
    # Text Extraction (for .rm file parsing)
    # ==========================================================================

    #: Line height in text-relative coordinate space
    #: This is different from actual rendered LINE_HEIGHT - the coordinate
    #: space inside RootTextBlock is condensed.
    #: Used by text_extraction.py for parsing existing .rm files.
    RM_TEXT_BLOCK_LINE_HEIGHT: Final[float] = _DEVICE.rm_text_block_line_height


# =============================================================================
# Backward-compatible module-level aliases
# =============================================================================
# These are maintained for existing code that imports constants directly.
# New code should use DeviceGeometry properties instead.

PAGE_WIDTH = LayoutConstants.PAGE_WIDTH
PAGE_HEIGHT = LayoutConstants.PAGE_HEIGHT
TEXT_WIDTH = LayoutConstants.TEXT_WIDTH
TEXT_POS_X = LayoutConstants.TEXT_POS_X
TEXT_POS_Y = LayoutConstants.TEXT_POS_Y
LINE_HEIGHT = LayoutConstants.LINE_HEIGHT
CHAR_WIDTH = LayoutConstants.CHAR_WIDTH
CHARS_PER_LINE = LayoutConstants.CHARS_PER_LINE
BOTTOM_MARGIN = LayoutConstants.BOTTOM_MARGIN
LINES_PER_PAGE = LayoutConstants.LINES_PER_PAGE
ROOT_LAYER_ID = LayoutConstants.ROOT_LAYER_ID
NEGATIVE_Y_OFFSET = LayoutConstants.NEGATIVE_Y_OFFSET
BASELINE_OFFSET = LayoutConstants.BASELINE_OFFSET
RM_TEXT_BLOCK_LINE_HEIGHT = LayoutConstants.RM_TEXT_BLOCK_LINE_HEIGHT
