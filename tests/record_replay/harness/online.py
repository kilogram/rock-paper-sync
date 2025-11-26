"""Online device implementation for real device testing.

Wraps interaction with a physical reMarkable device, prompting users
for manual actions and automatically capturing testdata for later replay.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from .prompts import user_prompt
from .protocol import DeviceInteractionManager, DocumentState
from .testdata import TestdataStore

if TYPE_CHECKING:
    from .bench import Bench
    from .workspace import WorkspaceManager


class OnlineDevice(DeviceInteractionManager):
    """Device implementation for online (real device) testing.

    Features:
    - Prompts user for device actions (annotating, syncing)
    - Runs sync commands through workspace
    - Automatically captures testdata for offline replay
    - Collects .rm files from local cache after sync

    Usage:
        device = OnlineDevice(workspace, testdata_store, bench)
        device.start_test("annotation_roundtrip_001")

        doc_uuid = device.upload_document(workspace.test_doc)
        state = device.wait_for_annotations(doc_uuid)

        device.end_test("annotation_roundtrip_001", success=True)
    """

    def __init__(
        self,
        workspace: "WorkspaceManager",
        testdata_store: TestdataStore,
        bench: "Bench",
    ) -> None:
        """Initialize online device.

        Args:
            workspace: Workspace manager for sync operations
            testdata_store: Store for captured testdata
            bench: Bench utilities for logging
        """
        self.workspace = workspace
        self.testdata_store = testdata_store
        self.bench = bench
        self._current_test_id: str | None = None
        self._capture_enabled: bool = True

    def start_test(self, test_id: str) -> None:
        """Begin capturing artifacts for a test.

        Args:
            test_id: Unique identifier for this test run
        """
        self._current_test_id = test_id
        self.bench.info(f"Started test: {test_id} (capture enabled)")

    def end_test(self, test_id: str, success: bool) -> None:
        """End test and finalize capture.

        Args:
            test_id: Test identifier (should match start_test)
            success: Whether the test passed
        """
        if success and self._current_test_id == test_id:
            self.bench.ok(f"Test {test_id} completed successfully, artifacts captured")
        self._current_test_id = None

    def upload_document(self, markdown_path: Path) -> str:
        """Upload document via sync.

        Args:
            markdown_path: Path to markdown file

        Returns:
            Document UUID

        Raises:
            RuntimeError: If sync fails or UUID not found
        """
        ret, out, err = self.workspace.run_sync("Upload document")

        if ret != 0:
            raise RuntimeError(f"Failed to upload document: {err}")

        doc_uuid = self.workspace.get_document_uuid()
        if not doc_uuid:
            raise RuntimeError("Document UUID not found after sync")

        self.bench.ok(f"Uploaded document: {doc_uuid}")
        return doc_uuid

    def wait_for_annotations(
        self, doc_uuid: str, timeout: float = 300.0
    ) -> DocumentState:
        """Wait for user to annotate and sync back.

        Prompts user to:
        1. Open document on reMarkable
        2. Add annotations
        3. Wait for cloud sync

        Then downloads annotations and captures artifacts.

        Args:
            doc_uuid: Document UUID to wait for
            timeout: Timeout in seconds (for display only)

        Returns:
            Document state with annotations
        """
        # Prompt user to annotate on device
        device_folder = self.workspace.device_folder
        if not user_prompt(
            "Annotate document",
            [
                f"Open '{device_folder}/document' on your reMarkable",
                "Add annotations (highlights, strokes, handwriting)",
                "Wait for cloud sync indicator to complete",
                f"You have {int(timeout)}s before timeout",
            ],
        ):
            raise RuntimeError("User cancelled annotation step")

        # Run sync to download annotations
        ret, out, err = self.workspace.run_sync("Download annotations")

        if ret != 0:
            raise RuntimeError(f"Failed to download annotations: {err}")

        # Get document state
        state = self.get_document_state(doc_uuid)

        # Auto-capture artifacts if test is running
        if self._capture_enabled and self._current_test_id:
            self._capture_artifacts(doc_uuid, state)

        return state

    def trigger_sync(self) -> None:
        """Run sync command."""
        ret, out, err = self.workspace.run_sync("Sync")
        if ret != 0:
            raise RuntimeError(f"Sync failed: {err}")

    def get_document_state(self, doc_uuid: str) -> DocumentState:
        """Get current document state from local cache.

        Args:
            doc_uuid: Document UUID

        Returns:
            Document state with .rm files from cache
        """
        # Get cached .rm files
        rm_files: dict[str, bytes] = {}
        page_uuids: list[str] = []

        cached_files = self.workspace.get_cached_rm_files()
        for rm_path in sorted(cached_files):
            page_uuid = rm_path.stem
            page_uuids.append(page_uuid)
            rm_files[page_uuid] = rm_path.read_bytes()

        has_annotations = len(rm_files) > 0

        if has_annotations:
            self.bench.observe(f"Found {len(rm_files)} .rm file(s) with annotations")
        else:
            self.bench.warn("No .rm files found in cache")

        return DocumentState(
            doc_uuid=doc_uuid,
            page_uuids=page_uuids,
            rm_files=rm_files,
            has_annotations=has_annotations,
        )

    def _capture_artifacts(self, doc_uuid: str, state: DocumentState) -> None:
        """Capture test artifacts for offline replay.

        Args:
            doc_uuid: Document UUID
            state: Current document state
        """
        if not self._current_test_id:
            return

        test_id = self._current_test_id

        try:
            save_path = self.testdata_store.save_artifacts(
                test_id=test_id,
                doc_uuid=doc_uuid,
                page_uuids=state.page_uuids,
                rm_files=state.rm_files,
                source_markdown=self.workspace.test_doc,
                description=f"Auto-captured from online test: {test_id}",
                metadata={
                    "device_folder": self.workspace.device_folder,
                    "has_annotations": str(state.has_annotations),
                },
            )
            self.bench.ok(f"Captured artifacts: {save_path}")
        except Exception as e:
            self.bench.error(f"Failed to capture artifacts: {e}")

    def disable_capture(self) -> None:
        """Disable automatic testdata capture."""
        self._capture_enabled = False

    def enable_capture(self) -> None:
        """Enable automatic testdata capture."""
        self._capture_enabled = True

    def unsync_vault(self, vault_name: str | None = None) -> tuple[int, int]:
        """Unsync entire vault from cloud.

        Args:
            vault_name: Vault to unsync (default: first vault in config)

        Returns:
            Tuple of (files_removed_from_state, files_deleted_from_cloud)

        Raises:
            RuntimeError: If unsync operation fails
        """
        if not vault_name:
            vault_name = "device-bench"

        ret, out, err = self.workspace.bench.run_unsync(
            self.workspace.config_file, delete_from_cloud=True, vault_name=vault_name
        )

        if ret != 0:
            raise RuntimeError(f"Failed to unsync vault '{vault_name}': {err}")

        # Count removed files from output or state
        # For now, return placeholder - actual count would come from sync output or state query
        self.bench.ok(f"Unsynced vault: {vault_name}")
        return (0, 0)

    def get_remaining_folders(self, vault_name: str | None = None) -> list[tuple[str, str]]:
        """Get folders still tracked in state.

        Args:
            vault_name: Vault to query (default: first vault in config)

        Returns:
            List of (folder_path, folder_uuid) tuples still in state
        """
        if not vault_name:
            vault_name = "device-bench"

        import sqlite3

        db_path = self.workspace.state_dir / "state.db"
        if not db_path.exists():
            return []

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT obsidian_path, remarkable_uuid FROM folder_mapping "
                "WHERE vault_name = ?",
                (vault_name,),
            )
            rows = cursor.fetchall()
            conn.close()
            return [(row[0], row[1]) for row in rows]
        except Exception as e:
            self.bench.error(f"Failed to query remaining folders: {e}")
            return []
