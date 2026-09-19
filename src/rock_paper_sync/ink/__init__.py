"""Ink interpretation pipeline for the PDF substrate.

This package converts device ink (strokes, highlights, glyph ranges)
into edits, attachments, and calibration data by geometric analysis
against the renderer's ``WordMap``.

Import restrictions (enforced by import-linter):
    This package must NOT import ``weasyprint``, ``fitz``, or
    ``pymupdf``.  PDF rendering is the renderer's responsibility;
    ink interpretation works on abstract word-map geometry.
"""

__all__: list[str] = []
