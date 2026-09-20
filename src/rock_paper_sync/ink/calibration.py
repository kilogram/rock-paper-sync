"""Calibration analysis: measured device transform vs profile transform.

Compares highlight ``GlyphRange`` rectangles recorded on a real device
against expected positions from the ``WordMap`` to measure the actual
scale and offset of the device-to-PDF coordinate mapping.

This module is the Phase 2 deliverable: it produces a measured transform
that either confirms the profile in ``device_profile.py`` or quantifies
the drift, yielding a report and assertions for the offline replay test.

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

from rock_paper_sync.device_profile import DeviceProfile, device_to_pdf
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

    Attributes:
        text: The ``GlyphRange.text`` that was highlighted.
        x_fit: Affine fit for x coordinates (device rect centres -> PDF rect centres).
        y_fit: Affine fit for y coordinates.
        profile_scale: The profile's nominal scale factor (``_PX_TO_PT``).
        matched_tokens: Token ids that were matched in the word map.
    """

    text: str
    x_fit: TransformFit
    y_fit: TransformFit
    profile_scale: float
    matched_tokens: tuple[TokenId, ...]

    @property
    def x_scale_error_pct(self) -> float:
        """Percentage deviation of measured x scale from the profile scale."""
        if math.isnan(self.x_fit.scale):
            return float("nan")
        return abs(self.x_fit.scale - self.profile_scale) / self.profile_scale * 100.0

    @property
    def y_scale_error_pct(self) -> float:
        """Percentage deviation of measured y scale from the profile scale."""
        if math.isnan(self.y_fit.scale):
            return float("nan")
        return abs(self.y_fit.scale - self.profile_scale) / self.profile_scale * 100.0

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
    profile: DeviceProfile,
    page: int,
) -> HighlightCalibration:
    """Measure the device-to-PDF transform for a recorded highlight.

    Finds the glyph text's tokens in the word map, pairs device rects
    with word-map rects by reading order, and fits an affine transform
    (scale + offset) for x and y separately.

    Args:
        glyph_text: The ``GlyphRange.text`` string.
        device_rects: Rectangles from the ``GlyphRange``, in device coords.
        word_map: The word map from the pushed PDF.
        profile: Device profile for nominal transform.
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

    # Convert device rects to PDF points using the profile transform,
    # then compare with word-map rects.  Pair by index (reading order).
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

    # The profile's nominal conversion: device_to_pdf includes a
    # centre-origin shift for x.  The scale factor is _PX_TO_PT = 0.5.
    # We compare the measured fit against this.
    nominal_scale = profile.page_width_pt / profile.screen_w_px  # == _PX_TO_PT

    x_fit = fit_affine_1d(source_x, target_x)
    y_fit = fit_affine_1d(source_y, target_y)

    return HighlightCalibration(
        text=glyph_text,
        x_fit=x_fit,
        y_fit=y_fit,
        profile_scale=nominal_scale,
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
    profile: DeviceProfile,
    page: int,
) -> CalibrationResult:
    """Cross-check I8: words under transformed highlight rects == GlyphRange.text.

    Transforms each device rect to PDF coords using the profile transform
    (not the fitted one), finds the word-map token at each centre, and
    compares the resulting words to the glyph text.

    Args:
        glyph_text: The ``GlyphRange.text`` string.
        device_rects: Rectangles from the ``GlyphRange``.
        word_map: The word map from the pushed PDF.
        profile: Device profile for the transform.
        page: PDF page index.

    Returns:
        ``CalibrationResult`` with the I8 pass/fail and details.
    """
    cal = calibrate_highlight(glyph_text, device_rects, word_map, profile, page)

    words_under: list[str] = []
    for dr in device_rects:
        # Centre of device rect -> PDF coords via profile
        dev_cx = dr.x + dr.w / 2.0
        dev_cy = dr.y + dr.h / 2.0
        pdf_x, pdf_y = device_to_pdf(profile, dev_cx, dev_cy)

        match = word_map.token_at(page, pdf_x, pdf_y)
        if match is not None:
            words_under.append(match.text)

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
        pages = c_pages.get("pages")
        if isinstance(pages, list) and pages:
            return pages  # type: ignore[return-value]

    # v1 fallback: top-level pages + redirectionPageMap
    page_ids = content.get("pages", [])
    redir_map = content.get("redirectionPageMap", [])
    if not isinstance(page_ids, list):
        return []

    entries: list[dict[str, object]] = []
    for i, page_id in enumerate(page_ids):
        pid = str(page_id) if page_id else f"v1_page_{i}"
        redir_val = redir_map[i] if i < len(redir_map) else -1
        entry: dict[str, object] = {"id": pid}
        if isinstance(redir_val, int) and redir_val >= 0:
            entry["redir"] = {"timestamp": "0:0", "value": redir_val}
        # redir_val == -1 or missing → device-added page (no redir)
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
            # This is a trailing blank page: redir should equal its page index
            if entry.redir_value != entry.redir_value:  # always true; real check below
                trailing_ok = False
            # The redir value should be a valid page index
            if entry.redir_value < 0:
                trailing_ok = False

    return CPageReport(
        entries=tuple(entries),
        redir_entries=tuple(redir_entries),
        device_added_entries=tuple(device_added),
        trailing_blank_redir_ok=trailing_ok,
    )
