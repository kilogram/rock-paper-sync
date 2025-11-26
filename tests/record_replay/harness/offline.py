"""Offline device emulator for testing without a physical device.

Replays pre-recorded testdata by injecting .rm files into rmfakecloud,
simulating device annotation sync without requiring a real reMarkable.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from .protocol import DeviceInteractionManager, DocumentState
from .testdata import TestArtifacts, TestdataStore

if TYPE_CHECKING:
    from .logging import Bench
    from .workspace import WorkspaceManager


class OfflineEmulator(DeviceInteractionManager):
    """Device emulator for offline (replay) testing.

    Replays pre-recorded .rm files by injecting them into rmfakecloud
    as if a device had synced them. This enables running device tests
    without a physical reMarkable.

    Requirements:
    - rmfakecloud running (e.g., docker run -p 3000:3000 ddvk/rmfakecloud)
    - Pre-captured testdata from online test runs

    Usage:
        device = OfflineEmulator(workspace, testdata_store, bench)
        device.load_test("annotation_roundtrip_001")

        doc_uuid = device.upload_document(workspace.test_doc)
        # Injects pre-recorded .rm files, no user interaction needed
        state = device.wait_for_annotations(doc_uuid)

    Architecture:
        The emulator uses the Sync v3 protocol to upload .rm files directly
        to rmfakecloud, simulating what a real device would do:

        1. Upload document normally (creates structure in cloud)
        2. When wait_for_annotations is called:
           - Upload recorded .rm files using Sync v3 PUT
           - Update document metadata with file hashes
           - Trigger sync to download the "device" annotations
    """

    def __init__(
        self,
        workspace: "WorkspaceManager",
        testdata_store: TestdataStore,
        bench: "Bench",
        cloud_url: str = "http://localhost:3000",
    ) -> None:
        """Initialize offline emulator.

        Args:
            workspace: Workspace manager for sync operations
            testdata_store: Store for loading testdata
            bench: Bench utilities for logging
            cloud_url: rmfakecloud URL
        """
        self.workspace = workspace
        self.testdata_store = testdata_store
        self.bench = bench
        self.cloud_url = cloud_url
        self._current_artifacts: TestArtifacts | None = None
        self._current_test_id: str | None = None

    def load_test(self, test_id: str) -> None:
        """Load artifacts for a specific test.

        Must be called before running the test.

        Args:
            test_id: Test identifier to load

        Raises:
            FileNotFoundError: If test artifacts not found
        """
        self._current_artifacts = self.testdata_store.load_artifacts(test_id)
        self._current_test_id = test_id
        self.bench.ok(
            f"Loaded test artifacts: {test_id} "
            f"({len(self._current_artifacts.rm_files)} .rm files)"
        )

    def start_test(self, test_id: str) -> None:
        """Begin test with the specified test_id.

        Loads artifacts if not already loaded.

        Args:
            test_id: Test identifier
        """
        if self._current_test_id != test_id:
            self.load_test(test_id)
        self.bench.info(f"Started offline test: {test_id}")

    def end_test(self, test_id: str, success: bool) -> None:
        """End test.

        Args:
            test_id: Test identifier
            success: Whether test passed
        """
        if success:
            self.bench.ok(f"Offline test {test_id} completed successfully")
        self._current_artifacts = None
        self._current_test_id = None

    def upload_document(self, markdown_path: Path) -> str:
        """Upload document via normal sync.

        Args:
            markdown_path: Path to markdown file

        Returns:
            Document UUID

        Raises:
            RuntimeError: If sync fails
        """
        ret, out, err = self.workspace.run_sync("Upload document to rmfakecloud")

        if ret != 0:
            raise RuntimeError(f"Failed to upload document: {err}")

        doc_uuid = self.workspace.get_document_uuid()
        if not doc_uuid:
            raise RuntimeError("Document UUID not found after sync")

        self.bench.ok(f"Uploaded document to rmfakecloud: {doc_uuid}")
        return doc_uuid

    def wait_for_annotations(
        self, doc_uuid: str, timeout: float = 0.0
    ) -> DocumentState:
        """Inject pre-recorded annotations and sync.

        Instead of waiting for user input, this injects the pre-recorded
        .rm files from testdata into rmfakecloud, then syncs to download
        them as if they came from a real device.

        Args:
            doc_uuid: Document UUID
            timeout: Ignored in offline mode

        Returns:
            Document state with injected annotations

        Raises:
            RuntimeError: If no test loaded or injection fails
        """
        if not self._current_artifacts:
            raise RuntimeError(
                "No test loaded - call load_test() or start_test() first"
            )

        artifacts = self._current_artifacts

        if not artifacts.rm_files:
            self.bench.warn("No .rm files in test artifacts - skipping injection")
            return self.get_document_state(doc_uuid)

        # Inject .rm files into rmfakecloud
        self._inject_rm_files(doc_uuid, artifacts.rm_files)

        # Sync to download the injected annotations
        ret, out, err = self.workspace.run_sync("Download injected annotations")

        if ret != 0:
            raise RuntimeError(f"Failed to sync after injection: {err}")

        return self.get_document_state(doc_uuid)

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
            Document state
        """
        rm_files: dict[str, bytes] = {}
        page_uuids: list[str] = []

        cached_files = self.workspace.get_cached_rm_files()
        for rm_path in sorted(cached_files):
            page_uuid = rm_path.stem
            page_uuids.append(page_uuid)
            rm_files[page_uuid] = rm_path.read_bytes()

        has_annotations = len(rm_files) > 0

        if has_annotations:
            self.bench.observe(f"Found {len(rm_files)} .rm file(s) after injection")

        return DocumentState(
            doc_uuid=doc_uuid,
            page_uuids=page_uuids,
            rm_files=rm_files,
            has_annotations=has_annotations,
        )

    def _inject_rm_files(
        self, doc_uuid: str, rm_files: dict[str, bytes]
    ) -> None:
        """Inject .rm files into rmfakecloud.

        Uses production RmCloudSync to upload documents via Sync v3 protocol.
        This ensures annotations are properly integrated with the document.

        Args:
            doc_uuid: Document UUID
            rm_files: Mapping of page_uuid -> .rm bytes
        """
        from rock_paper_sync.rm_cloud_client import RmCloudClient
        from rock_paper_sync.rm_cloud_sync import RmCloudSync

        self.bench.info(f"Injecting {len(rm_files)} .rm files into rmfakecloud...")

        # Create cloud client (loads credentials from ~/.config/rock-paper-sync/device-credentials.json)
        client = RmCloudClient(base_url=self.cloud_url)

        # Create sync instance using production code
        sync = RmCloudSync(base_url=self.cloud_url, client=client)

        # Convert rm_files dict to list of (page_uuid, rm_data) tuples
        pages = [(page_uuid, rm_data) for page_uuid, rm_data in rm_files.items()]

        # Use production upload_document which handles everything:
        # - Creates .metadata, .content, .local files
        # - Uploads all files via Sync v3
        # - Updates document root with new pages
        try:
            sync.upload_document(
                doc_uuid=doc_uuid,
                document_name=f"Document {doc_uuid[:8]}",
                pages=pages,
                parent_uuid="",
            )
            self.bench.ok(f"Injected {len(rm_files)} .rm files into document")
        except Exception as e:
            self.bench.error(f"Failed to inject .rm files: {e}")
            raise RuntimeError(f"Failed to inject .rm files: {e}") from e

    def unsync_vault(self, vault_name: str | None = None) -> tuple[int, int]:
        """Simulate unsync in offline mode.

        In offline mode, unsync doesn't actually contact the cloud.
        Instead, returns expected results from testdata manifest.

        Args:
            vault_name: Vault to unsync (default: first vault in config)

        Returns:
            Tuple of (files_removed_from_state, files_deleted_from_cloud)
        """
        if not vault_name:
            vault_name = "device-bench"

        # In offline mode, we simulate unsync by returning expected values
        # The manifest should include expected_state_after_unsync if available
        if (
            self._current_artifacts
            and self._current_artifacts.manifest
            and hasattr(self._current_artifacts.manifest, "expected_state_after_unsync")
        ):
            expected = self._current_artifacts.manifest.expected_state_after_unsync
            files_removed = expected.get("files_removed", 0)
            files_deleted = expected.get("files_deleted", 0)
        else:
            files_removed = 0
            files_deleted = 0

        self.bench.info(f"Simulated unsync (offline): {files_removed} removed, {files_deleted} deleted")
        return (files_removed, files_deleted)

    def get_remaining_folders(self, vault_name: str | None = None) -> list[tuple[str, str]]:
        """Get expected remaining folders from testdata.

        In offline mode, returns expected folders from the manifest.

        Args:
            vault_name: Vault to query (default: first vault in config)

        Returns:
            List of expected (folder_path, folder_uuid) tuples
        """
        if not vault_name:
            vault_name = "device-bench"

        # In offline mode, return expected folders from manifest
        if (
            self._current_artifacts
            and self._current_artifacts.manifest
            and hasattr(self._current_artifacts.manifest, "expected_folders_remaining")
        ):
            expected = self._current_artifacts.manifest.expected_folders_remaining
            return expected
        else:
            # Default: no folders remaining after unsync
            return []
