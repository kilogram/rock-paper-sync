"""Sync engine for reMarkable-Obsidian synchronization.

Orchestrates the full conversion pipeline:
1. Parse markdown files
2. Check if sync is needed (hash comparison)
3. Ensure folder hierarchy exists
4. Generate reMarkable documents
5. Write files to output directory
6. Update state database
"""

import json
import logging
import time
import uuid as uuid_module
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .generator import RemarkableGenerator
from .parser import parse_markdown_file
from .rm_cloud_client import RmCloudClient
from .rm_cloud_sync import RmCloudSync
from .state import StateManager, SyncRecord

logger = logging.getLogger("rock_paper_sync.converter")


@dataclass
class SyncResult:
    """Result of syncing a single file.

    Attributes:
        path: Path to the markdown file that was synced
        success: Whether sync completed successfully
        remarkable_uuid: UUID of generated reMarkable document (if successful)
        page_count: Number of pages generated (if successful)
        error: Error message (if failed)
    """

    path: Path
    success: bool
    remarkable_uuid: Optional[str] = None
    page_count: Optional[int] = None
    error: Optional[str] = None


class SyncEngine:
    """Orchestrates markdown to reMarkable conversion pipeline.

    Handles:
    - Incremental sync (skip unchanged files)
    - Folder hierarchy creation
    - Error handling and recovery
    - State database updates
    """

    def __init__(self, config: AppConfig, state: StateManager) -> None:
        """Initialize sync engine.

        Args:
            config: Application configuration
            state: State manager for tracking sync status
        """
        self.config = config
        self.state = state
        self.generator = RemarkableGenerator(config.layout)

        # Initialize cloud sync (required)
        try:
            client = RmCloudClient(base_url=config.cloud.base_url)
            self.cloud_sync = RmCloudSync(
                base_url=config.cloud.base_url,
                client=client,
            )
            logger.info("Cloud sync initialized (Sync v3 API)")
        except ValueError as e:
            logger.error(f"Cloud sync initialization failed: {e}")
            logger.error("Device must be registered. Run: rock-paper-sync register <code>")
            raise

        logger.debug("Sync engine initialized")

    def sync_file(self, markdown_path: Path) -> SyncResult:
        """Sync a single markdown file to reMarkable format.

        Full pipeline for one file:
        1. Parse markdown
        2. Check if needs sync (content hash comparison)
        3. Create parent folders if needed
        4. Generate reMarkable document
        5. Write files to output
        6. Update state database

        Args:
            markdown_path: Absolute path to markdown file

        Returns:
            SyncResult indicating success or failure
        """
        try:
            # Validate file exists and is in vault
            if not markdown_path.exists():
                return SyncResult(
                    path=markdown_path,
                    success=False,
                    error=f"File not found: {markdown_path}",
                )

            if not markdown_path.is_relative_to(self.config.sync.obsidian_vault):
                return SyncResult(
                    path=markdown_path,
                    success=False,
                    error=f"File is not in vault: {markdown_path}",
                )

            # Parse markdown
            logger.info(f"Parsing {markdown_path}")
            md_doc = parse_markdown_file(markdown_path)

            # Get relative path for state tracking
            relative_path = str(
                markdown_path.relative_to(self.config.sync.obsidian_vault)
            )

            # Check if needs sync (compare hash)
            current_state = self.state.get_file_state(relative_path)

            if current_state and current_state.content_hash == md_doc.content_hash:
                logger.debug(f"File unchanged, skipping: {relative_path}")
                return SyncResult(
                    path=markdown_path,
                    success=True,
                    remarkable_uuid=current_state.remarkable_uuid,
                    page_count=current_state.page_count,
                )

            # Ensure parent folder hierarchy exists
            parent_uuid = self.ensure_folder_hierarchy(markdown_path)

            # Generate reMarkable document (reuse UUID if updating existing file)
            existing_uuid = current_state.remarkable_uuid if current_state else None
            if existing_uuid:
                logger.info(f"Updating existing document {existing_uuid} for {markdown_path}")
            else:
                logger.info(f"Generating new reMarkable document for {markdown_path}")
            rm_doc = self.generator.generate_document(md_doc, parent_uuid, existing_uuid)

            # Upload via cloud API (Sync v3 protocol)
            self.cloud_sync.upload_document(
                doc_uuid=rm_doc.uuid,
                document_name=rm_doc.metadata.get("visibleName", md_doc.title),
                pages=rm_doc.pages,  # List of (page_uuid, rm_data) tuples
                parent_uuid=parent_uuid,
            )

            # Update state database
            new_state = SyncRecord(
                obsidian_path=relative_path,
                remarkable_uuid=rm_doc.uuid,
                content_hash=md_doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=len(rm_doc.pages),
                status="synced",
            )
            self.state.update_file_state(new_state)
            self.state.log_sync_action(
                relative_path, "synced", f"Generated {len(rm_doc.pages)} page(s)"
            )

            logger.info(
                f"Successfully synced {markdown_path} -> {rm_doc.uuid} "
                f"({len(rm_doc.pages)} page(s))"
            )
            return SyncResult(
                path=markdown_path,
                success=True,
                remarkable_uuid=rm_doc.uuid,
                page_count=len(rm_doc.pages),
            )

        except Exception as e:
            logger.error(f"Failed to sync {markdown_path}: {e}", exc_info=True)
            self.state.log_sync_action(str(markdown_path), "error", str(e))
            return SyncResult(path=markdown_path, success=False, error=str(e))

    def sync_all_changed(self) -> list[SyncResult]:
        """Sync all files that have changed since last sync.

        Uses state database to identify files with different content hashes.
        Errors in individual files don't stop the overall sync.

        Returns:
            List of SyncResults for all processed files
        """
        changed_files = self.state.find_changed_files(
            self.config.sync.obsidian_vault,
            self.config.sync.include_patterns,
            self.config.sync.exclude_patterns,
        )

        logger.info(f"Found {len(changed_files)} file(s) to sync")

        results = []
        for file_path in changed_files:
            result = self.sync_file(file_path)
            results.append(result)

        success_count = sum(1 for r in results if r.success)
        logger.info(f"Sync complete: {success_count}/{len(results)} succeeded")

        return results

    def ensure_folder_hierarchy(self, obsidian_path: Path) -> str:
        """Create reMarkable folders for directory structure.

        Creates folder metadata files for each directory level in the path.
        Uses state database to track existing folder→UUID mappings.

        Args:
            obsidian_path: Absolute path to file in vault

        Returns:
            UUID of immediate parent folder (empty string if file is at vault root)

        Example:
            vault/projects/work/notes.md
            Creates folders: "projects", "projects/work"
            Returns UUID of "projects/work"
        """
        relative_path = obsidian_path.relative_to(self.config.sync.obsidian_vault)

        if not relative_path.parent.parts:
            # File is in vault root
            return ""

        parent_uuid = ""
        folder_path_parts = []

        # Create each folder level
        for part in relative_path.parent.parts:
            folder_path_parts.append(part)
            folder_path = "/".join(folder_path_parts)

            # Check if folder already exists
            existing_uuid = self.state.get_folder_uuid(folder_path)

            if existing_uuid:
                parent_uuid = existing_uuid
            else:
                # Create new folder
                new_uuid = str(uuid_module.uuid4())
                self._create_rm_folder(part, new_uuid, parent_uuid)
                self.state.create_folder_mapping(folder_path, new_uuid)
                parent_uuid = new_uuid
                logger.info(f"Created folder: {folder_path} -> {new_uuid}")

        return parent_uuid

    def _create_rm_folder(self, name: str, uuid: str, parent_uuid: str) -> None:
        """Create reMarkable folder (CollectionType) metadata file.

        Uses RemarkableFilesystem abstraction to handle file structure.

        Args:
            name: Folder display name
            uuid: UUID for this folder
            parent_uuid: UUID of parent folder (empty string for root)
        """
        output_dir = self.config.sync.remarkable_output
        filesystem = RemarkableFilesystem(output_dir)

        # Use filesystem abstraction to create folder
        # This handles: metadata, local files at root level, etc.
        timestamp = int(time.time() * 1000)
        filesystem.write_folder(
            folder_uuid=uuid,
            visible_name=name,
            parent_uuid=parent_uuid,
            modified_time=timestamp,
        )
