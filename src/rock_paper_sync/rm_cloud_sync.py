"""Integration layer for rm_cloud sync."""

import logging
from pathlib import Path
from typing import Optional

from .rm_filesystem import RemarkableFilesystem
from .rm_cloud_client import RmCloudClient

logger = logging.getLogger(__name__)


class RmCloudSync:
    """
    Sync documents to rm_cloud and trigger live reload on xochitl.

    This combines:
    1. Writing files to rm_cloud's data directory
    2. Triggering sync-complete notification via API
    """

    def __init__(
        self,
        rm_cloud_data_dir: Path,
        user_id: str,
        client: Optional[RmCloudClient] = None,
    ):
        """
        Initialize rm_cloud sync.

        Args:
            rm_cloud_data_dir: Root data directory of rm_cloud instance
            user_id: User ID (email/username used in rm_cloud)
            client: Optional RmCloudClient (will create default if not provided)
        """
        self.rm_cloud_data_dir = rm_cloud_data_dir
        self.user_id = user_id
        self.client = client or RmCloudClient()

        # Calculate the user's output directory
        # Structure: {DataDir}/users/{user_id}/
        self.user_output_dir = rm_cloud_data_dir / "users" / user_id

        if not self.user_output_dir.exists():
            logger.warning(
                f"rm_cloud user directory does not exist: {self.user_output_dir}"
            )
            logger.warning("Will create it when writing files")

        # Create filesystem that writes to rm_cloud directory
        self.filesystem = RemarkableFilesystem(output_dir=self.user_output_dir)

        logger.info(f"rm_cloud sync initialized for user: {user_id}")
        logger.info(f"Writing to: {self.user_output_dir}")

    def write_document(
        self,
        doc_uuid: str,
        document_name: str,
        pages: list,
        parent_uuid: str = "",
        trigger_sync: bool = True,
    ) -> None:
        """
        Write a document and optionally trigger sync notification.

        Args:
            doc_uuid: Document UUID
            document_name: Display name for the document
            pages: List of page data (from generator)
            parent_uuid: Parent folder UUID (empty for root)
            trigger_sync: Whether to trigger sync notification after writing

        Raises:
            Exception: If writing fails or sync trigger fails
        """
        # Write files using the filesystem
        logger.info(f"Writing document to rm_cloud: {document_name} ({doc_uuid})")
        self.filesystem.write_document(
            doc_uuid=doc_uuid,
            document_name=document_name,
            pages=pages,
            parent_uuid=parent_uuid,
        )

        # Trigger sync notification if requested
        if trigger_sync and self.client.is_registered():
            try:
                notification_id = self.client.trigger_sync()
                logger.info(f"Sync notification sent: {notification_id}")
            except Exception as e:
                logger.warning(f"Failed to trigger sync notification: {e}")
                logger.warning("Document written but xochitl may not reload automatically")
        elif trigger_sync and not self.client.is_registered():
            logger.warning("Device not registered - cannot trigger sync notification")
            logger.warning("Run: rock-paper-sync register <code>")

    def is_sync_enabled(self) -> bool:
        """Check if automatic sync triggering is enabled (device registered)."""
        return self.client.is_registered()
