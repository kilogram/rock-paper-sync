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
