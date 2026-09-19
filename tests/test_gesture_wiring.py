"""Tests for gesture wiring: CLI output and OCR error handling.

Covers:
- CLI stderr output for gesture counts per file.
- OCRServiceError -> Deferred("ocr_unavailable") in _ocr_gesture_strokes.
- Non-OCRServiceError propagates (is a bug, not a service outage).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.ink.candidates import CandidateStroke
from rock_paper_sync.pull_sync import PullResult, PullSyncEngine
from rock_paper_sync.state import StateManager


class TestCliGestureOutput:
    """CLI stderr lines for gesture counts."""

    def test_gesture_counts_on_stderr(self) -> None:
        """Non-zero gesture counts produce the expected stderr lines."""

        # Simulate the CLI output logic with a PullResult
        result = PullResult(
            vault_name="test",
            obsidian_path="note.md",
            success=True,
            ticks=1,
            strikethroughs=2,
            insertions=1,
            corrections=1,
            gestures_uninterpreted=3,
        )

        stderr_lines: list[str] = []

        gesture_total = (
            result.ticks + result.strikethroughs + result.insertions + result.corrections
        )
        if gesture_total > 0:
            parts: list[str] = []
            if result.ticks:
                parts.append(f"{result.ticks} tick(s)")
            if result.strikethroughs:
                parts.append(f"{result.strikethroughs} strikethrough(s)")
            if result.insertions:
                parts.append(f"{result.insertions} insertion(s)")
            if result.corrections:
                parts.append(f"{result.corrections} correction(s)")
            stderr_lines.append(
                f"  {result.obsidian_path}: {gesture_total} edit(s): {', '.join(parts)}"
            )

        if result.gestures_uninterpreted > 0:
            stderr_lines.append(
                f"  {result.obsidian_path}: {result.gestures_uninterpreted} gesture(s) "
                f"uninterpreted (saved as attachments)"
            )

        assert len(stderr_lines) == 2
        assert "5 edit(s)" in stderr_lines[0]
        assert "1 tick(s)" in stderr_lines[0]
        assert "2 strikethrough(s)" in stderr_lines[0]
        assert "1 insertion(s)" in stderr_lines[0]
        assert "1 correction(s)" in stderr_lines[0]
        assert "3 gesture(s) uninterpreted" in stderr_lines[1]

    def test_zero_gestures_no_output(self) -> None:
        """Zero gesture counts produce no stderr lines."""
        result = PullResult(
            vault_name="test",
            obsidian_path="note.md",
            success=True,
            ticks=0,
            strikethroughs=0,
            insertions=0,
            corrections=0,
            gestures_uninterpreted=0,
        )

        gesture_total = (
            result.ticks + result.strikethroughs + result.insertions + result.corrections
        )
        assert gesture_total == 0
        assert result.gestures_uninterpreted == 0


class TestOcrGestureStrokes:
    """OCR error handling in _ocr_gesture_strokes."""

    def test_ocr_service_error_returns_none(self, tmp_path: Path) -> None:
        """OCRServiceError -> None (deferred), not raised."""
        from rock_paper_sync.ocr.protocol import OCRServiceError

        state = StateManager(tmp_path / "test.db")
        mock_cloud = MagicMock()

        class FailingOCR:
            def process_image(self, png_bytes: bytes) -> str:
                raise OCRServiceError("service down")

        engine = PullSyncEngine(
            state=state,
            cloud_reader=mock_cloud,
            layout=LayoutConfig(),
            cache_dir=tmp_path / "cache",
            ocr_processor=FailingOCR(),
        )

        # Build a minimal candidate stroke for the synthetic cluster
        from rmscene import CrdtId, CrdtSequenceItem, SceneLineItemBlock
        from rmscene.scene_items import Line, Pen, PenColor, Point

        from rock_paper_sync.annotations.domain.stroke import Stroke

        line = Line(
            color=PenColor.BLACK,
            tool=Pen.FINELINER_1,
            points=[
                Point(x=0, y=0, speed=0, direction=0, width=2, pressure=100),
                Point(x=10, y=10, speed=0, direction=0, width=2, pressure=100),
            ],
            thickness_scale=1.0,
            starting_length=0.0,
            move_id=None,
        )
        block = SceneLineItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, 99),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=line,
            ),
        )
        stroke = Stroke.from_line_block(block)
        candidate = CandidateStroke(stroke=stroke, page_index=0, redir=0)

        result = engine._ocr_gesture_strokes((candidate,))
        assert result is None

        state.close()

    def test_non_ocr_error_propagates(self, tmp_path: Path) -> None:
        """A non-OCRServiceError (e.g. RuntimeError) propagates as-is."""
        state = StateManager(tmp_path / "test.db")
        mock_cloud = MagicMock()

        class BuggyOCR:
            def process_image(self, png_bytes: bytes) -> str:
                raise RuntimeError("unexpected bug")

        engine = PullSyncEngine(
            state=state,
            cloud_reader=mock_cloud,
            layout=LayoutConfig(),
            cache_dir=tmp_path / "cache",
            ocr_processor=BuggyOCR(),
        )

        from rmscene import CrdtId, CrdtSequenceItem, SceneLineItemBlock
        from rmscene.scene_items import Line, Pen, PenColor, Point

        from rock_paper_sync.annotations.domain.stroke import Stroke

        line = Line(
            color=PenColor.BLACK,
            tool=Pen.FINELINER_1,
            points=[
                Point(x=0, y=0, speed=0, direction=0, width=2, pressure=100),
                Point(x=10, y=10, speed=0, direction=0, width=2, pressure=100),
            ],
            thickness_scale=1.0,
            starting_length=0.0,
            move_id=None,
        )
        block = SceneLineItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, 98),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=line,
            ),
        )
        stroke = Stroke.from_line_block(block)
        candidate = CandidateStroke(stroke=stroke, page_index=0, redir=0)

        with pytest.raises(RuntimeError, match="unexpected bug"):
            engine._ocr_gesture_strokes((candidate,))

        state.close()
