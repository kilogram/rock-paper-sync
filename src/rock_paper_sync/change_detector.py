"""Change detection for file synchronization.

This module extracts change detection logic from StateManager to provide
a cleaner separation of concerns:
- StateManager: Persistence (read/write state)
- ChangeDetector: Business logic (detect what needs syncing)

This separation enables future extension for bidirectional sync (M7):
- Local file changes (current - Obsidian edits)
- Device annotation changes (via AnnotationSyncHelper)
- Device content changes (future - for M7)

Usage:
    state = StateManager(db_path)
    detector = ChangeDetector(state)

    changed = detector.find_changed_files(vault_name, vault_path, include, exclude)
    deleted = detector.find_deleted_files(vault_name, vault_path)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rock_paper_sync.state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class ChangeResult:
    """Result of change detection for a vault.

    Attributes:
        changed_files: Files that are new or have changed content
        deleted_files: Files that were synced but no longer exist locally
    """

    changed_files: list[Path]
    deleted_files: list[tuple[str, str]]  # (relative_path, remarkable_uuid)

    @property
    def has_changes(self) -> bool:
        """Return True if there are any changes to process."""
        return bool(self.changed_files or self.deleted_files)

    @property
    def total_changes(self) -> int:
        """Return total number of changes."""
        return len(self.changed_files) + len(self.deleted_files)


class ChangeDetector:
    """Detects changes in files for sync operations.

    Provides methods for detecting:
    - Local file changes (content hash comparison)
    - Deleted files (files in state but missing on disk)

    Future extension points (for M7 bidirectional sync):
    - Device content changes
    - Device annotation changes (currently in AnnotationSyncHelper)

    Example:
        >>> state = StateManager(db_path)
        >>> detector = ChangeDetector(state)
        >>> result = detector.detect_all_changes(vault_name, vault_path, include, exclude)
        >>> print(f"Found {result.total_changes} changes")
    """

    def __init__(self, state_manager: StateManager) -> None:
        """Initialize change detector.

        Args:
            state_manager: StateManager for accessing sync state
        """
        self._state = state_manager

    def detect_all_changes(
        self,
        vault_name: str,
        vault_path: Path,
        include_patterns: list[str],
        exclude_patterns: list[str],
    ) -> ChangeResult:
        """Detect all changes (changed + deleted files) for a vault.

        Convenience method that runs both change detection types.

        Args:
            vault_name: Name of the vault
            vault_path: Path to Obsidian vault root
            include_patterns: Glob patterns for files to include
            exclude_patterns: Glob patterns for files to exclude

        Returns:
            ChangeResult with changed and deleted files
        """
        changed = self.find_changed_files(
            vault_name, vault_path, include_patterns, exclude_patterns
        )
        deleted = self.find_deleted_files(vault_name, vault_path)

        return ChangeResult(changed_files=changed, deleted_files=deleted)

    def find_changed_files(
        self,
        vault_name: str,
        vault_path: Path,
        include_patterns: list[str],
        exclude_patterns: list[str],
    ) -> list[Path]:
        """Find files that need syncing based on content hash.

        Compares current file hashes with stored hashes to identify changes.
        Uses semantic hash (annotation markers stripped) for comparison.

        Args:
            vault_name: Name of the vault
            vault_path: Path to Obsidian vault root
            include_patterns: Glob patterns for files to include
            exclude_patterns: Glob patterns for files to exclude

        Returns:
            List of Path objects for files that have changed or are new
        """
        from rock_paper_sync.parser import parse_markdown_file

        changed: list[Path] = []

        for pattern in include_patterns:
            for file_path in vault_path.glob(pattern):
                # Skip if not a file
                if not file_path.is_file():
                    continue

                # Check exclusions
                if self.is_file_excluded(file_path, vault_path, exclude_patterns):
                    logger.debug(f"Excluded: {file_path}")
                    continue

                relative_path = str(file_path.relative_to(vault_path))

                # Parse file to get semantic hash (markers stripped by parser)
                # This ensures consistent comparison with state.content_hash
                try:
                    md_doc = parse_markdown_file(file_path)
                    current_hash = md_doc.content_hash  # Semantic hash
                except Exception as e:
                    logger.warning(f"Failed to parse {file_path}: {e}")
                    continue

                # Get existing state
                state = self._state.get_file_state(vault_name, relative_path)

                # File is new or changed if no state or hash differs
                if state is None:
                    logger.debug(f"New file: {vault_name}:{relative_path}")
                    changed.append(file_path)
                elif state.content_hash != current_hash:
                    logger.debug(f"Changed file: {vault_name}:{relative_path}")
                    changed.append(file_path)

        logger.info(f"Found {len(changed)} changed files in vault '{vault_name}'")
        return changed

    def find_deleted_files(self, vault_name: str, vault_path: Path) -> list[tuple[str, str]]:
        """Find files that have been deleted from vault but still exist in state.

        Returns list of (relative_path, remarkable_uuid) tuples for deleted files.

        Args:
            vault_name: Name of the vault
            vault_path: Path to Obsidian vault root

        Returns:
            List of (relative_path, uuid) tuples for files that no longer exist
        """
        deleted: list[tuple[str, str]] = []

        # Get all synced files for this vault
        synced_files = self._state.get_all_synced_files(vault_name)

        for state in synced_files:
            absolute_path = vault_path / state.obsidian_path

            if not absolute_path.exists():
                logger.debug(
                    f"Deleted file: {vault_name}:{state.obsidian_path} "
                    f"(UUID: {state.remarkable_uuid})"
                )
                deleted.append((state.obsidian_path, state.remarkable_uuid))

        logger.info(f"Found {len(deleted)} deleted files in vault '{vault_name}'")
        return deleted

    def is_file_excluded(
        self, file_path: Path, vault_path: Path, exclude_patterns: list[str]
    ) -> bool:
        """Check if file matches any exclude pattern.

        Args:
            file_path: Absolute path to file
            vault_path: Vault root path
            exclude_patterns: List of glob patterns to exclude

        Returns:
            True if file should be excluded, False otherwise
        """
        relative = file_path.relative_to(vault_path)
        for pattern in exclude_patterns:
            if relative.match(pattern):
                return True
        return False

    def needs_sync(self, vault_name: str, relative_path: str, file_path: Path) -> bool:
        """Check if a specific file needs syncing.

        Useful for checking individual files without scanning the entire vault.

        Args:
            vault_name: Name of the vault
            relative_path: Path relative to vault root
            file_path: Absolute path to the file

        Returns:
            True if file is new or changed, False otherwise
        """
        from rock_paper_sync.parser import parse_markdown_file

        if not file_path.exists():
            return False

        try:
            md_doc = parse_markdown_file(file_path)
            current_hash = md_doc.content_hash
        except Exception as e:
            logger.warning(f"Failed to parse {file_path}: {e}")
            return False

        state = self._state.get_file_state(vault_name, relative_path)

        if state is None:
            return True  # New file

        return state.content_hash != current_hash
