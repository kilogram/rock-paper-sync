"""Sync engine for reMarkable-Obsidian synchronization.

Orchestrates the complete conversion and sync pipeline from Obsidian vaults
to reMarkable cloud via Sync v3 protocol.

Sync Pipeline
-------------

For each vault configured in the application:

1. **File Discovery**: Scan vault for markdown files matching include/exclude patterns
2. **Change Detection**: Compare file content hash against state database
3. **Folder Hierarchy**: Ensure parent folders exist on reMarkable
   - Create vault root folder if remarkable_folder is configured
   - Create nested subfolders matching Obsidian directory structure
   - Reuse existing folder UUIDs from state database
4. **Document Generation**: Convert markdown to reMarkable format
   - Parse markdown with mistune (see parser.py)
   - Paginate content (see generator.py)
   - Generate binary .rm files with rmscene
5. **Cloud Upload**: Upload via Sync v3 protocol (see rm_cloud_sync.py)
   - Upload .metadata, .content, .local, and .rm files
   - Reuse existing page UUIDs to avoid CRDT conflicts on updates
   - Trigger WebSocket sync notification to device
6. **State Update**: Record sync in SQLite database
   - Store remarkable_uuid, content_hash, sync timestamp
   - Log to sync_history for status reporting

Incremental Sync
----------------

Only files with changed content are re-synced:
- SHA-256 hash comparison against last sync
- Reuse document UUID for updates (overwrites existing document)
- Reuse page UUIDs to maintain CRDT consistency
- Skip unchanged files entirely (no cloud API calls)

Multi-Vault Support
-------------------

Each vault is synced independently:
- State database tracks (vault_name, obsidian_path) pairs
- Optional vault-specific folder on reMarkable (remarkable_folder config)
- Per-vault statistics and history tracking
- CLI --vault flag to sync specific vault
"""

import json
import logging
import time
import uuid as uuid_module
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AppConfig, VaultConfig
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
        vault_name: Name of the vault
        path: Path to the markdown file that was synced
        success: Whether sync completed successfully
        remarkable_uuid: UUID of generated reMarkable document (if successful)
        page_count: Number of pages generated (if successful)
        error: Error message (if failed)
    """

    vault_name: str
    path: Path
    success: bool
    remarkable_uuid: Optional[str] = None
    page_count: Optional[int] = None
    error: Optional[str] = None


class SyncEngine:
    """Orchestrates markdown to reMarkable conversion pipeline.

    This is the main coordinator for syncing Obsidian vaults to reMarkable.
    It combines all pipeline components (parser, generator, cloud sync, state)
    into a cohesive sync engine with incremental updates and error recovery.

    Responsibilities
    ----------------

    **Incremental Sync**:
    - Hash-based change detection (skip unchanged files)
    - Reuse document/page UUIDs for updates (preserves device annotations)
    - Only sync files matching include/exclude patterns

    **Folder Management**:
    - Create vault root folder if remarkable_folder configured
    - Mirror Obsidian directory structure on reMarkable
    - Maintain folder UUID mappings in state database

    **Error Handling**:
    - Graceful failure (continue syncing other files)
    - Detailed error logging to sync_history
    - Return SyncResult for each file (success/error details)

    **State Tracking**:
    - Update state database after successful sync
    - Record sync history for status/statistics
    - Track per-vault file states with composite keys

    Key Methods
    -----------

    - `sync_file()`: Sync single markdown file to reMarkable
    - `sync_vault()`: Sync all changed files in a vault
    - `sync_all_changed()`: Sync all changed files across all vaults
    - `ensure_folder_hierarchy()`: Create folder structure for a file
    """

    def __init__(
        self,
        config: AppConfig,
        state: StateManager,
        cloud_sync: Optional[RmCloudSync] = None,
        generator: Optional[RemarkableGenerator] = None,
    ) -> None:
        """Initialize sync engine.

        Args:
            config: Application configuration
            state: State manager for tracking sync status
            cloud_sync: Cloud sync client (will be created if not provided)
            generator: Document generator (will be created if not provided)

        Raises:
            ValueError: If cloud sync initialization fails
        """
        self.config = config
        self.state = state
        self.generator = generator or RemarkableGenerator(config.layout)

        # Initialize cloud sync (injected or created)
        if cloud_sync is not None:
            self.cloud_sync = cloud_sync
            logger.debug("Using injected cloud sync client")
        else:
            # Create default cloud sync
            try:
                client = RmCloudClient(base_url=config.cloud.base_url)
                self.cloud_sync = RmCloudSync(
                    base_url=config.cloud.base_url,
                    client=client,
                )
                logger.info("Cloud sync initialized (Sync v3 API)")
            except ValueError as e:
                logger.error(f"Cloud sync initialization failed: {e}")
                logger.error(
                    "Device must be registered. Run: rock-paper-sync register <code>"
                )
                raise

        logger.debug("Sync engine initialized")

    def sync_file(self, vault: VaultConfig, markdown_path: Path) -> SyncResult:
        """Sync a single markdown file to reMarkable format.

        Full pipeline for one file:
        1. Parse markdown
        2. Check if needs sync (content hash comparison)
        3. Create parent folders if needed
        4. Generate reMarkable document
        5. Write files to output
        6. Update state database

        Args:
            vault: Vault configuration
            markdown_path: Absolute path to markdown file

        Returns:
            SyncResult indicating success or failure
        """
        try:
            # Validate file exists and is in vault
            if not markdown_path.exists():
                return SyncResult(
                    vault_name=vault.name,
                    path=markdown_path,
                    success=False,
                    error=f"File not found: {markdown_path}",
                )

            if not markdown_path.is_relative_to(vault.path):
                return SyncResult(
                    vault_name=vault.name,
                    path=markdown_path,
                    success=False,
                    error=f"File is not in vault: {markdown_path}",
                )

            # Parse markdown
            logger.info(f"Parsing {markdown_path}")
            md_doc = parse_markdown_file(markdown_path)

            # Get relative path for state tracking
            relative_path = str(markdown_path.relative_to(vault.path))

            # Check if needs sync (compare hash)
            current_state = self.state.get_file_state(vault.name, relative_path)

            if current_state and current_state.content_hash == md_doc.content_hash:
                logger.debug(f"File unchanged, skipping: {vault.name}:{relative_path}")
                return SyncResult(
                    vault_name=vault.name,
                    path=markdown_path,
                    success=True,
                    remarkable_uuid=current_state.remarkable_uuid,
                    page_count=current_state.page_count,
                )

            # Ensure parent folder hierarchy exists (including vault root folder if configured)
            parent_uuid = self.ensure_folder_hierarchy(vault, markdown_path)

            # Generate reMarkable document (reuse UUID if updating existing file)
            existing_uuid = current_state.remarkable_uuid if current_state else None
            existing_page_uuids = []

            if existing_uuid:
                logger.info(f"Updating existing document {existing_uuid} for {markdown_path}")
                # Fetch existing page UUIDs to avoid CRDT conflicts
                existing_page_uuids = self.cloud_sync.get_existing_page_uuids(existing_uuid)
                if existing_page_uuids:
                    logger.debug(f"Found {len(existing_page_uuids)} existing pages to reuse")
            else:
                logger.info(f"Generating new reMarkable document for {markdown_path}")

            rm_doc = self.generator.generate_document(
                md_doc, parent_uuid, existing_uuid, existing_page_uuids
            )

            # Generate binary .rm files for each page
            pages_with_data = [
                (page.uuid, self.generator.generate_rm_file(page))
                for page in rm_doc.pages
            ]

            # Upload via cloud API (Sync v3 protocol)
            self.cloud_sync.upload_document(
                doc_uuid=rm_doc.uuid,
                document_name=rm_doc.visible_name,
                pages=pages_with_data,  # List of (page_uuid, rm_binary_data) tuples
                parent_uuid=parent_uuid,
            )

            # Update state database
            new_state = SyncRecord(
                vault_name=vault.name,
                obsidian_path=relative_path,
                remarkable_uuid=rm_doc.uuid,
                content_hash=md_doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=len(rm_doc.pages),
                status="synced",
            )
            self.state.update_file_state(new_state)
            self.state.log_sync_action(
                vault.name, relative_path, "synced", f"Generated {len(rm_doc.pages)} page(s)"
            )

            logger.info(
                f"Successfully synced {vault.name}:{markdown_path} -> {rm_doc.uuid} "
                f"({len(rm_doc.pages)} page(s))"
            )
            return SyncResult(
                vault_name=vault.name,
                path=markdown_path,
                success=True,
                remarkable_uuid=rm_doc.uuid,
                page_count=len(rm_doc.pages),
            )

        except Exception as e:
            logger.error(f"Failed to sync {vault.name}:{markdown_path}: {e}", exc_info=True)
            self.state.log_sync_action(vault.name, str(markdown_path), "error", str(e))
            return SyncResult(
                vault_name=vault.name, path=markdown_path, success=False, error=str(e)
            )

    def delete_file(self, vault_name: str, relative_path: str, uuid: str) -> bool:
        """Delete a file from the cloud and state database.

        Args:
            vault_name: Name of the vault
            relative_path: Relative path in vault
            uuid: reMarkable UUID to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Deleting {vault_name}:{relative_path} (UUID: {uuid})")
            self.cloud_sync.delete_document(uuid)
            self.state.delete_file_state(vault_name, relative_path)
            self.state.log_sync_action(vault_name, relative_path, "deleted", f"Removed from cloud")
            logger.info(f"Successfully deleted {vault_name}:{relative_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete {vault_name}:{relative_path}: {e}", exc_info=True)
            self.state.log_sync_action(
                vault_name, relative_path, "error", f"Delete failed: {e}"
            )
            return False

    def sync_vault(self, vault: VaultConfig) -> list[SyncResult]:
        """Sync all changed files in a specific vault.

        Args:
            vault: Vault configuration

        Returns:
            List of SyncResults for all processed files in this vault
        """
        logger.info(f"Syncing vault '{vault.name}' at {vault.path}")

        # Handle deletions first
        deleted_files = self.state.find_deleted_files(vault.name, vault.path)

        if deleted_files:
            logger.info(f"Processing {len(deleted_files)} deleted file(s) in '{vault.name}'")
            for relative_path, uuid in deleted_files:
                self.delete_file(vault.name, relative_path, uuid)

        # Then handle changed/new files
        changed_files = self.state.find_changed_files(
            vault.name,
            vault.path,
            vault.include_patterns,
            vault.exclude_patterns,
        )

        logger.info(f"Found {len(changed_files)} file(s) to sync in '{vault.name}'")

        results = []
        for file_path in changed_files:
            result = self.sync_file(vault, file_path)
            results.append(result)

        success_count = sum(1 for r in results if r.success)
        logger.info(
            f"Vault '{vault.name}' sync complete: {success_count}/{len(results)} succeeded"
        )

        return results

    def sync_all_changed(self, vault_name: Optional[str] = None) -> list[SyncResult]:
        """Sync all files that have changed since last sync.

        Uses state database to identify files with different content hashes.
        Errors in individual files don't stop the overall sync.

        Also handles file deletions.

        Args:
            vault_name: Optional vault name to sync. If None, syncs all vaults.

        Returns:
            List of SyncResults for all processed files
        """
        results = []

        # Determine which vaults to sync
        if vault_name:
            vaults = [v for v in self.config.sync.vaults if v.name == vault_name]
            if not vaults:
                logger.error(f"Vault '{vault_name}' not found in configuration")
                return results
        else:
            vaults = self.config.sync.vaults

        # Sync each vault
        for vault in vaults:
            vault_results = self.sync_vault(vault)
            results.extend(vault_results)

        total_success = sum(1 for r in results if r.success)
        logger.info(f"Total sync complete: {total_success}/{len(results)} succeeded")

        return results

    def ensure_folder_hierarchy(self, vault: VaultConfig, obsidian_path: Path) -> str:
        """Create reMarkable folders for directory structure.

        Creates folder metadata files for each directory level in the path.
        If vault has a remarkable_folder configured, creates that as the root.
        Uses state database to track existing folder→UUID mappings.

        Args:
            vault: Vault configuration
            obsidian_path: Absolute path to file in vault

        Returns:
            UUID of immediate parent folder (empty string if file is at vault root with no vault folder)

        Example:
            vault='work' with remarkable_folder='Work Notes'
            vault/projects/notes.md
            Creates: "Work Notes" (root) -> "Work Notes/projects"
            Returns UUID of "Work Notes/projects"
        """
        relative_path = obsidian_path.relative_to(vault.path)

        # Start with vault root folder (if configured)
        parent_uuid = ""
        folder_path_parts = []

        if vault.remarkable_folder:
            # Check if vault root folder exists
            existing_uuid = self.state.get_folder_uuid(vault.name, "")
            if existing_uuid:
                parent_uuid = existing_uuid
            else:
                # Create vault root folder
                new_uuid = str(uuid_module.uuid4())
                self._create_rm_folder(vault.remarkable_folder, new_uuid, "")
                self.state.create_folder_mapping(vault.name, "", new_uuid)
                parent_uuid = new_uuid
                logger.info(
                    f"Created vault root folder: '{vault.remarkable_folder}' -> {new_uuid}"
                )

        # If file is directly in vault root, return parent (vault folder UUID or empty)
        if not relative_path.parent.parts:
            return parent_uuid

        # Create each subfolder level
        for part in relative_path.parent.parts:
            folder_path_parts.append(part)
            folder_path = "/".join(folder_path_parts)

            # Check if folder already exists
            existing_uuid = self.state.get_folder_uuid(vault.name, folder_path)

            if existing_uuid:
                parent_uuid = existing_uuid
            else:
                # Create new folder
                new_uuid = str(uuid_module.uuid4())
                self._create_rm_folder(part, new_uuid, parent_uuid)
                self.state.create_folder_mapping(vault.name, folder_path, new_uuid)
                parent_uuid = new_uuid
                logger.info(f"Created folder: {vault.name}:{folder_path} -> {new_uuid}")

        return parent_uuid

    def _create_rm_folder(self, name: str, uuid: str, parent_uuid: str) -> None:
        """Create reMarkable folder (CollectionType) via cloud API.

        Args:
            name: Folder display name
            uuid: UUID for this folder
            parent_uuid: UUID of parent folder (empty string for root)
        """
        # Upload folder via cloud sync
        self.cloud_sync.upload_folder(
            folder_uuid=uuid,
            folder_name=name,
            parent_uuid=parent_uuid,
        )
