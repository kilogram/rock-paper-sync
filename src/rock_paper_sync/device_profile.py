"""Device profiles for reMarkable tablets.

Maps device screen geometry to PDF page geometry. This is the only module
allowed to contain screen-pixel literals (enforced by I7 test guard).

Coordinate systems
------------------
- **Device coords** (`.rm` files): centre-top origin. x in [-w/2, w/2],
  y in [0, h]. Unit: device pixels.
- **PDF coords**: top-left origin. x in [0, page_w], y in [0, page_h].
  Unit: PDF points (1 pt = 1/72 inch).

The conversion factor is 0.5 (device px -> PDF pt) for Paper Pro Move.

Invariants: INV-I6 (round-trip within 0.5 px, aspect equality).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DeviceProfile",
    "PROFILES",
    "device_to_pdf",
    "pdf_to_device",
]

# Conversion factor: device pixels -> PDF points
_PX_TO_PT = 0.5


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Screen geometry for a reMarkable device.

    Attributes:
        name: Human-readable device name.
        screen_w_px: Screen width in device pixels.
        screen_h_px: Screen height in device pixels.
    """

    name: str
    screen_w_px: int
    screen_h_px: int

    @property
    def page_width_pt(self) -> float:
        """PDF page width in points."""
        return self.screen_w_px * _PX_TO_PT

    @property
    def page_height_pt(self) -> float:
        """PDF page height in points."""
        return self.screen_h_px * _PX_TO_PT

    @property
    def page_size_pt(self) -> tuple[float, float]:
        """PDF page size as (width, height) in points."""
        return (self.page_width_pt, self.page_height_pt)


# Screen-pixel literals live here and only here (I7).
PROFILES: dict[str, DeviceProfile] = {
    "paper_pro_move": DeviceProfile(
        name="paper_pro_move",
        screen_w_px=954,
        screen_h_px=1696,
    ),
}


def device_to_pdf(profile: DeviceProfile, x: float, y: float) -> tuple[float, float]:
    """Convert device coordinates to PDF points.

    Device coords: centre-top origin, x in [-w/2, w/2], y in [0, h].
    PDF coords: top-left origin, x in [0, page_w], y in [0, page_h].

    Args:
        profile: Target device profile.
        x: Device x coordinate.
        y: Device y coordinate.

    Returns:
        (pdf_x, pdf_y) in PDF points.
    """
    pdf_x = (x + profile.screen_w_px / 2.0) * _PX_TO_PT
    pdf_y = y * _PX_TO_PT
    return (pdf_x, pdf_y)


def pdf_to_device(profile: DeviceProfile, pdf_x: float, pdf_y: float) -> tuple[float, float]:
    """Convert PDF points to device coordinates.

    Inverse of ``device_to_pdf``.

    Args:
        profile: Target device profile.
        pdf_x: PDF x coordinate in points.
        pdf_y: PDF y coordinate in points.

    Returns:
        (device_x, device_y) in device pixels.
    """
    x = pdf_x / _PX_TO_PT - profile.screen_w_px / 2.0
    y = pdf_y / _PX_TO_PT
    return (x, y)
