"""Overlay device ink onto a PDF for visual calibration inspection.

Parses ``.rm`` files (via ``rmscene``), transforms strokes and highlight
rectangles through ``DeviceProfile.device_to_pdf``, and draws them onto
the source PDF pages using pymupdf.  Produces an annotated ``overlay.pdf``
and one PNG per page.

This module lives in ``tools/`` and is deliberately outside the
``ink/`` import-linter contract, because it depends on ``pymupdf``.

Usage (programmatic)::

    from tools.ink_overlay import overlay_ink
    pngs = overlay_ink(pdf_bytes, {0: rm_bytes}, transform, out_dir=Path("out"))

Usage (CLI)::

    rock-paper-sync bench overlay document.pdf rm_dir/
"""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping
from pathlib import Path

import pymupdf
import rmscene
from rmscene import SceneGlyphItemBlock, SceneLineItemBlock

from rock_paper_sync.device_profile import CanvasTransform, device_to_pdf
from rock_paper_sync.render.wordmap import WordMap

__all__ = ["overlay_ink"]

logger = logging.getLogger(__name__)

# Drawing constants
_STROKE_COLOR = (0.2, 0.4, 1.0)  # blue
_STROKE_WIDTH = 0.8  # pt
_HIGHLIGHT_COLOR = (1.0, 0.95, 0.0)  # yellow
_HIGHLIGHT_OPACITY = 0.35
_WORDMAP_COLOR = (1.0, 0.0, 0.0)  # red
_WORDMAP_WIDTH = 0.3  # pt

# PNG resolution
_PNG_DPI = 110


def overlay_ink(
    pdf_bytes: bytes,
    page_files: Mapping[int, bytes],
    transform: CanvasTransform,
    word_map: WordMap | None = None,
    out_dir: Path | None = None,
) -> list[Path]:
    """Overlay device ink onto a PDF and write output files.

    For each PDF page index in ``page_files``, parses the ``.rm`` bytes,
    transforms every stroke point and ``GlyphRange`` rectangle through
    ``device_to_pdf``, and draws them on the PDF page.

    Device-added pages (page indices beyond the PDF's page count) get a
    blank page appended so their ink is visible.

    Args:
        pdf_bytes: Source PDF file bytes.
        page_files: Mapping of PDF page index -> ``.rm`` file bytes.
        transform: Canvas transform for coordinate conversion.
        word_map: Optional word map; if provided, its token rects are
            drawn in red outlines (like ``bench render --overlay``).
        out_dir: Output directory.  If ``None``, a temporary directory
            is used (caller is responsible for cleanup).

    Returns:
        List of PNG file paths, one per page (in page-index order).

    Raises:
        ValueError: If ``pdf_bytes`` is empty.
    """
    if not pdf_bytes:
        raise ValueError("pdf_bytes is empty")

    if out_dir is None:
        import tempfile

        out_dir = Path(tempfile.mkdtemp(prefix="ink_overlay_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")

    # Determine the highest page index referenced, append blank pages
    # for device-added pages that are beyond the PDF page count.
    max_page_idx = max(page_files.keys()) if page_files else -1
    while doc.page_count <= max_page_idx:
        # Append a blank page with the same dimensions as page 0
        if doc.page_count > 0:
            w = doc[0].rect.width
            h = doc[0].rect.height
        else:
            w = transform.pdf_w
            h = transform.pdf_h
        doc.new_page(width=w, height=h)

    # Draw word-map rects if provided
    if word_map is not None:
        for pw in word_map.pages:
            if pw.page >= doc.page_count:
                continue
            page = doc[pw.page]
            for _tid, rect in pw.tokens:
                r = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1)
                page.draw_rect(r, color=_WORDMAP_COLOR, width=_WORDMAP_WIDTH)

    # Draw ink from each .rm file
    for page_idx, rm_bytes in sorted(page_files.items()):
        if page_idx >= doc.page_count:
            continue
        page = doc[page_idx]

        strokes, highlights = _parse_rm(rm_bytes)

        # Draw strokes as blue polylines
        for points_dev in strokes:
            if len(points_dev) < 2:
                continue
            pdf_points = [device_to_pdf(transform, x, y) for x, y in points_dev]
            for i in range(len(pdf_points) - 1):
                p1 = pymupdf.Point(*pdf_points[i])
                p2 = pymupdf.Point(*pdf_points[i + 1])
                page.draw_line(p1, p2, color=_STROKE_COLOR, width=_STROKE_WIDTH)

        # Draw highlight rects as translucent yellow
        for rect_dev in highlights:
            x0, y0 = device_to_pdf(transform, rect_dev[0], rect_dev[1])
            x1, y1 = device_to_pdf(
                transform,
                rect_dev[0] + rect_dev[2],
                rect_dev[1] + rect_dev[3],
            )
            r = pymupdf.Rect(x0, y0, x1, y1)
            shape = page.new_shape()
            shape.draw_rect(r)
            shape.finish(
                color=_HIGHLIGHT_COLOR,
                fill=_HIGHLIGHT_COLOR,
                fill_opacity=_HIGHLIGHT_OPACITY,
            )
            shape.commit()

    # Save overlay PDF
    overlay_pdf_path = out_dir / "overlay.pdf"
    doc.save(str(overlay_pdf_path))

    # Render PNGs
    png_paths: list[Path] = []
    mat = pymupdf.Matrix(_PNG_DPI / 72.0, _PNG_DPI / 72.0)
    for page_idx in range(doc.page_count):
        pix = doc[page_idx].get_pixmap(matrix=mat)
        png_path = out_dir / f"page_{page_idx}.png"
        pix.save(str(png_path))
        png_paths.append(png_path)

    doc.close()
    logger.info("Overlay written: %s (%d PNGs)", overlay_pdf_path, len(png_paths))
    return png_paths


def _parse_rm(
    rm_bytes: bytes,
) -> tuple[list[list[tuple[float, float]]], list[tuple[float, float, float, float]]]:
    """Parse an .rm file and extract strokes and highlight rects.

    Returns:
        Tuple of (strokes, highlights) where:
        - strokes: list of polylines, each a list of (x, y) in device coords
        - highlights: list of (x, y, w, h) rectangles in device coords
    """
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))

    strokes: list[list[tuple[float, float]]] = []
    highlights: list[tuple[float, float, float, float]] = []

    for block in blocks:
        if isinstance(block, SceneLineItemBlock) and block.item.value is not None:
            line = block.item.value
            points = [(p.x, p.y) for p in line.points]
            if points:
                strokes.append(points)

        elif isinstance(block, SceneGlyphItemBlock) and block.item.value is not None:
            gr = block.item.value
            for rect in gr.rectangles:
                highlights.append((rect.x, rect.y, rect.w, rect.h))

    return strokes, highlights
