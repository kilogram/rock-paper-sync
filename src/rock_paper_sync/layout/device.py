"""Device geometry configuration for reMarkable documents.

This module provides the DeviceGeometry dataclass which encapsulates all
device-specific layout parameters. It replaces the hardcoded constants
in constants.py with a flexible, device-aware abstraction.

The DeviceGeometry class is immutable (frozen dataclass) and provides:
- Physical page dimensions
- Text area positioning and sizing
- Typography parameters (line height, character width)
- Coordinate transformation constants
- Computed properties for derived values (lines_per_page, etc.)

All other modules should use DeviceGeometry rather than accessing
raw constants directly.

Example:
    from rock_paper_sync.layout.device import PAPER_PRO, DeviceGeometry

    # Use the pre-defined Paper Pro geometry
    geometry = PAPER_PRO
    print(f"Page size: {geometry.page_width}x{geometry.page_height}")
    print(f"Lines per page: {geometry.lines_per_page}")

    # Or create custom geometry for a different device
    custom = DeviceGeometry(
        page_width=1404,
        page_height=1872,
        ...
    )
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class DeviceGeometry:
    """Immutable device geometry parameters.

    All values are in pixels unless otherwise noted.

    This class encapsulates all device-specific layout parameters needed
    for document generation and annotation processing. Derived values
    (like lines_per_page) are computed from the base parameters.

    Attributes:
        page_width: Page width in pixels
        page_height: Page height in pixels
        text_width: Text area width in pixels
        text_pos_x: Text area X position (typically negative for centering)
        text_pos_y: Text area Y position (top margin)
        line_height: Line height in pixels
        char_width: Average character width (fallback when font metrics unavailable)
        baseline_offset: Offset from top of line to text baseline
        bottom_margin: Space below last line of text
        font_point_size: Font size in points for font metrics
        rm_text_block_line_height: Line height in RootTextBlock coordinate space
                                   (used for parsing existing .rm files)
    """

    # Physical page dimensions
    page_width: int
    page_height: int

    # Text area positioning
    text_width: float
    text_pos_x: float
    text_pos_y: float

    # Typography
    line_height: float
    char_width: float
    baseline_offset: float

    # Margins
    bottom_margin: float

    # Font metrics
    font_point_size: float

    # Text extraction (for .rm file parsing)
    rm_text_block_line_height: float

    # =========================================================================
    # Computed Properties
    # =========================================================================

    @property
    def text_area_height(self) -> float:
        """Available height for text content."""
        return self.page_height - self.text_pos_y - self.bottom_margin

    @property
    def lines_per_page(self) -> int:
        """Maximum lines per page (with 1 line safety margin)."""
        return int(self.text_area_height / self.line_height) - 1

    @property
    def chars_per_line(self) -> int:
        """Approximate characters per line (using average char width)."""
        return int(self.text_width / self.char_width)

    @property
    def negative_y_offset(self) -> float:
        """Y offset for negative-Y text-relative coordinates.

        This accounts for baseline positioning in the device's coordinate system.
        Calculated as: line_height + baseline_offset
        """
        return self.line_height + self.baseline_offset

    @property
    def root_layer_id(self) -> tuple[int, int]:
        """Root layer ID for absolute coordinates.

        This is fixed for the rmscene format and doesn't vary by device.
        """
        return (0, 11)

    @property
    def origin(self) -> tuple[float, float]:
        """Text origin as (x, y) tuple."""
        return (self.text_pos_x, self.text_pos_y)


# =============================================================================
# Pre-defined Device Profiles
# =============================================================================

#: reMarkable Paper Pro Move device geometry
#:
#: These values were calibrated from device analysis on 2025-11-30.
#: Key findings:
#: - LINE_HEIGHT of 57px matches actual device highlight positioning
#: - CHAR_WIDTH of 15px is average for Noto Sans at the default text size
#: - TEXT_WIDTH of 750px gives 1.0x display zoom on Paper Pro Move
#:
#: See docs/RMSCENE_FINDINGS.md for detailed calibration methodology.
PAPER_PRO_MOVE: Final[DeviceGeometry] = DeviceGeometry(
    # Physical dimensions
    page_width=1404,
    page_height=1872,
    # Text area positioning (centered: text_pos_x = -text_width/2)
    text_width=750.0,
    text_pos_x=-375.0,
    text_pos_y=94.0,  # Top margin, ~2 lines
    # Typography (calibrated from device highlight analysis)
    line_height=57.0,
    char_width=15.0,  # Average for Noto Sans at default size
    baseline_offset=25.0,  # From top of line to text baseline
    # Margins
    bottom_margin=100.0,
    # Font metrics (derived from: 159.5px shift / 4928 font units * 1000)
    font_point_size=32.4,
    # Text extraction coordinate space
    rm_text_block_line_height=8.0,
)


#: Default device geometry (currently Paper Pro Move)
#:
#: This can be used as the default when no device is specified.
#: Future versions may support device auto-detection or user configuration.
DEFAULT_DEVICE: Final[DeviceGeometry] = PAPER_PRO_MOVE
