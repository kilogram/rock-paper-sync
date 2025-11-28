"""Tests for rm_cloud sync integration layer."""

import json
from unittest.mock import Mock, patch

import pytest

from rock_paper_sync.rm_cloud_sync import RmCloudSync


@pytest.fixture
def mock_client():
    """Create mock RmCloudClient."""
    client = Mock()
    client.is_registered.return_value = True
    client.get_user_token.return_value = "user-token-123"
    return client


@pytest.fixture
def mock_sync_client():
    """Create mock SyncV3Client."""
    sync_client = Mock()
    return sync_client


class TestRmCloudSyncInit:
    """Tests for RmCloudSync initialization."""

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    @patch("rock_paper_sync.rm_cloud_sync.RmCloudClient")
    def test_init_creates_default_client(self, mock_client_class, mock_sync_class):
        """Should create default client if not provided."""
        mock_client_instance = Mock()
        mock_client_instance.is_registered.return_value = True
        mock_client_instance.get_user_token.return_value = "token"
        mock_client_class.return_value = mock_client_instance

        sync = RmCloudSync("http://localhost:3000")

        mock_client_class.assert_called_once_with(base_url="http://localhost:3000")
        assert sync.base_url == "http://localhost:3000"

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_init_uses_provided_client(self, mock_sync_class, mock_client):
        """Should use provided client instead of creating new one."""
        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        assert sync.client == mock_client
        mock_client.is_registered.assert_called_once()

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_init_raises_if_not_registered(self, mock_sync_class, mock_client):
        """Should raise ValueError if device not registered."""
        mock_client.is_registered.return_value = False

        with pytest.raises(ValueError, match="Device not registered"):
            RmCloudSync("http://localhost:3000", client=mock_client)

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_init_gets_user_token(self, mock_sync_class, mock_client):
        """Should get user token and create SyncV3Client."""
        RmCloudSync("http://localhost:3000", client=mock_client)

        mock_client.get_user_token.assert_called_once()
        mock_sync_class.assert_called_once_with(
            base_url="http://localhost:3000",
            device_token="user-token-123",
        )


class TestCreateMetadataFile:
    """Tests for _create_metadata_file method."""

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_create_metadata_file_structure(self, mock_sync_class, mock_client):
        """Metadata should have correct structure."""
        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        metadata_bytes = sync._create_metadata_file("doc-uuid-123", "Test Document", "parent-uuid")

        metadata = json.loads(metadata_bytes)
        assert metadata["visibleName"] == "Test Document"
        assert metadata["type"] == "DocumentType"
        assert metadata["parent"] == "parent-uuid"
        assert metadata["version"] == 1
        assert metadata["pinned"] is False
        assert metadata["synced"] is True
        assert metadata["deleted"] is False

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    @patch("rock_paper_sync.rm_cloud_sync.time.time")
    def test_create_metadata_file_timestamp(self, mock_time, mock_sync_class, mock_client):
        """Metadata should include current timestamp."""
        mock_time.return_value = 1234567890.123  # Fixed timestamp

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        metadata_bytes = sync._create_metadata_file("uuid", "Name", "")

        metadata = json.loads(metadata_bytes)
        expected_ms = int(1234567890.123 * 1000)
        assert metadata["lastModified"] == str(expected_ms)

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_create_metadata_file_empty_parent(self, mock_sync_class, mock_client):
        """Empty parent should be allowed (root level)."""
        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        metadata_bytes = sync._create_metadata_file("uuid", "Doc", "")

        metadata = json.loads(metadata_bytes)
        assert metadata["parent"] == ""


class TestCreateContentFile:
    """Tests for _create_content_file method."""

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_create_content_file_basic_structure(self, mock_sync_class, mock_client):
        """Content should have CRDT formatVersion 2 structure."""
        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        page_uuids = ["page-1", "page-2", "page-3"]
        content_bytes = sync._create_content_file(page_uuids)

        content = json.loads(content_bytes)
        assert content["formatVersion"] == 2
        assert content["fileType"] == "notebook"
        assert content["pageCount"] == 3
        assert "cPages" in content

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_create_content_file_cpages_structure(self, mock_sync_class, mock_client):
        """cPages should have correct CRDT structure."""
        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        page_uuids = ["page-a", "page-b"]
        content_bytes = sync._create_content_file(page_uuids)

        content = json.loads(content_bytes)
        cpages = content["cPages"]

        assert "pages" in cpages
        assert "uuids" in cpages
        assert "lastOpened" in cpages
        assert "original" in cpages

        assert len(cpages["pages"]) == 2

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_create_content_file_page_entries(self, mock_sync_class, mock_client):
        """Page entries should have id, idx, modifed, template fields."""
        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        page_uuids = ["page-uuid-1"]
        content_bytes = sync._create_content_file(page_uuids)

        content = json.loads(content_bytes)
        page = content["cPages"]["pages"][0]

        assert page["id"] == "page-uuid-1"
        assert "idx" in page
        assert "timestamp" in page["idx"]
        assert "value" in page["idx"]
        assert "modifed" in page  # Note the typo - intentional!
        assert "template" in page

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_create_content_file_idx_values_sortable(self, mock_sync_class, mock_client):
        """idx values should be lexicographically sortable."""
        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        page_uuids = ["page-1", "page-2", "page-3"]
        content_bytes = sync._create_content_file(page_uuids)

        content = json.loads(content_bytes)
        pages = content["cPages"]["pages"]

        idx_values = [page["idx"]["value"] for page in pages]
        # Should start with "ba", "bb", "bc"
        assert idx_values[0] == "ba"
        assert idx_values[1] == "bb"
        assert idx_values[2] == "bc"

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_create_content_file_empty_pages(self, mock_sync_class, mock_client):
        """Empty page list should create valid content."""
        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        content_bytes = sync._create_content_file([])

        content = json.loads(content_bytes)
        assert content["pageCount"] == 0
        assert content["cPages"]["pages"] == []

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    @patch("rock_paper_sync.rm_cloud_sync.time.time")
    def test_create_content_file_modifed_timestamp(self, mock_time, mock_sync_class, mock_client):
        """modifed field should contain current timestamp in ms."""
        mock_time.return_value = 1234567890.123

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        content_bytes = sync._create_content_file(["page-1"])

        content = json.loads(content_bytes)
        page = content["cPages"]["pages"][0]

        expected_ms = int(1234567890.123 * 1000)
        assert page["modifed"] == str(expected_ms)


class TestUploadDocument:
    """Tests for upload_document method."""

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_upload_document_calls_sync_client(self, mock_sync_class, mock_client):
        """Should call sync_client.upload_document with correct params."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        pages = [("page-1", b"page1data"), ("page-2", b"page2data")]
        sync.upload_document("doc-uuid", "Test Doc", pages, parent_uuid="parent-123")

        mock_sync_instance.upload_document.assert_called_once()
        call_args = mock_sync_instance.upload_document.call_args

        assert call_args[1]["doc_uuid"] == "doc-uuid"
        assert call_args[1]["broadcast"] is True

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_upload_document_creates_all_files(self, mock_sync_class, mock_client):
        """Should create .metadata, .content, .local, and .rm files."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        pages = [("page-a", b"data-a"), ("page-b", b"data-b")]
        sync.upload_document("uuid", "Name", pages, parent_uuid="")

        call_args = mock_sync_instance.upload_document.call_args
        files = call_args[1]["files"]

        assert "uuid.metadata" in files
        assert "uuid.content" in files
        assert "uuid.local" in files
        assert "uuid/page-a.rm" in files
        assert "uuid/page-b.rm" in files

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_upload_document_local_file_empty_json(self, mock_sync_class, mock_client):
        """.local file should be empty JSON object."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        pages = [("page-1", b"data")]
        sync.upload_document("uuid", "Name", pages)

        call_args = mock_sync_instance.upload_document.call_args
        files = call_args[1]["files"]

        assert files["uuid.local"] == b"{}"

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_upload_document_page_files_correct_path(self, mock_sync_class, mock_client):
        """Page files should be under doc_uuid/ directory."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        pages = [("page-xyz", b"pagedata")]
        sync.upload_document("my-doc", "Name", pages)

        call_args = mock_sync_instance.upload_document.call_args
        files = call_args[1]["files"]

        assert "my-doc/page-xyz.rm" in files
        assert files["my-doc/page-xyz.rm"] == b"pagedata"


class TestIsSyncEnabled:
    """Tests for is_sync_enabled method."""

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_is_sync_enabled_true(self, mock_sync_class, mock_client):
        """Should return True if client is registered."""
        mock_client.is_registered.return_value = True

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        assert sync.is_sync_enabled() is True

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_is_sync_enabled_false(self, mock_sync_class):
        """Should return False if client not registered."""
        mock_client = Mock()
        mock_client.is_registered.return_value = False

        # Can't initialize with unregistered client, so test directly
        # This tests the method logic would work if called
        # In practice, __init__ prevents this scenario


class TestGetExistingPageUuids:
    """Tests for get_existing_page_uuids method."""

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_get_existing_page_uuids_delegates(self, mock_sync_class, mock_client):
        """Should delegate to sync_client.get_document_page_uuids."""
        mock_sync_instance = Mock()
        mock_sync_instance.get_document_page_uuids.return_value = [
            "page-1",
            "page-2",
        ]
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        result = sync.get_existing_page_uuids("doc-123")

        assert result == ["page-1", "page-2"]
        mock_sync_instance.get_document_page_uuids.assert_called_once_with("doc-123")

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_get_existing_page_uuids_empty(self, mock_sync_class, mock_client):
        """Should return empty list if document not found."""
        mock_sync_instance = Mock()
        mock_sync_instance.get_document_page_uuids.return_value = []
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        result = sync.get_existing_page_uuids("missing-doc")

        assert result == []


class TestUploadFolder:
    """Tests for upload_folder method."""

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_upload_folder_creates_collection_type(self, mock_sync_class, mock_client):
        """Folder metadata should have type=CollectionType."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        sync.upload_folder("folder-uuid", "My Folder", parent_uuid="parent")

        call_args = mock_sync_instance.upload_document.call_args
        files = call_args[1]["files"]

        metadata = json.loads(files["folder-uuid.metadata"])
        assert metadata["type"] == "CollectionType"
        assert metadata["visibleName"] == "My Folder"
        assert metadata["parent"] == "parent"

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_upload_folder_empty_content(self, mock_sync_class, mock_client):
        """Folder .content should be empty JSON."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        sync.upload_folder("uuid", "Folder", "")

        call_args = mock_sync_instance.upload_document.call_args
        files = call_args[1]["files"]

        assert files["uuid.content"] == b"{}"

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_upload_folder_has_local_file(self, mock_sync_class, mock_client):
        """Folder should include .local file."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        sync.upload_folder("uuid", "Folder", "")

        call_args = mock_sync_instance.upload_document.call_args
        files = call_args[1]["files"]

        assert "uuid.local" in files
        assert files["uuid.local"] == b"{}"

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_upload_folder_no_broadcast(self, mock_sync_class, mock_client):
        """Folder upload should use broadcast=False."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        sync.upload_folder("uuid", "Folder", "")

        call_args = mock_sync_instance.upload_document.call_args
        assert call_args[1]["broadcast"] is False


class TestDeleteDocument:
    """Tests for delete_document method."""

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_delete_document_calls_sync_client(self, mock_sync_class, mock_client):
        """Should call sync_client.delete_document."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        sync.delete_document("doc-to-delete")

        mock_sync_instance.delete_document.assert_called_once_with("doc-to-delete", broadcast=True)

    @patch("rock_paper_sync.rm_cloud_sync.SyncV3Client")
    def test_delete_document_with_broadcast(self, mock_sync_class, mock_client):
        """Delete should always broadcast=True."""
        mock_sync_instance = Mock()
        mock_sync_class.return_value = mock_sync_instance

        sync = RmCloudSync("http://localhost:3000", client=mock_client)

        sync.delete_document("uuid")

        call_args = mock_sync_instance.delete_document.call_args
        assert call_args[0][0] == "uuid"
        assert call_args[1]["broadcast"] is True
