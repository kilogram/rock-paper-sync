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
from .metadata import generate_folder_metadata
from .parser import parse_markdown_file
from .state import StateManager, SyncRecord

logger = logging.getLogger("rm_obsidian_sync.converter")


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

            # Generate reMarkable document
            logger.info(f"Generating reMarkable document for {markdown_path}")
            rm_doc = self.generator.generate_document(md_doc, parent_uuid)

            # Write files to output directory
            self.generator.write_document_files(
                rm_doc, self.config.sync.remarkable_output
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

        Args:
            name: Folder display name
            uuid: UUID for this folder
            parent_uuid: UUID of parent folder (empty string for root)
        """
        folder_dir = self.config.sync.remarkable_output / uuid
        folder_dir.mkdir(parents=True, exist_ok=True)

        metadata = generate_folder_metadata(name, parent_uuid)
        metadata_path = folder_dir / f"{uuid}.metadata"
        metadata_path.write_text(json.dumps(metadata, indent=2))

        # Write .local file (required by xochitl for folder recognition)
        local_path = folder_dir / f"{uuid}.local"
        local_path.write_text("{}")

        logger.debug(f"Created folder metadata: {metadata_path}")
