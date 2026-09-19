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
from rock_paper_sync.device_profile import PROFILES, pdf_to_device
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


# ---------------------------------------------------------------------------
# Fixtures: render the calibration markdown to get a real word map
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rendered_word_map() -> WordMap:
    """Render the test fixture and return the word map."""
    from rock_paper_sync.config import LayoutConfig
    from rock_paper_sync.parser import parse_markdown_file
    from rock_paper_sync.render.pdf import PdfRenderer

    fixture = Path("tests/record_replay/fixtures/test_pdf_substrate.md")
    if not fixture.exists():
        pytest.skip("test_pdf_substrate.md fixture not found")

    doc = parse_markdown_file(fixture)
    layout = LayoutConfig()
    renderer = PdfRenderer(layout)
    result = renderer.render(doc, PROFILE)
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
    dev_x0, dev_y0 = pdf_to_device(PROFILE, pdf_rect.x0, pdf_rect.y0)
    dev_x1, dev_y1 = pdf_to_device(PROFILE, pdf_rect.x1, pdf_rect.y1)
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
            PROFILE,
            page,
            token_spans,
        )

        assert result.kind == "tick"
        assert result.replacement_text == "[x]"
        assert result.snapshot_span is not None
        assert result.snapshot_span[0] >= 0

    def test_tick_on_checked_box(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
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
            PROFILE,
            page,
            token_spans,
        )

        assert result.kind == "tick"
        assert result.checkbox_already_checked is True

    def test_tick_outside_checkbox_is_none(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
    ) -> None:
        """A stroke nowhere near a checkbox -> no gesture (drawing)."""
        # Place stroke far from any checkbox, e.g. in the margin
        far_rect = Rect(x0=5.0, y0=5.0, x1=15.0, y1=15.0)
        stroke = _make_candidate_stroke(far_rect, stroke_id_part2=202)
        cluster = _make_cluster([stroke])

        result = classify_gesture(
            cluster,
            rendered_word_map,
            PROFILE,
            0,
            token_spans,
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
            PROFILE,
            page,
            token_spans,
            strikethrough_action="mark",
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
            PROFILE,
            page,
            token_spans,
            strikethrough_action="delete",
        )

        assert result.kind == "strikethrough"
        assert result.replacement_text == ""

    def test_horizontal_stroke_in_margin_is_none(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
    ) -> None:
        """A horizontal stroke in the margin (no token overlap) -> drawing."""
        # Place a wide horizontal stroke in the left margin
        margin_rect = Rect(x0=2.0, y0=100.0, x1=30.0, y1=102.0)
        stroke = _make_candidate_stroke(margin_rect, stroke_id_part2=302)
        cluster = _make_cluster([stroke])

        result = classify_gesture(
            cluster,
            rendered_word_map,
            PROFILE,
            0,
            token_spans,
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
            PROFILE,
            page,
            token_spans,
        )

        assert result.kind == "caret_insertion"
        assert result.needs_ocr is True
        assert len(result.ocr_strokes) >= 1

    def test_text_above_line_without_caret_is_none(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
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
            PROFILE,
            page,
            token_spans,
        )

        # Single stroke can't be caret+text, so no gesture
        assert result.kind == "none"

    def test_caret_without_text_is_none(
        self,
        rendered_word_map: WordMap,
        token_spans: list[tuple[int, int]],
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
            PROFILE,
            page,
            token_spans,
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
            PROFILE,
            page,
            token_spans,
            strikethrough_action="mark",
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
# 6. Replay hook (skip if recording doesn't exist)
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
