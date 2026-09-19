"""Ink interpretation pipeline for the PDF substrate.

This package converts device ink (strokes, highlights, glyph ranges)
into edits, attachments, and calibration data by geometric analysis
against the renderer's ``WordMap``.

Import restrictions (enforced by import-linter):
    This package must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.render.pdf``, ``rock_paper_sync.render.html``,
    ``rock_paper_sync.converter``, ``rock_paper_sync.sync_v3``, or
    ``rock_paper_sync.rm_cloud_sync``.
"""

__all__: list[str] = []
