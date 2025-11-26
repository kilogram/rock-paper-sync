"""Tests for recording and replaying highlight annotations.

This test suite records actual highlight annotations from a device
and verifies they are captured and replayed correctly.

Recording Usage:
    uv run pytest tests/record_replay/test_highlights.py \
        --device-mode=online --online
    
    When prompted, use the highlight tool to highlight text on your device.

Replaying:
    uv run pytest tests/record_replay/test_highlights.py \
        --device-mode=offline
"""

import io
import pytest

from rmscene import read_blocks
from rock_paper_sync.annotations import read_annotations, AnnotationType


@pytest.mark.device
@pytest.mark.online_only
class TestHighlightsRecording:
    """Record highlight annotations from a physical reMarkable device."""

    def test_record_highlights(self, device, workspace, fixtures_dir):
        """Record highlight annotations on a physical device.
        
        User Instructions:
        1. Use the highlight tool to highlight text on the document
        2. Try multiple colors if supported
        3. Create overlapping highlights
        4. Press Enter when done
        """
        test_id = "highlights"
        
        # Load fixture document
        fixture_doc = fixtures_dir / "test_highlights.md"
        workspace.test_doc.write_text(fixture_doc.read_text())
        
        # Start recording
        device.start_test(
            test_id,
            description="Highlight annotations with multiple colors"
        )
        
        try:
            # Upload document
            doc_uuid = device.upload_document(workspace.test_doc)
            
            # Wait for user to annotate
            state = device.wait_for_annotations(doc_uuid)
            assert state.has_annotations, "No annotations captured"
            
            # Finalize recording
            device.end_test(test_id, success=True)
        except Exception as e:
            device.end_test(test_id, success=False)
            raise


@pytest.mark.offline_only
class TestHighlightsReplay:
    """Replay recorded highlight annotations without a device."""

    def test_replay_highlights(self, offline_device, workspace, fixtures_dir, testdata_store):
        """Replay highlight annotations from testdata."""
        test_id = "highlights"
        
        # Load fixture document
        fixture_doc = fixtures_dir / "test_highlights.md"
        workspace.test_doc.write_text(fixture_doc.read_text())
        
        # Load testdata
        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(
                f"Testdata '{test_id}' not available. "
                f"Run with --device-mode=online --online to record."
            )
        
        # Replay
        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)
        
        # Verify annotations exist
        assert state.has_annotations, "No annotations in replayed testdata"
        assert len(state.rm_files) > 0, "No .rm files in testdata"

    def test_highlights_contain_valid_blocks(self, offline_device, workspace, fixtures_dir):
        """Verify highlight .rm files contain valid rmscene blocks."""
        test_id = "highlights"
        
        fixture_doc = fixtures_dir / "test_highlights.md"
        workspace.test_doc.write_text(fixture_doc.read_text())
        
        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")
        
        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)
        
        # Verify .rm files are valid
        for page_uuid, rm_data in state.rm_files.items():
            blocks = list(read_blocks(io.BytesIO(rm_data)))
            assert len(blocks) > 0, f"No blocks in {page_uuid}.rm"

    def test_highlights_extracted_as_annotations(self, offline_device, workspace, fixtures_dir):
        """Verify highlights are extracted as annotation objects."""
        test_id = "highlights"
        
        fixture_doc = fixtures_dir / "test_highlights.md"
        workspace.test_doc.write_text(fixture_doc.read_text())
        
        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")
        
        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)
        
        # Extract annotations
        all_annotations = []
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            all_annotations.extend(annotations)
        
        # Verify we have some annotations
        assert len(all_annotations) > 0, "No annotations extracted"
        
        # Count highlights
        highlight_count = sum(
            1 for a in all_annotations 
            if a.type == AnnotationType.HIGHLIGHT
        )
        
        # Should have at least one highlight annotation
        assert highlight_count >= 1, f"Expected highlights, found {highlight_count}"
