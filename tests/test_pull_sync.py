"""Tests for pull sync engine (M5 bidirectional sync).

Tests the pull direction of sync: reMarkable → Obsidian.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rock_paper_sync.annotation_renderer import RenderConfig
from rock_paper_sync.annotation_sync_helper import AnnotationChange
from rock_paper_sync.config import VaultConfig
from rock_paper_sync.pull_sync import PullResult, PullStats, PullSyncEngine
from rock_paper_sync.state import StateManager


class TestPullResult:
    """Tests for PullResult dataclass."""

    def test_success_result(self) -> None:
        """Test successful pull result."""
        result = PullResult(
            vault_name="test",
            obsidian_path="notes/test.md",
            success=True,
            highlights_added=3,
            strokes_added=1,
        )
        assert result.success is True
        assert result.highlights_added == 3
        assert result.strokes_added == 1
        assert result.orphans_count == 0
        assert result.error is None

    def test_error_result(self) -> None:
        """Test error pull result."""
        result = PullResult(
            vault_name="test",
            obsidian_path="notes/test.md",
            success=False,
            error="File not found",
        )
        assert result.success is False
        assert result.error == "File not found"


class TestPullStats:
    """Tests for PullStats dataclass."""

    def test_default_stats(self) -> None:
        """Test default statistics values."""
        stats = PullStats()
        assert stats.files_checked == 0
        assert stats.files_updated == 0
        assert stats.files_skipped == 0
        assert stats.files_errored == 0
        assert stats.total_highlights == 0
        assert stats.total_strokes == 0
        assert stats.total_orphans == 0


class TestPullSyncEngineInit:
    """Tests for PullSyncEngine initialization."""

    def test_init_with_defaults(self, tmp_path: Path) -> None:
        """Test engine initialization with default config."""
        state_db = tmp_path / "state.db"
        state = StateManager(state_db)
        cloud_sync = MagicMock()
        annotation_helper = MagicMock()
        cache_dir = tmp_path / "cache"

        engine = PullSyncEngine(
            state=state,
            cloud_sync=cloud_sync,
            annotation_helper=annotation_helper,
            cache_dir=cache_dir,
        )

        assert engine.state is state
        assert engine.cloud_sync is cloud_sync
        assert engine.annotation_helper is annotation_helper
        assert engine.cache_dir == cache_dir
        assert engine.render_config is not None

        state.close()

    def test_init_with_custom_config(self, tmp_path: Path) -> None:
        """Test engine initialization with custom render config."""
        state = StateManager(tmp_path / "state.db")
        cloud_sync = MagicMock()
        annotation_helper = MagicMock()
        cache_dir = tmp_path / "cache"
        config = RenderConfig(stroke_style="inline")

        engine = PullSyncEngine(
            state=state,
            cloud_sync=cloud_sync,
            annotation_helper=annotation_helper,
            cache_dir=cache_dir,
            render_config=config,
        )

        assert engine.render_config.stroke_style == "inline"

        state.close()


class TestPullFileBasics:
    """Tests for basic pull_file operations."""

    @pytest.fixture
    def engine_setup(self, tmp_path: Path):
        """Set up a basic engine for testing."""
        state = StateManager(tmp_path / "state.db")
        cloud_sync = MagicMock()
        annotation_helper = MagicMock()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        engine = PullSyncEngine(
            state=state,
            cloud_sync=cloud_sync,
            annotation_helper=annotation_helper,
            cache_dir=cache_dir,
        )

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        yield engine, state, cloud_sync, vault_path

        state.close()

    def test_pull_file_not_found(self, engine_setup) -> None:
        """Test pulling for a file that doesn't exist."""
        engine, state, cloud_sync, vault_path = engine_setup

        change = AnnotationChange(
            vault_name="test",
            obsidian_path="nonexistent.md",
            remarkable_uuid="uuid-123",
            change_type="modified",
            current_annotation_hash="hash123",
            previous_annotation_hash=None,
        )

        result = engine.pull_file(change, vault_path)

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_pull_file_no_pages(self, engine_setup) -> None:
        """Test pulling when document has no pages."""
        engine, state, cloud_sync, vault_path = engine_setup

        # Create the file
        (vault_path / "test.md").write_text("# Test")

        # Mock: no pages exist
        cloud_sync.get_existing_page_uuids.return_value = []

        change = AnnotationChange(
            vault_name="test",
            obsidian_path="test.md",
            remarkable_uuid="uuid-123",
            change_type="new",
            current_annotation_hash="hash123",
            previous_annotation_hash=None,
        )

        result = engine.pull_file(change, vault_path)

        assert result.success is True
        assert result.highlights_added == 0
        assert result.strokes_added == 0

    def test_pull_file_no_rm_files(self, engine_setup) -> None:
        """Test pulling when no valid .rm files are downloaded."""
        engine, state, cloud_sync, vault_path = engine_setup

        # Create the file
        (vault_path / "test.md").write_text("# Test")

        # Mock: pages exist but no valid rm files
        cloud_sync.get_existing_page_uuids.return_value = ["page-1"]
        cloud_sync.download_page_rm_files.return_value = [None]

        change = AnnotationChange(
            vault_name="test",
            obsidian_path="test.md",
            remarkable_uuid="uuid-123",
            change_type="modified",
            current_annotation_hash="hash123",
            previous_annotation_hash=None,
        )

        result = engine.pull_file(change, vault_path)

        assert result.success is True
        assert result.highlights_added == 0

    def test_pull_file_dry_run(self, engine_setup) -> None:
        """Test dry run doesn't write files."""
        engine, state, cloud_sync, vault_path = engine_setup

        # Create the file
        original_content = "# Test\n\nOriginal content."
        file_path = vault_path / "test.md"
        file_path.write_text(original_content)

        # Mock: no pages (simplest case)
        cloud_sync.get_existing_page_uuids.return_value = []

        change = AnnotationChange(
            vault_name="test",
            obsidian_path="test.md",
            remarkable_uuid="uuid-123",
            change_type="new",
            current_annotation_hash="hash123",
            previous_annotation_hash=None,
        )

        result = engine.pull_file(change, vault_path, dry_run=True)

        assert result.success is True
        # File should be unchanged
        assert file_path.read_text() == original_content


class TestPullVault:
    """Tests for pull_vault operations."""

    @pytest.fixture
    def vault_setup(self, tmp_path: Path):
        """Set up a vault for testing."""
        state = StateManager(tmp_path / "state.db")
        cloud_sync = MagicMock()
        annotation_helper = MagicMock()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        engine = PullSyncEngine(
            state=state,
            cloud_sync=cloud_sync,
            annotation_helper=annotation_helper,
            cache_dir=cache_dir,
        )

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        vault_config = VaultConfig(
            name="test-vault",
            path=vault_path,
            remarkable_folder=None,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
        )

        yield engine, state, annotation_helper, vault_path, vault_config

        state.close()

    def test_pull_vault_no_changes(self, vault_setup) -> None:
        """Test pulling vault with no annotation changes."""
        engine, state, annotation_helper, vault_path, vault_config = vault_setup

        # Mock: no changes detected
        annotation_helper.detect_annotation_changes.return_value = []

        results, stats = engine.pull_vault(vault_config)

        assert len(results) == 0
        assert stats.files_updated == 0

    def test_pull_vault_with_changes(self, vault_setup) -> None:
        """Test pulling vault with annotation changes."""
        engine, state, annotation_helper, vault_path, vault_config = vault_setup

        # Create test files
        (vault_path / "file1.md").write_text("# File 1")
        (vault_path / "file2.md").write_text("# File 2")

        # Mock: changes detected
        changes = [
            AnnotationChange(
                vault_name="test-vault",
                obsidian_path="file1.md",
                remarkable_uuid="uuid-1",
                change_type="new",
                current_annotation_hash="hash1",
                previous_annotation_hash=None,
            ),
            AnnotationChange(
                vault_name="test-vault",
                obsidian_path="file2.md",
                remarkable_uuid="uuid-2",
                change_type="modified",
                current_annotation_hash="hash2",
                previous_annotation_hash="hash2-old",
            ),
        ]
        annotation_helper.detect_annotation_changes.return_value = changes

        # Mock: no pages for simplicity
        engine.cloud_sync.get_existing_page_uuids.return_value = []

        results, stats = engine.pull_vault(vault_config)

        assert len(results) == 2
        assert all(r.success for r in results)


class TestReanchorAnnotations:
    """Tests for annotation reanchoring logic."""

    @pytest.fixture
    def engine(self, tmp_path: Path) -> PullSyncEngine:
        """Create engine for reanchor testing."""
        state = StateManager(tmp_path / "state.db")
        engine = PullSyncEngine(
            state=state,
            cloud_sync=MagicMock(),
            annotation_helper=MagicMock(),
            cache_dir=tmp_path / "cache",
        )
        yield engine
        state.close()

    def test_reanchor_no_annotations(self, engine: PullSyncEngine) -> None:
        """Test reanchoring with no annotations."""
        migrated, orphaned = engine._reanchor_annotations([], "New content here.")

        assert len(migrated) == 0
        assert len(orphaned) == 0

    def test_reanchor_annotation_without_context(self, engine: PullSyncEngine) -> None:
        """Test annotation without anchor context becomes orphan."""
        from rock_paper_sync.annotations.document_model import DocumentAnnotation

        annotation = DocumentAnnotation(
            annotation_id="test-1",
            annotation_type="highlight",
            source_page_idx=0,
            anchor_context=None,
        )

        migrated, orphaned = engine._reanchor_annotations([annotation], "Some content.")

        assert len(migrated) == 0
        assert len(orphaned) == 1
        assert orphaned[0].annotation_id == "test-1"

    def test_reanchor_finds_text(self, engine: PullSyncEngine) -> None:
        """Test reanchoring finds text in new content."""
        from rock_paper_sync.annotations.document_model import (
            AnchorContext,
            DocumentAnnotation,
        )

        original_content = "The quick brown fox."
        new_content = "The quick brown fox jumps."

        # Create anchor for "quick" - it exists in both texts
        anchor = AnchorContext.from_text_span(original_content, 4, 9)  # "quick"
        annotation = DocumentAnnotation(
            annotation_id="test-1",
            annotation_type="highlight",
            source_page_idx=0,
            anchor_context=anchor,
        )

        migrated, orphaned = engine._reanchor_annotations([annotation], new_content)

        # "quick" exists in both texts, should be migrated
        assert len(migrated) == 1
        assert len(orphaned) == 0
        assert migrated[0].annotation_id == "test-1"

    def test_reanchor_orphans_deleted_text(self, engine: PullSyncEngine) -> None:
        """Test reanchoring orphans annotation when text is deleted."""
        from rock_paper_sync.annotations.document_model import (
            AnchorContext,
            DocumentAnnotation,
        )

        original_content = "The uniquephrase here."
        new_content = "Completely different content."

        # Create anchor for text that won't be found in new content
        anchor = AnchorContext.from_text_span(original_content, 4, 16)  # "uniquephrase"
        annotation = DocumentAnnotation(
            annotation_id="test-1",
            annotation_type="highlight",
            source_page_idx=0,
            anchor_context=anchor,
        )

        migrated, orphaned = engine._reanchor_annotations([annotation], new_content)

        # Text doesn't exist in new content, should be orphaned
        assert len(migrated) == 0
        assert len(orphaned) == 1


class TestRecordOrphans:
    """Tests for orphan recording."""

    def test_record_orphans_clears_previous(self, tmp_path: Path) -> None:
        """Test that recording orphans clears previous orphans."""
        from rock_paper_sync.annotations.document_model import (
            AnchorContext,
            DocumentAnnotation,
        )

        state = StateManager(tmp_path / "state.db")
        engine = PullSyncEngine(
            state=state,
            cloud_sync=MagicMock(),
            annotation_helper=MagicMock(),
            cache_dir=tmp_path / "cache",
        )

        change = AnnotationChange(
            vault_name="test",
            obsidian_path="test.md",
            remarkable_uuid="uuid-123",
            change_type="modified",
            current_annotation_hash="hash123",
            previous_annotation_hash=None,
        )

        # Create orphan with anchor context
        content = "Some text here."
        anchor = AnchorContext.from_text_span(content, 0, 4)
        orphan = DocumentAnnotation(
            annotation_id="orphan-1",
            annotation_type="highlight",
            source_page_idx=0,
            anchor_context=anchor,
        )

        # Record orphans
        engine._record_orphans(change, [orphan])

        # Verify orphan was recorded
        orphans = state.get_orphaned_annotations("test", "test.md")
        assert len(orphans) == 1
        assert orphans[0].annotation_id == "orphan-1"

        # Record different orphans
        orphan2 = DocumentAnnotation(
            annotation_id="orphan-2",
            annotation_type="stroke",
            source_page_idx=0,
            anchor_context=anchor,
        )
        engine._record_orphans(change, [orphan2])

        # Verify old orphan was cleared and new one recorded
        orphans = state.get_orphaned_annotations("test", "test.md")
        assert len(orphans) == 1
        assert orphans[0].annotation_id == "orphan-2"

        state.close()


class TestDetectChanges:
    """Tests for change detection wrapper."""

    def test_detect_changes_delegates(self, tmp_path: Path) -> None:
        """Test that detect_changes delegates to annotation_helper."""
        state = StateManager(tmp_path / "state.db")
        annotation_helper = MagicMock()
        expected_changes = [
            AnnotationChange(
                vault_name="test",
                obsidian_path="test.md",
                remarkable_uuid="uuid-123",
                change_type="new",
                current_annotation_hash="hash123",
                previous_annotation_hash=None,
            )
        ]
        annotation_helper.detect_annotation_changes.return_value = expected_changes

        engine = PullSyncEngine(
            state=state,
            cloud_sync=MagicMock(),
            annotation_helper=annotation_helper,
            cache_dir=tmp_path / "cache",
        )

        changes = engine.detect_changes("test")

        annotation_helper.detect_annotation_changes.assert_called_once_with("test")
        assert changes == expected_changes

        state.close()


class TestOrphanRecovery:
    """Tests for orphan recovery workflow (P0 #2 from TEST_TODO.md)."""

    @pytest.fixture
    def recovery_setup(self, tmp_path: Path):
        """Set up environment for orphan recovery testing."""

        state = StateManager(tmp_path / "state.db")
        cloud_sync = MagicMock()
        annotation_helper = MagicMock()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        engine = PullSyncEngine(
            state=state,
            cloud_sync=cloud_sync,
            annotation_helper=annotation_helper,
            cache_dir=cache_dir,
        )

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        vault_config = VaultConfig(
            name="test-vault",
            path=vault_path,
            remarkable_folder=None,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
        )

        yield engine, state, cloud_sync, vault_path, vault_config

        state.close()

    def test_recovery_no_orphans(self, recovery_setup) -> None:
        """Test recovery when there are no orphans."""
        engine, state, cloud_sync, vault_path, vault_config = recovery_setup

        recovered = engine.attempt_orphan_recovery(vault_config)

        assert len(recovered) == 0

    def test_recovery_no_synced_files(self, recovery_setup) -> None:
        """Test recovery when vault has no synced files."""
        engine, state, cloud_sync, vault_path, vault_config = recovery_setup

        # Create orphan but no synced file record
        from rock_paper_sync.state import OrphanedAnnotation

        orphan = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_id="orphan-1",
            annotation_type="highlight",
            original_anchor_text="important text",
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan)

        recovered = engine.attempt_orphan_recovery(vault_config)

        # No synced files = nothing to recover
        assert len(recovered) == 0

    def test_recovery_file_not_found(self, recovery_setup) -> None:
        """Test recovery when file has been deleted."""
        engine, state, cloud_sync, vault_path, vault_config = recovery_setup
        from rock_paper_sync.state import OrphanedAnnotation, SyncRecord

        # Add synced file record but don't create the actual file
        sync_record = SyncRecord(
            vault_name="test-vault",
            obsidian_path="deleted.md",
            remarkable_uuid="uuid-123",
            content_hash="hash123",
            last_sync_time=1000,
            page_count=1,
            status="synced",
        )
        state.update_file_state(sync_record)

        # Add orphan
        orphan = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="deleted.md",
            annotation_id="orphan-1",
            annotation_type="highlight",
            original_anchor_text="important text",
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan)

        recovered = engine.attempt_orphan_recovery(vault_config)

        # File doesn't exist, can't recover
        assert len(recovered) == 0

    def test_recovery_text_not_in_content(self, recovery_setup) -> None:
        """Test recovery when orphan text is still not in content."""
        engine, state, cloud_sync, vault_path, vault_config = recovery_setup
        from rock_paper_sync.state import OrphanedAnnotation, SyncRecord

        # Create file without the orphan's text
        file_path = vault_path / "test.md"
        file_path.write_text("# Test\n\nSome completely different content.")

        # Add synced file record
        sync_record = SyncRecord(
            vault_name="test-vault",
            obsidian_path="test.md",
            remarkable_uuid="uuid-123",
            content_hash="hash123",
            last_sync_time=1000,
            page_count=1,
            status="synced",
        )
        state.update_file_state(sync_record)

        # Add orphan with text not in the file
        orphan = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_id="orphan-1",
            annotation_type="highlight",
            original_anchor_text="important text that was deleted",
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan)

        recovered = engine.attempt_orphan_recovery(vault_config)

        # Text not found, no recovery attempt
        assert len(recovered) == 0

    def test_recovery_dry_run(self, recovery_setup) -> None:
        """Test dry run mode reports but doesn't modify."""
        engine, state, cloud_sync, vault_path, vault_config = recovery_setup
        from rock_paper_sync.state import OrphanedAnnotation, SyncRecord

        # Create file WITH the orphan's text (user restored it)
        file_path = vault_path / "test.md"
        file_path.write_text("# Test\n\nThis is the important text here.")

        # Add synced file record
        sync_record = SyncRecord(
            vault_name="test-vault",
            obsidian_path="test.md",
            remarkable_uuid="uuid-123",
            content_hash="hash123",
            last_sync_time=1000,
            page_count=1,
            status="synced",
        )
        state.update_file_state(sync_record)

        # Add orphan with text that IS in the file
        orphan = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_id="orphan-1",
            annotation_type="highlight",
            original_anchor_text="important text",
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan)

        # Dry run should detect but not attempt recovery
        recovered = engine.attempt_orphan_recovery(vault_config, dry_run=True)

        # Reports found recoverable orphan
        assert len(recovered) == 1
        assert recovered[0][0] == "test.md"
        assert recovered[0][1] == "orphan-1"

        # Orphan should still be in DB (not actually recovered)
        orphans = state.get_orphaned_annotations("test-vault", "test.md")
        assert len(orphans) == 1

    def test_recovery_successful(self, recovery_setup) -> None:
        """Test successful orphan recovery when text is restored."""
        engine, state, cloud_sync, vault_path, vault_config = recovery_setup
        from rock_paper_sync.annotations.document_model import (
            AnchorContext,
            DocumentAnnotation,
            DocumentModel,
        )
        from rock_paper_sync.state import OrphanedAnnotation, SyncRecord

        # Create file WITH the orphan's text (user restored it)
        content = "# Test\n\nThis is the important text here."
        file_path = vault_path / "test.md"
        file_path.write_text(content)

        # Add synced file record
        sync_record = SyncRecord(
            vault_name="test-vault",
            obsidian_path="test.md",
            remarkable_uuid="uuid-123",
            content_hash="hash123",
            last_sync_time=1000,
            page_count=1,
            status="synced",
        )
        state.update_file_state(sync_record)

        # Add orphan with text that IS in the file
        orphan = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_id="orphan-1",
            annotation_type="highlight",
            original_anchor_text="important text",
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan)

        # Mock cloud_sync to return page UUIDs and rm files
        cloud_sync.get_existing_page_uuids.return_value = ["page-1"]

        # Create a mock rm file path
        rm_file_path = vault_path / "mock.rm"
        rm_file_path.write_bytes(b"mock")
        cloud_sync.download_page_rm_files.return_value = [rm_file_path]

        # Mock DocumentModel.from_rm_files to return a model with the annotation
        # that can be reanchored (text exists in new content)
        anchor = AnchorContext.from_text_span(content, 22, 36)  # "important text"
        annotation = DocumentAnnotation(
            annotation_id="orphan-1",
            annotation_type="highlight",
            source_page_idx=0,
            anchor_context=anchor,
        )
        mock_model = DocumentModel(
            paragraphs=[],
            annotations=[annotation],
            full_text=content,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "rock_paper_sync.pull_sync.DocumentModel.from_rm_files",
                lambda *args, **kwargs: mock_model,
            )

            recovered = engine.attempt_orphan_recovery(vault_config)

        # Should have recovered the orphan
        assert len(recovered) == 1
        assert recovered[0][0] == "test.md"
        assert recovered[0][1] == "orphan-1"

        # Orphan should be cleared from DB (annotation was reanchored)
        orphans = state.get_orphaned_annotations("test-vault", "test.md")
        assert len(orphans) == 0

    def test_recovery_partial_success(self, recovery_setup) -> None:
        """Test recovery when some orphans recover but others don't."""
        engine, state, cloud_sync, vault_path, vault_config = recovery_setup
        from rock_paper_sync.annotations.document_model import (
            AnchorContext,
            DocumentAnnotation,
            DocumentModel,
        )
        from rock_paper_sync.state import OrphanedAnnotation, SyncRecord

        # Create file with only ONE orphan's text restored
        content = "# Test\n\nThis is the important text here.\n\nNothing about the other thing."
        file_path = vault_path / "test.md"
        file_path.write_text(content)

        # Add synced file record
        sync_record = SyncRecord(
            vault_name="test-vault",
            obsidian_path="test.md",
            remarkable_uuid="uuid-123",
            content_hash="hash123",
            last_sync_time=1000,
            page_count=1,
            status="synced",
        )
        state.update_file_state(sync_record)

        # Add two orphans - one recoverable, one not
        orphan1 = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_id="orphan-1",
            annotation_type="highlight",
            original_anchor_text="important text",  # This IS in content
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan1)

        orphan2 = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_id="orphan-2",
            annotation_type="highlight",
            original_anchor_text="deleted forever",  # This is NOT in content
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan2)

        # Mock cloud_sync
        cloud_sync.get_existing_page_uuids.return_value = ["page-1"]
        rm_file_path = vault_path / "mock.rm"
        rm_file_path.write_bytes(b"mock")
        cloud_sync.download_page_rm_files.return_value = [rm_file_path]

        # Mock DocumentModel - orphan-1 can reanchor, orphan-2 cannot
        # Only include orphan-1's annotation (with matching anchor)
        # orphan-2's text is NOT in content, so it should stay orphaned
        anchor1 = AnchorContext.from_text_span(content, 22, 36)  # "important text"
        annotation1 = DocumentAnnotation(
            annotation_id="orphan-1",
            annotation_type="highlight",
            source_page_idx=0,
            anchor_context=anchor1,
        )
        # orphan-2 has an anchor for text that doesn't exist in current content
        # Create it with None anchor_context to simulate failed reanchoring
        annotation2 = DocumentAnnotation(
            annotation_id="orphan-2",
            annotation_type="highlight",
            source_page_idx=0,
            anchor_context=None,  # No valid anchor = becomes orphan
        )

        mock_model = DocumentModel(
            paragraphs=[],
            annotations=[annotation1, annotation2],
            full_text=content,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "rock_paper_sync.pull_sync.DocumentModel.from_rm_files",
                lambda *args, **kwargs: mock_model,
            )

            recovered = engine.attempt_orphan_recovery(vault_config)

        # orphan-1 should be recovered, orphan-2 should still be orphaned
        assert len(recovered) == 1
        assert recovered[0][1] == "orphan-1"

        # Check DB state
        orphans = state.get_orphaned_annotations("test-vault", "test.md")
        assert len(orphans) == 1
        assert orphans[0].annotation_id == "orphan-2"

    def test_recovery_multiple_files(self, recovery_setup) -> None:
        """Test recovery across multiple files in vault."""
        engine, state, cloud_sync, vault_path, vault_config = recovery_setup
        from rock_paper_sync.state import OrphanedAnnotation, SyncRecord

        # Create two files
        (vault_path / "file1.md").write_text("# File 1\n\nimportant text here")
        (vault_path / "file2.md").write_text("# File 2\n\nsecond important thing")

        # Add synced file records
        for i, path in enumerate(["file1.md", "file2.md"], 1):
            sync_record = SyncRecord(
                vault_name="test-vault",
                obsidian_path=path,
                remarkable_uuid=f"uuid-{i}",
                content_hash=f"hash{i}",
                last_sync_time=1000,
                page_count=1,
                status="synced",
            )
            state.update_file_state(sync_record)

        # Add orphans for both files (recoverable)
        orphan1 = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="file1.md",
            annotation_id="orphan-1",
            annotation_type="highlight",
            original_anchor_text="important text",
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan1)

        orphan2 = OrphanedAnnotation(
            vault_name="test-vault",
            obsidian_path="file2.md",
            annotation_id="orphan-2",
            annotation_type="highlight",
            original_anchor_text="second important",
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(orphan2)

        # Dry run to check detection
        recovered = engine.attempt_orphan_recovery(vault_config, dry_run=True)

        # Should detect both as potentially recoverable
        assert len(recovered) == 2
        files = {r[0] for r in recovered}
        assert "file1.md" in files
        assert "file2.md" in files
