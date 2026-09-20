"""Gesture interpretation for ink on content pages.

Pure functions that classify ink strokes as checkbox ticks,
strikethroughs, caret insertions, or corrections by geometric analysis
against the ``WordMap``.  No I/O, no OCR calls, no cloud access.

Gesture priority (per cluster, §8 Phase 5):
    glyph highlight (separate) -> tick -> correction -> strikethrough
    -> caret insertion -> drawing (bake).

Design:
    Strokes within a cluster are sub-classified into three roles:
    - **horizontal-line**: narrow bbox, wide, suitable for strikethrough.
    - **compact-caret**: small bbox near a baseline, suitable for caret.
    - **text**: everything else (handwriting above a line).

    A cluster that yields a gesture plus leftover text strokes produces
    the gesture edit and the leftovers get a derived cluster id for
    baking as a separate drawing.

Import restrictions (enforced by import-linter):
    Must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.

Invariants relied upon:
    INV-I5: One stroke belongs to at most one cluster/disposition.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Literal

from rock_paper_sync.device_profile import CanvasTransform, device_to_pdf
from rock_paper_sync.ink.candidates import CandidateStroke
from rock_paper_sync.ink.clusters import InkCluster
from rock_paper_sync.render.wordmap import LineBox, Rect, TokenId, WordMap

__all__ = [
    "GestureEdit",
    "GestureResult",
    "classify_gesture",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Geometric thresholds (named constants with rationale)
# ---------------------------------------------------------------------------

TICK_OVERLAP_THRESHOLD = 0.60
"""Minimum fraction of a stroke cluster's area that must lie inside a
checkbox token's expanded rect to classify as a tick.  0.60 (60%) allows
a sloppy check mark that overshoots the box edges slightly."""

TICK_RECT_EXPANSION_PT = 2.0
"""Points added to each side of a checkbox token's rect before
computing overlap.  Accounts for pen-tracking slop on the device."""

STRIKETHROUGH_MAX_HEIGHT_RATIO = 0.50
"""Maximum ratio of a stroke's bbox height to line height for it to
qualify as a horizontal strikethrough stroke.  A real strikethrough is
a thin horizontal line, not a tall mark."""

STRIKETHROUGH_MIN_WIDTH_TOKENS = 1.5
"""Minimum width of a strikethrough stroke expressed as a multiple of
the median token width on that line.  Filters out small dots and ticks."""

STRIKETHROUGH_VERTICAL_BAND = (0.30, 0.75)
"""The stroke's vertical centre must lie within this fraction range of
the token boxes' height band (0.0 = top, 1.0 = bottom).  The middle
30-75% band ensures the stroke runs through the text body, not above
or below it."""

STRIKETHROUGH_TOKEN_OVERLAP = 0.50
"""Minimum horizontal overlap fraction between a strikethrough stroke
and a token box to consider that token 'covered'.  Also the minimum
for at least one token to trigger the gesture at all."""

CARET_MAX_SIZE_RATIO = 1.0
"""Maximum bbox dimension (width or height) of a caret stroke relative
to line height.  A caret mark is a small symbol, not a large drawing."""

CARET_BASELINE_TOLERANCE_RATIO = 0.5
"""The caret's bottom edge must be within this fraction of line height
below the baseline to qualify.  Allows carets sitting on or slightly
below the text baseline."""

CARET_TEXT_X_TOLERANCE_TOKENS = 3.0
"""Maximum horizontal offset (in median-token-widths) between a caret
and an associated text cluster above the line.  Limits how far away
the insertion text can be from the caret."""


# ---------------------------------------------------------------------------
# Sub-classification of strokes within a cluster
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StrokeGeom:
    """Precomputed geometry for a single stroke in PDF coordinates."""

    candidate: CandidateStroke
    bbox: Rect  # in PDF points
    width: float
    height: float
    cx: float
    cy: float


def _stroke_geom(cs: CandidateStroke, transform: CanvasTransform) -> _StrokeGeom:
    """Compute a stroke's bounding box and dimensions in PDF points."""
    bx, by, bw, bh = cs.stroke.bounding_box
    x0_dev, y0_dev = bx, by
    x1_dev, y1_dev = bx + bw, by + bh
    x0_pdf, y0_pdf = device_to_pdf(transform, x0_dev, y0_dev)
    x1_pdf, y1_pdf = device_to_pdf(transform, x1_dev, y1_dev)
    # device_to_pdf shifts x by half canvas width; ensure x0 < x1
    px0 = min(x0_pdf, x1_pdf)
    px1 = max(x0_pdf, x1_pdf)
    py0 = min(y0_pdf, y1_pdf)
    py1 = max(y0_pdf, y1_pdf)
    rect = Rect(x0=px0, y0=py0, x1=px1, y1=py1)
    return _StrokeGeom(
        candidate=cs,
        bbox=rect,
        width=px1 - px0,
        height=py1 - py0,
        cx=(px0 + px1) / 2.0,
        cy=(py0 + py1) / 2.0,
    )


def _cluster_bbox_pdf(geoms: list[_StrokeGeom]) -> Rect:
    """Union bounding box in PDF points for a list of stroke geometries."""
    return Rect(
        x0=min(g.bbox.x0 for g in geoms),
        y0=min(g.bbox.y0 for g in geoms),
        x1=max(g.bbox.x1 for g in geoms),
        y1=max(g.bbox.y1 for g in geoms),
    )


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GestureEdit:
    """One contiguous edit produced by a gesture.

    A strikethrough over non-adjacent tokens produces one ``GestureEdit``
    per contiguous run so that untouched tokens between runs survive.
    """

    snapshot_span: tuple[int, int]
    replacement_text: str


@dataclass(frozen=True, slots=True)
class GestureResult:
    """Outcome of gesture classification for a cluster.

    Attributes:
        kind: The gesture type, or ``"none"`` if no gesture was found.
        token_ids: Tokens affected by the gesture (empty for ``"none"``).
        snapshot_span: ``(start, end)`` in the snapshot for the primary edit,
            or ``None``.
        replacement_text: The text to insert/replace for the primary edit.
        extra_edits: Additional contiguous-run edits (for non-contiguous
            strikethrough/correction).  Empty for single-run edits.
        leftover_strokes: Strokes not consumed by the gesture (bake these).
        checkbox_already_checked: True when a tick lands on ``[x]``.
        needs_ocr: True when the gesture requires OCR for its text
            (caret insertion, correction).
        ocr_strokes: The strokes to render for OCR (text cluster above line).
        confidence: Geometric confidence (1.0 for geometry-only gestures;
            set to OCR confidence later for OCR-based ones).
    """

    kind: Literal["tick", "strikethrough", "caret_insertion", "correction", "none"]
    token_ids: tuple[TokenId, ...]
    snapshot_span: tuple[int, int] | None
    replacement_text: str | None
    leftover_strokes: tuple[CandidateStroke, ...]
    extra_edits: tuple[GestureEdit, ...] = ()
    checkbox_already_checked: bool = False
    needs_ocr: bool = False
    ocr_strokes: tuple[CandidateStroke, ...] = ()
    confidence: float = 1.0


_NO_GESTURE = GestureResult(
    kind="none",
    token_ids=(),
    snapshot_span=None,
    replacement_text=None,
    leftover_strokes=(),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rect_area(r: Rect) -> float:
    return max(0.0, r.x1 - r.x0) * max(0.0, r.y1 - r.y0)


def _rect_intersection(a: Rect, b: Rect) -> Rect:
    return Rect(
        x0=max(a.x0, b.x0),
        y0=max(a.y0, b.y0),
        x1=min(a.x1, b.x1),
        y1=min(a.y1, b.y1),
    )


def _rect_overlap_fraction(inner: Rect, outer: Rect) -> float:
    """Fraction of *inner*'s area that overlaps *outer*."""
    inner_area = _rect_area(inner)
    if inner_area <= 0:
        return 0.0
    ix = _rect_intersection(inner, outer)
    return _rect_area(ix) / inner_area


def _horizontal_overlap_fraction(stroke: Rect, token: Rect) -> float:
    """Fraction of *token*'s horizontal extent overlapped by *stroke*."""
    tw = token.x1 - token.x0
    if tw <= 0:
        return 0.0
    overlap = max(0.0, min(stroke.x1, token.x1) - max(stroke.x0, token.x0))
    return overlap / tw


def _expand_rect(r: Rect, amount: float) -> Rect:
    return Rect(
        x0=r.x0 - amount,
        y0=r.y0 - amount,
        x1=r.x1 + amount,
        y1=r.y1 + amount,
    )


def _token_rect(word_map: WordMap, page: int, tid: TokenId) -> Rect | None:
    """Get a token's rect on the given page."""
    for pw in word_map.pages:
        if pw.page == page:
            for t, r in pw.tokens:
                if t == tid:
                    return r
    return None


def _median_token_width(word_map: WordMap, line: LineBox) -> float:
    """Median width of tokens in a line."""
    widths: list[float] = []
    for tid in line.token_ids:
        for pw in word_map.pages:
            for t, r in pw.tokens:
                if t == tid:
                    widths.append(r.x1 - r.x0)
    if not widths:
        return 1.0
    widths.sort()
    return widths[len(widths) // 2]


def _line_height(line: LineBox) -> float:
    return line.rect.y1 - line.rect.y0


def _contiguous_runs(tids: list[TokenId]) -> list[list[TokenId]]:
    """Split a sorted list of TokenIds into contiguous runs.

    Two tokens are contiguous if their int values differ by 1.
    """
    if not tids:
        return []
    runs: list[list[TokenId]] = [[tids[0]]]
    for tid in tids[1:]:
        if int(tid) == int(runs[-1][-1]) + 1:
            runs[-1].append(tid)
        else:
            runs.append([tid])
    return runs


def _build_run_edits(
    runs: list[list[TokenId]],
    token_spans: list[tuple[int, int]],
    snapshot_text: str,
    strikethrough_action: str,
) -> list[GestureEdit]:
    """Build one GestureEdit per contiguous run using the literal snapshot slice."""
    edits: list[GestureEdit] = []
    for run in runs:
        spans = [
            token_spans[int(tid)]
            for tid in run
            if int(tid) < len(token_spans) and token_spans[int(tid)][0] >= 0
        ]
        if not spans:
            continue
        start = min(s for s, _ in spans)
        end = max(e for _, e in spans)
        original = snapshot_text[start:end]
        if strikethrough_action == "delete":
            replacement = ""
        else:
            replacement = f"~~{original}~~"
        edits.append(GestureEdit(snapshot_span=(start, end), replacement_text=replacement))
    return edits


def _derive_cluster_id(stroke_ids: list[str]) -> str:
    """Deterministic cluster id from sorted stroke ids (matches clusters.py)."""
    joined = "\n".join(sorted(stroke_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _stroke_id_str(cs: CandidateStroke) -> str:
    cid = cs.stroke.stroke_id
    return f"{cid.part1}:{cid.part2}"


# ---------------------------------------------------------------------------
# 1. Checkbox tick
# ---------------------------------------------------------------------------


def _try_tick(
    cluster: InkCluster,
    geoms: list[_StrokeGeom],
    word_map: WordMap,
    page: int,
    token_spans: list[tuple[int, int]],
) -> GestureResult:
    """Detect a checkbox tick gesture.

    A cluster whose bbox lies mostly (>= TICK_OVERLAP_THRESHOLD of its
    area) inside a checkbox token's rect (expanded by TICK_RECT_EXPANSION_PT)
    flips ``- [ ]`` to ``- [x]``.

    A stroke on an already-checked box -> Deferred("already_checked").
    """
    cluster_rect = _cluster_bbox_pdf(geoms)

    # Find checkbox tokens on this page
    for pw in word_map.pages:
        if pw.page != page:
            continue
        for tid, tok_rect in pw.tokens:
            text = word_map.texts[tid]
            # Checkbox tokens carry "[ ]" (unchecked) or "[x]" (checked)
            # as their text in WordMap.texts (from pdf.py's box-tree walk).
            is_checkbox = text in ("[ ]", "[x]")
            if not is_checkbox:
                continue

            expanded = _expand_rect(tok_rect, TICK_RECT_EXPANSION_PT)
            overlap = _rect_overlap_fraction(cluster_rect, expanded)

            # Centroid must be inside the *unexpanded* checkbox box to
            # reject strokes on adjacent item text that merely graze
            # the checkbox edge.  The expansion is for overlap tolerance
            # only; the centroid check ensures the stroke is aimed at
            # the checkbox, not at the text next to it.
            cluster_cx = (cluster_rect.x0 + cluster_rect.x1) / 2.0
            cluster_cy = (cluster_rect.y0 + cluster_rect.y1) / 2.0
            centroid_inside = (
                tok_rect.x0 <= cluster_cx < tok_rect.x1 and tok_rect.y0 <= cluster_cy < tok_rect.y1
            )

            if overlap >= TICK_OVERLAP_THRESHOLD and centroid_inside:
                if text == "[x]":
                    # Already checked
                    return GestureResult(
                        kind="tick",
                        token_ids=(tid,),
                        snapshot_span=None,
                        replacement_text=None,
                        leftover_strokes=(),
                        checkbox_already_checked=True,
                    )

                # Find the snapshot span for this checkbox token
                span = token_spans[tid] if tid < len(token_spans) else (-1, -1)
                if span[0] < 0:
                    continue

                return GestureResult(
                    kind="tick",
                    token_ids=(tid,),
                    snapshot_span=span,
                    replacement_text="[x]",
                    leftover_strokes=(),
                )

    return _NO_GESTURE


# ---------------------------------------------------------------------------
# 2. Strikethrough
# ---------------------------------------------------------------------------


def _is_horizontal_stroke(
    sg: _StrokeGeom,
    line: LineBox,
    word_map: WordMap,
) -> bool:
    """Check if a stroke qualifies as a horizontal strikethrough on *line*."""
    lh = _line_height(line)
    if lh <= 0:
        return False
    # Height constraint: narrow
    if sg.height > STRIKETHROUGH_MAX_HEIGHT_RATIO * lh:
        return False
    # Width constraint: at least 1.5x median token width
    med_w = _median_token_width(word_map, line)
    if sg.width < STRIKETHROUGH_MIN_WIDTH_TOKENS * med_w:
        return False
    # Vertical centre within middle 30-75% of the line's token boxes
    band_top = line.rect.y0 + STRIKETHROUGH_VERTICAL_BAND[0] * lh
    band_bot = line.rect.y0 + STRIKETHROUGH_VERTICAL_BAND[1] * lh
    if not (band_top <= sg.cy <= band_bot):
        return False
    return True


def _find_line_for_stroke(
    sg: _StrokeGeom,
    lines: tuple[LineBox, ...],
) -> LineBox | None:
    """Find the line whose y-band contains the stroke's vertical centre."""
    for line in lines:
        if line.rect.y0 <= sg.cy <= line.rect.y1:
            return line
    return None


def _covered_tokens(
    sg: _StrokeGeom,
    line: LineBox,
    word_map: WordMap,
    page: int,
) -> list[TokenId]:
    """Tokens on *line* whose boxes overlap the stroke by >= threshold."""
    covered: list[TokenId] = []
    for tid in line.token_ids:
        tok_rect = _token_rect(word_map, page, tid)
        if tok_rect is None:
            continue
        frac = _horizontal_overlap_fraction(sg.bbox, tok_rect)
        if frac >= STRIKETHROUGH_TOKEN_OVERLAP:
            covered.append(tid)
    return covered


def _try_strikethrough(
    cluster: InkCluster,
    geoms: list[_StrokeGeom],
    word_map: WordMap,
    page: int,
    token_spans: list[tuple[int, int]],
    lines: tuple[LineBox, ...],
    strikethrough_action: str,
    snapshot_text: str = "",
) -> GestureResult:
    """Detect a strikethrough gesture.

    One or more near-horizontal strokes that overlap tokens on a line.
    Covered tokens are grouped into contiguous runs (by token id) and
    each run produces its own edit from the literal snapshot slice, so
    non-adjacent words between runs survive verbatim.
    """
    horizontal_strokes: list[_StrokeGeom] = []
    other_strokes: list[CandidateStroke] = []

    for sg in geoms:
        line = _find_line_for_stroke(sg, lines)
        if line is not None and _is_horizontal_stroke(sg, line, word_map):
            horizontal_strokes.append(sg)
        else:
            other_strokes.append(sg.candidate)

    if not horizontal_strokes:
        return _NO_GESTURE

    # Collect all covered tokens from all horizontal strokes
    all_covered: list[TokenId] = []
    consumed_strokes: list[CandidateStroke] = []

    for sg in horizontal_strokes:
        line = _find_line_for_stroke(sg, lines)
        if line is None:
            other_strokes.append(sg.candidate)
            continue
        covered = _covered_tokens(sg, line, word_map, page)
        if covered:
            all_covered.extend(covered)
            consumed_strokes.append(sg.candidate)
        else:
            other_strokes.append(sg.candidate)

    if not all_covered:
        return _NO_GESTURE

    # Deduplicate and sort by token id
    unique_tids = sorted(set(all_covered), key=int)

    # Group into contiguous runs to avoid deleting uncovered tokens
    # between non-adjacent covered tokens.
    runs = _contiguous_runs(unique_tids)
    run_edits = _build_run_edits(runs, token_spans, snapshot_text, strikethrough_action)
    if not run_edits:
        return _NO_GESTURE

    primary = run_edits[0]
    return GestureResult(
        kind="strikethrough",
        token_ids=tuple(unique_tids),
        snapshot_span=primary.snapshot_span,
        replacement_text=primary.replacement_text,
        extra_edits=tuple(run_edits[1:]),
        leftover_strokes=tuple(other_strokes),
    )


# ---------------------------------------------------------------------------
# 3. Caret insertion
# ---------------------------------------------------------------------------


def _is_caret_stroke(sg: _StrokeGeom, line: LineBox) -> bool:
    """Check if a stroke qualifies as a caret mark near a line's baseline."""
    lh = _line_height(line)
    if lh <= 0:
        return False
    # Size constraint: small (bbox <= 1 line height square)
    if sg.width > CARET_MAX_SIZE_RATIO * lh:
        return False
    if sg.height > CARET_MAX_SIZE_RATIO * lh:
        return False
    # Position: bottom of caret on or just below the baseline
    baseline = line.baseline_y
    tolerance = CARET_BASELINE_TOLERANCE_RATIO * lh
    caret_bottom = sg.bbox.y1
    if not (baseline - lh * 0.3 <= caret_bottom <= baseline + tolerance):
        return False
    return True


def _find_caret_insert_position(
    caret: _StrokeGeom,
    line: LineBox,
    word_map: WordMap,
    page: int,
    token_spans: list[tuple[int, int]],
) -> tuple[int, int] | None:
    """Find the snapshot offset where a caret should insert text.

    The caret is between two tokens if its x centre falls between them,
    or at the start/end of the line.

    Returns:
        ``(offset, offset)`` in the snapshot for an insertion point,
        or ``None`` if no valid position found.
    """
    if not line.token_ids:
        return None

    # Get token rects in left-to-right order
    tok_rects: list[tuple[TokenId, Rect]] = []
    for tid in line.token_ids:
        r = _token_rect(word_map, page, tid)
        if r is not None:
            tok_rects.append((tid, r))

    if not tok_rects:
        return None

    cx = caret.cx

    # Before first token?
    first_tid, first_rect = tok_rects[0]
    if cx < first_rect.x0:
        span = token_spans[int(first_tid)] if int(first_tid) < len(token_spans) else (-1, -1)
        if span[0] >= 0:
            return (span[0], span[0])
        return None

    # After last token?
    last_tid, last_rect = tok_rects[-1]
    if cx > last_rect.x1:
        span = token_spans[int(last_tid)] if int(last_tid) < len(token_spans) else (-1, -1)
        if span[1] >= 0:
            return (span[1], span[1])
        return None

    # Between two tokens
    for i in range(len(tok_rects) - 1):
        _, rect_a = tok_rects[i]
        tid_b, rect_b = tok_rects[i + 1]
        if rect_a.x1 <= cx <= rect_b.x0:
            span_b = token_spans[int(tid_b)] if int(tid_b) < len(token_spans) else (-1, -1)
            if span_b[0] >= 0:
                return (span_b[0], span_b[0])
            return None

    # Caret overlaps a token directly -> insert before it
    for tid, rect in tok_rects:
        if rect.x0 <= cx <= rect.x1:
            span = token_spans[int(tid)] if int(tid) < len(token_spans) else (-1, -1)
            if span[0] >= 0:
                return (span[0], span[0])
            return None

    return None


def _is_text_above_line(
    sg: _StrokeGeom,
    line: LineBox,
    prev_line: LineBox | None,
) -> bool:
    """Check if a stroke sits in the interline gap above a line."""
    if prev_line is not None:
        gap_top = prev_line.rect.y1
    else:
        gap_top = 0.0  # above the first line

    gap_bottom = line.rect.y0

    # The stroke should be predominantly in the gap
    stroke_mid_y = sg.cy
    return gap_top <= stroke_mid_y <= gap_bottom


def _try_caret_insertion(
    cluster: InkCluster,
    geoms: list[_StrokeGeom],
    word_map: WordMap,
    page: int,
    token_spans: list[tuple[int, int]],
    lines: tuple[LineBox, ...],
) -> GestureResult:
    """Detect a caret insertion gesture.

    A small caret stroke near a baseline, paired with text strokes in
    the interline gap above that line.
    """
    if len(geoms) < 2:
        return _NO_GESTURE

    # Try each stroke as a potential caret
    for i, sg in enumerate(geoms):
        for line_idx, line in enumerate(lines):
            if not _is_caret_stroke(sg, line):
                continue

            # Check x proximity: caret must be within the line's x range
            # (with tolerance of 3 median-token-widths)
            med_w = _median_token_width(word_map, line)
            x_tol = CARET_TEXT_X_TOLERANCE_TOKENS * med_w
            if sg.cx < line.rect.x0 - x_tol or sg.cx > line.rect.x1 + x_tol:
                continue

            insert_pos = _find_caret_insert_position(
                sg,
                line,
                word_map,
                page,
                token_spans,
            )
            if insert_pos is None:
                continue

            # Find text strokes above this line
            prev_line = lines[line_idx - 1] if line_idx > 0 else None
            text_strokes: list[CandidateStroke] = []
            other_strokes: list[CandidateStroke] = []

            for j, other_sg in enumerate(geoms):
                if j == i:
                    continue  # skip the caret itself
                if _is_text_above_line(other_sg, line, prev_line):
                    # Check x proximity to caret
                    if abs(other_sg.cx - sg.cx) <= x_tol:
                        text_strokes.append(other_sg.candidate)
                    else:
                        other_strokes.append(other_sg.candidate)
                else:
                    other_strokes.append(other_sg.candidate)

            if not text_strokes:
                continue

            # Found a caret + text above: needs OCR
            return GestureResult(
                kind="caret_insertion",
                token_ids=(),
                snapshot_span=insert_pos,
                replacement_text=None,  # filled by OCR
                leftover_strokes=tuple(other_strokes),
                needs_ocr=True,
                ocr_strokes=tuple(text_strokes),
            )

    return _NO_GESTURE


# ---------------------------------------------------------------------------
# 4. Correction = strikethrough + caret insertion above
# ---------------------------------------------------------------------------


def _try_correction(
    cluster: InkCluster,
    geoms: list[_StrokeGeom],
    word_map: WordMap,
    page: int,
    token_spans: list[tuple[int, int]],
    lines: tuple[LineBox, ...],
    strikethrough_action: str,
    snapshot_text: str = "",
) -> GestureResult:
    """Detect a correction gesture.

    A strikethrough whose covered tokens also have text strokes in the
    gap directly above them.  Edit becomes ``~~old~~ new``.
    """
    # Find horizontal strokes and text strokes above
    horizontal_strokes: list[_StrokeGeom] = []
    text_strokes: list[_StrokeGeom] = []
    other_strokes: list[CandidateStroke] = []

    for sg in geoms:
        line = _find_line_for_stroke(sg, lines)
        if line is not None and _is_horizontal_stroke(sg, line, word_map):
            horizontal_strokes.append(sg)
            continue

        # Check if this stroke is text above any line
        is_text = False
        for line_idx, line in enumerate(lines):
            prev_line = lines[line_idx - 1] if line_idx > 0 else None
            if _is_text_above_line(sg, line, prev_line):
                is_text = True
                break
        if is_text:
            text_strokes.append(sg)
        else:
            other_strokes.append(sg.candidate)

    if not horizontal_strokes or not text_strokes:
        return _NO_GESTURE

    # Check that the text strokes are above the same line as the strikethrough
    # Find covered tokens from the horizontal strokes
    all_covered: list[TokenId] = []
    consumed_h_strokes: list[CandidateStroke] = []

    for sg in horizontal_strokes:
        line = _find_line_for_stroke(sg, lines)
        if line is None:
            other_strokes.append(sg.candidate)
            continue
        covered = _covered_tokens(sg, line, word_map, page)
        if covered:
            all_covered.extend(covered)
            consumed_h_strokes.append(sg.candidate)
        else:
            other_strokes.append(sg.candidate)

    if not all_covered:
        return _NO_GESTURE

    # Verify text strokes are above the line containing the covered tokens
    # Find the line for the covered tokens
    covered_line: LineBox | None = None
    covered_line_idx: int = -1
    for line_idx, line in enumerate(lines):
        if any(tid in line.token_ids for tid in all_covered):
            covered_line = line
            covered_line_idx = line_idx
            break

    if covered_line is None:
        return _NO_GESTURE

    prev_line = lines[covered_line_idx - 1] if covered_line_idx > 0 else None

    # Filter text strokes to those actually above the covered line
    text_above: list[CandidateStroke] = []
    med_w = _median_token_width(word_map, covered_line)
    x_range_min = min(
        _token_rect(word_map, page, tid).x0  # type: ignore[union-attr]
        for tid in all_covered
        if _token_rect(word_map, page, tid) is not None
    )
    x_range_max = max(
        _token_rect(word_map, page, tid).x1  # type: ignore[union-attr]
        for tid in all_covered
        if _token_rect(word_map, page, tid) is not None
    )
    x_tol = CARET_TEXT_X_TOLERANCE_TOKENS * med_w

    for tsg in text_strokes:
        if _is_text_above_line(tsg, covered_line, prev_line):
            if tsg.cx >= x_range_min - x_tol and tsg.cx <= x_range_max + x_tol:
                text_above.append(tsg.candidate)
            else:
                other_strokes.append(tsg.candidate)
        else:
            other_strokes.append(tsg.candidate)

    if not text_above:
        return _NO_GESTURE

    unique_tids = sorted(set(all_covered), key=int)

    # Group into contiguous runs (same fix as strikethrough)
    runs = _contiguous_runs(unique_tids)
    run_edits = _build_run_edits(runs, token_spans, snapshot_text, strikethrough_action)
    if not run_edits:
        return _NO_GESTURE

    # For correction, the OCR text is appended after the last run's replacement.
    # The primary edit is the first run; extra_edits are the rest.
    # The replacement_prefix carries the strikethrough text; OCR text is
    # appended by _process_gesture in pull_sync.
    primary = run_edits[0]
    # Append a space to primary replacement for OCR text to follow
    replacement_prefix = primary.replacement_text + " " if primary.replacement_text else ""

    return GestureResult(
        kind="correction",
        token_ids=tuple(unique_tids),
        snapshot_span=primary.snapshot_span,
        replacement_text=replacement_prefix,  # OCR text appended later
        extra_edits=tuple(run_edits[1:]),
        leftover_strokes=tuple(other_strokes),
        needs_ocr=True,
        ocr_strokes=tuple(text_above),
    )


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------


def classify_gesture(
    cluster: InkCluster,
    word_map: WordMap,
    transform: CanvasTransform,
    page: int,
    token_spans: list[tuple[int, int]],
    strikethrough_action: str = "mark",
    snapshot_text: str = "",
) -> GestureResult:
    """Classify a content-page cluster as a gesture or ``"none"``.

    Tries gestures in priority order: tick -> correction ->
    strikethrough -> caret insertion.  Returns the first match.

    Args:
        cluster: The ink cluster to classify.
        word_map: Word map from the pushed PDF.
        transform: Canvas transform for coordinate conversion.
        page: PDF page index (redir value).
        token_spans: Pre-built ``(start, end)`` per token in the snapshot.
        strikethrough_action: ``"mark"`` or ``"delete"``.
        snapshot_text: The pushed snapshot markdown text, used to build
            literal replacement text for contiguous token runs.

    Returns:
        ``GestureResult`` with the classification.
    """
    geoms = [_stroke_geom(cs, transform) for cs in cluster.strokes]
    lines = word_map.lines_on_page(page)

    if not lines:
        return _NO_GESTURE

    # 1. Checkbox tick (highest priority)
    result = _try_tick(cluster, geoms, word_map, page, token_spans)
    if result.kind != "none":
        return result

    # 2. Correction (strikethrough + text above — before pure strikethrough)
    result = _try_correction(
        cluster,
        geoms,
        word_map,
        page,
        token_spans,
        lines,
        strikethrough_action,
        snapshot_text,
    )
    if result.kind != "none":
        return result

    # 3. Strikethrough
    result = _try_strikethrough(
        cluster,
        geoms,
        word_map,
        page,
        token_spans,
        lines,
        strikethrough_action,
        snapshot_text,
    )
    if result.kind != "none":
        return result

    # 4. Caret insertion
    result = _try_caret_insertion(
        cluster,
        geoms,
        word_map,
        page,
        token_spans,
        lines,
    )
    if result.kind != "none":
        return result

    return _NO_GESTURE
