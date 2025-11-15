"""High-level abstraction for managing reMarkable filesystem structure.

This module provides a clean API for creating and updating reMarkable documents
and folders, hiding the complexity of the file structure and preventing common
bugs related to file placement and page management.
"""

import json
import logging
from pathlib import Path
from typing import Any

from .metadata import (
    generate_content_metadata,
    generate_document_metadata,
    generate_folder_metadata,
    generate_page_metadata,
)

logger = logging.getLogger("rm_obsidian_sync.rm_filesystem")


class RemarkableFilesystem:
    """Manages reMarkable document filesystem structure.

    This class encapsulates all knowledge about where files should be placed
    in the reMarkable filesystem, including:
    - Root-level metadata files (.metadata, .content, .local)
    - Subdirectory page files (.rm, -metadata.json)
    - Update handling (cleanup of old pages)
    - Atomic operations where possible

    File structure managed:
    ```
    output_dir/
    ├── {uuid}.metadata        # Document/folder metadata (root level)
    ├── {uuid}.content         # Page list (documents only, root level)
    ├── {uuid}.local           # Empty JSON (root level, required by xochitl)
    └── {uuid}/                # Document directory (documents only)
        ├── {page-uuid}.rm     # Page content
        └── {page-uuid}-metadata.json  # Page settings
    ```

    Attributes:
        output_dir: Base directory for reMarkable files
    """

    def __init__(self, output_dir: Path) -> None:
        """Initialize filesystem manager.

        Args:
            output_dir: Base output directory for reMarkable files
        """
        self.output_dir = output_dir
        logger.debug(f"RemarkableFilesystem initialized with output_dir={output_dir}")

    def write_document(
        self,
        doc_uuid: str,
        visible_name: str,
        parent_uuid: str,
        modified_time: int,
        pages: list[tuple[str, bytes]],
    ) -> None:
        """Write a complete document with all its pages.

        This method handles both new document creation and updates. For updates,
        it automatically cleans up old page files before writing new ones.

        Args:
            doc_uuid: Document UUID
            visible_name: Display name in reMarkable UI
            parent_uuid: Parent folder UUID (empty string for root)
            modified_time: Last modification timestamp (milliseconds)
            pages: List of (page_uuid, rm_bytes) tuples

        Raises:
            OSError: If file writing fails

        Note:
            This operation is atomic at the filesystem level - if any write fails,
            previously written files remain, but the document may be incomplete.
            The .content file is written last to minimize the window of inconsistency.
        """
        is_update = self.document_exists(doc_uuid)

        if is_update:
            logger.debug(f"Updating existing document {doc_uuid}")
            self._cleanup_old_pages(doc_uuid)
        else:
            logger.debug(f"Creating new document {doc_uuid}")

        # Create document directory for page files
        doc_dir = self.output_dir / doc_uuid
        doc_dir.mkdir(parents=True, exist_ok=True)

        # Write root-level metadata files
        self._write_document_metadata(doc_uuid, visible_name, parent_uuid, modified_time)
        self._write_local_file(doc_uuid)

        # Write page files (in subdirectory)
        for page_uuid, rm_bytes in pages:
            self._write_page(doc_dir, page_uuid, rm_bytes)

        # Write .content file last (makes document visible to xochitl)
        self._write_content_metadata(doc_uuid, [page_uuid for page_uuid, _ in pages])

        logger.info(
            f"{'Updated' if is_update else 'Created'} document {doc_uuid} "
            f"({visible_name}) with {len(pages)} page(s)"
        )

    def write_folder(
        self,
        folder_uuid: str,
        visible_name: str,
        parent_uuid: str,
        modified_time: int,
    ) -> None:
        """Write folder metadata.

        Folders only have root-level metadata files - no subdirectory or pages.

        Args:
            folder_uuid: Folder UUID
            visible_name: Display name in reMarkable UI
            parent_uuid: Parent folder UUID (empty string for root)
            modified_time: Last modification timestamp (milliseconds)

        Raises:
            OSError: If file writing fails
        """
        # Folders have no subdirectory or pages
        self._write_folder_metadata(folder_uuid, visible_name, parent_uuid, modified_time)
        self._write_local_file(folder_uuid)

        logger.info(f"Created folder {folder_uuid} ({visible_name})")

    def document_exists(self, doc_uuid: str) -> bool:
        """Check if a document exists.

        A document is considered to exist if its .metadata file is present.

        Args:
            doc_uuid: Document UUID to check

        Returns:
            True if document exists, False otherwise
        """
        return (self.output_dir / f"{doc_uuid}.metadata").exists()

    def delete_document(self, doc_uuid: str) -> None:
        """Delete a document and all its files.

        Removes:
        - Root-level .metadata, .content, .local files
        - Document subdirectory with all page files

        Args:
            doc_uuid: Document UUID to delete

        Note:
            This operation is NOT atomic - if deletion fails partway through,
            some files may remain. However, xochitl will ignore incomplete documents.
        """
        # Delete root-level files
        for suffix in [".metadata", ".content", ".local"]:
            filepath = self.output_dir / f"{doc_uuid}{suffix}"
            if filepath.exists():
                filepath.unlink()

        # Delete document directory and all page files
        doc_dir = self.output_dir / doc_uuid
        if doc_dir.exists():
            for page_file in doc_dir.iterdir():
                page_file.unlink()
            doc_dir.rmdir()

        logger.info(f"Deleted document {doc_uuid}")

    # Private helper methods

    def _cleanup_old_pages(self, doc_uuid: str) -> None:
        """Remove old page files from document directory.

        This is called before updating a document to prevent accumulation
        of orphaned page files when the page count changes.

        Args:
            doc_uuid: Document UUID
        """
        doc_dir = self.output_dir / doc_uuid
        if not doc_dir.exists():
            return

        # Remove all .rm and -metadata.json files
        for pattern in ["*.rm", "*-metadata.json"]:
            for old_file in doc_dir.glob(pattern):
                old_file.unlink()
                logger.debug(f"Cleaned up old page file: {old_file.name}")

    def _write_document_metadata(
        self, doc_uuid: str, visible_name: str, parent_uuid: str, modified_time: int
    ) -> None:
        """Write .metadata file at root level."""
        metadata = generate_document_metadata(
            visible_name=visible_name,
            parent_uuid=parent_uuid,
            modified_time=modified_time,
        )
        filepath = self.output_dir / f"{doc_uuid}.metadata"
        filepath.write_text(json.dumps(metadata, indent=2))

    def _write_folder_metadata(
        self, folder_uuid: str, visible_name: str, parent_uuid: str, modified_time: int
    ) -> None:
        """Write folder .metadata file at root level.

        Note: modified_time parameter is accepted for consistency but not used,
        as generate_folder_metadata creates its own timestamp.
        """
        metadata = generate_folder_metadata(
            name=visible_name,
            parent_uuid=parent_uuid,
        )
        filepath = self.output_dir / f"{folder_uuid}.metadata"
        filepath.write_text(json.dumps(metadata, indent=2))

    def _write_content_metadata(self, doc_uuid: str, page_uuids: list[str]) -> None:
        """Write .content file at root level."""
        content = generate_content_metadata(page_uuids)
        filepath = self.output_dir / f"{doc_uuid}.content"
        filepath.write_text(json.dumps(content, indent=2))

    def _write_local_file(self, uuid: str) -> None:
        """Write .local file at root level (required by xochitl)."""
        filepath = self.output_dir / f"{uuid}.local"
        filepath.write_text("{}")

    def _write_page(self, doc_dir: Path, page_uuid: str, rm_bytes: bytes) -> None:
        """Write page files in document subdirectory."""
        # Write .rm file
        (doc_dir / f"{page_uuid}.rm").write_bytes(rm_bytes)

        # Write page metadata
        page_meta = generate_page_metadata()
        (doc_dir / f"{page_uuid}-metadata.json").write_text(
            json.dumps(page_meta, indent=2)
        )
