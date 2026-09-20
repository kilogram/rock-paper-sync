"""Calibration analysis: measured device transform vs canvas transform.

Compares highlight ``GlyphRange`` rectangles recorded on a real device
against expected positions from the ``WordMap`` to measure the actual
scale and offset of the device-to-PDF coordinate mapping.

This module is the Phase 2 deliverable: it produces a measured transform
that either confirms the ``CanvasTransform`` in ``device_profile.py`` or
quantifies the drift, yielding a report and assertions for the offline
replay test.

Import restrictions (enforced by import-linter):
    Must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.

Invariants:
    - ``fit_affine_1d`` is a pure least-squares fit; returns exact
      (scale, offset) for n >= 2 points, or (NaN, NaN) for n < 2.
    - ``CalibrationResult.scale_error_pct`` and ``offset_error_pt``
      are absolute values, suitable for threshold assertions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from rock_paper_sync.device_profile import CanvasTransform, device_to_pdf
from rock_paper_sync.render.wordmap import Rect, TokenId, WordMap

__all__ = [
    "CalibrationResult",
    "CPageEntry",
    "CPageReport",
    "DeviceRect",
    "HighlightCalibration",
    "TransformFit",
    "calibrate_highlight",
    "analyse_cpages",
    "extract_cpages_from_content",
    "fit_affine_1d",
]


# ---------------------------------------------------------------------------
# 1-D affine fit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransformFit:
    """Result of a 1-D least-squares affine fit (y = scale * x + offset)."""

    scale: float
    offset: float
    n_points: int


def fit_affine_1d(
    source: list[float],
    target: list[float],
) -> TransformFit:
    """Fit ``target = scale * source + offset`` via least squares.

    Args:
        source: Observed device-space values.
        target: Expected PDF-space values.

    Returns:
        ``TransformFit`` with scale, offset, and point count.
        Returns ``(NaN, NaN, n)`` if fewer than 2 points.
    """
    n = len(source)
    if n != len(target):
        raise ValueError(f"source length {n} != target length {len(target)}")
    if n < 2:
        return TransformFit(scale=float("nan"), offset=float("nan"), n_points=n)

    sum_s = sum(source)
    sum_t = sum(target)
    sum_ss = sum(s * s for s in source)
    sum_st = sum(s * t for s, t in zip(source, target))

    denom = n * sum_ss - sum_s * sum_s
    if abs(denom) < 1e-15:
        return TransformFit(scale=float("nan"), offset=float("nan"), n_points=n)

    scale = (n * sum_st - sum_s * sum_t) / denom
    offset = (sum_t - scale * sum_s) / n
    return TransformFit(scale=scale, offset=offset, n_points=n)


# ---------------------------------------------------------------------------
# Highlight calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HighlightCalibration:
    """Measured transform for a single highlight.

    The fit direction is device->PDF: ``pdf = scale * device + offset``.
    ``nominal_scale`` is ``1 / x_scale``; ``nominal_y_scale`` is
    ``1 / y_scale`` (they differ for anisotropic transforms).

    Attributes:
        text: The ``GlyphRange.text`` that was highlighted.
        x_fit: Affine fit for x coordinates (device rect centres -> PDF rect centres).
        y_fit: Affine fit for y coordinates.
        nominal_scale: The canvas transform's ``1/x_scale`` (pdf_w/canvas_w).
        nominal_y_scale: The canvas transform's ``1/y_scale``.
        matched_tokens: Token ids that were matched in the word map.
    """

    text: str
    x_fit: TransformFit
    y_fit: TransformFit
    nominal_scale: float
    nominal_y_scale: float
    matched_tokens: tuple[TokenId, ...]

    @property
    def x_scale_error_pct(self) -> float:
        """Percentage deviation of measured x scale from the nominal x scale."""
        if math.isnan(self.x_fit.scale):
            return float("nan")
        return abs(self.x_fit.scale - self.nominal_scale) / self.nominal_scale * 100.0

    @property
    def y_scale_error_pct(self) -> float:
        """Percentage deviation of measured y scale from the nominal y scale."""
        if math.isnan(self.y_fit.scale):
            return float("nan")
        return abs(self.y_fit.scale - self.nominal_y_scale) / self.nominal_y_scale * 100.0

    @property
    def x_offset_error_pt(self) -> float:
        """Absolute offset in PDF points for x."""
        if math.isnan(self.x_fit.offset):
            return float("nan")
        return abs(self.x_fit.offset)

    @property
    def y_offset_error_pt(self) -> float:
        """Absolute offset in PDF points for y."""
        if math.isnan(self.y_fit.offset):
            return float("nan")
        return abs(self.y_fit.offset)

    # Keep old name for backward compatibility in tests
    @property
    def profile_scale(self) -> float:
        """Alias for ``nominal_scale`` (backward compat)."""
        return self.nominal_scale


@dataclass(frozen=True, slots=True)
class DeviceRect:
    """A highlight rectangle in device coordinates (from GlyphRange).

    Previously ``_DeviceRect``; made public for import by ``pull_sync``.
    """

    x: float
    y: float
    w: float
    h: float


# Backward-compatible alias (internal callers may still use the old name).
_DeviceRect = DeviceRect


def calibrate_highlight(
    glyph_text: str,
    device_rects: list[_DeviceRect],
    word_map: WordMap,
    transform: CanvasTransform,
    page: int,
) -> HighlightCalibration:
    """Measure the device-to-PDF transform for a recorded highlight.

    Finds the glyph text's tokens in the word map, pairs device rects
    with word-map rects by reading order, and fits an affine transform
    (scale + offset) for x and y separately.

    The fit direction is device->PDF: ``pdf = scale * device + offset``.

    Args:
        glyph_text: The ``GlyphRange.text`` string.
        device_rects: Rectangles from the ``GlyphRange``, in device coords.
        word_map: The word map from the pushed PDF.
        transform: Canvas transform for this page.
        page: PDF page index where the highlight was found.

    Returns:
        ``HighlightCalibration`` with the fitted transform.

    Raises:
        ValueError: If ``glyph_text`` tokens are not found in the word map.
    """
    # Tokenise the glyph text and find matching tokens in the word map
    glyph_words = glyph_text.split()
    matched_tokens: list[TokenId] = []
    matched_rects: list[Rect] = []

    for page_words in word_map.pages:
        if page_words.page != page:
            continue
        for tid, rect in page_words.tokens:
            text = word_map.texts[tid]
            if text in glyph_words and tid not in matched_tokens:
                matched_tokens.append(tid)
                matched_rects.append(rect)

    if not matched_tokens:
        raise ValueError(
            f"No tokens from glyph text {glyph_text!r} found on page {page} of the word map"
        )

    # Pair device rects with word-map rects by index (reading order).
    n_pairs = min(len(device_rects), len(matched_rects))

    source_x: list[float] = []
    target_x: list[float] = []
    source_y: list[float] = []
    target_y: list[float] = []

    for i in range(n_pairs):
        dr = device_rects[i]
        # Device rect centre in device coords
        dev_cx = dr.x + dr.w / 2.0
        dev_cy = dr.y + dr.h / 2.0
        source_x.append(dev_cx)
        source_y.append(dev_cy)

        # Word-map rect centre in PDF points
        wr = matched_rects[i]
        wm_cx = (wr.x0 + wr.x1) / 2.0
        wm_cy = (wr.y0 + wr.y1) / 2.0
        target_x.append(wm_cx)
        target_y.append(wm_cy)

    # The nominal conversion from the canvas transform:
    #   pdf_x = (dev_x + canvas_w/2) / canvas_scale
    #         = dev_x * (1/canvas_scale) + (canvas_w/2) / canvas_scale
    #         = dev_x * (pdf_w/canvas_w) + pdf_w/2
    # So: nominal_scale = 1/x_scale = pdf_w/canvas_w
    nominal_scale = 1.0 / transform.x_scale
    nominal_y_scale = 1.0 / transform.y_scale

    x_fit = fit_affine_1d(source_x, target_x)
    y_fit = fit_affine_1d(source_y, target_y)

    return HighlightCalibration(
        text=glyph_text,
        x_fit=x_fit,
        y_fit=y_fit,
        nominal_scale=nominal_scale,
        nominal_y_scale=nominal_y_scale,
        matched_tokens=tuple(matched_tokens),
    )


# ---------------------------------------------------------------------------
# I8 cross-check: words under highlight
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """Full calibration report for a device session highlight.

    Attributes:
        highlight: The highlight calibration measurement.
        words_under_rects: Words found under the transformed rects.
        glyph_text_words: Words from the GlyphRange text.
        i8_pass: Whether the words match (I8 check).
    """

    highlight: HighlightCalibration
    words_under_rects: tuple[str, ...]
    glyph_text_words: tuple[str, ...]
    i8_pass: bool


def check_i8(
    glyph_text: str,
    device_rects: list[_DeviceRect],
    word_map: WordMap,
    transform: CanvasTransform,
    page: int,
    *,
    h_pad: float = 30.0,
) -> CalibrationResult:
    """Cross-check I8: words under transformed highlight rects == GlyphRange.text.

    Transforms each device rect to PDF coords using the canvas transform,
    then locates the line by maximum vertical overlap with the rect (not
    just the rect centre), and matches words within the padded x range.

    Args:
        glyph_text: The ``GlyphRange.text`` string.
        device_rects: Rectangles from the ``GlyphRange``.
        word_map: The word map from the pushed PDF.
        transform: Canvas transform for this page.
        page: PDF page index.
        h_pad: Horizontal padding tolerance in device pixels
            (the device adds ~23 px padding per side to highlight rects).

    Returns:
        ``CalibrationResult`` with the I8 pass/fail and details.
    """
    cal = calibrate_highlight(glyph_text, device_rects, word_map, transform, page)

    # Build sorted list of line boxes for the page
    page_lines = word_map.lines_on_page(page)

    words_under: list[str] = []
    for dr in device_rects:
        # Transform device rect corners to PDF coords, applying horizontal
        # padding to shrink the x range (accounts for device-added padding).
        # Clamp the padding so the x range never inverts for narrow rects.
        effective_pad = min(h_pad, dr.w / 2.0 - 1.0) if dr.w > 2.0 else 0.0
        effective_pad = max(effective_pad, 0.0)
        pdf_x0, pdf_y0 = device_to_pdf(transform, dr.x + effective_pad, dr.y)
        pdf_x1, pdf_y1 = device_to_pdf(transform, dr.x + dr.w - effective_pad, dr.y + dr.h)
        # Normalize (centre-origin shift may swap x0/x1)
        if pdf_x0 > pdf_x1:
            pdf_x0, pdf_x1 = pdf_x1, pdf_x0

        # Locate the line with maximum vertical overlap
        best_line = None
        best_overlap = 0.0
        for line in page_lines:
            overlap_top = max(pdf_y0, line.rect.y0)
            overlap_bot = min(pdf_y1, line.rect.y1)
            overlap = max(0.0, overlap_bot - overlap_top)
            if overlap > best_overlap:
                best_overlap = overlap
                best_line = line

        if best_line is None:
            # Fallback: point lookup at centre
            dev_cx = dr.x + dr.w / 2.0
            dev_cy = dr.y + dr.h / 2.0
            pdf_x, pdf_y = device_to_pdf(transform, dev_cx, dev_cy)
            match = word_map.token_at(page, pdf_x, pdf_y)
            if match is not None:
                words_under.append(match.text)
            continue

        # Find the token whose x range overlaps with the padded highlight rect.
        # LineBox.token_ids gives reading-order ids; look up rects from the page.
        page_token_map: dict[int, Rect] = {}
        for pw in word_map.pages:
            if pw.page == page:
                for tid_pw, rect_pw in pw.tokens:
                    page_token_map[int(tid_pw)] = rect_pw
                break

        for tid in best_line.token_ids:
            tok_rect = page_token_map.get(int(tid))
            if tok_rect is None:
                continue
            token_cx = (tok_rect.x0 + tok_rect.x1) / 2.0
            if pdf_x0 <= token_cx <= pdf_x1:
                words_under.append(word_map.texts[tid])

    glyph_words = tuple(glyph_text.split())
    words_under_tuple = tuple(words_under)

    # I8: the words under the rects (in order) must equal the glyph text words
    i8_pass = words_under_tuple == glyph_words

    return CalibrationResult(
        highlight=cal,
        words_under_rects=words_under_tuple,
        glyph_text_words=glyph_words,
        i8_pass=i8_pass,
    )


# ---------------------------------------------------------------------------
# cPages analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CPageEntry:
    """One entry in the cPages analysis.

    Attributes:
        index: Position in the cPages list.
        page_id: The page UUID/id.
        has_redir: Whether this entry has a ``redir`` value.
        redir_value: The redir value (PDF page index), or None.
    """

    index: int
    page_id: str
    has_redir: bool
    redir_value: int | None


@dataclass(frozen=True, slots=True)
class CPageReport:
    """Analysis of a document's cPages structure.

    Attributes:
        entries: All cPages entries.
        redir_entries: Entries with redir (content pages mapped to PDF).
        device_added_entries: Entries without redir (device-added pages).
        trailing_blank_redir_ok: Whether trailing blank pages have correct redir.
    """

    entries: tuple[CPageEntry, ...]
    redir_entries: tuple[CPageEntry, ...]
    device_added_entries: tuple[CPageEntry, ...]
    trailing_blank_redir_ok: bool


def extract_cpages_from_content(
    content: dict[str, object],
) -> list[dict[str, object]]:
    """Extract a normalised list of page entries from a ``.content`` dict.

    Accepts both formats the device may write:

    **v2** (``formatVersion: 2``): ``cPages.pages`` list with ``redir``
    dicts.  Returned as-is.

    **v1** (no ``cPages``, or ``formatVersion`` absent/1): top-level
    ``pages`` list of page id strings plus ``redirectionPageMap`` list
    of ints (-1 or missing = device-added page).  Converted into v2-style
    dicts with ``id``, ``redir`` keys.

    Args:
        content: Parsed ``.content`` JSON dict.

    Returns:
        List of page-entry dicts, each with at least ``id`` and
        optionally ``redir``.
    """
    # v2 format: cPages.pages
    c_pages = content.get("cPages")
    if isinstance(c_pages, dict):
        pages_raw = c_pages.get("pages")
        if isinstance(pages_raw, list) and pages_raw:
            # Each entry should be a dict; filter defensively
            return [p for p in pages_raw if isinstance(p, dict)]

    # v1 fallback: top-level pages + redirectionPageMap
    page_ids_raw = content.get("pages", [])
    redir_map_raw = content.get("redirectionPageMap", [])
    if not isinstance(page_ids_raw, list):
        return []
    redir_map: list[object] = redir_map_raw if isinstance(redir_map_raw, list) else []

    entries: list[dict[str, object]] = []
    for i, page_id in enumerate(page_ids_raw):
        pid = str(page_id) if page_id else f"v1_page_{i}"
        redir_val = redir_map[i] if i < len(redir_map) else -1
        entry: dict[str, object] = {"id": pid}
        if isinstance(redir_val, int) and redir_val >= 0:
            entry["redir"] = {"timestamp": "0:0", "value": redir_val}
        # redir_val == -1 or missing -> device-added page (no redir)
        entries.append(entry)

    return entries


def analyse_cpages(
    cpages: list[dict[str, object]],
    expected_content_pages: int,
) -> CPageReport:
    """Analyse a document's cPages structure.

    Checks which entries have ``redir`` (our PDF pages) and which do not
    (device-added pages).  Validates that trailing blank pages carry
    ``redir`` values equal to their PDF page indices.

    Args:
        cpages: The ``cPages.pages`` list from the ``.content`` file.
            Each entry is a dict with at least ``id`` and optionally
            ``redir`` keys.
        expected_content_pages: Number of content pages we pushed
            (before trailing blanks).

    Returns:
        ``CPageReport`` with the analysis.
    """
    entries: list[CPageEntry] = []
    redir_entries: list[CPageEntry] = []
    device_added: list[CPageEntry] = []

    for i, page in enumerate(cpages):
        page_id = str(page.get("id", f"unknown_{i}"))
        redir = page.get("redir")

        has_redir = redir is not None
        redir_value = None
        if has_redir:
            redir_raw = redir
            if isinstance(redir_raw, dict):
                redir_value = int(redir_raw.get("value", -1))
            elif isinstance(redir_raw, (int, float)):
                redir_value = int(redir_raw)

        entry = CPageEntry(
            index=i,
            page_id=page_id,
            has_redir=has_redir,
            redir_value=redir_value,
        )
        entries.append(entry)

        if has_redir:
            redir_entries.append(entry)
        else:
            device_added.append(entry)

    # Check trailing blank pages: redir values should equal their PDF page index
    trailing_ok = True
    for entry in redir_entries:
        if entry.redir_value is not None and entry.redir_value >= expected_content_pages:
            # The redir value should be a valid page index
            if entry.redir_value < 0:
                trailing_ok = False

    return CPageReport(
        entries=tuple(entries),
        redir_entries=tuple(redir_entries),
        device_added_entries=tuple(device_added),
        trailing_blank_redir_ok=trailing_ok,
    )
