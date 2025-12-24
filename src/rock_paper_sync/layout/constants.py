"""Layout constants for reMarkable document generation.

This module re-exports device geometry and coordinate system documentation.
All layout values should be accessed via DeviceGeometry instances.

For device-specific parameters:

    from rock_paper_sync.layout import DEFAULT_DEVICE, PAPER_PRO_MOVE

    # Access geometry properties directly
    geometry = DEFAULT_DEVICE
    print(geometry.page_width)      # 1404
    print(geometry.lines_per_page)  # 28

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

from .device import DEFAULT_DEVICE, PAPER_PRO_MOVE, DeviceGeometry

# Re-export for convenience
__all__ = [
    "DeviceGeometry",
    "PAPER_PRO_MOVE",
    "DEFAULT_DEVICE",
]
