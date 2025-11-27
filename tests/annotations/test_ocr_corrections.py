"""Tests for OCR correction detection (Phase 4).

Tests the simple, focused OCR correction detection system for training data
collection. Tests cover:
- OCRCorrection dataclass
- StrokeHandler.detect_ocr_corrections()
- detect_ocr_corrections_for_file() coordinator
"""

import pytest
from pathlib import Path

from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.annotations.core.data_types import RenderConfig, OCRCorrection
from rock_paper_sync.annotations.ocr_corrections import (
    detect_ocr_corrections_for_file,
    parse_paragraphs,
)
from rock_paper_sync.annotations.common.snapshots import SnapshotStore, ContentStore
from rock_paper_sync.state import StateManager


class TestOCRCorrection:
    """Tests for OCRCorrection dataclass."""

    def test_ocr_correction_fields(self):
        """Test OCRCorrection has correct fields."""
        correction = OCRCorrection(
            image_hash="abc123",
            original_text="helo world",
            corrected_text="hello world",
            paragraph_context="This is helo world in context.",
            document_id="vault/path/to/file.md",
            annotation_id="anno-456",
        )

        assert correction.image_hash == "abc123"
        assert correction.original_text == "helo world"
        assert correction.corrected_text == "hello world"
        assert correction.paragraph_context == "This is helo world in context."
        assert correction.document_id == "vault/path/to/file.md"
        assert correction.annotation_id == "anno-456"


class TestStrokeHandlerDetection:
    """Tests for StrokeHandler.detect_ocr_corrections()."""

    def test_detect_correction_comment_style(self):
        """Test detecting correction with comment style OCR."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="comment")

        old_paragraph = "Text before <!-- OCR: helo world --> text after."
        new_paragraph = "Text before <!-- OCR: hello world --> text after."

        correction = handler.detect_ocr_corrections(
            vault_name="MyVault",
            file_path="notes/example.md",
            paragraph_index=0,
            old_paragraph=old_paragraph,
            new_paragraph=new_paragraph,
            annotation_id="anno-123",
            image_hash="img-hash-456",
            config=config,
        )

        assert correction is not None
        assert correction.original_text == "helo world"
        assert correction.corrected_text == "hello world"
        assert correction.paragraph_context == new_paragraph
        assert correction.document_id == "MyVault/notes/example.md"
        assert correction.annotation_id == "anno-123"
        assert correction.image_hash == "img-hash-456"

    def test_detect_correction_footnote_style(self):
        """Test detecting correction with footnote style OCR."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="footnote")

        old_paragraph = "Text before helo world[^1]\n\n[^1]: OCR confidence 0.95"
        new_paragraph = "Text before hello world[^1]\n\n[^1]: OCR confidence 0.95"

        correction = handler.detect_ocr_corrections(
            vault_name="Vault",
            file_path="file.md",
            paragraph_index=1,
            old_paragraph=old_paragraph,
            new_paragraph=new_paragraph,
            annotation_id="anno-789",
            image_hash="img-abc",
            config=config,
        )

        assert correction is not None
        # Footnote pattern captures text before marker
        assert "helo world" in correction.original_text
        assert "hello world" in correction.corrected_text

    def test_no_correction_when_unchanged(self):
        """Test no correction when OCR text unchanged."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="comment")

        paragraph = "Text with <!-- OCR: same text --> here."

        correction = handler.detect_ocr_corrections(
            vault_name="Vault",
            file_path="file.md",
            paragraph_index=0,
            old_paragraph=paragraph,
            new_paragraph=paragraph,
            annotation_id="anno-123",
            image_hash="img-456",
            config=config,
        )

        assert correction is None

    def test_no_correction_when_no_ocr(self):
        """Test no correction when no OCR text present."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="comment")

        old_paragraph = "Plain text without OCR."
        new_paragraph = "Plain text without OCR."

        correction = handler.detect_ocr_corrections(
            vault_name="Vault",
            file_path="file.md",
            paragraph_index=0,
            old_paragraph=old_paragraph,
            new_paragraph=new_paragraph,
            annotation_id="anno-123",
            image_hash="img-456",
            config=config,
        )

        assert correction is None

    def test_detect_multiple_changes(self):
        """Test detecting significant text changes."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="comment")

        old_paragraph = "<!-- OCR: The quck brown fox -->"
        new_paragraph = "<!-- OCR: The quick brown fox -->"

        correction = handler.detect_ocr_corrections(
            vault_name="Vault",
            file_path="file.md",
            paragraph_index=0,
            old_paragraph=old_paragraph,
            new_paragraph=new_paragraph,
            annotation_id="anno",
            image_hash="img",
            config=config,
        )

        assert correction is not None
        assert correction.original_text == "The quck brown fox"
        assert correction.corrected_text == "The quick brown fox"


class TestParseParagraphs:
    """Tests for parse_paragraphs() helper."""

    def test_parse_simple_paragraphs(self):
        """Test parsing markdown with blank line separators."""
        markdown = """First paragraph.

Second paragraph here.

Third paragraph."""

        paragraphs = parse_paragraphs(markdown)

        assert len(paragraphs) == 3
        assert paragraphs[0] == "First paragraph."
        assert paragraphs[1] == "Second paragraph here."
        assert paragraphs[2] == "Third paragraph."

    def test_parse_multiline_paragraphs(self):
        """Test paragraphs with multiple lines."""
        markdown = """First line of first paragraph.
Second line of first paragraph.

Second paragraph
also has
multiple lines."""

        paragraphs = parse_paragraphs(markdown)

        assert len(paragraphs) == 2
        assert "First line" in paragraphs[0]
        assert "Second line" in paragraphs[0]
        assert "Second paragraph" in paragraphs[1]
        assert "multiple lines" in paragraphs[1]

    def test_parse_single_paragraph(self):
        """Test single paragraph."""
        markdown = "Just one paragraph."
        paragraphs = parse_paragraphs(markdown)

        assert len(paragraphs) == 1
        assert paragraphs[0] == "Just one paragraph."

    def test_parse_empty_markdown(self):
        """Test empty markdown."""
        paragraphs = parse_paragraphs("")
        assert len(paragraphs) == 0

    def test_parse_multiple_blank_lines(self):
        """Test handling multiple consecutive blank lines."""
        markdown = """Paragraph one.


Paragraph two."""

        paragraphs = parse_paragraphs(markdown)

        assert len(paragraphs) == 2
        assert paragraphs[0] == "Paragraph one."
        assert paragraphs[1] == "Paragraph two."


class TestCoordinatorFunction:
    """Tests for detect_ocr_corrections_for_file() coordinator."""

    def test_detect_single_correction(self, tmp_path):
        """Test detecting single OCR correction in file."""
        # Setup snapshot store
        content_store = ContentStore(tmp_path / "snapshots")
        state_manager = StateManager(tmp_path / "state.db")
        snapshot_store = SnapshotStore(state_manager.conn, content_store)

        # Create snapshot of old paragraph
        vault_name = "TestVault"
        file_path = "test.md"
        old_paragraph = "Text with <!-- OCR: helo --> here."
        snapshot_store.snapshot_block(
            vault_name, file_path, 0, old_paragraph, ["stroke"]
        )

        # Current markdown with corrected OCR
        current_markdown = "Text with <!-- OCR: hello --> here."

        # Stroke metadata
        stroke_metadata = {
            0: [{"annotation_id": "anno-123", "image_hash": "img-hash"}]
        }

        # Detect corrections
        corrections = detect_ocr_corrections_for_file(
            vault_name=vault_name,
            file_path=file_path,
            current_markdown=current_markdown,
            snapshot_store=snapshot_store,
            stroke_metadata=stroke_metadata,
            config=RenderConfig(stroke_style="comment"),
        )

        assert len(corrections) == 1
        assert corrections[0].original_text == "helo"
        assert corrections[0].corrected_text == "hello"
        assert corrections[0].annotation_id == "anno-123"
        assert corrections[0].image_hash == "img-hash"

    def test_detect_multiple_corrections(self, tmp_path):
        """Test detecting multiple corrections in same file."""
        content_store = ContentStore(tmp_path / "snapshots")
        state_manager = StateManager(tmp_path / "state.db")
        snapshot_store = SnapshotStore(state_manager.conn, content_store)

        vault_name = "Vault"
        file_path = "notes.md"

        # Snapshot first paragraph
        old_para_0 = "First <!-- OCR: wrng --> paragraph."
        snapshot_store.snapshot_block(vault_name, file_path, 0, old_para_0, ["stroke"])

        # Snapshot third paragraph
        old_para_2 = "Third <!-- OCR: incorect --> paragraph."
        snapshot_store.snapshot_block(vault_name, file_path, 2, old_para_2, ["stroke"])

        # Current markdown with corrections
        current_markdown = """First <!-- OCR: wrong --> paragraph.

Second paragraph unchanged.

Third <!-- OCR: incorrect --> paragraph."""

        stroke_metadata = {
            0: [{"annotation_id": "anno-1", "image_hash": "hash-1"}],
            2: [{"annotation_id": "anno-2", "image_hash": "hash-2"}],
        }

        corrections = detect_ocr_corrections_for_file(
            vault_name=vault_name,
            file_path=file_path,
            current_markdown=current_markdown,
            snapshot_store=snapshot_store,
            stroke_metadata=stroke_metadata,
            config=RenderConfig(stroke_style="comment"),
        )

        assert len(corrections) == 2
        # Check first correction
        assert corrections[0].original_text == "wrng"
        assert corrections[0].corrected_text == "wrong"
        # Check second correction
        assert corrections[1].original_text == "incorect"
        assert corrections[1].corrected_text == "incorrect"

    def test_no_corrections_when_unchanged(self, tmp_path):
        """Test no corrections detected when text unchanged."""
        content_store = ContentStore(tmp_path / "snapshots")
        state_manager = StateManager(tmp_path / "state.db")
        snapshot_store = SnapshotStore(state_manager.conn, content_store)

        vault_name = "Vault"
        file_path = "test.md"
        paragraph = "Text with <!-- OCR: unchanged --> here."

        snapshot_store.snapshot_block(vault_name, file_path, 0, paragraph, ["stroke"])

        stroke_metadata = {0: [{"annotation_id": "anno", "image_hash": "hash"}]}

        corrections = detect_ocr_corrections_for_file(
            vault_name=vault_name,
            file_path=file_path,
            current_markdown=paragraph,
            snapshot_store=snapshot_store,
            stroke_metadata=stroke_metadata,
        )

        assert len(corrections) == 0

    def test_no_snapshot_available(self, tmp_path):
        """Test graceful handling when no snapshot available."""
        content_store = ContentStore(tmp_path / "snapshots")
        state_manager = StateManager(tmp_path / "state.db")
        snapshot_store = SnapshotStore(state_manager.conn, content_store)

        # No snapshot created
        current_markdown = "Text with <!-- OCR: hello --> here."
        stroke_metadata = {0: [{"annotation_id": "anno", "image_hash": "hash"}]}

        corrections = detect_ocr_corrections_for_file(
            vault_name="Vault",
            file_path="test.md",
            current_markdown=current_markdown,
            snapshot_store=snapshot_store,
            stroke_metadata=stroke_metadata,
        )

        # Should return empty list, not error
        assert len(corrections) == 0

    def test_multiple_strokes_per_paragraph(self, tmp_path):
        """Test handling multiple stroke annotations in same paragraph."""
        content_store = ContentStore(tmp_path / "snapshots")
        state_manager = StateManager(tmp_path / "state.db")
        snapshot_store = SnapshotStore(state_manager.conn, content_store)

        vault_name = "Vault"
        file_path = "test.md"

        # Note: Current implementation assumes one OCR per paragraph
        # This test documents that limitation
        old_paragraph = "First <!-- OCR: helo --> and second <!-- OCR: wrld --> text."
        snapshot_store.snapshot_block(
            vault_name, file_path, 0, old_paragraph, ["stroke"]
        )

        current_markdown = (
            "First <!-- OCR: hello --> and second <!-- OCR: world --> text."
        )

        stroke_metadata = {
            0: [
                {"annotation_id": "anno-1", "image_hash": "hash-1"},
                {"annotation_id": "anno-2", "image_hash": "hash-2"},
            ]
        }

        corrections = detect_ocr_corrections_for_file(
            vault_name=vault_name,
            file_path=file_path,
            current_markdown=current_markdown,
            snapshot_store=snapshot_store,
            stroke_metadata=stroke_metadata,
        )

        # Current implementation only detects first OCR change
        # Both strokes will report the same correction (first OCR)
        # This is a known limitation documented in the plan
        assert len(corrections) == 2  # One per stroke annotation
        assert all(c.original_text == "helo" for c in corrections)
