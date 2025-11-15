"""Tests for reMarkable metadata generation.

This module tests the metadata generation functions that create JSON files
required by reMarkable v6 format.
"""

import time

import pytest

from rock_paper_sync.metadata import (
    current_rm_timestamp,
    generate_content_metadata,
    generate_document_metadata,
    generate_folder_metadata,
    generate_page_metadata,
)


class TestCurrentRmTimestamp:
    """Tests for current_rm_timestamp function."""

    def test_returns_13_digit_integer(self) -> None:
        """Timestamp should be 13 digits (milliseconds)."""
        timestamp = current_rm_timestamp()
        assert isinstance(timestamp, int)
        assert len(str(timestamp)) == 13

    def test_approximately_current_time(self) -> None:
        """Timestamp should be close to current time."""
        before = int(time.time() * 1000)
        timestamp = current_rm_timestamp()
        after = int(time.time() * 1000)

        # Should be within 1 second of current time
        assert before <= timestamp <= after + 1000

    def test_increases_over_time(self) -> None:
        """Later timestamps should be larger."""
        ts1 = current_rm_timestamp()
        time.sleep(0.01)  # Sleep 10ms
        ts2 = current_rm_timestamp()
        assert ts2 > ts1


class TestGenerateDocumentMetadata:
    """Tests for generate_document_metadata function."""

    def test_basic_structure(self) -> None:
        """Generated metadata should have required fields."""
        metadata = generate_document_metadata(
            visible_name="Test Doc",
            parent_uuid="parent-123",
            modified_time=1700000000000,
        )

        assert isinstance(metadata, dict)
        assert metadata["visibleName"] == "Test Doc"
        assert metadata["parent"] == "parent-123"
        assert metadata["lastModified"] == "1700000000000"
        assert metadata["type"] == "DocumentType"
        assert metadata["version"] == 1

    def test_root_document(self) -> None:
        """Document at root should have empty parent."""
        metadata = generate_document_metadata(
            visible_name="Root Doc",
            parent_uuid="",
            modified_time=1700000000000,
        )

        assert metadata["parent"] == ""

    def test_boolean_flags(self) -> None:
        """Boolean flags should be properly set."""
        metadata = generate_document_metadata(
            visible_name="Test",
            parent_uuid="",
            modified_time=1700000000000,
        )

        assert metadata["deleted"] is False
        assert metadata["metadatamodified"] is False
        assert metadata["modified"] is False
        assert metadata["pinned"] is False
        assert metadata["synced"] is True

    def test_last_opened_defaults(self) -> None:
        """Last opened fields should have default values."""
        metadata = generate_document_metadata(
            visible_name="Test",
            parent_uuid="",
            modified_time=1700000000000,
        )

        assert metadata["lastOpened"] == ""
        assert metadata["lastOpenedPage"] == 0


class TestGenerateContentMetadata:
    """Tests for generate_content_metadata function."""

    def test_single_page(self) -> None:
        """Content metadata for single-page document."""
        pages = ["page-uuid-1"]
        content = generate_content_metadata(pages)

        assert content["pageCount"] == 1
        assert content["pages"] == pages

    def test_multi_page(self) -> None:
        """Content metadata for multi-page document."""
        pages = ["page-1", "page-2", "page-3"]
        content = generate_content_metadata(pages)

        assert content["pageCount"] == 3
        assert content["pages"] == pages

    def test_default_settings(self) -> None:
        """Default tool and display settings."""
        content = generate_content_metadata(["page-1"])

        assert content["fileType"] == "notebook"
        assert content["orientation"] == "portrait"
        assert content["textAlignment"] == "left"
        assert content["textScale"] == 1
        assert content["margins"] == 100
        assert content["lineHeight"] == -1
        assert content["fontName"] == ""

    def test_extra_metadata_structure(self) -> None:
        """ExtraMetadata should have tool settings."""
        content = generate_content_metadata(["page-1"])

        extra = content["extraMetadata"]
        assert isinstance(extra, dict)
        assert extra["LastPen"] == "Ballpointv2"
        assert extra["LastColor"] == "Black"
        assert extra["LastTool"] == "Ballpointv2"

    def test_document_metadata_structure(self) -> None:
        """DocumentMetadata should be empty dict."""
        content = generate_content_metadata(["page-1"])
        assert content["documentMetadata"] == {}

    def test_cover_page_number(self) -> None:
        """Cover page number should default to 0."""
        content = generate_content_metadata(["page-1"])
        assert content["coverPageNumber"] == 0


class TestGeneratePageMetadata:
    """Tests for generate_page_metadata function."""

    def test_basic_structure(self) -> None:
        """Page metadata should have layers."""
        metadata = generate_page_metadata()

        assert isinstance(metadata, dict)
        assert "layers" in metadata
        assert isinstance(metadata["layers"], list)

    def test_default_layer(self) -> None:
        """Default layer should be named 'Layer 1' and visible."""
        metadata = generate_page_metadata()

        assert len(metadata["layers"]) == 1
        layer = metadata["layers"][0]
        assert layer["name"] == "Layer 1"
        assert layer["visible"] is True


class TestGenerateFolderMetadata:
    """Tests for generate_folder_metadata function."""

    def test_basic_structure(self) -> None:
        """Folder metadata should have required fields."""
        metadata = generate_folder_metadata(
            name="My Folder",
            parent_uuid="parent-123",
        )

        assert metadata["visibleName"] == "My Folder"
        assert metadata["parent"] == "parent-123"
        assert metadata["type"] == "CollectionType"
        assert metadata["version"] == 1

    def test_collection_type(self) -> None:
        """Folders should have CollectionType, not DocumentType."""
        metadata = generate_folder_metadata(
            name="Folder",
            parent_uuid="",
        )

        assert metadata["type"] == "CollectionType"

    def test_root_folder(self) -> None:
        """Folder at root should have empty parent."""
        metadata = generate_folder_metadata(
            name="Root Folder",
            parent_uuid="",
        )

        assert metadata["parent"] == ""

    def test_boolean_flags(self) -> None:
        """Folder boolean flags should match document flags."""
        metadata = generate_folder_metadata(
            name="Folder",
            parent_uuid="",
        )

        assert metadata["deleted"] is False
        assert metadata["metadatamodified"] is False
        assert metadata["modified"] is False
        assert metadata["pinned"] is False
        assert metadata["synced"] is True

    def test_timestamp_is_current(self) -> None:
        """Folder timestamp should be approximately current."""
        before = int(time.time() * 1000)
        metadata = generate_folder_metadata(
            name="Folder",
            parent_uuid="",
        )
        after = int(time.time() * 1000)

        last_modified = int(metadata["lastModified"])
        assert before <= last_modified <= after + 1000


class TestMetadataIntegration:
    """Integration tests for metadata generation."""

    def test_document_and_content_match(self) -> None:
        """Document and content metadata should have matching page counts."""
        pages = ["page-1", "page-2", "page-3"]

        doc_metadata = generate_document_metadata(
            visible_name="Test",
            parent_uuid="",
            modified_time=1700000000000,
        )
        content_metadata = generate_content_metadata(pages)

        # They should be compatible
        assert doc_metadata["type"] == "DocumentType"
        assert content_metadata["pageCount"] == len(pages)

    def test_folder_vs_document_type(self) -> None:
        """Folders and documents should differ only in type field."""
        doc = generate_document_metadata(
            visible_name="Name",
            parent_uuid="parent",
            modified_time=1700000000000,
        )
        folder = generate_folder_metadata(
            name="Name",
            parent_uuid="parent",
        )

        # Documents have lastOpenedPage field that folders don't
        assert set(doc.keys()) - set(folder.keys()) == {"lastOpenedPage"}
        assert doc["type"] == "DocumentType"
        assert folder["type"] == "CollectionType"

    def test_all_metadata_is_json_serializable(self) -> None:
        """All generated metadata should be JSON-serializable."""
        import json

        doc_meta = generate_document_metadata("Doc", "", 1700000000000)
        content_meta = generate_content_metadata(["page-1"])
        page_meta = generate_page_metadata()
        folder_meta = generate_folder_metadata("Folder", "")

        # Should not raise
        json.dumps(doc_meta)
        json.dumps(content_meta)
        json.dumps(page_meta)
        json.dumps(folder_meta)
