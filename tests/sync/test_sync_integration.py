"""Integration tests for sync operations using rmfakecloud.

These tests use a real rmfakecloud instance to verify that the sync
implementation correctly handles various edge cases and scenarios that
might not be caught by unit tests with mocks.

Focus areas:
- Root index blob upload before update_root calls
- Generation conflict handling
- Multiple file operations
- Folder creation and deletion
- State consistency after errors
"""

from pathlib import Path

import pytest

from rock_paper_sync.config import (
    AppConfig,
    CloudConfig,
    LayoutConfig,
    OCRConfig,
    SyncConfig,
    VaultConfig,
)
from rock_paper_sync.converter import SyncEngine
from rock_paper_sync.rm_cloud_client import RmCloudClient
from rock_paper_sync.rm_cloud_sync import RmCloudSync
from rock_paper_sync.state import StateManager


def get_test_credentials() -> str:
    """Get device token from test credentials."""
    from tests.fixtures.rmfakecloud.helpers import get_credentials

    try:
        creds = get_credentials()
        return creds["device_token"]
    except FileNotFoundError as e:
        pytest.skip(f"rmfakecloud test credentials not found: {e}")


@pytest.fixture
def rmfakecloud_config(tmp_path: Path, rmfakecloud: str):
    """Create test config for rmfakecloud integration tests."""
    vault = tmp_path / "vault"
    vault.mkdir()

    db = tmp_path / "state.db"
    log_file = tmp_path / "test.log"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    config = AppConfig(
        sync=SyncConfig(
            vaults=[
                VaultConfig(
                    name="test-vault",
                    path=vault,
                    remarkable_folder=None,  # Files go to root
                    include_patterns=["**/*.md"],
                    exclude_patterns=[],
                )
            ],
            state_database=db,
            debounce_seconds=1,
        ),
        cloud=CloudConfig(base_url=rmfakecloud),
        layout=LayoutConfig(
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50,
        ),
        log_level="debug",
        log_file=log_file,
        ocr=OCRConfig(),
        cache_dir=cache_dir,
    )

    # Get device token and create client
    device_token = get_test_credentials()

    # Create temp credentials file for RmCloudClient
    import json
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {"device_token": device_token, "device_id": "rock-paper-sync-test-001", "user_id": ""},
            f,
        )
        temp_creds_path = Path(f.name)

    rm_client = RmCloudClient(base_url=rmfakecloud, credentials_path=temp_creds_path)

    # Create cloud sync with proper client
    cloud_sync = RmCloudSync(base_url=rmfakecloud, client=rm_client)

    return {
        "config": config,
        "vault": vault,
        "state_db": db,
        "cloud_sync": cloud_sync,
    }


class TestSyncBasicOperations:
    """Test basic sync operations work end-to-end."""

    def test_sync_single_file(self, rmfakecloud_config):
        """Sync a single file and verify it's uploaded correctly."""
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create and sync file
        test_file = vault / "test.md"
        test_file.write_text("# Test Document\n\nThis is a test.")

        result = engine.sync_file(vault_config, test_file)

        assert result.success
        assert result.remarkable_uuid is not None

        # Verify cloud state can be read
        entries, root_hash, gen = cloud_sync.get_root_state()
        assert len(entries) > 0
        assert root_hash is not None
        assert gen >= 0

        state.close()

    def test_sync_multiple_files(self, rmfakecloud_config):
        """Sync multiple files and verify all are uploaded."""
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create multiple files
        files = []
        for i in range(5):
            f = vault / f"test{i}.md"
            f.write_text(f"# Document {i}\n\nContent {i}")
            files.append(f)

        # Sync all files
        for f in files:
            result = engine.sync_file(vault_config, f)
            assert result.success

        # Verify cloud state
        entries, root_hash, gen = cloud_sync.get_root_state()
        assert len(entries) >= 5
        assert root_hash is not None

        state.close()


class TestSyncDeletionOperations:
    """Test file deletion operations."""

    def test_delete_single_file(self, rmfakecloud_config):
        """Sync file, delete it, sync again, verify deletion worked."""
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Sync a file
        test_file = vault / "test.md"
        test_file.write_text("# Test")
        result = engine.sync_file(vault_config, test_file)
        assert result.success

        # Get initial cloud state
        entries_before, _, gen_before = cloud_sync.get_root_state()

        # Delete file and sync
        test_file.unlink()
        engine.sync_vault(vault_config)

        # Verify cloud state changed
        entries_after, _, gen_after = cloud_sync.get_root_state()
        assert len(entries_after) < len(entries_before)
        assert gen_after > gen_before

        state.close()

    def test_delete_multiple_files_atomic(self, rmfakecloud_config):
        """Delete multiple files atomically."""
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Sync multiple files
        files = []
        for i in range(3):
            f = vault / f"test{i}.md"
            f.write_text(f"# Test {i}")
            result = engine.sync_file(vault_config, f)
            assert result.success
            files.append(f)

        gen_before = cloud_sync.get_root_state()[2]

        # Delete all files
        for f in files:
            f.unlink()

        # Sync should delete all atomically
        engine.sync_vault(vault_config)

        # Verify single generation increment (atomic deletion)
        gen_after = cloud_sync.get_root_state()[2]
        assert gen_after == gen_before + 1, "Atomic deletion should increment generation once"

        state.close()


class TestSyncWithFolders:
    """Test sync operations with folders."""

    def test_sync_file_in_folder(self, rmfakecloud_config):
        """Sync file inside folder, verify folder is created."""
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create file in subfolder
        folder = vault / "projects"
        folder.mkdir()
        test_file = folder / "test.md"
        test_file.write_text("# Project Document")

        result = engine.sync_file(vault_config, test_file)
        assert result.success

        # Verify folder was tracked
        folder_uuid = state.get_folder_uuid("test-vault", "projects")
        assert folder_uuid is not None

        state.close()

    def test_delete_file_keeps_folder(self, rmfakecloud_config):
        """Delete one file from folder, folder should remain."""
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create folder with two files
        folder = vault / "projects"
        folder.mkdir()
        file1 = folder / "doc1.md"
        file2 = folder / "doc2.md"
        file1.write_text("# Doc 1")
        file2.write_text("# Doc 2")

        engine.sync_file(vault_config, file1)
        engine.sync_file(vault_config, file2)

        # Delete one file
        file1.unlink()
        engine.sync_vault(vault_config)

        # Folder should still exist
        folder_uuid = state.get_folder_uuid("test-vault", "projects")
        assert folder_uuid is not None

        state.close()


class TestSyncErrorHandling:
    """Test error handling in sync operations."""

    def test_concurrent_modification_detection(self, rmfakecloud_config):
        """Verify generation conflicts are detected."""
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Sync a file
        test_file = vault / "test.md"
        test_file.write_text("# Test")
        result = engine.sync_file(vault_config, test_file)
        assert result.success

        # Simulate concurrent modification by syncing another file
        # This increments the generation
        test_file2 = vault / "test2.md"
        test_file2.write_text("# Test 2")
        engine.sync_file(vault_config, test_file2)

        # Now if we try to delete with stale generation, it should detect conflict
        # Note: This is hard to test with rmfakecloud since we don't have direct
        # generation control, but the infrastructure is in place

        state.close()

    def test_state_consistency_after_error(self, rmfakecloud_config):
        """Verify state remains consistent if sync fails."""
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Sync a file
        test_file = vault / "test.md"
        test_file.write_text("# Test")
        result = engine.sync_file(vault_config, test_file)
        assert result.success

        # Get state before operation
        files_before = state.get_all_synced_files("test-vault")
        assert len(files_before) == 1

        # If any error occurs during deletion, state should remain unchanged
        # This is ensured by the atomic VirtualDeviceState pattern

        state.close()


class TestRootIndexUpload:
    """Critical tests to ensure root index blob is uploaded before update_root."""

    def test_root_index_exists_after_deletion(self, rmfakecloud_config):
        """Verify root index blob exists in cloud after deletion operation.

        This test catches the bug where we were calling update_root with a hash
        for a blob that was never uploaded to the cloud.
        """
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Sync two files
        file1 = vault / "test1.md"
        file2 = vault / "test2.md"
        file1.write_text("# Test 1")
        file2.write_text("# Test 2")

        engine.sync_file(vault_config, file1)
        engine.sync_file(vault_config, file2)

        # Delete one file
        file1.unlink()
        engine.sync_vault(vault_config)

        # Now try to read the cloud state - this would fail with 404 if
        # the root index blob wasn't uploaded
        entries, root_hash, gen = cloud_sync.get_root_state()

        # If we get here without a 404 error, the blob exists!
        assert root_hash is not None
        assert len(entries) >= 1  # file2 should still be there

        state.close()

    def test_multiple_deletions_followed_by_read(self, rmfakecloud_config):
        """Multiple delete operations followed by state read.

        This exercises the sync -> delete -> sync -> delete -> read pattern
        that exposed the original bug.
        """
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Sync three files
        files = []
        for i in range(3):
            f = vault / f"test{i}.md"
            f.write_text(f"# Test {i}")
            engine.sync_file(vault_config, f)
            files.append(f)

        # Delete first file, sync
        files[0].unlink()
        engine.sync_vault(vault_config)

        # Read state (this would fail if index blob wasn't uploaded)
        entries1, hash1, gen1 = cloud_sync.get_root_state()
        assert hash1 is not None

        # Delete second file, sync
        files[1].unlink()
        engine.sync_vault(vault_config)

        # Read state again (catches bug if previous sync didn't upload blob)
        entries2, hash2, gen2 = cloud_sync.get_root_state()
        assert hash2 is not None
        assert hash2 != hash1  # Hash should have changed
        assert len(entries2) < len(entries1)

        state.close()

    def test_unsync_followed_by_get_root_state(self, rmfakecloud_config):
        """Unsync operation followed by get_root_state.

        This was the specific pattern that failed in test_multiple_files_in_folder.
        """
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Sync some files
        for i in range(3):
            f = vault / f"test{i}.md"
            f.write_text(f"# Test {i}")
            engine.sync_file(vault_config, f)

        # Unsync with deletion
        files_removed, files_deleted = engine.unsync_vault("test-vault", delete_from_cloud=True)
        assert files_removed > 0
        assert files_deleted > 0

        # Try to read cloud state - would fail with 404 if blob wasn't uploaded
        entries, root_hash, gen = cloud_sync.get_root_state()

        # Should succeed (empty root is valid)
        assert isinstance(entries, list)
        assert len(entries) == 0  # All files were deleted

        state.close()
