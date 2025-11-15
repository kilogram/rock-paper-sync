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

    def _create_content_file(self, page_count: int) -> bytes:
        """Create .content file."""
        content = {
            "fileType": "notebook",
            "fontName": "",
            "lastOpenedPage": 0,
            "lineHeight": -1,
            "margins": 180,
            "orientation": "portrait",
            "pageCount": page_count,
            "pages": [],
            "textScale": 1,
            "transform": {
                "m11": 1,
                "m12": 0,
                "m13": 0,
                "m21": 0,
                "m22": 1,
                "m23": 0,
                "m31": 0,
                "m32": 0,
                "m33": 1,
            },
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

        # Add metadata file
        files[f"{doc_uuid}.metadata"] = self._create_metadata_file(
            doc_uuid, document_name, parent_uuid
        )

        # Add content file
        files[f"{doc_uuid}.content"] = self._create_content_file(len(pages))

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
