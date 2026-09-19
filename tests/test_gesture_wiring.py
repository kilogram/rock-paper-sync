"""Tests for gesture wiring: CLI output and OCR error handling.

Covers:
- CLI stderr output for gesture counts per file (via CliRunner).
- OCRServiceError -> Deferred("ocr_unavailable") in _ocr_gesture_strokes.
- Non-OCRServiceError propagates (is a bug, not a service outage).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.ink.candidates import CandidateStroke
from rock_paper_sync.pull_sync import PullResult, PullStats, PullSyncEngine
from rock_paper_sync.state import StateManager

# ---------------------------------------------------------------------------
# CLI gesture output via CliRunner
# ---------------------------------------------------------------------------


class TestCliGestureOutput:
    """CLI stderr lines for gesture counts, exercised through CliRunner."""

    def test_gesture_counts_on_stderr(self, tmp_path: Path) -> None:
        """Non-zero gesture counts produce the expected stderr lines.

        Uses ``click.testing.CliRunner`` with mocked ``PullSyncEngine``
        returning a ``PullResult`` with non-zero gesture counts.
        """
        from click.testing import CliRunner

        from rock_paper_sync import cli

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "note.md").write_text("# Test\n")

        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""\
[paths]
state_database = "{tmp_path / "state.db"}"

[[vaults]]
name = "test-vault"
path = "{vault_dir}"

[cloud]
base_url = "http://fake:3000"

[logging]
level = "warning"
file = "{tmp_path / "test.log"}"
""")

        # Build the canned pull result
        canned_result = PullResult(
            vault_name="test-vault",
            obsidian_path="note.md",
            success=True,
            highlights_added=0,
            notes_appended=0,
            drawings_saved=0,
            ticks=1,
            strikethroughs=2,
            insertions=1,
            corrections=1,
            gestures_uninterpreted=3,
        )
        canned_stats = PullStats(
            files_checked=1,
            files_updated=1,
            files_skipped=0,
            files_errored=0,
            total_highlights=0,
            total_notes=0,
            total_drawings=0,
            total_deferred=0,
        )

        mock_engine = MagicMock()
        mock_engine.pull_vault.return_value = ([canned_result], canned_stats)

        with (
            patch(
                "rock_paper_sync.pull_sync.PullSyncEngine",
                return_value=mock_engine,
            ),
            patch("rock_paper_sync.rm_cloud_sync.RmCloudSync"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli.main,
                ["--config", str(config_path), "sync", "--direction", "pull"],
            )

        # Click 8.x mixes stderr into result.output by default.
        output = result.output

        assert "5 edit(s)" in output, f"Expected '5 edit(s)' in output:\n{output}"
        assert "1 tick(s)" in output
        assert "2 strikethrough(s)" in output
        assert "1 insertion(s)" in output
        assert "1 correction(s)" in output
        assert "3 gesture(s) uninterpreted (saved as attachments)" in output

    def test_zero_gestures_no_stderr_lines(self, tmp_path: Path) -> None:
        """Zero gesture counts produce no gesture-related stderr lines."""
        from click.testing import CliRunner

        from rock_paper_sync import cli

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "note.md").write_text("# Test\n")

        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""\
[paths]
state_database = "{tmp_path / "state.db"}"

[[vaults]]
name = "test-vault"
path = "{vault_dir}"

[cloud]
base_url = "http://fake:3000"

[logging]
level = "warning"
file = "{tmp_path / "test.log"}"
""")

        canned_result = PullResult(
            vault_name="test-vault",
            obsidian_path="note.md",
            success=True,
            highlights_added=2,
            ticks=0,
            strikethroughs=0,
            insertions=0,
            corrections=0,
            gestures_uninterpreted=0,
        )
        canned_stats = PullStats(
            files_checked=1,
            files_updated=1,
            total_highlights=2,
        )

        mock_engine = MagicMock()
        mock_engine.pull_vault.return_value = ([canned_result], canned_stats)

        with (
            patch(
                "rock_paper_sync.pull_sync.PullSyncEngine",
                return_value=mock_engine,
            ),
            patch("rock_paper_sync.rm_cloud_sync.RmCloudSync"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli.main,
                ["--config", str(config_path), "sync", "--direction", "pull"],
            )

        output = result.output

        assert "edit(s)" not in output, f"Unexpected gesture line in output:\n{output}"
        assert "uninterpreted" not in output


# ---------------------------------------------------------------------------
# OCR error handling in _ocr_gesture_strokes
# ---------------------------------------------------------------------------


def _make_test_candidate(stroke_id_part2: int = 99) -> CandidateStroke:
    """Build a minimal CandidateStroke for OCR error tests."""
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
            item_id=CrdtId(1, stroke_id_part2),
            left_id=CrdtId(0, 0),
            right_id=CrdtId(0, 0),
            deleted_length=0,
            value=line,
        ),
    )
    stroke = Stroke.from_line_block(block)
    return CandidateStroke(stroke=stroke, page_index=0, redir=0)


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

        result = engine._ocr_gesture_strokes((_make_test_candidate(99),))
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

        with pytest.raises(RuntimeError, match="unexpected bug"):
            engine._ocr_gesture_strokes((_make_test_candidate(98),))

        state.close()


class TestOcrConfidenceThreshold:
    """Fix 3: OCR confidence threshold from config."""

    def test_high_threshold_rejects_low_confidence(self, tmp_path: Path) -> None:
        """threshold=0.95 with confidence=0.9 -> Baked + UNINTERPRETED."""
        from rock_paper_sync.ink.classify import Classification
        from rock_paper_sync.ink.clusters import InkCluster
        from rock_paper_sync.ink.dispositions import (
            Baked,
            Disposition,
            MarkdownEdit,
        )
        from rock_paper_sync.ink.gestures import GestureResult

        state = StateManager(tmp_path / "test.db")
        mock_cloud = MagicMock()

        class ConfidenceOCR:
            def process_image(self, png_bytes: bytes) -> object:
                class Result:
                    text = "replacement"
                    confidence = 0.9

                return Result()

        engine = PullSyncEngine(
            state=state,
            cloud_reader=mock_cloud,
            layout=LayoutConfig(),
            cache_dir=tmp_path / "cache",
            ocr_processor=ConfidenceOCR(),
            ocr_confidence_threshold=0.95,
        )

        # Build a minimal gesture that needs OCR
        cs = _make_test_candidate(80)
        cs2 = _make_test_candidate(81)
        gesture = GestureResult(
            kind="caret_insertion",
            token_ids=(),
            snapshot_span=(10, 10),
            replacement_text=None,
            leftover_strokes=(),
            needs_ocr=True,
            ocr_strokes=(cs2,),
        )

        cluster = InkCluster(
            cluster_id="test_cluster",
            strokes=(cs,),
            bbox=(0, 0, 10, 10),
            page_index=0,
            redir=0,
        )

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        from rock_paper_sync.config import VaultConfig

        vault = VaultConfig(
            name="v",
            path=vault_dir,
            remarkable_folder=None,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
        )

        edits: list[MarkdownEdit] = []
        dispositions: dict[str, Disposition] = {}
        deferred_reasons: list[str] = []

        classification = Classification(kind="drawing", cluster=cluster)

        engine._process_gesture(
            gesture,
            cluster,
            "test_cluster",
            None,  # type: ignore[arg-type]  # word_map not used
            vault,
            "test.md",
            0,
            [],
            edits,
            dispositions,
            deferred_reasons,
            classification,
        )

        # With threshold 0.95 and confidence 0.9, should be Baked (UNINTERPRETED)
        assert "test_cluster" in dispositions
        assert isinstance(dispositions["test_cluster"], Baked), (
            f"Expected Baked, got {type(dispositions['test_cluster']).__name__}"
        )

        state.close()

    def test_low_threshold_accepts_low_confidence(self, tmp_path: Path) -> None:
        """threshold=0.5 with confidence=0.9 -> Consumed."""
        from rock_paper_sync.ink.classify import Classification
        from rock_paper_sync.ink.clusters import InkCluster
        from rock_paper_sync.ink.dispositions import (
            Consumed,
            Disposition,
            MarkdownEdit,
        )
        from rock_paper_sync.ink.gestures import GestureResult

        state = StateManager(tmp_path / "test.db")
        mock_cloud = MagicMock()

        class ConfidenceOCR:
            def process_image(self, png_bytes: bytes) -> object:
                class Result:
                    text = "replacement"
                    confidence = 0.9

                return Result()

        engine = PullSyncEngine(
            state=state,
            cloud_reader=mock_cloud,
            layout=LayoutConfig(),
            cache_dir=tmp_path / "cache",
            ocr_processor=ConfidenceOCR(),
            ocr_confidence_threshold=0.5,
        )

        cs = _make_test_candidate(82)
        cs2 = _make_test_candidate(83)
        gesture = GestureResult(
            kind="caret_insertion",
            token_ids=(),
            snapshot_span=(10, 10),
            replacement_text=None,
            leftover_strokes=(),
            needs_ocr=True,
            ocr_strokes=(cs2,),
        )

        cluster = InkCluster(
            cluster_id="test_cluster2",
            strokes=(cs,),
            bbox=(0, 0, 10, 10),
            page_index=0,
            redir=0,
        )

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        from rock_paper_sync.config import VaultConfig

        vault = VaultConfig(
            name="v",
            path=vault_dir,
            remarkable_folder=None,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
        )

        edits: list[MarkdownEdit] = []
        dispositions: dict[str, Disposition] = {}
        deferred_reasons: list[str] = []

        classification = Classification(kind="drawing", cluster=cluster)

        engine._process_gesture(
            gesture,
            cluster,
            "test_cluster2",
            None,  # type: ignore[arg-type]
            vault,
            "test.md",
            0,
            [],
            edits,
            dispositions,
            deferred_reasons,
            classification,
        )

        # With threshold 0.5 and confidence 0.9, should be Consumed
        assert "test_cluster2" in dispositions
        assert isinstance(dispositions["test_cluster2"], Consumed), (
            f"Expected Consumed, got {type(dispositions['test_cluster2']).__name__}"
        )
