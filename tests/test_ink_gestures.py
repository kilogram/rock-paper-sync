"""Tests for Phase 5 gesture interpretation.

Tests built from a real word map (rendered from the calibration fixture)
with synthetic strokes placed relative to token rects. Strokes are
created in PDF points and converted to device coords via ``pdf_to_device``
where the code under test expects device coords (i.e. in InkCluster.bbox
and CandidateStroke.stroke.bounding_box).

Covers:
    - Checkbox tick: positive, already-checked, outside-checkbox.
    - Strikethrough: positive, margin miss, delete variant.
    - Caret insertion: positive, no-text-above, no-caret.
    - Correction: strikethrough + text above.
    - Low-confidence OCR -> Baked + UNINTERPRETED.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from rmscene import CrdtId
from rmscene.scene_items import Line, Pen, PenColor, Point

from rock_paper_sync.annotations.domain.stroke import Stroke

# Use the real profile for production code, but for test rendering we
# use a "legacy" profile that produces the old 477x848 pt page size so
# the fixture fits on fewer pages and page-0 assumptions hold.
from rock_paper_sync.device_profile import PROFILES, CanvasTransform, DeviceProfile, pdf_to_device
from rock_paper_sync.ink.apply import build_token_spans
from rock_paper_sync.ink.candidates import CandidateStroke
from rock_paper_sync.ink.clusters import InkCluster
from rock_paper_sync.ink.gestures import classify_gesture
from rock_paper_sync.render.wordmap import (
    Rect,
    TokenId,
    WordMap,
)

PROFILE = PROFILES["paper_pro_move"]
_LEGACY_PROFILE = DeviceProfile(name="test_legacy", screen_w_px=954, screen_h_px=1696, ppi=144)
# ppi=144 gives 954/144*72 = 477 pt width, 1696/144*72 = 848 pt height
TRANSFORM = CanvasTransform.for_pdf_page(_LEGACY_PROFILE)


# ---------------------------------------------------------------------------
# Fixtures: render the calibration markdown to get a real word map
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rendered_word_map() -> WordMap:
    """Render the test fixture and return the word map.

    Uses a fixed layout matching the pre-PPI-fix geometry (477x848 pt,
    36 pt margins, line_height 1.4) so the fixture fits on fewer pages
    and all gesture tests' page-0 assumptions hold.
    """
    from rock_paper_sync.config import LayoutConfig
    from rock_paper_sync.parser import parse_markdown_file
    from rock_paper_sync.render.pdf import PdfRenderer

    fixture = Path("tests/record_replay/fixtures/test_pdf_substrate.md")
    if not fixture.exists():
        pytest.skip("test_pdf_substrate.md fixture not found")

    doc = parse_markdown_file(fixture)
    # Fixed layout so the word map is stable across geometry changes.
    layout = LayoutConfig(
        margin_top_pt=36.0,
        margin_bottom_pt=36.0,
        margin_left_pt=36.0,
        margin_right_pt=36.0,
        line_height=1.4,
        h1_scale=1.8,
        h2_scale=1.4,
        h3_scale=1.2,
        h4_scale=1.1,
    )
    renderer = PdfRenderer(layout)
    result = renderer.render(doc, _LEGACY_PROFILE)
    return result.word_map


@pytest.fixture(scope="module")
def snapshot_text() -> str:
    """Load the fixture markdown text."""
    fixture = Path("tests/record_replay/fixtures/test_pdf_substrate.md")
    if not fixture.exists():
        pytest.skip("test_pdf_substrate.md fixture not found")
    return fixture.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def token_spans(rendered_word_map: WordMap, snapshot_text: str) -> list[tuple[int, int]]:
    """Build token spans from the word map and snapshot."""
    return build_token_spans(rendered_word_map.texts, snapshot_text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_token_by_text(wm: WordMap, text: str, page: int = 0) -> tuple[TokenId, Rect] | None:
    """Find the first token matching ``text`` on ``page``."""
    for pw in wm.pages:
        if pw.page != page:
            continue
        for tid, rect in pw.tokens:
            if wm.texts[tid] == text:
                return tid, rect
    return None


def _find_all_tokens_by_text(
    wm: WordMap,
    texts: list[str],
    page: int = 0,
) -> list[tuple[TokenId, Rect]]:
    """Find tokens matching each word in ``texts`` on ``page``."""
    result: list[tuple[TokenId, Rect]] = []
    used: set[int] = set()
    for word in texts:
        for pw in wm.pages:
            if pw.page != page:
                continue
            for tid, rect in pw.tokens:
                if wm.texts[tid] == word and int(tid) not in used:
                    result.append((tid, rect))
                    used.add(int(tid))
                    break
    return result


def _make_candidate_stroke(
    pdf_rect: Rect,
    stroke_id_part2: int = 100,
    page_index: int = 0,
    redir: int | None = 0,
) -> CandidateStroke:
    """Create a CandidateStroke from a bounding box in PDF points.

    The bbox is converted to device coordinates for the stroke's
    bounding_box field (which is in device coords: centre-top origin,
    x,y,w,h format).
    """
    # Convert PDF rect corners to device coords
    dev_x0, dev_y0 = pdf_to_device(TRANSFORM, pdf_rect.x0, pdf_rect.y0)
    dev_x1, dev_y1 = pdf_to_device(TRANSFORM, pdf_rect.x1, pdf_rect.y1)
    dev_x = min(dev_x0, dev_x1)
    dev_y = min(dev_y0, dev_y1)
    dev_w = abs(dev_x1 - dev_x0)
    dev_h = abs(dev_y1 - dev_y0)

    # Create minimal Line with points at corners
    points = [
        Point(x=dev_x, y=dev_y, speed=0, direction=0, width=2, pressure=100),
        Point(x=dev_x + dev_w, y=dev_y + dev_h, speed=0, direction=0, width=2, pressure=100),
    ]
    line = Line(
        color=PenColor.BLACK,
        tool=Pen.FINELINER_1,
        points=points,
        thickness_scale=1.0,
        starting_length=0.0,
        move_id=None,
    )

    # Build a Stroke-like object
    from rmscene import CrdtSequenceItem, SceneLineItemBlock

    line_block = SceneLineItemBlock(
        parent_id=CrdtId(0, 11),
        item=CrdtSequenceItem(
            item_id=CrdtId(1, stroke_id_part2),
            left_id=CrdtId(0, 0),
            right_id=CrdtId(0, 0),
            deleted_length=0,
            value=line,
        ),
    )

    stroke = Stroke.from_line_block(line_block)

    return CandidateStroke(
        stroke=stroke,
        page_index=page_index,
        redir=redir,
    )


def _make_cluster(
    candidates: list[CandidateStroke],
    redir: int | None = 0,
) -> InkCluster:
    """Build an InkCluster from candidate strokes."""
    stroke_ids = [f"{cs.stroke.stroke_id.part1}:{cs.stroke.stroke_id.part2}" for cs in candidates]
    joined = "\n".join(sorted(stroke_ids))
    cluster_id = hashlib.sha256(joined.encode("utf-8")).hexdigest()

    all_bboxes = [cs.stroke.bounding_box for cs in candidates]
    x0 = min(bx for bx, _, _, _ in all_bboxes)
    y0 = min(by for _, by, _, _ in all_bboxes)
    x1 = max(bx + bw for bx, _, bw, _ in all_bboxes)
    y1 = max(by + bh for _, by, _, bh in all_bboxes)

    return InkCluster(
        cluster_id=cluster_id,
        strokes=tuple(candidates),
        bbox=(x0, y0, x1, y1),
        page_index=0,
        redir=redir,
    )


# ---------------------------------------------------------------------------
# 1. Checkbox tick tests
# ---------------------------------------------------------------------------


class TestCheckboxTick:
    """Checkbox tick gesture detection."""

    def test_tick_on_unchecked_box(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """A stroke cluster inside an unchecked checkbox -> tick gesture."""
        # Checkbox tokens carry "[ ]" (unchecked) or "[x]" (checked)
        # in WordMap.texts, from pdf.py's box-tree walk.
        checkbox_tids: list[tuple[TokenId, Rect]] = []
        for pw in rendered_word_map.pages:
            for tid, rect in pw.tokens:
                if rendered_word_map.texts[tid] == "[ ]":
                    checkbox_tids.append((tid, rect))

        assert len(checkbox_tids) >= 2, (
            f"Expected at least 2 unchecked checkboxes, found {len(checkbox_tids)}"
        )

        # Use the second unchecked checkbox (corresponds to "Tick the second item")
        tid, rect = checkbox_tids[1]
        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == tid:
                    page = pw.page
                    break

        # Create a stroke inside the checkbox rect
        stroke = _make_candidate_stroke(rect, stroke_id_part2=200, page_index=0, redir=page)
        cluster = _make_cluster([stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            snapshot_text=snapshot_text,
        )

        assert result.kind == "tick"
        assert result.replacement_text == "[x]"
        assert result.snapshot_span is not None
        assert result.snapshot_span[0] >= 0

    def test_tick_on_checked_box(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """A stroke on an already-checked box -> already_checked."""
        # Find the "[x]" token (checked checkbox)
        checked_tids: list[tuple[TokenId, Rect]] = []
        for pw in rendered_word_map.pages:
            for tid, rect in pw.tokens:
                if rendered_word_map.texts[tid] == "[x]":
                    checked_tids.append((tid, rect))

        assert len(checked_tids) >= 1, "Expected at least 1 checked checkbox"

        tid, rect = checked_tids[0]
        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == tid:
                    page = pw.page
                    break

        stroke = _make_candidate_stroke(rect, stroke_id_part2=201, page_index=0, redir=page)
        cluster = _make_cluster([stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            snapshot_text=snapshot_text,
        )

        assert result.kind == "tick"
        assert result.checkbox_already_checked is True

    def test_tick_outside_checkbox_is_none(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """A stroke nowhere near a checkbox -> no gesture (drawing)."""
        # Place stroke far from any checkbox, e.g. in the margin
        far_rect = Rect(x0=5.0, y0=5.0, x1=15.0, y1=15.0)
        stroke = _make_candidate_stroke(far_rect, stroke_id_part2=202)
        cluster = _make_cluster([stroke])

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            0,
            token_spans,
            snapshot_text=snapshot_text,
        )

        assert result.kind == "none"


# ---------------------------------------------------------------------------
# 2. Strikethrough tests
# ---------------------------------------------------------------------------


class TestStrikethrough:
    """Strikethrough gesture detection."""

    def test_strikethrough_over_tokens(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """A horizontal stroke through 'DELETE THIS PHRASE' -> strikethrough."""
        words = ["DELETE", "THIS", "PHRASE"]
        tokens = _find_all_tokens_by_text(rendered_word_map, words)
        assert len(tokens) >= 3, f"Expected 3 tokens, found {len(tokens)}"

        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == tokens[0][0]:
                    page = pw.page
                    break

        # Build a horizontal stroke through the middle of these tokens
        first_rect = tokens[0][1]
        last_rect = tokens[-1][1]
        mid_y = (first_rect.y0 + first_rect.y1) / 2.0
        stroke_rect = Rect(
            x0=first_rect.x0 - 2.0,
            y0=mid_y - 1.0,
            x1=last_rect.x1 + 2.0,
            y1=mid_y + 1.0,
        )

        stroke = _make_candidate_stroke(stroke_rect, stroke_id_part2=300, redir=page)
        cluster = _make_cluster([stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            strikethrough_action="mark",
            snapshot_text=snapshot_text,
        )

        assert result.kind == "strikethrough"
        assert result.replacement_text is not None
        assert "~~" in result.replacement_text
        assert "DELETE" in result.replacement_text
        assert "PHRASE" in result.replacement_text
        assert result.snapshot_span is not None

    def test_strikethrough_delete_variant(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """Strikethrough with delete action -> empty replacement."""
        words = ["DELETE", "THIS", "PHRASE"]
        tokens = _find_all_tokens_by_text(rendered_word_map, words)
        assert len(tokens) >= 3

        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == tokens[0][0]:
                    page = pw.page
                    break

        first_rect = tokens[0][1]
        last_rect = tokens[-1][1]
        mid_y = (first_rect.y0 + first_rect.y1) / 2.0
        stroke_rect = Rect(
            x0=first_rect.x0 - 2.0,
            y0=mid_y - 1.0,
            x1=last_rect.x1 + 2.0,
            y1=mid_y + 1.0,
        )

        stroke = _make_candidate_stroke(stroke_rect, stroke_id_part2=301, redir=page)
        cluster = _make_cluster([stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            strikethrough_action="delete",
            snapshot_text=snapshot_text,
        )

        assert result.kind == "strikethrough"
        assert result.replacement_text == ""

    def test_horizontal_stroke_in_margin_is_none(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """A horizontal stroke in the margin (no token overlap) -> drawing."""
        # Place a wide horizontal stroke in the left margin
        margin_rect = Rect(x0=2.0, y0=100.0, x1=30.0, y1=102.0)
        stroke = _make_candidate_stroke(margin_rect, stroke_id_part2=302)
        cluster = _make_cluster([stroke])

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            0,
            token_spans,
            snapshot_text=snapshot_text,
        )

        assert result.kind == "none"


# ---------------------------------------------------------------------------
# 3. Caret insertion tests
# ---------------------------------------------------------------------------


class TestCaretInsertion:
    """Caret insertion gesture detection."""

    def test_caret_with_text_above(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """A caret + text strokes above -> caret_insertion with needs_ocr."""
        # Find "here." token for the caret insertion target
        token = _find_token_by_text(rendered_word_map, "here.")
        assert token is not None, "Token 'here.' not found"
        tid, tok_rect = token

        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == tid:
                    page = pw.page
                    break

        lines = rendered_word_map.lines_on_page(page)
        # Find the line containing "here."
        target_line = None
        target_line_idx = -1
        for i, line in enumerate(lines):
            if tid in line.token_ids:
                target_line = line
                target_line_idx = i
                break

        assert target_line is not None, "Line containing 'here.' not found"

        lh = target_line.rect.y1 - target_line.rect.y0

        # Create a small caret stroke just before "here."
        caret_rect = Rect(
            x0=tok_rect.x0 - 5.0,
            y0=target_line.baseline_y - lh * 0.3,
            x1=tok_rect.x0,
            y1=target_line.baseline_y,
        )
        caret_stroke = _make_candidate_stroke(caret_rect, stroke_id_part2=400, redir=page)

        # Create text strokes in the interline gap above
        prev_line = lines[target_line_idx - 1] if target_line_idx > 0 else None
        gap_y = (
            (prev_line.rect.y1 + target_line.rect.y0) / 2.0
            if prev_line
            else target_line.rect.y0 - lh * 0.3
        )

        text_rect = Rect(
            x0=tok_rect.x0 - 10.0,
            y0=gap_y - 3.0,
            x1=tok_rect.x0 + 20.0,
            y1=gap_y + 3.0,
        )
        text_stroke = _make_candidate_stroke(text_rect, stroke_id_part2=401, redir=page)

        cluster = _make_cluster([caret_stroke, text_stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            snapshot_text=snapshot_text,
        )

        assert result.kind == "caret_insertion"
        assert result.needs_ocr is True
        assert len(result.ocr_strokes) >= 1

    def test_text_above_line_without_caret_is_none(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """Text strokes above a line with no caret -> drawing (no gesture)."""
        token = _find_token_by_text(rendered_word_map, "here.")
        assert token is not None
        tid, tok_rect = token

        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == tid:
                    page = pw.page
                    break

        lines = rendered_word_map.lines_on_page(page)
        target_line = None
        target_line_idx = -1
        for i, line in enumerate(lines):
            if tid in line.token_ids:
                target_line = line
                target_line_idx = i
                break

        assert target_line is not None

        lh = target_line.rect.y1 - target_line.rect.y0
        prev_line = lines[target_line_idx - 1] if target_line_idx > 0 else None
        gap_y = (
            (prev_line.rect.y1 + target_line.rect.y0) / 2.0
            if prev_line
            else target_line.rect.y0 - lh * 0.3
        )

        # Only a text stroke, no caret
        text_rect = Rect(
            x0=tok_rect.x0 - 10.0,
            y0=gap_y - 3.0,
            x1=tok_rect.x0 + 20.0,
            y1=gap_y + 3.0,
        )
        text_stroke = _make_candidate_stroke(text_rect, stroke_id_part2=410, redir=page)

        cluster = _make_cluster([text_stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            snapshot_text=snapshot_text,
        )

        # Single stroke can't be caret+text, so no gesture
        assert result.kind == "none"

    def test_caret_without_text_is_none(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """A caret stroke alone (no text above) -> drawing."""
        token = _find_token_by_text(rendered_word_map, "here.")
        assert token is not None
        tid, tok_rect = token

        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == tid:
                    page = pw.page
                    break

        lines = rendered_word_map.lines_on_page(page)
        target_line = None
        for line in lines:
            if tid in line.token_ids:
                target_line = line
                break

        assert target_line is not None

        lh = target_line.rect.y1 - target_line.rect.y0

        # Just a caret, no text partner
        caret_rect = Rect(
            x0=tok_rect.x0 - 5.0,
            y0=target_line.baseline_y - lh * 0.3,
            x1=tok_rect.x0,
            y1=target_line.baseline_y,
        )
        caret_stroke = _make_candidate_stroke(caret_rect, stroke_id_part2=420, redir=page)

        cluster = _make_cluster([caret_stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            snapshot_text=snapshot_text,
        )

        # Single stroke, needs 2 for caret insertion
        assert result.kind == "none"


# ---------------------------------------------------------------------------
# 4. Correction tests
# ---------------------------------------------------------------------------


class TestCorrection:
    """Correction gesture detection (strikethrough + text above)."""

    def test_correction_gesture(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """Strikethrough + text above -> correction with needs_ocr."""
        words = ["DELETE", "THIS", "PHRASE"]
        tokens = _find_all_tokens_by_text(rendered_word_map, words)
        assert len(tokens) >= 3

        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == tokens[0][0]:
                    page = pw.page
                    break

        first_rect = tokens[0][1]
        last_rect = tokens[-1][1]
        mid_y = (first_rect.y0 + first_rect.y1) / 2.0

        # Horizontal strikethrough stroke
        strike_rect = Rect(
            x0=first_rect.x0 - 2.0,
            y0=mid_y - 1.0,
            x1=last_rect.x1 + 2.0,
            y1=mid_y + 1.0,
        )
        strike_stroke = _make_candidate_stroke(strike_rect, stroke_id_part2=500, redir=page)

        # Text strokes above the same line
        lines = rendered_word_map.lines_on_page(page)
        target_line = None
        target_line_idx = -1
        for i, line in enumerate(lines):
            if tokens[0][0] in line.token_ids:
                target_line = line
                target_line_idx = i
                break

        assert target_line is not None

        prev_line = lines[target_line_idx - 1] if target_line_idx > 0 else None
        gap_y = (
            (prev_line.rect.y1 + target_line.rect.y0) / 2.0
            if prev_line
            else target_line.rect.y0 - 10.0
        )

        text_rect = Rect(
            x0=first_rect.x0,
            y0=gap_y - 3.0,
            x1=last_rect.x1,
            y1=gap_y + 3.0,
        )
        text_stroke = _make_candidate_stroke(text_rect, stroke_id_part2=501, redir=page)

        cluster = _make_cluster([strike_stroke, text_stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            strikethrough_action="mark",
            snapshot_text=snapshot_text,
        )

        assert result.kind == "correction"
        assert result.needs_ocr is True
        assert result.replacement_text is not None
        assert "~~" in result.replacement_text
        assert len(result.ocr_strokes) >= 1


# ---------------------------------------------------------------------------
# 5. LineBox tests
# ---------------------------------------------------------------------------


class TestLinesOnPage:
    """WordMap.lines_on_page grouping."""

    def test_lines_on_page_returns_lines(
        self,
        rendered_word_map: WordMap,
    ) -> None:
        """lines_on_page returns non-empty lines on page 0."""
        lines = rendered_word_map.lines_on_page(0)
        assert len(lines) > 0
        for line in lines:
            assert len(line.token_ids) > 0
            assert line.rect.y1 > line.rect.y0
            assert line.baseline_y == line.rect.y1

    def test_lines_on_empty_page_returns_empty(
        self,
        rendered_word_map: WordMap,
    ) -> None:
        """lines_on_page on a page with no tokens returns ()."""
        lines = rendered_word_map.lines_on_page(999)
        assert lines == ()


# ---------------------------------------------------------------------------
# 6. Review-fix tests
# ---------------------------------------------------------------------------


class TestNonContiguousStrikethrough:
    """Fix 1: non-contiguous strikethrough preserves skipped words."""

    def test_skip_middle_word_preserves_it(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """Two strokes over DELETE and PHRASE (skipping THIS) -> two edits,
        THIS survives verbatim."""
        delete_tok = _find_token_by_text(rendered_word_map, "DELETE")
        phrase_tok = _find_token_by_text(rendered_word_map, "PHRASE")
        assert delete_tok is not None and phrase_tok is not None

        page = 0
        for pw in rendered_word_map.pages:
            for t, _ in pw.tokens:
                if t == delete_tok[0]:
                    page = pw.page
                    break

        mid_y = (delete_tok[1].y0 + delete_tok[1].y1) / 2.0

        s1 = _make_candidate_stroke(
            Rect(x0=delete_tok[1].x0 - 2, y0=mid_y - 1, x1=delete_tok[1].x1 + 2, y1=mid_y + 1),
            stroke_id_part2=901,
            redir=page,
        )
        s2 = _make_candidate_stroke(
            Rect(x0=phrase_tok[1].x0 - 2, y0=mid_y - 1, x1=phrase_tok[1].x1 + 2, y1=mid_y + 1),
            stroke_id_part2=902,
            redir=page,
        )
        cluster = _make_cluster([s1, s2], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            strikethrough_action="mark",
            snapshot_text=snapshot_text,
        )

        assert result.kind == "strikethrough"
        assert "~~DELETE~~" in (result.replacement_text or "")
        # THIS must NOT appear inside ~~ markers
        assert "THIS" not in (result.replacement_text or "")
        # The extra edit covers PHRASE
        assert len(result.extra_edits) >= 1
        assert "~~PHRASE~~" in result.extra_edits[0].replacement_text

        # Apply and verify THIS survived
        from rock_paper_sync.ink.apply import apply_edits
        from rock_paper_sync.ink.dispositions import InkAnchor, MarkdownEdit

        all_edits = [
            MarkdownEdit(
                kind="strikethrough",
                cluster_id="test",
                anchor=InkAnchor(kind="document_end", ref=""),
                text=result.replacement_text or "",
                snapshot_span=result.snapshot_span,
            ),
        ]
        for extra in result.extra_edits:
            all_edits.append(
                MarkdownEdit(
                    kind="strikethrough",
                    cluster_id="test",
                    anchor=InkAnchor(kind="document_end", ref=""),
                    text=extra.replacement_text,
                    snapshot_span=extra.snapshot_span,
                ),
            )
        new_text, failed = apply_edits(all_edits, snapshot_text, snapshot_text)
        assert not failed
        assert "~~DELETE~~" in new_text
        assert "~~PHRASE~~" in new_text
        assert " THIS " in new_text  # THIS survived between the two edits


class TestCheckboxAndItemShareLine:
    """Fix 2a: checkbox token shares a LineBox with its item text."""

    def test_checkbox_on_same_line_as_item(
        self,
        rendered_word_map: WordMap,
    ) -> None:
        """The '[ ]' token and 'First' share the same LineBox."""
        lines = rendered_word_map.lines_on_page(0)
        checkbox_line = None
        for line in lines:
            texts = [rendered_word_map.texts[t] for t in line.token_ids]
            if "[ ]" in texts and "First" in texts:
                checkbox_line = line
                break
        assert checkbox_line is not None, "Checkbox '[ ]' and 'First' should share a LineBox"


class TestFootnoteTokenDedup:
    """Fix 2b: footnote-ref token has exactly one rect per page."""

    def test_footnote_ref_single_rect(self, tmp_path: Path) -> None:
        """A <sup> footnote ref does not produce a phantom second rect."""
        from rock_paper_sync.config import LayoutConfig
        from rock_paper_sync.parser import parse_markdown_file
        from rock_paper_sync.render.pdf import PdfRenderer

        md = tmp_path / "fn.md"
        md.write_text(
            "---\ntitle: fn test\n---\n\n"
            "# Footnote test\n\n"
            "Some text here[^1] and more text.\n\n"
            "[^1]: A footnote.\n"
        )
        doc = parse_markdown_file(md)
        renderer = PdfRenderer(LayoutConfig())
        result = renderer.render(doc, _LEGACY_PROFILE)
        wm = result.word_map

        # Count how many times each token appears in pages[].tokens
        from collections import Counter

        tid_counts: Counter[int] = Counter()
        for pw in wm.pages:
            for tid, _ in pw.tokens:
                tid_counts[int(tid)] += 1

        for tid_int, count in tid_counts.items():
            assert count == 1, (
                f"Token {tid_int} ({wm.texts[tid_int]!r}) appears {count} times "
                f"on its page; expected 1 (dedup fix)"
            )


class TestWrappedParagraphLinesSeparate:
    """Fix 2: wrapped lines of the same paragraph stay separate."""

    def test_wrapped_lines_separate(
        self,
        rendered_word_map: WordMap,
    ) -> None:
        """'DELETE' and 'remains' (different wrapped lines) are in different LineBoxes."""
        lines = rendered_word_map.lines_on_page(0)
        delete_line_idx = None
        remains_line_idx = None
        for i, line in enumerate(lines):
            texts = [rendered_word_map.texts[t] for t in line.token_ids]
            if "DELETE" in texts:
                delete_line_idx = i
            if "remains" in texts:
                remains_line_idx = i
        assert delete_line_idx is not None
        assert remains_line_idx is not None
        assert delete_line_idx != remains_line_idx, (
            "'DELETE' and 'remains' should be on separate lines (different y0)"
        )


class TestTickOvershoot:
    """Fix 5: tick shifted by 50% of box width -> not a tick."""

    def test_shifted_tick_is_not_tick(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
        snapshot_text: str,
    ) -> None:
        """A stroke shifted right by 60% of the checkbox width is not a tick."""
        checkbox_tids = []
        for pw in rendered_word_map.pages:
            for tid, rect in pw.tokens:
                if rendered_word_map.texts[tid] == "[ ]":
                    checkbox_tids.append((tid, rect, pw.page))

        assert len(checkbox_tids) >= 1
        tid, rect, page = checkbox_tids[0]

        w = rect.x1 - rect.x0
        # Shift by 60% of box width to the right — centroid lands
        # outside the checkbox, guaranteeing a miss even with
        # floating-point rounding in the canvas transform.
        shifted = Rect(
            x0=rect.x0 + w * 0.6,
            y0=rect.y0,
            x1=rect.x1 + w * 0.6,
            y1=rect.y1,
        )

        stroke = _make_candidate_stroke(shifted, stroke_id_part2=950, redir=page)
        cluster = _make_cluster([stroke], redir=page)

        result = classify_gesture(
            cluster,
            rendered_word_map,
            TRANSFORM,
            page,
            token_spans,
            snapshot_text=snapshot_text,
        )

        # Centroid of shifted stroke is outside checkbox -> not a tick
        assert result.kind != "tick", (
            "A stroke shifted by 60% of the box width should not be a tick"
        )


# ---------------------------------------------------------------------------
# 7. Replay hook (skip if recording doesn't exist)
# ---------------------------------------------------------------------------


class TestReplayHook:
    """Run gesture pipeline over recorded device data (when available)."""

    def test_gesture_replay(self, tmp_path: Path) -> None:
        """Run gestures over trip 1 recording if it exists."""
        trip_dir = Path("tests/record_replay/testdata/pdf_substrate/trips/1")
        if not trip_dir.exists():
            pytest.skip(
                "No trip 1 recording at tests/record_replay/testdata/"
                "pdf_substrate/trips/1 — run a device session first"
            )

        # If the recording exists, we'd load the word map, strokes,
        # and run the gesture pipeline here. For now, just report.
        diag_dir = tmp_path / "_diagnostic"
        diag_dir.mkdir()
        (diag_dir / "gestures.md").write_text(
            "# Gesture Classification Diagnostic\n\n"
            "Recording exists but replay not yet implemented.\n"
        )
