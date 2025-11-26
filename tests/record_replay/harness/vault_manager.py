"""Vault filesystem interaction manager for online and offline testing.

Abstracts vault file/folder operations allowing the same test code to work
in both modes:

1. **Online Mode**: User prompted for actions, operations recorded
2. **Offline Mode**: Vault restored from snapshots, operations silent
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class VaultOperation:
    """Records a vault filesystem operation."""

    type: str  # create_file, delete_file, create_folder, delete_folder
    path: str  # Relative path in vault
    content: str | None = None  # For create_file operations
    timestamp: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "type": self.type,
            "path": self.path,
            "content": self.content,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VaultOperation":
        """Create from dictionary."""
        return cls(
            type=data.get("type", ""),
            path=data.get("path", ""),
            content=data.get("content"),
            timestamp=data.get("timestamp", ""),
        )


class VaultInteractionManager(ABC):
    """Abstract interface for vault filesystem operations.

    Supports both online mode (user interaction, recording) and offline mode
    (replaying from testdata snapshots).

    Example:
        vault = OnlineVault(vault_dir, bench, testdata_store)
        vault.start_test("folder_deletion_001")

        # Create file in folder
        path = vault.create_file("projects/doc.md", "# Content")

        # Test operations...

        # Capture final state
        vault.snapshot_vault("final")

        vault.end_test("folder_deletion_001", success=True)
    """

    @abstractmethod
    def create_file(self, rel_path: str, content: str) -> Path:
        """Create a file in the vault.

        Online mode: Prompts user for confirmation, creates file, records operation
        Offline mode: Creates file silently from test setup

        Args:
            rel_path: Path relative to vault root (e.g., "projects/doc.md")
            content: File content

        Returns:
            Absolute path to created file

        Raises:
            RuntimeError: If creation fails
        """
        ...

    @abstractmethod
    def delete_file(self, rel_path: str) -> None:
        """Delete a file from the vault.

        Online mode: Prompts user confirmation, deletes, records operation
        Offline mode: Deletes file silently

        Args:
            rel_path: Path relative to vault root

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        ...

    @abstractmethod
    def create_folder(self, rel_path: str) -> Path:
        """Create a folder in the vault.

        Args:
            rel_path: Path relative to vault root

        Returns:
            Absolute path to created folder
        """
        ...

    @abstractmethod
    def delete_folder(self, rel_path: str) -> None:
        """Delete a folder from the vault.

        Args:
            rel_path: Path relative to vault root

        Raises:
            FileNotFoundError: If folder doesn't exist
        """
        ...

    @abstractmethod
    def get_vault_state(self) -> dict[str, Any]:
        """Get current vault file structure.

        Returns:
            Dict with keys: files, folders, total_size
        """
        ...

    @abstractmethod
    def snapshot_vault(self, name: str) -> Path:
        """Capture current vault state to snapshot.

        Online mode: Saves full copy of vault to testdata
        Offline mode: No-op (already in testdata)

        Args:
            name: Snapshot name (e.g., "initial", "final")

        Returns:
            Path to snapshot directory (or empty path in offline mode)

        Raises:
            RuntimeError: If snapshot fails
        """
        ...

    @abstractmethod
    def restore_vault(self, name: str) -> None:
        """Restore vault from snapshot.

        Online mode: No-op (no replay needed)
        Offline mode: Restores from testdata snapshot

        Args:
            name: Snapshot name to restore

        Raises:
            FileNotFoundError: If snapshot not found
        """
        ...

    def start_test(self, test_id: str) -> None:
        """Begin a test session.

        Called by test harness at the start. Online mode uses this to
        initialize operation recording.

        Args:
            test_id: Unique identifier for this test run
        """
        pass  # Default no-op, online mode overrides

    def end_test(self, test_id: str) -> None:
        """End a test session.

        Called by test harness at the end. Online mode uses this to finalize
        artifact capture and save operations.
        Assumes test succeeded (failed tests raise exceptions before reaching here).

        Args:
            test_id: Test identifier (same as start_test)
        """
        pass  # Default no-op, online mode overrides
