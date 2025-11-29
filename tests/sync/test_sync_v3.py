"""Tests for Sync v3 protocol client.

This module tests the hash-based blob storage protocol used by rm_cloud
and reMarkable devices.
"""

import json
from unittest.mock import Mock, patch

import pytest
import requests

from rock_paper_sync.sync_v3 import (
    DOC_TYPE,
    FILE_TYPE,
    SCHEMA_VERSION,
    BlobEntry,
    GenerationConflictError,
    SyncV3Client,
)


@pytest.fixture
def mock_session():
    """Create a mock requests session for testing."""
    session = Mock(spec=requests.Session)
    # Use a Mock for headers that supports update
    session.headers = Mock()
    session.headers.update = Mock()
    return session


@pytest.fixture
def client_with_mock_session(mock_session):
    """Create a SyncV3Client with a mocked session."""
    with patch("rock_paper_sync.sync_v3.requests.Session", return_value=mock_session):
        client = SyncV3Client("http://localhost:3000", "token")
    return client, mock_session


class TestBlobEntry:
    """Tests for BlobEntry dataclass."""

    def test_to_line_file_entry(self):
        """File entry should format correctly."""
        entry = BlobEntry(
            hash="abc123",
            type=FILE_TYPE,
            entry_name="test.metadata",
            subfiles=0,
            size=1234,
        )

        line = entry.to_line()
        assert line == "abc123:0:test.metadata:0:1234"

    def test_to_line_doc_entry(self):
        """Document entry should format correctly."""
        entry = BlobEntry(
            hash="def456",
            type=DOC_TYPE,
            entry_name="uuid-1234",
            subfiles=5,
            size=0,
        )

        line = entry.to_line()
        assert line == "def456:80000000:uuid-1234:5:0"


class TestSyncV3ClientInit:
    """Tests for SyncV3Client initialization."""

    def test_init_stores_credentials(self):
        """Client should store base URL and token."""
        client = SyncV3Client("http://localhost:3000", "test-token-123")

        assert client.base_url == "http://localhost:3000"
        assert client.device_token == "test-token-123"
        assert client.headers == {"Authorization": "Bearer test-token-123"}

    def test_init_strips_trailing_slash(self):
        """Base URL trailing slash should be removed."""
        client = SyncV3Client("http://localhost:3000/", "token")

        assert client.base_url == "http://localhost:3000"


class TestUploadBlob:
    """Tests for blob upload."""

    def test_upload_blob_success(self, client_with_mock_session):
        """Successful blob upload should call PUT with correct params."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_session.put.return_value = mock_response

        content = b"test content"
        blob_hash = "abc123"

        client.upload_blob(blob_hash, content)

        mock_session.put.assert_called_once_with(
            "http://localhost:3000/sync/v3/files/abc123",
            data=content,
        )
        mock_response.raise_for_status.assert_called_once()

    def test_upload_blob_http_error(self, client_with_mock_session):
        """HTTP error should be raised."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("Upload failed")
        mock_session.put.return_value = mock_response

        with pytest.raises(requests.HTTPError):
            client.upload_blob("hash", b"content")


class TestUploadIndex:
    """Tests for index file creation and upload."""

    def test_upload_index_creates_correct_format(self, client_with_mock_session):
        """Index should have schema version and sorted entries."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_session.put.return_value = mock_response

        entries = [
            BlobEntry("hash2", FILE_TYPE, "b_file.md", 0, 200),
            BlobEntry("hash1", FILE_TYPE, "a_file.md", 0, 100),
        ]

        index_hash, index_content = client.upload_index(entries)

        # Check content format
        lines = index_content.decode("utf-8").split("\n")
        assert lines[0] == SCHEMA_VERSION  # "3"
        assert lines[1] == "hash1:0:a_file.md:0:100"  # Sorted by entry_name
        assert lines[2] == "hash2:0:b_file.md:0:200"

        # Check upload was called
        assert mock_session.put.call_count == 1

    def test_upload_index_empty_entries(self, client_with_mock_session):
        """Empty index should still have schema version."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_session.put.return_value = mock_response

        index_hash, index_content = client.upload_index([])

        lines = index_content.decode("utf-8").split("\n")
        assert lines[0] == SCHEMA_VERSION
        assert len(lines) == 1


class TestGetCurrentGeneration:
    """Tests for getting root generation."""

    def test_get_current_generation_exists(self, client_with_mock_session):
        """Existing root should return hash and generation."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hash": "root-hash-123",
            "generation": 42,
        }
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        root_hash, generation = client.get_current_generation()

        assert root_hash == "root-hash-123"
        assert generation == 42
        mock_session.get.assert_called_once_with(
            "http://localhost:3000/sync/v3/root",
        )

    def test_get_current_generation_not_found(self, client_with_mock_session):
        """404 should return None, 0 (new account)."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.status_code = 404
        mock_session.get.return_value = mock_response

        root_hash, generation = client.get_current_generation()

        assert root_hash is None
        assert generation == 0

    def test_get_current_generation_missing_generation_field(self, client_with_mock_session):
        """Missing generation field should default to 0."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"hash": "root-hash"}
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        root_hash, generation = client.get_current_generation()

        assert root_hash == "root-hash"
        assert generation == 0


class TestDownloadBlob:
    """Tests for blob download."""

    def test_download_blob_success(self, client_with_mock_session):
        """Successful download should return content."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.content = b"blob content here"
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        content = client.download_blob("blob-hash-123")

        assert content == b"blob content here"
        mock_session.get.assert_called_once_with(
            "http://localhost:3000/sync/v3/files/blob-hash-123",
        )

    def test_download_blob_http_error(self, client_with_mock_session):
        """HTTP error should be raised."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("Not found")
        mock_session.get.return_value = mock_response

        with pytest.raises(requests.HTTPError):
            client.download_blob("missing-hash")


class TestParseIndex:
    """Tests for parsing index files."""

    def test_parse_index_valid(self):
        """Valid index should parse correctly."""
        client = SyncV3Client("http://localhost:3000", "token")
        index_content = b"3\nhash1:0:file1.md:0:100\nhash2:80000000:uuid-1:3:0"

        entries = client.parse_index(index_content)

        assert len(entries) == 2
        assert entries[0].hash == "hash1"
        assert entries[0].type == FILE_TYPE
        assert entries[0].entry_name == "file1.md"
        assert entries[0].subfiles == 0
        assert entries[0].size == 100

        assert entries[1].hash == "hash2"
        assert entries[1].type == DOC_TYPE
        assert entries[1].entry_name == "uuid-1"
        assert entries[1].subfiles == 3
        assert entries[1].size == 0

    def test_parse_index_invalid_line(self):
        """Invalid lines should be skipped with warning."""
        client = SyncV3Client("http://localhost:3000", "token")
        index_content = b"3\nhash1:0:file1.md:0:100\ninvalid-line\nhash2:0:file2.md:0:200"

        entries = client.parse_index(index_content)

        assert len(entries) == 2  # Invalid line skipped
        assert entries[0].entry_name == "file1.md"
        assert entries[1].entry_name == "file2.md"

    def test_parse_index_empty(self):
        """Empty index should return empty list."""
        client = SyncV3Client("http://localhost:3000", "token")
        index_content = b"3\n"

        entries = client.parse_index(index_content)

        assert len(entries) == 0

    def test_parse_index_unknown_schema(self):
        """Unknown schema version should log warning but continue."""
        client = SyncV3Client("http://localhost:3000", "token")
        index_content = b"999\nhash1:0:file1.md:0:100"

        entries = client.parse_index(index_content)

        assert len(entries) == 1  # Should still parse entries


class TestGetRootDocuments:
    """Tests for getting root documents."""

    @patch.object(SyncV3Client, "download_blob")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_get_root_documents_exists(self, mock_get_gen, mock_download):
        """Should download and parse root index."""
        mock_get_gen.return_value = ("root-hash", 5)
        mock_download.return_value = b"3\nhash1:80000000:doc-uuid-1:4:0"

        client = SyncV3Client("http://localhost:3000", "token")

        docs = client.get_root_documents()

        assert len(docs) == 1
        assert docs[0].entry_name == "doc-uuid-1"
        mock_download.assert_called_once_with("root-hash")

    @patch.object(SyncV3Client, "get_current_generation")
    def test_get_root_documents_no_root(self, mock_get_gen):
        """No root should return empty list."""
        mock_get_gen.return_value = (None, 0)

        client = SyncV3Client("http://localhost:3000", "token")

        docs = client.get_root_documents()

        assert docs == []


class TestUpdateRoot:
    """Tests for updating root hash tree."""

    def test_update_root_success(self, client_with_mock_session):
        """Successful update should return new generation."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"generation": 6}
        mock_response.raise_for_status = Mock()
        mock_session.put.return_value = mock_response

        new_gen = client.update_root("new-root-hash", 5, broadcast=True)

        assert new_gen == 6
        mock_session.put.assert_called_once_with(
            "http://localhost:3000/sync/v3/root",
            json={
                "generation": 5,
                "hash": "new-root-hash",
                "broadcast": True,
            },
        )

    def test_update_root_conflict(self, client_with_mock_session):
        """409 conflict should raise GenerationConflictError."""
        client, mock_session = client_with_mock_session
        # First call for update_root - returns 409
        mock_conflict_response = Mock()
        mock_conflict_response.status_code = 409
        # Second call for get_current_generation
        mock_gen_response = Mock()
        mock_gen_response.status_code = 200
        mock_gen_response.json.return_value = {"hash": "current-hash", "generation": 10}
        mock_gen_response.raise_for_status = Mock()

        mock_session.put.return_value = mock_conflict_response
        mock_session.get.return_value = mock_gen_response

        with pytest.raises(GenerationConflictError) as exc_info:
            client.update_root("new-root-hash", 5, broadcast=True)

        assert exc_info.value.expected == 5
        assert exc_info.value.actual == 10

    def test_update_root_no_broadcast(self, client_with_mock_session):
        """broadcast=False should be passed in payload."""
        client, mock_session = client_with_mock_session
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"generation": 2}
        mock_response.raise_for_status = Mock()
        mock_session.put.return_value = mock_response

        client.update_root("hash", 1, broadcast=False)

        call_args = mock_session.put.call_args
        assert call_args[1]["json"]["broadcast"] is False


class TestUploadDocumentFiles:
    """Tests for uploading document files and calculating hashOfHashesV3."""

    @patch.object(SyncV3Client, "upload_blob")
    def test_upload_document_files_hash_of_hashes(self, mock_upload):
        """Should calculate hashOfHashesV3 correctly."""
        client = SyncV3Client("http://localhost:3000", "token")

        files = {
            "uuid.metadata": b'{"visibleName": "Test"}',
            "uuid.content": b'{"formatVersion": 2}',
        }

        hash_of_hashes, file_entries = client.upload_document_files("uuid", files)

        # Verify file entries created
        assert len(file_entries) == 2

        # Verify all files uploaded
        assert mock_upload.call_count >= 2

        # hashOfHashesV3 should be SHA256 of concatenated binary hashes (sorted)
        import hashlib

        sorted_entries = sorted(file_entries, key=lambda e: e.entry_name)
        file_hashes_binary = b"".join(bytes.fromhex(entry.hash) for entry in sorted_entries)
        expected_hash = hashlib.sha256(file_hashes_binary).hexdigest()

        assert hash_of_hashes == expected_hash

    @patch.object(SyncV3Client, "upload_blob")
    def test_upload_document_files_double_upload(self, mock_upload):
        """Document index should be uploaded under both hashes."""
        client = SyncV3Client("http://localhost:3000", "token")

        files = {"uuid.metadata": b"test"}

        hash_of_hashes, file_entries = client.upload_document_files("uuid", files)

        # Should upload: file blob, doc index, doc index again under hashOfHashesV3
        # (if hashOfHashesV3 != doc_index_hash)
        assert mock_upload.call_count >= 2


class TestMergeDocumentIntoRoot:
    """Tests for merging document into root with retry logic."""

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_merge_new_document(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """New document should be added to root."""
        mock_get_gen.return_value = ("old-root", 5)
        mock_get_docs.return_value = []
        mock_upload_idx.return_value = ("new-root-hash", b"index")
        mock_update_root.return_value = 6

        client = SyncV3Client("http://localhost:3000", "token")

        new_gen = client.merge_document_into_root(
            "new-doc-uuid", "hash-of-hashes", 4, broadcast=True
        )

        assert new_gen == 6
        mock_update_root.assert_called_once_with("new-root-hash", 5, True)

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_merge_update_existing_document(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Existing document should be updated in root."""
        mock_get_gen.return_value = ("old-root", 5)
        mock_get_docs.return_value = [BlobEntry("old-hash", DOC_TYPE, "existing-uuid", 3, 0)]
        mock_upload_idx.return_value = ("new-root-hash", b"index")
        mock_update_root.return_value = 6

        client = SyncV3Client("http://localhost:3000", "token")

        client.merge_document_into_root("existing-uuid", "new-hash-of-hashes", 4, broadcast=True)

        # Should have uploaded index with updated entry
        call_args = mock_upload_idx.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0].entry_name == "existing-uuid"
        assert call_args[0].hash == "new-hash-of-hashes"

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_merge_retry_on_conflict(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Should retry on generation conflict."""
        mock_get_gen.side_effect = [
            ("root1", 5),  # First attempt
            ("root2", 6),  # Retry after conflict
        ]
        mock_get_docs.return_value = []
        mock_upload_idx.return_value = ("new-root", b"index")

        # First update raises conflict, second succeeds
        mock_update_root.side_effect = [
            GenerationConflictError(5, 6),
            7,  # Success on retry
        ]

        client = SyncV3Client("http://localhost:3000", "token")

        new_gen = client.merge_document_into_root("doc-uuid", "hash", 3, max_retries=3)

        assert new_gen == 7
        assert mock_update_root.call_count == 2

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_merge_max_retries_exceeded(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Should raise after max retries exceeded."""
        mock_get_gen.return_value = ("root", 5)
        mock_get_docs.return_value = []
        mock_upload_idx.return_value = ("new-root", b"index")
        mock_update_root.side_effect = GenerationConflictError(5, 6)

        client = SyncV3Client("http://localhost:3000", "token")

        with pytest.raises(GenerationConflictError):
            client.merge_document_into_root("doc", "hash", 3, max_retries=2)

        assert mock_update_root.call_count == 2


class TestUploadDocument:
    """Tests for high-level upload_document method."""

    @patch.object(SyncV3Client, "merge_document_into_root")
    @patch.object(SyncV3Client, "upload_document_files")
    def test_upload_document_success(self, mock_upload_files, mock_merge):
        """Should upload files then merge into root."""
        mock_upload_files.return_value = ("hash-of-hashes", [])
        mock_merge.return_value = 6

        client = SyncV3Client("http://localhost:3000", "token")
        files = {"uuid.metadata": b"test"}

        client.upload_document("doc-uuid", files, broadcast=True)

        mock_upload_files.assert_called_once_with("doc-uuid", files)
        mock_merge.assert_called_once_with("doc-uuid", "hash-of-hashes", 0, True)


class TestDeleteDocument:
    """Tests for document deletion."""

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_delete_document_success(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Should remove document from root."""
        mock_get_gen.return_value = ("root", 5)
        mock_get_docs.return_value = [
            BlobEntry("hash1", DOC_TYPE, "doc-to-delete", 3, 0),
            BlobEntry("hash2", DOC_TYPE, "doc-to-keep", 2, 0),
        ]
        mock_upload_idx.return_value = ("new-root", b"index")
        mock_update_root.return_value = 6

        client = SyncV3Client("http://localhost:3000", "token")

        client.delete_document("doc-to-delete", broadcast=True)

        # Should upload index with only remaining document
        call_args = mock_upload_idx.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0].entry_name == "doc-to-keep"

    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_delete_document_not_found(self, mock_get_gen, mock_get_docs):
        """Should return early if document not in root."""
        mock_get_gen.return_value = ("root", 5)
        mock_get_docs.return_value = [BlobEntry("hash", DOC_TYPE, "other-doc", 2, 0)]

        client = SyncV3Client("http://localhost:3000", "token")

        # Should not raise, just return
        client.delete_document("missing-doc")

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_delete_document_retry_on_conflict(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Should retry deletion on conflict."""
        mock_get_gen.side_effect = [("root1", 5), ("root2", 6)]
        mock_get_docs.return_value = [BlobEntry("hash", DOC_TYPE, "doc-uuid", 2, 0)]
        mock_upload_idx.return_value = ("new-root", b"index")
        mock_update_root.side_effect = [
            GenerationConflictError(5, 6),
            7,  # Success on retry
        ]

        client = SyncV3Client("http://localhost:3000", "token")

        client.delete_document("doc-uuid", max_retries=3)

        assert mock_update_root.call_count == 2


class TestDeleteDocumentsBatch:
    """Tests for batch document deletion."""

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_delete_multiple_documents_success(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Should delete multiple documents in single root update."""
        mock_get_gen.return_value = ("root", 5)
        mock_get_docs.return_value = [
            BlobEntry("hash1", DOC_TYPE, "doc-1", 3, 0),
            BlobEntry("hash2", DOC_TYPE, "doc-2", 2, 0),
            BlobEntry("hash3", DOC_TYPE, "doc-3", 1, 0),
            BlobEntry("hash4", DOC_TYPE, "doc-keep", 2, 0),
        ]
        mock_upload_idx.return_value = ("new-root", b"index")
        mock_update_root.return_value = 6

        client = SyncV3Client("http://localhost:3000", "token")

        client.delete_documents_batch(["doc-1", "doc-2", "doc-3"], broadcast=True)

        # Should make only one root update call (batch operation)
        assert mock_update_root.call_count == 1

        # Should upload index with only remaining document
        call_args = mock_upload_idx.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0].entry_name == "doc-keep"

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_delete_batch_partial_match(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Should handle case where only some documents exist."""
        mock_get_gen.return_value = ("root", 5)
        mock_get_docs.return_value = [
            BlobEntry("hash1", DOC_TYPE, "doc-1", 3, 0),
            BlobEntry("hash2", DOC_TYPE, "doc-2", 2, 0),
        ]
        mock_upload_idx.return_value = ("new-root", b"index")
        mock_update_root.return_value = 6

        client = SyncV3Client("http://localhost:3000", "token")

        # Request to delete 3 docs, but only 2 exist
        client.delete_documents_batch(["doc-1", "doc-2", "doc-nonexistent"])

        # Should still proceed and delete the 2 found docs
        assert mock_update_root.call_count == 1
        call_args = mock_upload_idx.call_args[0][0]
        assert len(call_args) == 0  # All found docs deleted

    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_delete_batch_empty_list(self, mock_get_gen, mock_get_docs):
        """Should return early for empty list."""
        client = SyncV3Client("http://localhost:3000", "token")

        # Should not make any API calls
        client.delete_documents_batch([])

        # Verify no API calls made
        assert mock_get_gen.call_count == 0
        assert mock_get_docs.call_count == 0

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_delete_batch_none_found(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Should skip root update when no documents found (Issue #1 fix)."""
        mock_get_gen.return_value = ("root", 5)
        mock_get_docs.return_value = [
            BlobEntry("hash1", DOC_TYPE, "doc-other", 3, 0),
        ]

        client = SyncV3Client("http://localhost:3000", "token")

        # Try to delete docs that don't exist
        client.delete_documents_batch(["doc-missing-1", "doc-missing-2"])

        # Should NOT upload index or update root (early exit)
        assert mock_upload_idx.call_count == 0
        assert mock_update_root.call_count == 0

    @patch.object(SyncV3Client, "update_root")
    @patch.object(SyncV3Client, "upload_index")
    @patch.object(SyncV3Client, "get_root_documents")
    @patch.object(SyncV3Client, "get_current_generation")
    def test_delete_batch_retry_on_conflict(
        self, mock_get_gen, mock_get_docs, mock_upload_idx, mock_update_root
    ):
        """Should retry batch deletion on conflict."""
        mock_get_gen.side_effect = [("root1", 5), ("root2", 6)]
        mock_get_docs.return_value = [
            BlobEntry("hash1", DOC_TYPE, "doc-1", 3, 0),
            BlobEntry("hash2", DOC_TYPE, "doc-2", 2, 0),
        ]
        mock_upload_idx.return_value = ("new-root", b"index")
        mock_update_root.side_effect = [
            GenerationConflictError(5, 6),
            7,  # Success on retry
        ]

        client = SyncV3Client("http://localhost:3000", "token")

        client.delete_documents_batch(["doc-1", "doc-2"], max_retries=3)

        # Should retry once after conflict
        assert mock_update_root.call_count == 2

    def test_delete_single_uses_batch(self):
        """delete_document should delegate to delete_documents_batch."""
        client = SyncV3Client("http://localhost:3000", "token")

        with patch.object(client, "delete_documents_batch") as mock_batch:
            client.delete_document("doc-uuid", broadcast=False, max_retries=5)

            # Should call batch method with single-item list (using positional args)
            mock_batch.assert_called_once_with(["doc-uuid"], False, 5)


class TestGetDocumentPageUUIDs:
    """Tests for extracting page UUIDs from .content file."""

    @patch.object(SyncV3Client, "download_blob")
    @patch.object(SyncV3Client, "get_root_documents")
    def test_get_page_uuids_formatversion_2(self, mock_get_docs, mock_download):
        """Should extract UUIDs from formatVersion 2 cPages."""
        mock_get_docs.return_value = [BlobEntry("doc-hash", DOC_TYPE, "doc-uuid", 3, 0)]

        # Mock document index
        doc_index = b"3\nfile-hash:0:doc-uuid.content:0:100"
        # Mock .content file with formatVersion 2
        content_json = {
            "formatVersion": 2,
            "cPages": {
                "pages": [
                    {"id": "page-2", "idx": {"value": "bb"}},
                    {"id": "page-1", "idx": {"value": "aa"}},
                    {"id": "page-3", "idx": {"value": "cc"}},
                ]
            },
        }

        mock_download.side_effect = [
            doc_index,
            json.dumps(content_json).encode("utf-8"),
        ]

        client = SyncV3Client("http://localhost:3000", "token")

        page_uuids = client.get_document_page_uuids("doc-uuid")

        # Should be sorted by idx value
        assert page_uuids == ["page-1", "page-2", "page-3"]

    @patch.object(SyncV3Client, "get_root_documents")
    def test_get_page_uuids_document_not_found(self, mock_get_docs):
        """Should return empty list if document not in root."""
        mock_get_docs.return_value = []

        client = SyncV3Client("http://localhost:3000", "token")

        page_uuids = client.get_document_page_uuids("missing-doc")

        assert page_uuids == []

    @patch.object(SyncV3Client, "download_blob")
    @patch.object(SyncV3Client, "get_root_documents")
    def test_get_page_uuids_no_content_file(self, mock_get_docs, mock_download):
        """Should return empty list if no .content file."""
        mock_get_docs.return_value = [BlobEntry("doc-hash", DOC_TYPE, "doc-uuid", 2, 0)]

        # Mock document index without .content file
        doc_index = b"3\nhash:0:doc-uuid.metadata:0:50"
        mock_download.return_value = doc_index

        client = SyncV3Client("http://localhost:3000", "token")

        page_uuids = client.get_document_page_uuids("doc-uuid")

        assert page_uuids == []

    @patch.object(SyncV3Client, "download_blob")
    @patch.object(SyncV3Client, "get_root_documents")
    def test_get_page_uuids_formatversion_1_fallback(self, mock_get_docs, mock_download):
        """Should handle formatVersion 1 pages array."""
        mock_get_docs.return_value = [BlobEntry("doc-hash", DOC_TYPE, "doc-uuid", 2, 0)]

        doc_index = b"3\nfile-hash:0:doc-uuid.content:0:100"
        content_json = {"formatVersion": 1, "pages": ["page-a", "page-b"]}

        mock_download.side_effect = [
            doc_index,
            json.dumps(content_json).encode("utf-8"),
        ]

        client = SyncV3Client("http://localhost:3000", "token")

        page_uuids = client.get_document_page_uuids("doc-uuid")

        assert page_uuids == ["page-a", "page-b"]
