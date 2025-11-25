"""Integration layer for rm_cloud sync."""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from .audit import get_audit_logger
from .rm_cloud_client import RmCloudClient
from .sync_v3 import SyncV3Client

logger = logging.getLogger(__name__)


class RmCloudSync:
    """Sync documents to rm_cloud using Sync v3 protocol.

    This class implements the reMarkable Sync v3 protocol for uploading documents
    directly to rm_cloud without filesystem manipulation. Documents sync to the
    device via WebSocket notifications.

    Sync v3 Protocol Overview
    -------------------------

    **hashOfHashesV3 Algorithm**:
    - Concatenate binary SHA256 hashes of all document files (sorted by filename)
    - Compute SHA256 of the concatenated bytes
    - Used for content addressing and deduplication

    **CRDT formatVersion 2** (.content file):
    - Static timestamp counters: "1:1", "1:2", etc. (NOT Unix timestamps)
    - "modifed" field (typo intentional): Actual modification time in milliseconds
    - The "modifed" field signals xochitl that content has changed
    - cPages structure with lexicographically sortable idx values

    **Required Files**:
    - {uuid}.metadata: Document metadata (name, parent, type)
    - {uuid}.content: CRDT page structure (formatVersion 2)
    - {uuid}.local: Empty JSON "{}" (required by xochitl for recognition)
    - {uuid}/{page-uuid}.rm: Binary v6 format page content

    **Double Upload Pattern**:
    - Document index stored under content hash (blob)
    - Also stored under hashOfHashesV3 (document index)
    - Ensures CRDT consistency and content deduplication

    For complete protocol details, see docs/SYNC_PROTOCOL.md.
    """

    def __init__(
        self,
        base_url: str,
        client: Optional[RmCloudClient] = None,
    ):
        """
        Initialize rm_cloud sync.

        Args:
            base_url: Base URL of rm_cloud instance
            client: Optional RmCloudClient (will create default if not provided)
        """
        self.base_url = base_url
        self.client = client or RmCloudClient(base_url=base_url)

        if not self.client.is_registered():
            raise ValueError(
                "Device not registered. Run: rock-paper-sync register <code>"
            )

        # Get user token from device token
        logger.debug("Getting user token from device token")
        user_token = self.client.get_user_token()

        # Create Sync v3 client with user token
        self.sync_client = SyncV3Client(
            base_url=base_url,
            device_token=user_token,  # Actually a user token, not device token
        )

        logger.info(f"rm_cloud Sync v3 initialized")
        logger.info(f"Connected to: {base_url}")

    def _create_metadata_file(
        self, doc_uuid: str, document_name: str, parent_uuid: str
    ) -> bytes:
        """Create .metadata file content."""
        now_ms = int(time.time() * 1000)
        metadata = {
            "visibleName": document_name,
            "type": "DocumentType",
            "parent": parent_uuid,
            "lastModified": str(now_ms),
            "lastOpened": "0",
            "lastOpenedPage": 0,
            "version": 1,
            "pinned": False,
            "synced": True,
            "modified": False,
            "deleted": False,
            "metadatamodified": False,
        }
        return json.dumps(metadata).encode("utf-8")

    def _create_content_file(self, page_uuids: list[str]) -> bytes:
        """Create .content file in CRDT formatVersion 2.

        Args:
            page_uuids: List of page UUIDs in order

        Returns:
            JSON-encoded .content file bytes

        Note:
            The reMarkable device uses STATIC CRDT timestamp counters (e.g., "1:1", "1:2")
            and indicates content updates via the "modifed" field (note: typo is intentional,
            device uses "modifed" not "modified"). The "modifed" field contains Unix timestamp
            in milliseconds and is the primary signal to xochitl that content has changed.
        """
        import uuid as uuid_module
        from datetime import datetime, timezone

        page_count = len(page_uuids)
        device_uuid = str(uuid_module.uuid4())  # Simulate device UUID
        timestamp_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Current time in milliseconds - used for "modifed" field (note the typo!)
        # This is how the device signals that content has been updated
        current_time_ms = int(time.time() * 1000)

        # Generate CRDT pages structure
        # Use lexicographically sortable idx values: "ba", "bb", "bc", etc.
        cPages = {
            "lastOpened": {
                "timestamp": "0:0",  # Static, doesn't change on updates
                "value": ""
            },
            "original": {
                "timestamp": "0:0",
                "value": -1
            },
            "pages": [],
            "uuids": [
                {
                    "first": device_uuid,
                    "second": 1
                }
            ]
        }

        # Generate idx values like "ba", "bb", "bc", ... "bz", "ca", "cb", etc.
        def generate_idx(i: int) -> str:
            """Generate lexicographically sortable index strings."""
            # Start at 'ba' and increment
            if i < 26:
                return f"b{chr(ord('a') + i)}"
            elif i < 52:
                return f"c{chr(ord('a') + (i - 26))}"
            elif i < 78:
                return f"d{chr(ord('a') + (i - 52))}"
            else:
                # Fallback for very large page counts
                return f"z{i:04d}"

        # Create page entries
        for i, page_uuid in enumerate(page_uuids):
            # Use SMALL STATIC CRDT counters - device uses 1, 2, 3... not Unix timestamps!
            # The counter represents the "logical clock" when the field was created, not modified
            page_counter = i + 1

            page_entry = {
                "id": page_uuid,
                "idx": {
                    "timestamp": f"1:{page_counter + 1}",  # e.g., "1:2" for first page
                    "value": generate_idx(i)
                },
                # CRITICAL: "modifed" field (typo is intentional!) with current time in ms
                # This is what tells xochitl the content has been updated!
                "modifed": str(current_time_ms)
            }

            # Add template field (matching device behavior)
            page_entry["template"] = {
                "timestamp": f"1:{page_counter}",  # e.g., "1:1" for first page
                "value": "Blank"
            }

            cPages["pages"].append(page_entry)

        content = {
            "cPages": cPages,
            "coverPageNumber": 0,
            "customZoomCenterX": 0,
            "customZoomCenterY": 936,
            "customZoomOrientation": "portrait",
            "customZoomPageHeight": 1872,
            "customZoomPageWidth": 1404,
            "customZoomScale": 1,
            "documentMetadata": {},
            "extraMetadata": {
                "LastActiveTool": "primary",
                "LastPen": "Ballpointv2",
                "LastTool": "Ballpointv2",
            },
            "fileType": "notebook",
            "fontName": "",
            "formatVersion": 2,
            "lineHeight": -1,
            "margins": 100,
            "orientation": "portrait",
            "pageCount": page_count,
            "pageTags": [],
            "sizeInBytes": "0",
            "tags": [],
            "textAlignment": "left",
            "textScale": 1,
            "zoomMode": "bestFit",
        }
        return json.dumps(content).encode("utf-8")

    def upload_document(
        self,
        doc_uuid: str,
        document_name: str,
        pages: list,
        parent_uuid: str = "",
    ) -> None:
        """
        Upload a document using Sync v3 protocol.

        Args:
            doc_uuid: Document UUID
            document_name: Display name for the document
            pages: List of page data (tuples of page_uuid, rm_data)
            parent_uuid: Parent folder UUID (empty for root)

        Raises:
            Exception: If upload fails
        """
        logger.info(f"Uploading document via Sync v3: {document_name} ({doc_uuid})")

        # Build files dict
        files = {}

        # Extract page UUIDs
        page_uuids = [page_uuid for page_uuid, _ in pages]

        # DEBUG: Log page order
        logger.info(f"Page order being uploaded: {[uuid[:8] for uuid in page_uuids]}")

        # Add metadata file
        files[f"{doc_uuid}.metadata"] = self._create_metadata_file(
            doc_uuid, document_name, parent_uuid
        )

        # Add content file (with CRDT formatVersion 2)
        files[f"{doc_uuid}.content"] = self._create_content_file(page_uuids)
        logger.info(f"Created .content file with page order: {[uuid[:8] for uuid in page_uuids]}")

        # Add .local file (required by xochitl for document recognition)
        files[f"{doc_uuid}.local"] = b"{}"

        # Add page files
        for page_uuid, rm_data in pages:
            files[f"{doc_uuid}/{page_uuid}.rm"] = rm_data

        # Calculate total size for audit logging
        total_size = sum(len(data) for data in files.values())

        # Upload via Sync v3
        self.sync_client.upload_document(
            doc_uuid=doc_uuid,
            files=files,
            broadcast=True,  # Always trigger sync notification
        )

        logger.info(f"Document uploaded successfully: {doc_uuid}")

        # AUDIT: Log cloud upload operation
        audit = get_audit_logger()
        audit.log_cloud_upload(
            doc_uuid=doc_uuid,
            file_count=len(files),
            total_size=total_size,
            broadcast=True,
        )

    def is_sync_enabled(self) -> bool:
        """Check if sync is enabled (device registered)."""
        return self.client.is_registered()

    def get_existing_page_uuids(self, doc_uuid: str) -> list[str]:
        """
        Get existing page UUIDs for a document.

        Args:
            doc_uuid: Document UUID

        Returns:
            List of page UUIDs in order. Empty list if document doesn't exist.
        """
        return self.sync_client.get_document_page_uuids(doc_uuid)

    def download_page_rm_files(
        self, doc_uuid: str, page_uuids: list[str], output_dir: Path
    ) -> list[Path | None]:
        """Download .rm files for document pages to preserve annotations.

        Args:
            doc_uuid: Document UUID
            page_uuids: List of page UUIDs to download
            output_dir: Directory to save .rm files

        Returns:
            List of paths to downloaded .rm files (or None if download failed)
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        rm_file_paths = []

        # Find document in root
        root_docs = self.sync_client.get_root_documents()
        doc_entry = None
        for entry in root_docs:
            if entry.entry_name == doc_uuid:
                doc_entry = entry
                break

        if not doc_entry:
            logger.warning(f"Document {doc_uuid} not found in root")
            return [None] * len(page_uuids)

        # Download document index to get file hashes
        doc_index_content = self.sync_client.download_blob(doc_entry.hash)
        doc_files = self.sync_client.parse_index(doc_index_content)

        # Build map of filename -> hash
        file_map = {entry.entry_name: entry.hash for entry in doc_files}

        for page_uuid in page_uuids:
            output_path = output_dir / f"{page_uuid}.rm"
            rm_filename = f"{doc_uuid}/{page_uuid}.rm"

            try:
                # Find .rm file in document index
                if rm_filename in file_map:
                    blob_hash = file_map[rm_filename]
                    blob_data = self.sync_client.download_blob(blob_hash)
                    output_path.write_bytes(blob_data)
                    rm_file_paths.append(output_path)
                    logger.debug(f"Downloaded {output_path} ({len(blob_data)} bytes)")
                else:
                    logger.warning(f"Could not find .rm file for page {page_uuid}")
                    rm_file_paths.append(None)

            except Exception as e:
                logger.warning(f"Failed to download .rm file for page {page_uuid}: {e}")
                rm_file_paths.append(None)

        return rm_file_paths

    def upload_folder(
        self, folder_uuid: str, folder_name: str, parent_uuid: str = ""
    ) -> None:
        """
        Upload a folder (CollectionType) using Sync v3 protocol.

        Folders are documents with type="CollectionType" and no pages.

        Args:
            folder_uuid: Folder UUID
            folder_name: Display name for the folder
            parent_uuid: Parent folder UUID (empty for root)

        Raises:
            Exception: If upload fails
        """
        logger.info(f"Uploading folder via Sync v3: {folder_name} ({folder_uuid})")

        # Build files dict for folder
        files = {}

        # Create folder metadata (type: CollectionType)
        now_ms = int(time.time() * 1000)
        metadata = {
            "visibleName": folder_name,
            "type": "CollectionType",  # This makes it a folder
            "parent": parent_uuid,
            "lastModified": str(now_ms),
            "lastOpened": "0",
            "lastOpenedPage": 0,
            "version": 1,
            "pinned": False,
            "synced": True,
            "modified": False,
            "deleted": False,
            "metadatamodified": False,
        }
        files[f"{folder_uuid}.metadata"] = json.dumps(metadata).encode("utf-8")

        # Folders have empty content
        files[f"{folder_uuid}.content"] = b"{}"

        # Add .local file (required by xochitl)
        files[f"{folder_uuid}.local"] = b"{}"

        # Upload via Sync v3
        self.sync_client.upload_document(
            doc_uuid=folder_uuid,
            files=files,
            broadcast=False,  # Don't notify for folder creation
        )

        logger.info(f"Folder uploaded successfully: {folder_uuid}")

    def delete_document(self, doc_uuid: str) -> None:
        """
        Delete a document from rm_cloud.

        Args:
            doc_uuid: Document UUID to delete

        Raises:
            Exception: If deletion fails
        """
        logger.info(f"Deleting document from cloud: {doc_uuid}")
        self.sync_client.delete_document(doc_uuid, broadcast=True)
        logger.info(f"Document deleted successfully: {doc_uuid}")

        # AUDIT: Log cloud delete operation
        audit = get_audit_logger()
        audit.log_cloud_delete(
            doc_uuid=doc_uuid,
            broadcast=True,
        )

    def delete_documents_batch(
        self, doc_uuids: list[str], broadcast: bool = True
    ) -> None:
        """
        Delete multiple documents from rm_cloud in a single operation.

        More efficient than multiple delete_document() calls and avoids
        device sync issues when deleting related documents (like nested folders).
        The device receives one notification showing the final state.

        Args:
            doc_uuids: List of document UUIDs to delete
            broadcast: Whether to trigger sync notification to device

        Raises:
            Exception: If deletion fails
        """
        if not doc_uuids:
            logger.warning("delete_documents_batch called with empty list")
            return

        logger.info(f"Deleting {len(doc_uuids)} documents from cloud in batch")
        self.sync_client.delete_documents_batch(doc_uuids, broadcast=broadcast)
        logger.info(f"Successfully deleted {len(doc_uuids)} documents")

        # AUDIT: Log batch delete operation
        audit = get_audit_logger()
        for doc_uuid in doc_uuids:
            audit.log_cloud_delete(
                doc_uuid=doc_uuid,
                broadcast=broadcast,
            )
