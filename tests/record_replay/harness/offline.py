"""Offline device emulator for testing without a physical device.

Replays pre-recorded testdata by injecting .rm files into rmfakecloud,
simulating device annotation sync without requiring a real reMarkable.

Multi-Phase Support:
    - Loads phases from testdata (initial, post_sync, final)
    - Restores vault state at each phase
    - Advances through phases as sync operations complete
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .protocol import DeviceInteractionManager, DocumentState, derive_test_id
from .testdata import PhaseData, TestdataStore

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
    ) -> None:
        """Initialize offline emulator.

        Args:
            workspace: Workspace manager for sync operations (provides cloud_url)
            testdata_store: Store for loading testdata
            bench: Bench utilities for logging
        """
        self.workspace = workspace
        self.testdata_store = testdata_store
        self.bench = bench
        self._current_test_id: str | None = None

        # Multi-phase support (only format supported)
        self._current_phase: int = 0
        self._phases: list[PhaseData] = []

    def load_test(self, test_id: str) -> None:
        """Load multi-phase test data.

        Args:
            test_id: Test identifier to load

        Raises:
            FileNotFoundError: If test not found
        """
        self._current_test_id = test_id
        self._current_phase = 0

        # Load multi-phase data (only format supported)
        self._phases = self.testdata_store.load_phases(test_id)

        self.bench.ok(f"Loaded test artifacts: {test_id} ({len(self._phases)} phases)")

        # Restore initial vault state from phase 0
        if self._phases:
            self._restore_phase(0)

    def start_test(self, test_id: str, description: str = "") -> None:
        """Begin test with the specified test_id.

        Loads artifacts if not already loaded.

        Args:
            test_id: Test identifier
            description: Test description (ignored in offline mode)
        """
        if self._current_test_id != test_id:
            self.load_test(test_id)
        self.bench.info(f"Started offline test: {test_id}")

    def start_test_for_fixture(self, fixture_path: Path, description: str = "") -> str:
        """Begin test, deriving test_id from fixture path.

        This is the preferred way to start tests as it ensures test_id
        matches the fixture.

        Args:
            fixture_path: Path to the fixture markdown file
            description: Test description (ignored in offline mode)

        Returns:
            The derived test_id
        """
        test_id = derive_test_id(fixture_path)
        self.start_test(test_id, description)
        return test_id

    def end_test(self, test_id: str) -> None:
        """End test.

        Assumes test succeeded (failed tests raise exceptions before reaching here).

        Args:
            test_id: Test identifier
        """
        self.bench.ok(f"Offline test {test_id} completed successfully")
        self._current_test_id = None
        self._current_phase = 0
        self._phases = []

    def observe_result(self, message: str = "") -> None:
        """No-op for offline mode.

        In offline mode, there's no device to observe.
        This is a no-op to satisfy the DeviceInteractionProtocol.

        Args:
            message: Ignored in offline mode
        """
        # No observation needed in offline mode
        pass

    def cleanup(self) -> None:
        """Cleanup after test (silent for offline tests).

        Offline tests are fully automated, so no user interaction is needed.
        This is a no-op to satisfy the DeviceInteractionProtocol.
        """
        # Silent cleanup - no user prompts for automated tests
        pass

    def _restore_phase(self, phase_num: int) -> None:
        """Restore vault to a specific phase state.

        Clears the workspace and restores files from the phase's vault snapshot.

        Args:
            phase_num: Phase number to restore

        Raises:
            ValueError: If phase not found
        """
        if phase_num >= len(self._phases):
            raise ValueError(f"Phase {phase_num} not found (have {len(self._phases)} phases)")

        phase = self._phases[phase_num]
        vault_snapshot = phase.vault_snapshot_path

        if not vault_snapshot.exists():
            self.bench.warn(f"Phase {phase_num} vault snapshot not found")
            return

        # Clear workspace (preserve .state, .cache, logs, config, .test_config)
        workspace_dir = self.workspace.workspace_dir
        for item in workspace_dir.iterdir():
            if item.name not in [".state", ".cache", "logs", "config.toml", ".test_config"]:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

        # Restore vault snapshot
        for item in vault_snapshot.iterdir():
            if item.is_file():
                shutil.copy(item, workspace_dir / item.name)
            elif item.is_dir():
                shutil.copytree(item, workspace_dir / item.name)

        self.bench.ok(f"Restored vault to phase {phase_num}: {phase.phase_name}")

    def _advance_phase(self) -> None:
        """Advance to next phase in multi-phase test.

        Moves to the next phase and updates logging.
        """
        self._current_phase += 1
        if self._current_phase < len(self._phases):
            phase = self._phases[self._current_phase]
            self.bench.observe(f"Advanced to phase {self._current_phase}: {phase.phase_name}")

    def upload_document(self, markdown_path: Path) -> str:
        """Upload document via normal sync.

        Advances through phases to match online mode:
        - Skips phase_0 (initial) - pre-sync state
        - Skips phase_1 (post_upload) - uploaded rm files
        This aligns with the online harness which captures both phases.

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

        # Advance past phase_0 (initial) and phase_1 (post_upload)
        # to match online mode which captures both phases during upload
        if self._phases:
            # Check if we have both initial and post_upload phases
            if len(self._phases) > 1 and self._phases[1].phase_name == "post_upload":
                self._advance_phase()  # Skip phase_0 -> phase_1
                self._advance_phase()  # Skip phase_1 -> phase_2
            else:
                # Legacy format: just advance once
                self._advance_phase()

        return doc_uuid

    def wait_for_annotations(self, doc_uuid: str, timeout: float = 0.0) -> DocumentState:
        """Inject pre-recorded annotations and sync.

        Instead of waiting for user input, this injects the pre-recorded
        .rm files from testdata into rmfakecloud, then syncs to download
        them as if they came from a real device.

        In multi-phase mode, uses .rm files from the current or next phase.
        Advances to next phase after injection and sync.

        Args:
            doc_uuid: Document UUID
            timeout: Ignored in offline mode

        Returns:
            Document state with injected annotations

        Raises:
            RuntimeError: If no test loaded or injection fails
        """
        if not self._phases:
            raise RuntimeError("No test loaded - call load_test() or start_test() first")

        # Find phase with rm_files starting from current phase
        rm_files: dict[str, bytes] = {}
        for phase in self._phases[self._current_phase :]:
            if phase.rm_files:
                rm_files = phase.rm_files
                self.bench.observe(
                    f"Using .rm files from phase {phase.phase_number}: {phase.phase_name}"
                )
                break

        if not rm_files:
            self.bench.warn("No .rm files found - skipping injection")
            # Still advance phase
            if self._phases:
                self._advance_phase()
            return self.get_document_state(doc_uuid)

        # Inject .rm files into rmfakecloud
        self._inject_rm_files(doc_uuid, rm_files)

        # Sync to download the injected annotations
        ret, out, err = self.workspace.run_sync("Download injected annotations")

        if ret != 0:
            raise RuntimeError(f"Failed to sync after injection: {err}")

        # Advance to next phase in multi-phase mode
        if self._phases:
            self._advance_phase()

        state = self.get_document_state(doc_uuid)

        # Validate testdata integrity automatically in offline mode
        self._validate_testdata(state)

        return state

    def trigger_sync(self) -> None:
        """Run sync command."""
        ret, out, err = self.workspace.run_sync("Sync")
        if ret != 0:
            raise RuntimeError(f"Sync failed: {err}")

    def capture_phase(self, phase_name: str, action: str = "capture") -> None:
        """No-op for offline mode.

        In offline mode, testdata is pre-recorded. This method exists
        only to satisfy the DeviceInteractionProtocol.

        Args:
            phase_name: Ignored in offline mode
            action: Ignored in offline mode
        """
        # No capture needed - testdata is pre-recorded
        pass

    def get_document_state(self, doc_uuid: str) -> DocumentState:
        """Get current document state by downloading fresh from cloud.

        Downloads .rm files directly from rmfakecloud to ensure we get
        the latest version after any sync operations that may have
        adjusted annotation positions.

        Args:
            doc_uuid: Document UUID

        Returns:
            Document state with fresh .rm files from cloud
        """
        from rock_paper_sync.rm_cloud_client import RmCloudClient
        from rock_paper_sync.rm_cloud_sync import RmCloudSync

        rm_files: dict[str, bytes] = {}
        page_uuids: list[str] = []

        # Create cloud client and sync instance
        client = RmCloudClient(base_url=self.workspace.cloud_url)
        sync = RmCloudSync(base_url=self.workspace.cloud_url, client=client)

        # Get page UUIDs from cloud
        try:
            page_uuids = sync.get_existing_page_uuids(doc_uuid)
        except Exception as e:
            self.bench.warn(f"Failed to get page UUIDs: {e}")
            page_uuids = []

        if page_uuids:
            # Download fresh .rm files from cloud to temp directory
            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                downloaded_files = sync.download_page_rm_files(doc_uuid, page_uuids, temp_path)
                for i, rm_path in enumerate(downloaded_files):
                    if rm_path and rm_path.exists():
                        page_uuid = page_uuids[i]
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

    def _inject_rm_files(self, doc_uuid: str, rm_files: dict[str, bytes]) -> None:
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
        client = RmCloudClient(base_url=self.workspace.cloud_url)

        # Create sync instance using production code
        sync = RmCloudSync(base_url=self.workspace.cloud_url, client=client)

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
        # Multi-phase tests don't have expected_state metadata
        # Just return defaults
        files_removed = 0
        files_deleted = 0

        self.bench.info(
            f"Simulated unsync (offline): {files_removed} removed, {files_deleted} deleted"
        )
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

        # Multi-phase tests don't have expected_folders metadata
        # Default: no folders remaining after unsync
        return []

    def _validate_testdata(self, state: DocumentState) -> None:
        """Validate testdata integrity automatically during replay.

        Performs generic sanity checks on replayed testdata:
        - .rm files contain valid rmscene blocks
        - Annotations can be extracted from .rm files
        - Basic data structure integrity

        Test-specific assertions (colors, widths, etc.) remain in test code.

        Args:
            state: Document state to validate

        Raises:
            AssertionError: If validation fails
        """
        import io

        from rmscene import read_blocks

        from rock_paper_sync.annotations import read_annotations

        if not state.has_annotations:
            # No annotations to validate
            return

        # Validate .rm files contain valid blocks
        for page_uuid, rm_data in state.rm_files.items():
            blocks = list(read_blocks(io.BytesIO(rm_data)))
            assert len(blocks) > 0, (
                f"Testdata validation failed: No blocks in {page_uuid}.rm. "
                f"The .rm file may be corrupted or empty."
            )

        # Validate annotations can be extracted
        total_annotations = 0
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            total_annotations += len(annotations)

        assert total_annotations > 0, (
            f"Testdata validation failed: No annotations extracted from {len(state.rm_files)} .rm file(s). "
            f"Annotations may not have been properly captured during recording."
        )

        self.bench.ok(
            f"Testdata validation passed: {len(state.rm_files)} .rm file(s), "
            f"{total_annotations} annotation(s)"
        )
