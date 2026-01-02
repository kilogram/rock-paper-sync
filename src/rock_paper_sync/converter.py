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

import logging
import time
import uuid as uuid_module
from dataclasses import dataclass, replace
from pathlib import Path

from .annotation_markers_v2 import (
    add_annotation_markers_aligned,
    has_annotation_markers,
    strip_annotation_markers,
)
from .annotation_sync_helper import AnnotationSyncHelper
from .annotations.core.processor import AnnotationProcessor
from .annotations.handlers.highlight_handler import HighlightHandler
from .annotations.handlers.stroke_handler import StrokeHandler
from .audit import get_audit_logger
from .change_detector import ChangeDetector
from .config import AppConfig, VaultConfig
from .generator import RemarkableGenerator
from .ocr.integration import OCRProcessor
from .parser import parse_markdown_file
from .rm_cloud_client import RmCloudClient
from .rm_cloud_sync import RmCloudSync
from .state import StateManager, SyncRecord
from .virtual_state import VirtualDeviceState

logger = logging.getLogger("rock_paper_sync.converter")

# Retry configuration constants
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0
RETRY_BACKOFF_MULTIPLIER = 2

# Batch operation constants
MAX_BATCH_DELETION_SIZE = 100


class ResyncRequiredError(Exception):
    """Exception raised when generation conflict requires resync.

    Indicates that the local state is out of sync with cloud due to
    concurrent modifications. The operation should be retried after
    re-reading cloud state.

    Attributes:
        vault_name: Name of vault requiring resync
        reason: Human-readable reason for resync requirement
        conflict_error: Original GenerationConflictError that triggered this
    """

    def __init__(
        self,
        vault_name: str,
        reason: str,
        conflict_error: Exception | None = None,
    ):
        self.vault_name = vault_name
        self.reason = reason
        self.conflict_error = conflict_error
        super().__init__(f"Resync required for vault '{vault_name}': {reason}")


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
        skipped: True if file was unchanged and not uploaded
    """

    vault_name: str
    path: Path
    success: bool
    remarkable_uuid: str | None = None
    page_count: int | None = None
    error: str | None = None
    skipped: bool = False


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
        cloud_sync: RmCloudSync | None = None,
        generator: RemarkableGenerator | None = None,
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
        self.generator = generator or RemarkableGenerator(
            config.layout, geometry=config.layout.get_device_geometry()
        )

        # Initialize OCR processor if enabled
        self.ocr_processor: OCRProcessor | None = None
        if config.ocr and config.ocr.enabled:
            self.ocr_processor = OCRProcessor(config.ocr, state)
            logger.info(f"OCR processor initialized (provider: {config.ocr.provider})")

        # Initialize annotation processor with handlers
        self.annotation_processor = AnnotationProcessor()
        self.annotation_processor.register_handler(HighlightHandler())
        self.annotation_processor.register_handler(StrokeHandler(self.ocr_processor))
        logger.debug("Annotation processor initialized with highlight and stroke handlers")

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
                logger.error("Device must be registered. Run: rock-paper-sync register <code>")
                raise

        # Initialize annotation sync helper
        self.annotation_helper = AnnotationSyncHelper(
            cloud_sync=self.cloud_sync,
            state=self.state,
            generator=self.generator,
            annotation_processor=self.annotation_processor,
            ocr_processor=self.ocr_processor,
            cache_dir=self.config.cache_dir,
        )
        logger.debug("Annotation sync helper initialized")

        # Initialize change detector
        self.change_detector = ChangeDetector(self.state)
        logger.debug("Change detector initialized")

        logger.debug("Sync engine initialized")

    def sync_file(
        self,
        vault: VaultConfig,
        markdown_path: Path,
        broadcast: bool = True,
        correlation_id: str = "",
    ) -> SyncResult:
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
            broadcast: Whether to broadcast to device (default True)
            correlation_id: Optional correlation ID for operation tracking

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
            logger.info(
                f"[{correlation_id}] Parsing {markdown_path}"
                if correlation_id
                else f"Parsing {markdown_path}"
            )
            md_doc = parse_markdown_file(markdown_path)

            # Get relative path for state tracking
            relative_path = str(markdown_path.relative_to(vault.path))
            file_size = markdown_path.stat().st_size

            # AUDIT: Log sync start with file metadata
            audit = get_audit_logger()
            audit.log_sync_start(
                vault_name=vault.name,
                file_path=relative_path,
                file_hash=md_doc.content_hash,
                file_size=file_size,
            )

            # Check if needs sync (compare semantic content hash)
            current_state = self.state.get_file_state(vault.name, relative_path)

            if current_state and current_state.content_hash == md_doc.content_hash:
                # Semantic content unchanged - check if annotations changed on device
                logger.debug(f"Semantic content unchanged: {vault.name}:{relative_path}")

                # CASE 2: Only annotations changed → download (Phase 2: efficient detection)
                existing_uuid = current_state.remarkable_uuid
                annotation_map = None

                # Check if annotations changed using cloud versioning
                if existing_uuid and self.annotation_helper.should_download_annotations(
                    vault.name, relative_path, existing_uuid
                ):
                    logger.info(f"Downloading new annotations for {vault.name}:{relative_path}")

                    # Download .rm files with new annotations
                    existing_page_uuids = self.cloud_sync.get_existing_page_uuids(existing_uuid)
                    if existing_page_uuids:
                        temp_dir = self.config.cache_dir / "annotations" / existing_uuid
                        existing_rm_files = self.cloud_sync.download_page_rm_files(
                            existing_uuid, existing_page_uuids, temp_dir
                        )

                        # Map annotations from .rm files
                        # Add pagination metadata for Y-position based annotation matching (issue #5)
                        self.generator.paginate_content(
                            md_doc.content
                        )  # Sets page_y_start as side effect

                        annotation_map = self.annotation_helper.build_annotation_map(
                            existing_rm_files, md_doc.content
                        )

                        # Update state with new cloud versioning info
                        _, _, current_gen = self.cloud_sync.get_root_state()
                        current_doc_hash = self.cloud_sync.get_document_index_hash(existing_uuid)

                        updated_state = SyncRecord(
                            vault_name=vault.name,
                            obsidian_path=relative_path,
                            remarkable_uuid=existing_uuid,
                            content_hash=current_state.content_hash,
                            last_sync_time=int(time.time()),
                            page_count=current_state.page_count,
                            status="synced",
                            last_root_generation=current_gen,
                            last_doc_index_hash=current_doc_hash,
                        )
                        self.state.update_file_state(updated_state)

                # Update markers if we have annotations
                if annotation_map:
                    logger.info("Updating annotation markers (content unchanged)")
                    # Filter out None values from downloaded files
                    rm_files = (
                        [f for f in existing_rm_files if f is not None]
                        if "existing_rm_files" in locals()
                        else []
                    )
                    self.annotation_helper.update_annotation_markers(
                        markdown_path,
                        md_doc.content,
                        annotation_map,
                        vault.name,
                        relative_path,
                        rm_files=rm_files,
                    )
                else:
                    logger.debug(
                        f"No annotations to update, skipping: {vault.name}:{relative_path}"
                    )

                return SyncResult(
                    vault_name=vault.name,
                    path=markdown_path,
                    success=True,
                    remarkable_uuid=current_state.remarkable_uuid,
                    page_count=current_state.page_count,
                    skipped=True,
                )

            # Ensure parent folder hierarchy exists (including vault root folder if configured)
            parent_uuid = self.ensure_folder_hierarchy(vault, markdown_path)

            # Generate reMarkable document (reuse UUID if updating existing file)
            # CASE 3: Check if annotations ALSO changed (three-way merge)
            existing_uuid = current_state.remarkable_uuid if current_state else None
            existing_page_uuids = []
            existing_rm_files = []
            annotations_also_changed = False

            if existing_uuid:
                logger.info(f"Updating existing document {existing_uuid} for {markdown_path}")

                # CASE 3 detection: Check if annotations changed on device
                annotations_also_changed = self.annotation_helper.should_download_annotations(
                    vault.name, relative_path, existing_uuid
                )
                if annotations_also_changed:
                    logger.info(
                        f"✓ CASE 3: Both content and annotations changed - "
                        f"three-way merge for {vault.name}:{relative_path}"
                    )

                # Fetch existing page UUIDs to avoid CRDT conflicts
                existing_page_uuids = self.cloud_sync.get_existing_page_uuids(existing_uuid)
                if existing_page_uuids:
                    logger.debug(f"Found {len(existing_page_uuids)} existing pages to reuse")

                    # Download .rm files (fresh from cloud if annotations changed)
                    temp_dir = self.config.cache_dir / "annotations" / existing_uuid
                    existing_rm_files = self.cloud_sync.download_page_rm_files(
                        existing_uuid, existing_page_uuids, temp_dir
                    )
                    status = "fresh from device" if annotations_also_changed else "cached"
                    logger.info(
                        f"Downloaded {len([f for f in existing_rm_files if f])} .rm files ({status})"
                    )
            else:
                logger.info(f"Generating new reMarkable document for {markdown_path}")

            # Strip markers before generating device document (keep device view clean)
            content_for_device = md_doc.content
            raw_content = markdown_path.read_text(encoding="utf-8")

            # Default to original document (will be replaced if markers need stripping)
            clean_doc = md_doc

            if has_annotation_markers(raw_content):
                logger.debug("Stripping annotation markers before device sync")
                # Strip markers and re-parse to get clean ContentBlocks
                clean_content = strip_annotation_markers(raw_content)

                # Write to temp file and parse
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False, encoding="utf-8"
                ) as tmp_file:
                    tmp_file.write(clean_content)
                    tmp_path = Path(tmp_file.name)

                try:
                    # Parse clean content
                    clean_doc = parse_markdown_file(tmp_path)
                    # Preserve original title (temp file has wrong name like "tmp3lvh83u3")
                    clean_doc = replace(clean_doc, title=md_doc.title, path=md_doc.path)
                    content_for_device = clean_doc.content
                    logger.debug("Using clean content (markers stripped) for device generation")
                finally:
                    tmp_path.unlink()

            # Create document with clean content (no markers)
            # Use clean_doc directly - parser strips markers before computing hash
            rm_doc = self.generator.generate_document(
                clean_doc, parent_uuid, existing_uuid, existing_page_uuids, existing_rm_files
            )

            # Generate binary .rm files for each page
            pages_with_data = [
                (page.uuid, self.generator.generate_rm_file(page)) for page in rm_doc.pages
            ]

            # Upload via cloud API (Sync v3 protocol)
            self.cloud_sync.upload_document(
                doc_uuid=rm_doc.uuid,
                document_name=rm_doc.visible_name,
                pages=pages_with_data,  # List of (page_uuid, rm_binary_data) tuples
                parent_uuid=parent_uuid,
                broadcast=broadcast,
            )

            # Create snapshot of clean content (markers stripped) as OLD for next merge
            # This snapshot will be the BASE in future three-way merges
            clean_markdown_content = strip_annotation_markers(markdown_path.read_text())
            self.state.snapshots.snapshot_file(
                vault_name=vault.name,
                file_path=relative_path,
                content=clean_markdown_content.encode("utf-8"),
                file_type="markdown",
                sync_time=int(time.time()),
            )
            logger.debug(f"Created snapshot for {vault.name}:{relative_path}")

            # Add annotation markers to file if we have annotations
            if existing_rm_files and any(existing_rm_files):
                # Map annotations from .rm files (iterate over each page)
                # Add pagination metadata for Y-position based annotation matching (issue #5)
                self.generator.paginate_content(md_doc.content)  # Sets page_y_start as side effect

                annotation_map = self.annotation_helper.build_annotation_map(
                    existing_rm_files, md_doc.content
                )

                if annotation_map:
                    logger.info("Adding annotation markers after sync")
                    # Add markers using ContentBlock alignment
                    marked_content = add_annotation_markers_aligned(md_doc.content, annotation_map)

                    # Write marked content back to file
                    with open(markdown_path, "w", encoding="utf-8") as f:
                        f.write(marked_content)

                    logger.info(
                        f"Added {len(annotation_map)} annotation markers to {vault.name}:{relative_path}"
                    )

            # Update state database with cloud versioning info (Phase 2)
            _, _, current_gen = self.cloud_sync.get_root_state()
            current_doc_hash = self.cloud_sync.get_document_index_hash(rm_doc.uuid)

            new_state = SyncRecord(
                vault_name=vault.name,
                obsidian_path=relative_path,
                remarkable_uuid=rm_doc.uuid,
                content_hash=md_doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=len(rm_doc.pages),
                status="synced",
                last_root_generation=current_gen,
                last_doc_index_hash=current_doc_hash,
            )
            self.state.update_file_state(new_state)
            self.state.log_sync_action(
                vault.name, relative_path, "synced", f"Generated {len(rm_doc.pages)} page(s)"
            )

            # AUDIT: Log successful sync with complete details
            audit.log_sync_success(
                vault_name=vault.name,
                file_path=relative_path,
                remarkable_uuid=rm_doc.uuid,
                page_count=len(rm_doc.pages),
                file_hash=md_doc.content_hash,
                previous_uuid=existing_uuid,
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

            # AUDIT: Log sync failure with error details
            relative_path_str = (
                str(markdown_path.relative_to(vault.path))
                if markdown_path.is_relative_to(vault.path)
                else str(markdown_path)
            )
            audit = get_audit_logger()
            audit.log_sync_failure(
                vault_name=vault.name,
                file_path=relative_path_str,
                error=str(e),
            )

            self.state.log_sync_action(vault.name, relative_path_str, "error", str(e))
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
            self.state.log_sync_action(vault_name, relative_path, "deleted", "Removed from cloud")
            logger.info(f"Successfully deleted {vault_name}:{relative_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete {vault_name}:{relative_path}: {e}", exc_info=True)
            self.state.log_sync_action(vault_name, relative_path, "error", f"Delete failed: {e}")
            return False

    def _retry_with_backoff(
        self,
        operation,
        operation_name: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_RETRY_BASE_DELAY,
    ) -> None:
        """Retry an operation with exponential backoff.

        Used for non-concurrency failures (network errors, timeouts).
        Concurrency failures should trigger resync instead.

        Args:
            operation: Callable that performs the operation
            operation_name: Human-readable operation name for logging
            max_retries: Maximum number of retry attempts (default 3)
            base_delay: Base delay in seconds, doubled each retry (default 1.0)

        Raises:
            GenerationConflictError: Re-raised immediately (triggers resync)
            Exception: After max retries exhausted
        """
        from .sync_v3 import GenerationConflictError

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                operation()
                if attempt > 0:
                    logger.info(f"{operation_name} succeeded on retry {attempt + 1}")
                return

            except GenerationConflictError:
                # Don't retry concurrency errors - trigger resync instead
                raise

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = base_delay * (RETRY_BACKOFF_MULTIPLIER**attempt)
                    logger.warning(
                        f"{operation_name} failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"{operation_name} failed after {max_retries} attempts: {e}", exc_info=True
                    )

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{operation_name} failed: No attempts made")

    def _find_annotation_only_changes(
        self, vault: VaultConfig, content_changed_files: list[Path], correlation_id: str
    ) -> list[Path]:
        """Find files where annotations changed but content didn't.

        Checks all previously-synced files (not in content_changed_files) for
        annotation changes using cloud versioning primitives.

        Uses batched cloud state fetching to avoid redundant API calls:
        - Fetches root state once (not per-file)
        - Extracts all document hashes in one pass
        - Compares against stored state without additional network requests

        Args:
            vault: Vault configuration
            content_changed_files: Files already identified as content-changed
            correlation_id: Correlation ID for logging

        Returns:
            List of file paths with annotation-only changes
        """
        annotation_changed_files: list[Path] = []

        # Get all previously-synced files for this vault using StateManager API
        synced_records = self.state.get_all_synced_files(vault.name)
        synced_files = [(r.obsidian_path, r.remarkable_uuid) for r in synced_records]

        logger.debug(
            f"[{correlation_id}] Checking {len(synced_files)} synced file(s) for annotation-only changes"
        )

        # Convert content-changed files to relative paths for exclusion
        content_changed_paths = {str(f.relative_to(vault.path)) for f in content_changed_files}

        # Batch fetch: Get cloud state once for all files
        # This avoids redundant get_root_state() calls for each file
        _, _, current_generation = self.cloud_sync.get_root_state()

        # Collect all doc UUIDs to check (excluding content-changed files)
        files_to_check: list[tuple[str, str, Path]] = []
        for relative_path, doc_uuid in synced_files:
            if relative_path in content_changed_paths:
                logger.debug(
                    f"[{correlation_id}] Skipping {relative_path} (content already changed)"
                )
                continue

            file_path = vault.path / relative_path
            if not file_path.exists():
                logger.debug(f"[{correlation_id}] Skipping {relative_path} (file deleted)")
                continue

            files_to_check.append((relative_path, doc_uuid, file_path))

        if not files_to_check:
            logger.debug(f"[{correlation_id}] No files to check for annotation changes")
            return []

        # Batch fetch all document hashes in one call
        doc_uuids = [doc_uuid for _, doc_uuid, _ in files_to_check]
        doc_hash_map = self.cloud_sync.get_document_index_hashes_batch(doc_uuids)

        logger.debug(
            f"[{correlation_id}] Fetched {len(doc_hash_map)} document hashes in single batch"
        )

        # Check each file for annotation changes using pre-fetched data
        for relative_path, doc_uuid, file_path in files_to_check:
            logger.debug(
                f"[{correlation_id}] Checking annotations for {vault.name}:{relative_path} (uuid={doc_uuid})"
            )
            if self.annotation_helper.should_download_annotations(
                vault.name,
                relative_path,
                doc_uuid,
                current_generation=current_generation,
                doc_hash_map=doc_hash_map,
            ):
                logger.info(
                    f"[{correlation_id}] Annotation-only change detected: {vault.name}:{relative_path}"
                )
                annotation_changed_files.append(file_path)
            else:
                logger.debug(
                    f"[{correlation_id}] No annotation changes for {vault.name}:{relative_path}"
                )

        if annotation_changed_files:
            logger.info(
                f"[{correlation_id}] Found {len(annotation_changed_files)} file(s) with annotation-only changes"
            )
        else:
            logger.debug(f"[{correlation_id}] No annotation-only changes found")

        return annotation_changed_files

    def sync_vault(self, vault: VaultConfig) -> list[SyncResult]:
        """Sync all changed files in a specific vault.

        Uses VirtualDeviceState pattern for atomic multi-step sync:
        1. Find deleted and changed files (local discovery)
        2. Read current cloud state (Phase 1)
        3. Stage deletions in virtual state (Phase 2a)
        4. Upload changed files (individual operations)
        5. Single atomic root update (Phase 3)
        6. Update local state (Phase 4)

        ATOMICITY SEMANTICS:
        - File deletions are atomic: either all delete or none do (via root update)
        - File uploads are non-atomic (one per file), but deletions are batched atomically
        - Generation conflicts (409) trigger ResyncRequiredError (no automatic retry)
        - State updates deferred until after atomic cloud operation succeeds

        Args:
            vault: Vault configuration

        Returns:
            List of SyncResults for all processed files in this vault
        """
        from .sync_v3 import GenerationConflictError

        # Generate correlation ID for tracking this operation
        correlation_id = str(uuid_module.uuid4())[:8]

        logger.info(f"[{correlation_id}] Syncing vault '{vault.name}' at {vault.path}")

        # Discover deleted and changed files locally using ChangeDetector
        deleted_files = self.change_detector.find_deleted_files(vault.name, vault.path)
        changed_files = self.change_detector.find_changed_files(
            vault.name,
            vault.path,
            vault.include_patterns,
            vault.exclude_patterns,
        )

        # Also check for annotation-only changes (Phase 2: annotation detection)
        # Files where content unchanged but annotations may have changed on device
        annotation_only_files = self._find_annotation_only_changes(
            vault, changed_files, correlation_id
        )

        # Combine content changes and annotation-only changes
        all_files_to_process = changed_files + annotation_only_files

        if not deleted_files and not all_files_to_process:
            logger.info(f"[{correlation_id}] No changes to sync for vault '{vault.name}'")
            return []

        # If only uploads and no deletions, handle simple case (no atomic update needed)
        if not deleted_files:
            logger.info(
                f"[{correlation_id}] Syncing {len(all_files_to_process)} new/changed files (no deletions)"
            )
            results = []
            for file_path in all_files_to_process:
                try:
                    # For uploads only, can broadcast per file
                    result = self.sync_file(
                        vault, file_path, broadcast=True, correlation_id=correlation_id
                    )
                    results.append(result)

                except GenerationConflictError as e:
                    logger.warning(
                        f"[{correlation_id}] Generation conflict uploading {file_path.name}: {e}"
                    )
                    raise ResyncRequiredError(
                        vault_name=vault.name,
                        reason=f"generation conflict uploading {file_path.name}",
                        conflict_error=e,
                    )

            success_count = sum(1 for r in results if r.success)
            logger.info(
                f"[{correlation_id}] Vault '{vault.name}' sync complete: {success_count}/{len(results)} succeeded"
            )
            return results

        # PHASE 1: Read current cloud state
        try:
            current_entries, current_hash, current_gen = self.cloud_sync.get_root_state()
            hash_str = current_hash[:8] if current_hash else "None"
            logger.debug(
                f"[{correlation_id}] Phase 1 (Read): {len(current_entries)} entries, "
                f"hash={hash_str}, gen={current_gen}"
            )
        except Exception as e:
            logger.error(f"[{correlation_id}] Failed to read cloud state: {e}")
            raise

        # Initialize virtual state with current cloud state
        # Use empty string if no root hash exists (no root in cloud)
        virtual_state = VirtualDeviceState(current_entries, current_hash or "", current_gen)

        # PHASE 2: Stage deletions in virtual state (no cloud calls yet)
        deleted_count = 0
        for relative_path, uuid in deleted_files:
            if virtual_state.delete_document(uuid):
                deleted_count += 1

        logger.debug(
            f"[{correlation_id}] Phase 2 (Stage): Staged {deleted_count} deletions in virtual state"
        )

        # Upload changed files (individual operations)
        results = []
        if all_files_to_process:
            logger.info(f"[{correlation_id}] Uploading {len(all_files_to_process)} changed file(s)")

        for file_path in all_files_to_process:
            try:
                # Upload without broadcasting (will broadcast once in atomic update)
                result = self.sync_file(
                    vault, file_path, broadcast=False, correlation_id=correlation_id
                )
                results.append(result)

            except GenerationConflictError as e:
                logger.warning(
                    f"[{correlation_id}] Generation conflict uploading {file_path.name}: {e}"
                )
                raise ResyncRequiredError(
                    vault_name=vault.name,
                    reason=f"generation conflict uploading {file_path.name}",
                    conflict_error=e,
                )

        # PHASE 3: Single atomic root update (for deletions)
        if virtual_state.has_changes():
            logger.info(
                f"[{correlation_id}] Phase 3 (Atomic Update): Applying deletion of {deleted_count} files "
                f"(gen {current_gen})"
            )

            try:
                # Single atomic root update (uploads index blob + updates root)
                self.cloud_sync.apply_virtual_state(
                    virtual_state=virtual_state,
                    broadcast=True,  # Single broadcast for all deletions + uploads
                )
                logger.info(f"[{correlation_id}] Root updated atomically")

            except GenerationConflictError as e:
                logger.warning(
                    f"[{correlation_id}] Generation conflict during atomic update: {e}. "
                    "Concurrent cloud modification detected. Triggering resync..."
                )
                raise ResyncRequiredError(
                    vault_name=vault.name,
                    reason="generation conflict during sync",
                    conflict_error=e,
                )
        else:
            # No deletions, but uploads might have happened
            if results:
                logger.info(f"[{correlation_id}] Uploads complete (no deletions to apply)")

        # PHASE 4: Update local state (only after Phase 3 succeeds if there were deletions)
        logger.info(f"[{correlation_id}] Phase 4 (Update Local State)")

        for relative_path, uuid in deleted_files:
            self.state.delete_file_state(vault.name, relative_path)
            self.state.log_sync_action(
                vault.name,
                relative_path,
                "deleted",
                f"Removed from cloud (batch of {len(deleted_files)})",
            )

        success_count = sum(1 for r in results if r.success)
        logger.info(
            f"[{correlation_id}] Vault '{vault.name}' sync complete: "
            f"{len(deleted_files)} deletions, {success_count}/{len(results)} uploads succeeded"
        )

        return results

    def sync_all_changed(self, vault_name: str | None = None) -> list[SyncResult]:
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

        # Clean up old snapshots to prevent unbounded storage growth
        self.state.snapshots.cleanup_old_snapshots()

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
                logger.info(f"Created vault root folder: '{vault.remarkable_folder}' -> {new_uuid}")

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

    def unsync_vault(self, vault_name: str, delete_from_cloud: bool = False) -> tuple[int, int]:
        """Remove sync state for all files in a vault.

        Uses VirtualDeviceState pattern for atomic multi-step deletion:
        1. Read current cloud state (Phase 1)
        2. Stage all deletions in virtual state (Phase 2)
        3. Single atomic root update (Phase 3)
        4. Update local state (Phase 4)

        ATOMICITY SEMANTICS:
        - Cloud operations are truly atomic: either all deletions apply or none do
        - Single root update with optimistic concurrency control (generation number)
        - State updates deferred until after atomic cloud operation succeeds
        - Generation conflicts (409) trigger ResyncRequiredError (no automatic retry)
        - Achieves 1 generation increment per unsync operation (not 1 per deletion)

        Args:
            vault_name: Name of vault to unsync
            delete_from_cloud: If True, also delete files from reMarkable cloud

        Returns:
            Tuple of (files_removed, files_deleted_from_cloud)

        Raises:
            ValueError: If vault_name not found in configuration
            ResyncRequiredError: If generation conflict during cloud operations
        """
        from .sync_v3 import GenerationConflictError

        # Generate correlation ID for tracking this operation
        correlation_id = str(uuid_module.uuid4())[:8]

        # Find the vault config
        vault_config = next((v for v in self.config.sync.vaults if v.name == vault_name), None)
        if not vault_config:
            raise ValueError(f"Vault '{vault_name}' not found in configuration")

        logger.info(
            f"[{correlation_id}] Unsyncing vault '{vault_name}' (delete_from_cloud={delete_from_cloud})"
        )

        # Get all synced files for this vault
        synced_files = self.state.get_all_synced_files(vault_name=vault_name)
        folders = self.state.get_all_folders(vault_name)

        files_removed = 0
        files_deleted = 0

        # If not deleting from cloud, just update local state
        if not delete_from_cloud:
            logger.info(f"[{correlation_id}] Removing local state only (not deleting from cloud)")

            # Remove synced files from state
            for record in synced_files:
                self.state.delete_file_state(vault_name, record.obsidian_path)
                files_removed += 1

            # Remove folder mappings from state
            for folder_path, _ in folders:
                self.state.delete_folder_mapping(vault_name, folder_path)

            logger.info(
                f"[{correlation_id}] Vault '{vault_name}' local state cleared: {files_removed} files"
            )

            # AUDIT: Log unsync operation
            audit = get_audit_logger()
            audit.log_unsync(
                vault_name=vault_name,
                files_removed=files_removed,
                files_deleted_from_cloud=0,
                delete_from_cloud=False,
            )

            return files_removed, 0

        # If no files or folders to delete, nothing to do
        if not synced_files and not folders:
            logger.info(
                f"[{correlation_id}] No files or folders to delete for vault '{vault_name}'"
            )
            return 0, 0

        # PHASE 1: Read current cloud state
        try:
            current_entries, current_hash, current_gen = self.cloud_sync.get_root_state()
            hash_str = current_hash[:8] if current_hash else "None"
            logger.debug(
                f"[{correlation_id}] Phase 1 (Read): {len(current_entries)} entries, "
                f"hash={hash_str}, gen={current_gen}"
            )
        except Exception as e:
            logger.error(f"[{correlation_id}] Failed to read cloud state: {e}")
            raise

        # Initialize virtual state with current cloud state
        # Use empty string if no root hash exists (no root in cloud)
        virtual_state = VirtualDeviceState(current_entries, current_hash or "", current_gen)

        # PHASE 2: Stage all deletions in virtual state (no cloud calls yet)
        deleted_count = 0
        all_uuids = [record.remarkable_uuid for record in synced_files] + [
            folder_uuid for _, folder_uuid in folders
        ]

        for uuid in all_uuids:
            if virtual_state.delete_document(uuid):
                deleted_count += 1

        logger.debug(
            f"[{correlation_id}] Phase 2 (Stage): Staged {deleted_count} deletions in virtual state"
        )

        # PHASE 3: Single atomic root update
        if not virtual_state.has_changes():
            logger.info(
                f"[{correlation_id}] No changes to apply (all items already absent from cloud)"
            )
            # Still update local state since we want to unsync
            for record in synced_files:
                self.state.delete_file_state(vault_name, record.obsidian_path)
                files_removed += 1
            for folder_path, _ in folders:
                self.state.delete_folder_mapping(vault_name, folder_path)
            return files_removed, 0

        logger.info(
            f"[{correlation_id}] Phase 3 (Atomic Update): Applying deletion of {deleted_count} items "
            f"(gen {current_gen})"
        )

        try:
            # Single atomic root update (uploads index blob + updates root)
            self.cloud_sync.apply_virtual_state(
                virtual_state=virtual_state,
                broadcast=True,
            )
            logger.info(f"[{correlation_id}] Root updated atomically")

        except GenerationConflictError as e:
            logger.warning(
                f"[{correlation_id}] Generation conflict during atomic update: {e}. "
                "Concurrent cloud modification detected. Triggering resync..."
            )
            raise ResyncRequiredError(
                vault_name=vault_name,
                reason="generation conflict during unsync",
                conflict_error=e,
            )

        # PHASE 4: Update local state (only after Phase 3 succeeds)
        logger.info(f"[{correlation_id}] Phase 4 (Update Local State)")

        # Remove synced files from state
        for record in synced_files:
            self.state.delete_file_state(vault_name, record.obsidian_path)
            self.state.log_sync_action(
                vault_name,
                record.obsidian_path,
                "deleted",
                f"Removed from cloud via unsync (atomic, {deleted_count} total)",
            )
            files_removed += 1

        # Remove folder mappings from state
        for folder_path, _ in folders:
            self.state.delete_folder_mapping(vault_name, folder_path)

        # Track deleted count for return value
        files_deleted = len(synced_files)

        logger.info(
            f"[{correlation_id}] Vault '{vault_name}' unsynced atomically: "
            f"{files_removed} files removed from state, {files_deleted} deleted from cloud"
        )

        # AUDIT: Log unsync operation with complete details
        audit = get_audit_logger()
        audit.log_unsync(
            vault_name=vault_name,
            files_removed=files_removed,
            files_deleted_from_cloud=files_deleted,
            delete_from_cloud=True,
        )

        return files_removed, files_deleted

    def unsync_all(self, delete_from_cloud: bool = False) -> dict[str, tuple[int, int]]:
        """Remove sync state for all vaults.

        Args:
            delete_from_cloud: If True, also delete files from reMarkable cloud

        Returns:
            Dictionary mapping vault name to (files_removed, files_deleted_from_cloud)
        """
        logger.info(f"Unsyncing all vaults (delete_from_cloud={delete_from_cloud})")

        results = {}
        for vault in self.config.sync.vaults:
            try:
                removed, deleted = self.unsync_vault(
                    vault.name, delete_from_cloud=delete_from_cloud
                )
                results[vault.name] = (removed, deleted)
            except Exception as e:
                logger.error(f"Failed to unsync vault '{vault.name}': {e}", exc_info=True)
                results[vault.name] = (0, 0)

        total_removed = sum(r[0] for r in results.values())
        total_deleted = sum(r[1] for r in results.values())
        logger.info(
            f"All vaults unsynced: {total_removed} files removed from state, "
            f"{total_deleted} deleted from cloud"
        )

        return results
