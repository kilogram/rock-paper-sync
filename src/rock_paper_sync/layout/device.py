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
    from rock_paper_sync.layout.device import DEFAULT_DEVICE, DeviceGeometry

    # Use the pre-defined Paper Pro Move geometry
    geometry = DEFAULT_DEVICE
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
        text_width: Text area width in pixels (used for RootTextBlock)
        text_pos_x: Text area X position (typically negative for centering)
        text_pos_y: Text area Y position (top margin)
        layout_text_width: Text width for layout calculations (word-wrap).
                          The device allows lines slightly wider than text_width
                          before wrapping, so this should be slightly larger.
                          If None, uses text_width.
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
    font_point_size: float  # Typographic points (1/72 inch)

    # Text extraction (for .rm file parsing)
    rm_text_block_line_height: float

    # Document coordinate system DPI (REQUIRED)
    # This is the "virtual" DPI that document coordinates use, independent of
    # physical device. For reMarkable devices, this is typically 226 (reMarkable 2's PPI)
    # to ensure cross-device compatibility.
    # Every device MUST specify this explicitly.
    document_ppi: int  # Document virtual DPI (e.g., 226 for rM2 compat)

    # Layout engine text width (for word-wrap calculations)
    # The device's word-wrap allows slightly wider lines than text_width
    # Must come after required fields since it has a default value
    layout_text_width: float | None = None

    # Physical display specs (optional, for reference/calibration)
    # These are the actual hardware specs, different from document coordinates
    physical_width: int | None = None  # Physical pixels (landscape orientation)
    physical_height: int | None = None  # Physical pixels (landscape orientation)
    physical_ppi: int | None = None  # Pixels per inch

    @property
    def effective_layout_width(self) -> float:
        """Text width for layout/word-wrap calculations."""
        return self.layout_text_width if self.layout_text_width is not None else self.text_width

    @property
    def font_pixel_size(self) -> float:
        """Font size in document pixels.

        Converts typographic points to pixels using document DPI:
            pixels = points × document_ppi / 72

        Example:
            10pt @ 226 DPI = 10 × 226 / 72 ≈ 31.4 pixels
        """
        return self.font_point_size * self.document_ppi / 72.0

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
    def doc_to_physical_scale_x(self) -> float | None:
        """Scale factor from document to physical X coordinates.

        Returns None if physical specs are not available.
        Document coordinates are typically larger than physical (scale > 1).
        """
        if self.physical_height is None:  # physical_height because portrait
            return None
        return self.page_width / self.physical_height  # portrait vs landscape

    @property
    def doc_to_physical_scale_y(self) -> float | None:
        """Scale factor from document to physical Y coordinates.

        Returns None if physical specs are not available.
        """
        if self.physical_width is None:  # physical_width because portrait
            return None
        return self.page_height / self.physical_width  # portrait vs landscape

    @property
    def effective_document_ppi(self) -> float | None:
        """Effective PPI in document coordinate space.

        This is what fonttools should assume for accurate rendering.
        Higher than physical PPI due to coordinate scaling.
        """
        if self.physical_ppi is None or self.doc_to_physical_scale_x is None:
            return None
        # Average of X and Y scaling applied to physical PPI
        avg_scale = (self.doc_to_physical_scale_x + self.doc_to_physical_scale_y) / 2
        return self.physical_ppi * avg_scale

    @property
    def origin(self) -> tuple[float, float]:
        """Text origin as (x, y) tuple."""
        return (self.text_pos_x, self.text_pos_y)

    def mm_to_doc_pixels(self, mm: float) -> float:
        """Convert millimeters to document pixels.

        Args:
            mm: Distance in millimeters

        Returns:
            Distance in document pixels (using document_ppi)
        """
        return (mm / 25.4) * self.document_ppi

    def doc_pixels_to_mm(self, pixels: float) -> float:
        """Convert document pixels to millimeters.

        Args:
            pixels: Distance in document pixels

        Returns:
            Distance in millimeters (using document_ppi)
        """
        return (pixels / self.document_ppi) * 25.4


# =============================================================================
# Pre-defined Device Profiles
# =============================================================================

#: reMarkable Paper Pro Move device geometry
#:
#: IMPORTANT: Document vs Physical Coordinate Systems
#: -------------------------------------------------
#: The .rm file format uses a "document coordinate system" inherited from
#: the reMarkable 2, which differs from this device's physical resolution:
#:
#:   Document coords:  1404 x 1872 pixels @ 226 DPI (reMarkable 2 format)
#:   Physical display: 1696 x 954 pixels @ 264 PPI (7.3" 16:9 landscape)
#:
#: KEY FINDING (Empirically validated 2025-12-12):
#: Document coordinates ALWAYS use 226 DPI (reMarkable 2's resolution),
#: regardless of the physical device. This ensures cross-device compatibility.
#:
#: Rendering on Paper Pro Move:
#:   Scale factor: 264 / 226 = 1.168× (uniform in both X and Y)
#:   Viewport: ~1443 doc pixels visible vertically (1696 physical - 253 UI chrome)
#:   Content below y≈1443 is off-screen but exists in document
#:
#: For physical measurements:
#:   Use document_ppi (226) for all coordinate calculations
#:   Example: 10mm = 10 / 25.4 × 226 ≈ 89 doc pixels
#:
#: Calibration notes (2025-11-30):
#: - LINE_HEIGHT of 57px matches actual device highlight positioning
#: - CHAR_WIDTH of 15px is average for Noto Sans at the default text size
#: - TEXT_WIDTH of 750px gives 1.0x display zoom on Paper Pro Move
#: - LAYOUT_TEXT_WIDTH of 758px matches device word-wrap behavior
#:   (device allows lines slightly wider than text_width before wrapping)
#:
#: See docs/RMSCENE_FINDINGS.md for detailed calibration methodology.
PAPER_PRO_MOVE: Final[DeviceGeometry] = DeviceGeometry(
    # Document coordinate dimensions (used in .rm files)
    page_width=1404,
    page_height=1872,
    # Text area positioning (centered: text_pos_x = -text_width/2)
    text_width=750.0,
    text_pos_x=-375.0,
    text_pos_y=234.0,  # Top margin - matches device-native reference (4 lines)
    # Layout engine text width (calibrated 2025-12-08 from cross-page annotation test)
    # Device allows lines ~8px wider than text_width before wrapping
    layout_text_width=758.0,
    # Typography (calibrated from device highlight analysis)
    line_height=57.0,
    char_width=15.0,  # Average for Noto Sans at default size
    baseline_offset=25.0,  # From top of line to text baseline
    # Margins
    bottom_margin=100.0,
    # Font metrics (discovered 2025-12-12 via font size calibration)
    # This is the TYPOGRAPHIC point size (1/72 inch)
    # At 226 DPI: 10.0pt × 226 / 72 = 31.4 pixels
    font_point_size=10.0,
    # Text extraction coordinate space
    rm_text_block_line_height=8.0,
    # Physical display specs (from remarkable.com/products/remarkable-paper/pro-move)
    physical_width=1696,  # 16:9 landscape
    physical_height=954,
    physical_ppi=264,
    # Document coordinate system (empirically validated 2025-12-12)
    document_ppi=226,  # reMarkable 2's DPI - used for ALL .rm files
)


#: Default device geometry (currently Paper Pro Move)
#:
#: This can be used as the default when no device is specified.
#: Future versions may support device auto-detection or user configuration.
DEFAULT_DEVICE: Final[DeviceGeometry] = PAPER_PRO_MOVE
