"""Workspace management for device testing.

Handles test workspace setup, configuration, state management, and cleanup.
"""

import hashlib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bench import Bench


class WorkspaceManager:
    """Manages test workspace for device tests.

    Handles:
    - Workspace directory creation
    - Config file generation
    - State directory management
    - Document setup and cleanup
    - Cloud sync operations

    The workspace structure:
        workspace/
        ├── config.toml      # Test configuration
        ├── document.md      # Test document
        ├── .state/          # Sync state database
        ├── .cache/          # Annotation cache
        └── logs/            # Sync and audit logs
    """

    DEFAULT_DEVICE_FOLDER = "DeviceBench"

    def __init__(
        self,
        workspace_dir: Path,
        repo_root: Path,
        bench: "Bench",
        device_folder: str | None = None,
    ) -> None:
        """Initialize workspace manager.

        Args:
            workspace_dir: Path to workspace directory
            repo_root: Path to repository root
            bench: Bench utilities for logging and commands
            device_folder: Folder name on reMarkable device
        """
        self.workspace_dir = workspace_dir
        self.repo_root = repo_root
        self.bench = bench
        self.device_folder = device_folder or self.DEFAULT_DEVICE_FOLDER

        # Derived paths
        self.config_file = workspace_dir / "config.toml"
        self.state_dir = workspace_dir / ".state"
        self.cache_dir = workspace_dir / ".cache"
        self.log_dir = workspace_dir / "logs"
        self.test_doc = workspace_dir / "document.md"

    def setup(self) -> None:
        """Create workspace directories and config file."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._write_config()
        self.bench.ok(f"Created workspace: {self.workspace_dir}")

    def _write_config(self) -> None:
        """Write test configuration file."""
        config = f"""# Auto-generated test config
[cloud]
base_url = "http://localhost:3000"

[paths]
state_database = "{self.state_dir}/state.db"
cache_dir = "{self.cache_dir}"

[logging]
level = "debug"
file = "{self.log_dir}/sync.log"

[layout]
lines_per_page = 28

[ocr]
enabled = true
provider = "runpods"
confidence_threshold = 0.5

[[vaults]]
name = "device-bench"
path = "{self.workspace_dir}"
remarkable_folder = "{self.device_folder}"
include_patterns = ["document.md"]
exclude_patterns = [".state/**", "logs/**", ".cache/**"]
"""
        self.config_file.write_text(config)

    def reset(self) -> None:
        """Reset workspace state (unsync and clear state)."""
        # Try to unsync from cloud first
        if self.config_file.exists():
            self.bench.run_unsync(self.config_file, delete_from_cloud=True)

        # Clear state directory
        if self.state_dir.exists():
            shutil.rmtree(self.state_dir)
            self.bench.ok("Removed state directory")

        # Remove test document
        if self.test_doc.exists():
            self.test_doc.unlink()
            self.bench.ok("Removed test document")

        # Clear old logs (keep directory)
        for log_file in self.log_dir.glob("*.json"):
            log_file.unlink()

    def cleanup(self) -> None:
        """Full cleanup including cloud unsync."""
        self.bench.info("Cleaning up...")

        try:
            # Unsync from cloud
            if self.config_file.exists():
                self.bench.run_unsync(self.config_file, delete_from_cloud=True)

            # Remove state
            if self.state_dir.exists():
                shutil.rmtree(self.state_dir)

            # Remove test document
            if self.test_doc.exists():
                self.test_doc.unlink()

            self.bench.ok("Cleanup complete")
        except Exception as e:
            self.bench.error(f"Cleanup error: {e}")

    def setup_document(self, source: Path) -> None:
        """Copy source document to workspace.

        Args:
            source: Path to source markdown file
        """
        shutil.copy(source, self.test_doc)
        self.bench.ok(f"Setup document from {source.name}")

    def run_sync(self, desc: str = "Sync") -> tuple[int, str, str]:
        """Run sync command.

        Args:
            desc: Description for logging

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        return self.bench.run_sync(self.config_file, desc)

    def file_hash(self, path: Path) -> str:
        """Calculate SHA-256 hash of file.

        Args:
            path: Path to file

        Returns:
            Hex-encoded hash string
        """
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def get_document_content(self) -> str:
        """Get current test document content.

        Returns:
            Document content as string
        """
        return self.test_doc.read_text()

    def update_document_content(self, content: str) -> None:
        """Update test document content.

        Args:
            content: New document content
        """
        self.test_doc.write_text(content)

    def get_document_uuid(self) -> str | None:
        """Get reMarkable UUID for test document from state database.

        Returns:
            Document UUID or None if not synced
        """
        import sqlite3

        db_path = self.state_dir / "state.db"
        if not db_path.exists():
            return None

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT remarkable_uuid FROM sync_state "
                "WHERE vault_name = 'device-bench' AND obsidian_path = 'document.md'"
            )
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def get_cached_rm_files(self) -> list[Path]:
        """Get list of cached .rm files for test document.

        Returns:
            List of .rm file paths
        """
        doc_uuid = self.get_document_uuid()
        if not doc_uuid:
            return []

        cache_dir = self.cache_dir / "annotations" / doc_uuid
        if not cache_dir.exists():
            return []

        return list(cache_dir.glob("*.rm"))
