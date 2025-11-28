"""Tests for sync engine / converter module."""

from pathlib import Path

import pytest

from rock_paper_sync.config import AppConfig, VaultConfig
from rock_paper_sync.converter import SyncEngine, SyncResult
from rock_paper_sync.state import StateManager


@pytest.fixture
def test_vault_config(temp_vault: Path) -> VaultConfig:
    """Create a test vault configuration."""
    return VaultConfig(
        name="test-vault",
        path=temp_vault,
        remarkable_folder="Test",
        include_patterns=["**/*.md"],
        exclude_patterns=[".obsidian/**"],
    )


class TestSyncEngine:
    """Test sync engine orchestration."""

    def test_init(
        self, sample_config: AppConfig, state_manager: StateManager, mock_cloud_sync
    ) -> None:
        """Test sync engine initialization."""
        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        assert engine.config == sample_config
        assert engine.state == state_manager
        assert engine.generator is not None

    def test_sync_file_success(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test successful file sync."""
        # Create test markdown file
        test_file = temp_vault / "test.md"
        test_file.write_text("# Test\n\nThis is a test document.")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        result = engine.sync_file(test_vault_config, test_file)

        assert result.success
        assert result.vault_name == "test-vault"
        assert result.remarkable_uuid is not None
        assert result.page_count == 1
        assert result.error is None

        # Verify cloud sync was called
        assert mock_cloud_sync.upload_document.called

    def test_sync_file_not_found(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test sync fails gracefully for non-existent file."""
        nonexistent = temp_vault / "nonexistent.md"
        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)

        result = engine.sync_file(test_vault_config, nonexistent)

        assert not result.success
        assert result.vault_name == "test-vault"
        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_sync_file_outside_vault(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        tmp_path: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test sync fails for file outside vault."""
        outside_file = tmp_path / "outside.md"
        outside_file.write_text("# Outside")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        result = engine.sync_file(test_vault_config, outside_file)

        assert not result.success
        assert result.vault_name == "test-vault"
        assert result.error is not None
        assert "not in vault" in result.error.lower()

    def test_sync_file_unchanged_skipped(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test unchanged files are skipped."""
        test_file = temp_vault / "test.md"
        test_file.write_text("# Test\n\nContent")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)

        # First sync
        result1 = engine.sync_file(test_vault_config, test_file)
        assert result1.success
        uuid1 = result1.remarkable_uuid

        # Second sync without changes
        result2 = engine.sync_file(test_vault_config, test_file)
        assert result2.success
        assert result2.remarkable_uuid == uuid1  # Same UUID means skipped

    def test_sync_file_changed_resynced(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test changed files are re-synced."""
        test_file = temp_vault / "test.md"
        test_file.write_text("# Test\n\nOriginal content")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)

        # First sync
        result1 = engine.sync_file(test_vault_config, test_file)
        assert result1.success

        # Modify file
        test_file.write_text("# Test\n\nModified content")

        # Second sync should detect change and UPDATE the same document
        result2 = engine.sync_file(test_vault_config, test_file)
        assert result2.success
        # UUID should be the SAME (document updated, not replaced)
        assert result2.remarkable_uuid == result1.remarkable_uuid

    def test_sync_all_changed(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        mock_cloud_sync,
    ) -> None:
        """Test syncing all changed files."""
        # Create multiple test files
        file1 = temp_vault / "test1.md"
        file2 = temp_vault / "test2.md"
        file1.write_text("# Test 1")
        file2.write_text("# Test 2")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        results = engine.sync_all_changed()

        assert len(results) == 2
        assert all(r.success for r in results)

    def test_sync_all_changed_with_errors(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        mock_cloud_sync,
    ) -> None:
        """Test sync continues after individual file errors."""
        # Create one good file and one that will cause an error
        good_file = temp_vault / "good.md"
        good_file.write_text("# Good")

        # Create a file, sync it, then delete it
        bad_file = temp_vault / "bad.md"
        bad_file.write_text("# Bad")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)

        # First sync both
        results1 = engine.sync_all_changed()
        assert len(results1) == 2
        assert all(r.success for r in results1)

        # Delete bad file but keep it in state
        bad_file.unlink()

        # Modify good file
        good_file.write_text("# Good Modified")

        # Sync again - bad file will fail but good file should succeed
        results2 = engine.sync_all_changed()

        # Only good file should be in results (bad file doesn't exist)
        assert len(results2) == 1
        assert results2[0].success
        assert results2[0].path == good_file

    def test_ensure_folder_hierarchy_single_level(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test folder hierarchy creation for single level."""
        # Create nested file
        folder = temp_vault / "projects"
        folder.mkdir()
        file_path = folder / "test.md"
        file_path.write_text("# Test")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        parent_uuid = engine.ensure_folder_hierarchy(test_vault_config, file_path)

        assert parent_uuid != ""
        # Verify folder was uploaded via cloud sync
        assert mock_cloud_sync.upload_folder.called

    def test_ensure_folder_hierarchy_nested(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test folder hierarchy creation for nested folders."""
        # Create deeply nested file
        folder = temp_vault / "projects" / "work" / "notes"
        folder.mkdir(parents=True)
        file_path = folder / "test.md"
        file_path.write_text("# Test")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        parent_uuid = engine.ensure_folder_hierarchy(test_vault_config, file_path)

        assert parent_uuid != ""

        # Verify all folders were created
        assert state_manager.get_folder_uuid("test-vault", "projects") is not None
        assert state_manager.get_folder_uuid("test-vault", "projects/work") is not None
        assert state_manager.get_folder_uuid("test-vault", "projects/work/notes") is not None

    def test_ensure_folder_hierarchy_root_file(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test files at vault root have vault folder UUID as parent."""
        file_path = temp_vault / "root.md"
        file_path.write_text("# Root")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        parent_uuid = engine.ensure_folder_hierarchy(test_vault_config, file_path)

        # Should return vault root folder UUID since test_vault_config has remarkable_folder set
        assert parent_uuid != ""

    def test_ensure_folder_hierarchy_reuses_existing(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test folder hierarchy reuses existing folder UUIDs."""
        # Create folder structure
        folder = temp_vault / "shared"
        folder.mkdir()
        file1 = folder / "test1.md"
        file2 = folder / "test2.md"
        file1.write_text("# Test 1")
        file2.write_text("# Test 2")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)

        # First file creates folder
        parent1 = engine.ensure_folder_hierarchy(test_vault_config, file1)

        # Second file should reuse same folder UUID
        parent2 = engine.ensure_folder_hierarchy(test_vault_config, file2)

        assert parent1 == parent2

    def test_state_database_updated(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test state database is updated after sync."""
        test_file = temp_vault / "test.md"
        test_file.write_text("# Test")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        result = engine.sync_file(test_vault_config, test_file)

        assert result.success

        # Verify state was recorded
        state = state_manager.get_file_state("test-vault", "test.md")
        assert state is not None
        assert state.remarkable_uuid == result.remarkable_uuid
        assert state.status == "synced"

    def test_sync_history_logged(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test sync actions are logged to history."""
        test_file = temp_vault / "test.md"
        test_file.write_text("# Test")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        engine.sync_file(test_vault_config, test_file)

        # Check history
        history = state_manager.get_recent_history(limit=5)
        assert len(history) > 0
        # Most recent entry should be our sync (format: vault_name, path, action, timestamp, details)
        vault_name, obsidian_path, action, timestamp, details = history[0]
        assert vault_name == "test-vault"
        assert obsidian_path == "test.md"
        assert action == "synced"

    def test_sync_file_exception_handling(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
        mocker,
    ) -> None:
        """Test that exceptions during sync are caught and logged."""
        test_file = temp_vault / "test.md"
        test_file.write_text("# Test")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)

        # Mock the generator to raise an exception
        mocker.patch.object(
            engine.generator,
            "generate_document",
            side_effect=RuntimeError("Test error during generation"),
        )

        result = engine.sync_file(test_vault_config, test_file)

        # Should return failure result
        assert not result.success
        assert "Test error during generation" in result.error

        # Error should be logged to history (format: vault_name, path, action, timestamp, details)
        history = state_manager.get_recent_history(limit=5)
        assert len(history) > 0
        vault_name, obsidian_path, action, timestamp, details = history[0]
        assert vault_name == "test-vault"
        assert action == "error"
        assert "Test error during generation" in details


class TestSyncResult:
    """Test SyncResult dataclass."""

    def test_sync_result_success(self) -> None:
        """Test successful sync result."""
        result = SyncResult(
            vault_name="test-vault",
            path=Path("/test/file.md"),
            success=True,
            remarkable_uuid="abc-123",
            page_count=2,
        )

        assert result.vault_name == "test-vault"
        assert result.success
        assert result.remarkable_uuid == "abc-123"
        assert result.page_count == 2
        assert result.error is None

    def test_sync_result_failure(self) -> None:
        """Test failed sync result."""
        result = SyncResult(
            vault_name="test-vault", path=Path("/test/file.md"), success=False, error="Test error"
        )

        assert result.vault_name == "test-vault"
        assert not result.success
        assert result.error == "Test error"
        assert result.remarkable_uuid is None
        assert result.page_count is None


class TestUnsync:
    """Test unsync functionality."""

    def test_unsync_vault_without_delete(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test unsyncing a vault without deleting from cloud."""
        # Sync some files first
        file1 = temp_vault / "test1.md"
        file2 = temp_vault / "test2.md"
        file1.write_text("# Test 1")
        file2.write_text("# Test 2")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        engine.sync_file(test_vault_config, file1)
        engine.sync_file(test_vault_config, file2)

        # Verify files are synced
        assert len(state_manager.get_all_synced_files("test-vault")) == 2

        # Unsync without deleting from cloud
        removed, deleted = engine.unsync_vault("test-vault", delete_from_cloud=False)

        assert removed == 2
        assert deleted == 0
        assert len(state_manager.get_all_synced_files("test-vault")) == 0
        # Verify cloud delete was NOT called
        assert not mock_cloud_sync.delete_document.called

    def test_unsync_vault_with_delete(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test unsyncing a vault with deletion from cloud."""
        # Sync some files first
        file1 = temp_vault / "test1.md"
        file2 = temp_vault / "test2.md"
        file1.write_text("# Test 1")
        file2.write_text("# Test 2")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        engine.sync_file(test_vault_config, file1)
        engine.sync_file(test_vault_config, file2)

        # Verify files are synced
        assert len(state_manager.get_all_synced_files("test-vault")) == 2

        # Unsync with deletion from cloud
        removed, deleted = engine.unsync_vault("test-vault", delete_from_cloud=True)

        assert removed == 2
        assert deleted == 2
        assert len(state_manager.get_all_synced_files("test-vault")) == 0
        # Verify atomic deletion was applied (VirtualDeviceState pattern):
        # - get_root_state is called to read current cloud state
        # - apply_virtual_state is called for atomic operation (uploads index + updates root)
        assert mock_cloud_sync.get_root_state.called
        assert mock_cloud_sync.apply_virtual_state.called

    def test_unsync_vault_invalid_name(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        mock_cloud_sync,
    ) -> None:
        """Test unsyncing with invalid vault name raises error."""
        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)

        with pytest.raises(ValueError, match="not found in configuration"):
            engine.unsync_vault("nonexistent-vault")

    def test_unsync_vault_delete_error(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test unsync raises ResyncRequired on generation conflicts."""
        # Sync a file first
        file1 = temp_vault / "test1.md"
        file1.write_text("# Test 1")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        engine.sync_file(test_vault_config, file1)

        # Make atomic update raise a generation conflict (concurrent cloud modification)
        from rock_paper_sync.sync_v3 import GenerationConflictError

        mock_cloud_sync.apply_virtual_state.side_effect = GenerationConflictError(
            expected=0, actual=1
        )

        # Unsync with deletion should raise ResyncRequiredError for generation conflicts
        from rock_paper_sync.converter import ResyncRequiredError

        with pytest.raises(ResyncRequiredError, match="generation conflict"):
            engine.unsync_vault("test-vault", delete_from_cloud=True)

    def test_unsync_all_vaults(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        temp_vault: Path,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test unsyncing all vaults."""
        # Sync files in the test vault
        file1 = temp_vault / "test1.md"
        file1.write_text("# Test 1")

        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
        engine.sync_file(test_vault_config, file1)

        # Verify file is synced
        assert len(state_manager.get_all_synced_files("test-vault")) == 1

        # Unsync all vaults
        results = engine.unsync_all(delete_from_cloud=False)

        assert "test-vault" in results
        removed, deleted = results["test-vault"]
        assert removed == 1
        assert deleted == 0
        assert len(state_manager.get_all_synced_files("test-vault")) == 0

    def test_unsync_empty_vault(
        self,
        sample_config: AppConfig,
        state_manager: StateManager,
        test_vault_config: VaultConfig,
        mock_cloud_sync,
    ) -> None:
        """Test unsyncing a vault with no synced files."""
        engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)

        # Unsync empty vault
        removed, deleted = engine.unsync_vault("test-vault", delete_from_cloud=False)

        assert removed == 0
        assert deleted == 0
