"""Device profiles and canvas transforms for reMarkable tablets.

Maps device screen geometry to PDF page geometry and provides per-page
coordinate transforms between device canvas space and PDF point space.

This is the only module allowed to contain screen-pixel and canvas-pixel
literals (enforced by I7 test guard).

Coordinate systems
------------------
- **Device canvas coords** (``.rm`` files): centre-top origin.
  x in [-canvas_w/2, canvas_w/2], y >= 0. Unit: device pixels.
  Canvas size comes from ``SceneInfo.paper_size`` in the ``.rm`` file;
  PDF pages on the Paper Pro Move use 1404x1872, device-added pages
  use 820x1458.
- **PDF coords**: top-left origin. x in [0, page_w], y in [0, page_h].
  Unit: PDF points (1 pt = 1/72 inch).

The transform is **anisotropic**: x and y scale factors differ.
``x_scale = canvas_w / pdf_w`` (fit-by-width); ``y_scale`` is measured
from device-session stroke anchors.  On the Paper Pro Move (firmware
3.28), ``y_scale / x_scale = 1.070``.  Expressed per screen pixel:
canvas x per screen px = 1404/954 = 1.4717; canvas y per screen px =
1.575 (measured 2026-09-19 from ``testdata/pdf_substrate/trips/1``).

Invariants:
    INV-I6: ``pdf_to_device(device_to_pdf(p)) == p`` within 0.5 px
            per axis.
    INV-I7: Screen-pixel and canvas-pixel literals exist only in this file.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CanvasTransform",
    "DeviceProfile",
    "PDF_CANVAS_H",
    "PDF_CANVAS_W",
    "PROFILES",
    "device_to_pdf",
    "pdf_to_device",
]


# ---------------------------------------------------------------------------
# Canvas constants — measured from SceneInfo.paper_size in the Phase 2
# device recording (Paper Pro Move, firmware 3.28).
# These are the ONLY canvas-pixel literals allowed outside this file (I7).
# ---------------------------------------------------------------------------

PDF_CANVAS_W: int = 1404
"""Canvas width for PDF-backed pages on the Paper Pro Move (device pixels)."""

PDF_CANVAS_H: int = 1872
"""Canvas height for PDF-backed pages on the Paper Pro Move (device pixels)."""

# Measured from Phase 2 stroke anchors (tick, strikethrough, highlight)
# on the Paper Pro Move: canvas y pixels per screen pixel.
# x factor is derived: PDF_CANVAS_W / screen_w_px = 1404/954 = 1.4717.
# y factor differs: 1.575 (ratio y/x = 1.070).
# Provenance: two-point fit (tick centre over checkbox-2 centre,
# strikethrough centre over DELETE-line centre) gives y_scale = 3.174
# with intercept -13 px; all four anchors (tick, strike, caret,
# highlight) agree within 1.5%.
_PDF_CANVAS_Y_PER_SCREEN_PX: float = 1.575


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Screen geometry for a reMarkable device.

    Used at render time to choose the PDF page size so one PDF point
    equals one physical point on the device (``screen_px / ppi * 72``).
    Does NOT drive coordinate transforms — use ``CanvasTransform``.

    The ``pdf_canvas_y_per_screen_px`` field carries the measured y-axis
    canvas factor.  For the Paper Pro Move this is 1.575 (measured
    2026-09-19 from ``testdata/pdf_substrate/trips/1``).  The x factor
    is derived: ``PDF_CANVAS_W / screen_w_px``.

    Attributes:
        name: Human-readable device name.
        screen_w_px: Screen width in device pixels.
        screen_h_px: Screen height in device pixels.
        ppi: Pixels per inch of the device screen.
        pdf_canvas_y_per_screen_px: Canvas y pixels per screen pixel
            (measured; default ``_PDF_CANVAS_Y_PER_SCREEN_PX``).
    """

    name: str
    screen_w_px: int
    screen_h_px: int
    ppi: int
    pdf_canvas_y_per_screen_px: float = _PDF_CANVAS_Y_PER_SCREEN_PX

    @property
    def page_width_pt(self) -> float:
        """PDF page width in points (1 PDF pt = 1 physical pt on device)."""
        return self.screen_w_px / self.ppi * 72.0

    @property
    def page_height_pt(self) -> float:
        """PDF page height in points."""
        return self.screen_h_px / self.ppi * 72.0

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
        ppi=264,
        pdf_canvas_y_per_screen_px=_PDF_CANVAS_Y_PER_SCREEN_PX,
    ),
}


# ---------------------------------------------------------------------------
# Per-page canvas transform
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanvasTransform:
    """Per-page coordinate transform between device canvas and PDF points.

    The device stores ink in a canvas whose size is recorded in
    ``SceneInfo.paper_size``.  The PDF page has a known size in points
    from the renderer.

    The transform is **anisotropic**: ``x_scale`` and ``y_scale`` differ.
    ``x_scale = canvas_w / pdf_w`` (fit-by-width); ``y_scale`` is
    derived from the measured ``pdf_canvas_y_per_screen_px`` factor.

    Attributes:
        canvas_w: Canvas width in device pixels.
        canvas_h: Canvas height in device pixels.
        pdf_w: PDF page width in points.
        pdf_h: PDF page height in points.
        x_scale: Canvas pixels per PDF point, x axis.
        y_scale: Canvas pixels per PDF point, y axis.
    """

    canvas_w: float
    canvas_h: float
    pdf_w: float
    pdf_h: float
    x_scale: float
    y_scale: float

    @property
    def scale(self) -> float:
        """X-axis scale factor (backward compat; prefer ``x_scale``)."""
        return self.x_scale

    def device_to_pdf(self, x: float, y: float) -> tuple[float, float]:
        """Convert device canvas coordinates to PDF points.

        Device canvas: centre-top origin, x in [-canvas_w/2, canvas_w/2].
        PDF: top-left origin.
        """
        pdf_x = (x + self.canvas_w / 2.0) / self.x_scale
        pdf_y = y / self.y_scale
        return (pdf_x, pdf_y)

    def pdf_to_device(self, pdf_x: float, pdf_y: float) -> tuple[float, float]:
        """Convert PDF points to device canvas coordinates.

        Inverse of :meth:`device_to_pdf`.
        """
        x = pdf_x * self.x_scale - self.canvas_w / 2.0
        y = pdf_y * self.y_scale
        return (x, y)

    @classmethod
    def for_pdf_page(cls, profile: DeviceProfile) -> CanvasTransform:
        """Default canvas transform for a PDF page on the given device.

        Uses the measured canvas constants ``PDF_CANVAS_W x PDF_CANVAS_H``
        and the profile's PDF page size.  The y scale is derived from
        ``profile.pdf_canvas_y_per_screen_px``.
        """
        x_scale = float(PDF_CANVAS_W) / profile.page_width_pt
        y_scale = (profile.screen_w_px / profile.page_width_pt) * profile.pdf_canvas_y_per_screen_px
        return cls(
            canvas_w=float(PDF_CANVAS_W),
            canvas_h=float(PDF_CANVAS_H),
            pdf_w=profile.page_width_pt,
            pdf_h=profile.page_height_pt,
            x_scale=x_scale,
            y_scale=y_scale,
        )

    @classmethod
    def from_scene_info(
        cls,
        paper_size: tuple[int, int] | tuple[float, float],
        pdf_w: float,
        pdf_h: float,
        *,
        y_scale: float | None = None,
    ) -> CanvasTransform:
        """Build from ``SceneInfo.paper_size`` and known PDF page dimensions.

        If ``y_scale`` is not provided, it is derived from the default
        profile's ``pdf_canvas_y_per_screen_px``.
        """
        x_s = float(paper_size[0]) / pdf_w
        if y_scale is None:
            default_profile = PROFILES.get("paper_pro_move")
            if default_profile is not None:
                y_s = (
                    default_profile.screen_w_px / pdf_w
                ) * default_profile.pdf_canvas_y_per_screen_px
            else:
                y_s = x_s  # fallback: isotropic
        else:
            y_s = y_scale
        return cls(
            canvas_w=float(paper_size[0]),
            canvas_h=float(paper_size[1]),
            pdf_w=pdf_w,
            pdf_h=pdf_h,
            x_scale=x_s,
            y_scale=y_s,
        )


# ---------------------------------------------------------------------------
# Free-function API (thin wrappers for backward compatibility)
# ---------------------------------------------------------------------------


def device_to_pdf(transform: CanvasTransform, x: float, y: float) -> tuple[float, float]:
    """Convert device coordinates to PDF points.

    Device canvas coords: centre-top origin,
    x in [-canvas_w/2, canvas_w/2], y >= 0.

    PDF coords: top-left origin, x in [0, pdf_w], y in [0, ...].

    Args:
        transform: Canvas transform for the page.
        x: Device x coordinate.
        y: Device y coordinate.

    Returns:
        (pdf_x, pdf_y) in PDF points.
    """
    return transform.device_to_pdf(x, y)


def pdf_to_device(transform: CanvasTransform, pdf_x: float, pdf_y: float) -> tuple[float, float]:
    """Convert PDF points to device coordinates.

    Inverse of ``device_to_pdf``.

    Args:
        transform: Canvas transform for the page.
        pdf_x: PDF x coordinate in points.
        pdf_y: PDF y coordinate in points.

    Returns:
        (device_x, device_y) in device pixels.
    """
    return transform.pdf_to_device(pdf_x, pdf_y)
