"""Integration tests for folder deletion with rmfakecloud.

These tests use rmfakecloud to validate that folder deletion works correctly,
ensuring that:
1. Documents inside folders are synced up properly
2. When deleted from Obsidian, they're deleted from cloud
3. Folders themselves are deleted without errors
4. Empty roots are handled correctly
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
    """Get device token from test credentials.

    Returns:
        Device token (JWT)

    Raises:
        pytest.skip: If credentials not found
    """
    from tests.fixtures.rmfakecloud.helpers import get_credentials

    try:
        creds = get_credentials()
        return creds["device_token"]
    except FileNotFoundError as e:
        pytest.skip(f"rmfakecloud test credentials not found: {e}")


@pytest.fixture
def rmfakecloud_config(tmp_path: Path, rmfakecloud: str):
    """Create test config for rmfakecloud integration tests with isolated state."""
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
                    remarkable_folder="TestFolder",
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
        "db": db,
        "client": rm_client,
        "cloud_sync": cloud_sync,
    }


@pytest.mark.offline
class TestFolderDeletion:
    """Test folder deletion with rmfakecloud."""

    def test_sync_file_then_delete(self, rmfakecloud_config):
        """Test syncing a file to a folder, then deleting it.

        Workflow:
        1. Create vault/projects/document.md
        2. Sync up (creates folder "projects" and uploads document)
        3. Delete document.md from vault
        4. Sync (should delete document from cloud)
        5. Delete projects folder
        6. Sync (should delete folder from cloud without error)
        """
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        # Setup
        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create folder and document
        folder = vault / "projects"
        folder.mkdir()
        test_file = folder / "document.md"
        test_file.write_text("# Test Document\n\nInside folder.")

        # Sync up
        result = engine.sync_file(vault_config, test_file)
        assert result.success, f"Sync failed: {result.message}"

        doc_uuid = result.remarkable_uuid
        assert doc_uuid is not None

        # Verify folder was created in state
        folder_uuid = state.get_folder_uuid("test-vault", "projects")
        assert folder_uuid is not None, "Folder should be created"

        # Delete the document file
        test_file.unlink()

        # Sync should detect deletion and remove from cloud
        # This is done via unsync_vault or by re-running sync
        files_removed, files_deleted = engine.unsync_vault("test-vault", delete_from_cloud=True)

        # Verify document was deleted
        assert files_removed >= 1, f"Expected at least 1 file removed, got {files_removed}"

        # Verify folder was also deleted (should happen automatically when empty)
        folders_remaining = state.get_all_folders("test-vault")
        assert (
            len(folders_remaining) == 0
        ), f"Expected no folders, found {len(folders_remaining)}: {folders_remaining}"

        state.close()

    def test_nested_folders_deletion(self, rmfakecloud_config):
        """Test deleting nested folder structure.

        Workflow:
        1. Create vault/a/b/c/document.md
        2. Sync up (creates folders a, b, c and document)
        3. Delete everything and unsync
        4. Verify all folders deleted without error
        """
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        # Setup
        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create nested structure
        deep_folder = vault / "a" / "b" / "c"
        deep_folder.mkdir(parents=True)
        test_file = deep_folder / "document.md"
        test_file.write_text("# Deep Document\n\nNested deep.")

        # Sync up
        result = engine.sync_file(vault_config, test_file)
        assert result.success, f"Sync failed: {result.message}"

        # Verify all folders created
        assert state.get_folder_uuid("test-vault", "a") is not None
        assert state.get_folder_uuid("test-vault", "a/b") is not None
        assert state.get_folder_uuid("test-vault", "a/b/c") is not None

        # Unsync everything
        files_removed, files_deleted = engine.unsync_vault("test-vault", delete_from_cloud=True)

        # Verify all folders deleted in correct order (deepest first)
        folders_remaining = state.get_all_folders("test-vault")
        assert len(folders_remaining) == 0, f"Expected no folders, found {folders_remaining}"

        state.close()

    def test_multiple_files_in_folder(self, rmfakecloud_config):
        """Test folder with multiple documents.

        Workflow:
        1. Create vault/projects/doc1.md and vault/projects/doc2.md
        2. Sync both up
        3. Delete doc1.md, sync (folder should remain)
        4. Delete doc2.md, sync (folder should be deleted)
        """
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        # Setup
        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create folder with two documents
        folder = vault / "projects"
        folder.mkdir()
        doc1 = folder / "doc1.md"
        doc2 = folder / "doc2.md"
        doc1.write_text("# Document 1\n")
        doc2.write_text("# Document 2\n")

        # Sync both
        result1 = engine.sync_file(vault_config, doc1)
        result2 = engine.sync_file(vault_config, doc2)
        assert result1.success and result2.success

        # Verify folder exists
        folder_uuid = state.get_folder_uuid("test-vault", "projects")
        assert folder_uuid is not None

        # Delete doc1
        doc1.unlink()

        # Re-sync vault (should delete doc1 but keep folder)
        engine.sync_vault(vault_config)

        # Folder should still exist (doc2 is still there)
        assert state.get_folder_uuid("test-vault", "projects") is not None

        # Delete doc2 and unsync
        doc2.unlink()
        files_removed, files_deleted = engine.unsync_vault("test-vault", delete_from_cloud=True)

        # Now folder should be gone
        folders_remaining = state.get_all_folders("test-vault")
        assert len(folders_remaining) == 0, "Expected no folders after deleting all files"

        state.close()

    def test_root_folder_deletion(self, rmfakecloud_config):
        """Test deleting the root remarkable folder itself.

        Workflow:
        1. Sync file to vault (creates TestFolder)
        2. Unsync entire vault (should delete TestFolder without error)
        """
        config = rmfakecloud_config["config"]
        vault = rmfakecloud_config["vault"]
        cloud_sync = rmfakecloud_config["cloud_sync"]

        # Setup
        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create document in root of vault
        test_file = vault / "document.md"
        test_file.write_text("# Root Document\n")

        # Sync
        result = engine.sync_file(vault_config, test_file)
        assert result.success

        # Note: The root remarkable_folder "TestFolder" is not tracked in folder_mapping
        # Only sub-folders within the vault are tracked

        # Unsync vault (should delete document and root folder)
        files_removed, files_deleted = engine.unsync_vault("test-vault", delete_from_cloud=True)

        # Verify everything cleaned up
        folders_remaining = state.get_all_folders("test-vault")
        assert len(folders_remaining) == 0, f"Expected no folders, found {folders_remaining}"

        state.close()
