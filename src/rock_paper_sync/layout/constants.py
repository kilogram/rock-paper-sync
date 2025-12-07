"""Consolidated layout constants for reMarkable document generation.

This module is the SINGLE SOURCE OF TRUTH for all layout-related constants
used across the codebase. All other modules should import from here rather
than defining their own values.

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


class LayoutConstants:
    """Immutable layout constants for reMarkable documents.

    All values are in pixels unless otherwise noted.
    """

    # ==========================================================================
    # Page Dimensions
    # ==========================================================================

    #: Page width in pixels (reMarkable Paper Pro)
    PAGE_WIDTH: Final[int] = 1404

    #: Page height in pixels (reMarkable Paper Pro)
    PAGE_HEIGHT: Final[int] = 1872

    # ==========================================================================
    # Text Area Dimensions
    # ==========================================================================

    #: Text area width in pixels (optimized for 1.0x zoom on Paper Pro)
    TEXT_WIDTH: Final[float] = 750.0

    #: Text area X position (centered: -TEXT_WIDTH/2)
    TEXT_POS_X: Final[float] = -375.0

    #: Text area Y position (top margin, ~2 lines)
    TEXT_POS_Y: Final[float] = 94.0

    # ==========================================================================
    # Typography
    # ==========================================================================

    #: Line height in pixels (calibrated 2025-11-30 from device highlight analysis)
    LINE_HEIGHT: Final[float] = 57.0

    #: Average character width in pixels (Noto Sans at default size)
    #: Used as fallback when font metrics are unavailable
    CHAR_WIDTH: Final[float] = 15.0

    #: Approximate characters per line (TEXT_WIDTH / CHAR_WIDTH)
    CHARS_PER_LINE: Final[int] = 50

    # ==========================================================================
    # Pagination
    # ==========================================================================

    #: Bottom margin in pixels (space below last line of text)
    BOTTOM_MARGIN: Final[float] = 100.0

    #: Available height for text content
    _TEXT_AREA_HEIGHT: Final[float] = PAGE_HEIGHT - TEXT_POS_Y - BOTTOM_MARGIN  # 1678.0

    #: Calculated lines per page (with 1 line safety margin)
    LINES_PER_PAGE: Final[int] = int(_TEXT_AREA_HEIGHT / LINE_HEIGHT) - 1  # 28

    # ==========================================================================
    # Coordinate Transformation
    # ==========================================================================

    #: Root layer ID for absolute coordinates
    ROOT_LAYER_ID: Final[tuple[int, int]] = (0, 11)

    #: Y offset for negative-Y text-relative coordinates
    #: This accounts for baseline positioning in the device's coordinate system.
    #: Calculated as: LINE_HEIGHT + BASELINE_OFFSET
    #: where BASELINE_OFFSET accounts for the text baseline within the line.
    #:
    #: NOTE: This was updated from 60 (based on LINE_HEIGHT=35) to match
    #: the calibrated LINE_HEIGHT=57. The baseline offset of 25px appears
    #: to be consistent.
    NEGATIVE_Y_OFFSET: Final[float] = 82.0  # LINE_HEIGHT (57) + BASELINE_OFFSET (25)

    #: Baseline offset within a line (from top of line to text baseline)
    BASELINE_OFFSET: Final[float] = 25.0

    # ==========================================================================
    # Text Extraction (for .rm file parsing)
    # ==========================================================================

    #: Line height in text-relative coordinate space
    #: This is different from actual rendered LINE_HEIGHT - the coordinate
    #: space inside RootTextBlock is condensed.
    #: Used by text_extraction.py for parsing existing .rm files.
    RM_TEXT_BLOCK_LINE_HEIGHT: Final[float] = 8.0


# Convenience aliases for common imports
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
