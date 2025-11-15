"""Integration layer for rm_cloud sync."""

import json
import logging
import time
from typing import Optional

from .rm_cloud_client import RmCloudClient
from .sync_v3 import SyncV3Client

logger = logging.getLogger(__name__)


class RmCloudSync:
    """
    Sync documents to rm_cloud using Sync v3 protocol.

    Pure API approach - no filesystem writes required!
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

        # Add metadata file
        files[f"{doc_uuid}.metadata"] = self._create_metadata_file(
            doc_uuid, document_name, parent_uuid
        )

        # Add content file (with CRDT formatVersion 2)
        files[f"{doc_uuid}.content"] = self._create_content_file(page_uuids)

        # Add .local file (required by xochitl for document recognition)
        files[f"{doc_uuid}.local"] = b"{}"

        # Add page files
        for page_uuid, rm_data in pages:
            files[f"{doc_uuid}/{page_uuid}.rm"] = rm_data

        # Upload via Sync v3
        self.sync_client.upload_document(
            doc_uuid=doc_uuid,
            files=files,
            broadcast=True,  # Always trigger sync notification
        )

        logger.info(f"Document uploaded successfully: {doc_uuid}")

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
