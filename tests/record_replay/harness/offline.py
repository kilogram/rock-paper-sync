"""Offline device emulator for testing without a physical device.

Replays pre-recorded testdata by injecting .rm files into rmfakecloud,
simulating device annotation sync without requiring a real reMarkable.

Trip-Based Format (New):
    - Loads trips from testdata (1, 2, ..., golden)
    - Restores vault state at each trip
    - Injects annotations from trip data

Legacy Phase Format (Still Supported):
    - Loads phases from testdata (initial, post_upload, ...)
    - Skips diagnostic phases during replay
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .protocol import DeviceInteractionManager, DocumentState, derive_test_id
from .testdata import PhaseData, TestdataStore, TripData

if TYPE_CHECKING:
    from .logging import Bench
    from .workspace import WorkspaceManager


class OfflineEmulator(DeviceInteractionManager):
    """Device emulator for offline (replay) testing.

    Replays pre-recorded .rm files by injecting them into rmfakecloud
    as if a device had synced them. This enables running device tests
    without a physical reMarkable.

    Supports both trip-based format (new) and legacy phase format.

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
        self._doc_uuid: str | None = None
        self._cached_page_order: list[str] = []

        # Trip-based format (new)
        self._trips: list[TripData] = []
        self._current_trip_idx: int = 0
        self._is_trip_format: bool = False

        # Legacy phase format
        self._current_phase: int = 0
        self._phases: list[PhaseData] = []

    def load_test(self, test_id: str) -> None:
        """Load test data (trip-based or legacy phase format).

        Automatically detects format and loads appropriately.

        Args:
            test_id: Test identifier to load

        Raises:
            FileNotFoundError: If test not found
        """
        self._current_test_id = test_id

        # Try trip-based format first (new)
        if self.testdata_store.is_trip_format(test_id):
            self._is_trip_format = True
            self._trips = self.testdata_store.load_trips(test_id)
            self._current_trip_idx = 0

            self.bench.ok(f"Loaded test artifacts: {test_id} ({len(self._trips)} trips)")

            # Restore initial vault state from trip 1
            if self._trips:
                self._restore_trip(0)
        else:
            # Fall back to legacy phase format
            self._is_trip_format = False
            self._current_phase = 0
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

    def compare_with_golden(
        self,
        doc_uuid: str,
        markdown_path: Path,
        observation: str,
        golden_prompt: str,
    ) -> tuple[DocumentState, DocumentState]:
        """Load re-anchored and golden states from testdata.

        In offline mode, both states are loaded from pre-recorded testdata.

        Args:
            doc_uuid: Document UUID to get re-anchored state for
            markdown_path: Ignored (used only for online recording)
            observation: Ignored (used only for online user prompt)
            golden_prompt: Ignored (used only for online user prompt)

        Returns:
            Tuple of (reanchored_state, golden_state)

        Raises:
            FileNotFoundError: If no golden data exists in testdata
        """
        # Get current re-anchored state from cloud
        reanchored_state = self.get_document_state(doc_uuid)

        # Load golden state from testdata
        golden_state = self.upload_golden_document(markdown_path, golden_prompt)

        return reanchored_state, golden_state

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

    def _restore_trip(self, trip_idx: int) -> None:
        """Restore vault to a specific trip state.

        Clears the workspace and restores files from the trip's vault directory.

        Args:
            trip_idx: Index into self._trips list

        Raises:
            ValueError: If trip not found
        """
        if trip_idx >= len(self._trips):
            raise ValueError(f"Trip index {trip_idx} not found (have {len(self._trips)} trips)")

        trip = self._trips[trip_idx]
        if not trip.vault_path or not trip.vault_path.exists():
            self.bench.warn(f"Trip {trip.trip_name} vault not found")
            return

        # Clear workspace (preserve .state, .cache, logs, config, .test_config)
        workspace_dir = self.workspace.workspace_dir
        for item in workspace_dir.iterdir():
            if item.name not in [".state", ".cache", "logs", "config.toml", ".test_config"]:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

        # Restore vault from trip
        for item in trip.vault_path.iterdir():
            if item.is_file():
                shutil.copy(item, workspace_dir / item.name)
            elif item.is_dir():
                shutil.copytree(item, workspace_dir / item.name)

        self.bench.ok(f"Restored vault to trip {trip.trip_name}")

    def _advance_trip(self) -> None:
        """Advance to next trip.

        Moves to the next trip and restores its vault state if available.
        """
        self._current_trip_idx += 1
        if self._current_trip_idx < len(self._trips):
            trip = self._trips[self._current_trip_idx]
            # Skip golden trip in normal advancement
            if trip.is_golden:
                self.bench.observe("Skipping golden trip in normal advancement")
                return
            self.bench.observe(f"Advanced to trip {trip.trip_name}")
            # Restore vault if this trip has one
            if trip.vault_path and trip.vault_path.exists():
                self._restore_trip(self._current_trip_idx)

    def upload_document(self, markdown_path: Path) -> str:
        """Upload document via normal sync.

        For trip-based format: Just uploads, no phase skipping needed.
        For legacy phase format: Advances past phase_0 and phase_1.

        Also captures uploaded rm files as diagnostic data for visual comparison.

        Args:
            markdown_path: Path to markdown file

        Returns:
            Document UUID

        Raises:
            RuntimeError: If sync fails
        """
        self.workspace.run_sync("Upload document to rmfakecloud")

        doc_uuid = self.workspace.get_document_uuid()
        if not doc_uuid:
            raise RuntimeError("Document UUID not found after sync")

        self._doc_uuid = doc_uuid
        self.bench.ok(f"Uploaded document to rmfakecloud: {doc_uuid}")

        # Capture uploaded rm files as diagnostic (for visual comparison)
        if self._current_test_id:
            self._download_rm_files_to_cache(doc_uuid)
            uploaded_rm = self._get_cached_rm_files_as_dict()
            if uploaded_rm:
                trip_number = self._current_trip_idx + 1  # Convert 0-indexed to 1-indexed
                self.testdata_store.save_trip_diagnostic(
                    self._current_test_id,
                    trip_number=trip_number,
                    diagnostic_name="offline/uploaded_rm",
                    rm_files=uploaded_rm,
                    page_order=self._cached_page_order,
                )
                self.bench.observe(
                    f"Trip {trip_number}: Saved diagnostic ({len(uploaded_rm)} uploaded .rm files)"
                )

        if self._is_trip_format:
            # Trip format: no phase skipping needed
            # First trip's vault was already restored in load_test()
            pass
        else:
            # Legacy phase format: advance past phase_0 (initial) and phase_1 (post_upload)
            if self._phases:
                if len(self._phases) > 1 and self._phases[1].phase_name == "post_upload":
                    self._advance_phase()  # Skip phase_0 -> phase_1
                    self._advance_phase()  # Skip phase_1 -> phase_2
                else:
                    self._advance_phase()

        return doc_uuid

    def wait_for_annotations(self, doc_uuid: str, timeout: float = 0.0) -> DocumentState:
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
        rm_files: dict[str, bytes] = {}

        if self._is_trip_format:
            # Trip-based format: get annotations from current trip
            if not self._trips:
                raise RuntimeError("No test loaded - call load_test() or start_test() first")

            # Find current trip with annotations
            if self._current_trip_idx < len(self._trips):
                trip = self._trips[self._current_trip_idx]
                if trip.has_annotations and trip.annotations:
                    rm_files = trip.annotations.rm_files
                    self.bench.observe(f"Using annotations from trip {trip.trip_name}")
        else:
            # Legacy phase format
            if not self._phases:
                raise RuntimeError("No test loaded - call load_test() or start_test() first")

            # Find phase with rm_files starting from current phase
            for phase in self._phases[self._current_phase :]:
                if phase.rm_files:
                    rm_files = phase.rm_files
                    self.bench.observe(
                        f"Using .rm files from phase {phase.phase_number}: {phase.phase_name}"
                    )
                    break

        if not rm_files:
            self.bench.warn("No .rm files found - skipping injection")
            # Still advance
            if self._is_trip_format:
                self._advance_trip()
            else:
                self._advance_phase()
            return self.get_document_state(doc_uuid)

        # Inject .rm files into rmfakecloud
        self._inject_rm_files(doc_uuid, rm_files)

        # Sync to download the injected annotations
        self.workspace.run_sync("Download injected annotations")

        # Advance to next trip/phase
        if self._is_trip_format:
            self._advance_trip()
        else:
            self._advance_phase()

        state = self.get_document_state(doc_uuid)

        # Validate testdata integrity automatically in offline mode
        self._validate_testdata(state)

        return state

    def trigger_sync(self) -> None:
        """Run sync command and capture uploaded rm files as diagnostic.

        After syncing, downloads the rm files from cloud to capture what
        was uploaded. This is critical for visual comparison tests that
        need to compare our generated output vs golden.
        """
        self.workspace.run_sync("Sync")

        # Capture uploaded rm files if we have a document UUID
        if self._doc_uuid and self._current_test_id:
            self._download_rm_files_to_cache(self._doc_uuid)
            uploaded_rm = self._get_cached_rm_files_as_dict()
            if uploaded_rm:
                trip_number = self._current_trip_idx + 1  # Convert 0-indexed to 1-indexed
                self.testdata_store.save_trip_diagnostic(
                    self._current_test_id,
                    trip_number=trip_number,
                    diagnostic_name="offline/uploaded_rm",
                    rm_files=uploaded_rm,
                    page_order=self._cached_page_order,
                )
                self.bench.observe(
                    f"Trip {trip_number}: Saved diagnostic "
                    f"({len(uploaded_rm)} uploaded .rm files)"
                )

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

    def upload_golden_document(self, markdown_path: Path, prompt: str) -> DocumentState:
        """Load pre-recorded golden data for device-native ground truth.

        In offline mode, this loads the golden trip/phase from testdata
        that was recorded during online capture.

        Args:
            markdown_path: Ignored (used only for online recording)
            prompt: Ignored (used only for online user prompt)

        Returns:
            DocumentState with device-native annotations from golden data

        Raises:
            FileNotFoundError: If no golden data exists in testdata
        """
        if self._is_trip_format:
            # Trip-based format: find golden trip
            if not self._trips:
                raise RuntimeError("No test loaded - call load_test() or start_test() first")

            golden_trip = None
            for trip in self._trips:
                if trip.is_golden:
                    golden_trip = trip
                    break

            if golden_trip is None:
                raise FileNotFoundError(
                    f"No golden trip in testdata for test '{self._current_test_id}'. "
                    "Re-run with --online -s to record golden ground truth."
                )

            if not golden_trip.annotations:
                raise RuntimeError("Golden trip has no annotations")

            self.bench.ok(
                f"Loaded golden trip: {len(golden_trip.annotations.rm_files)} .rm file(s)"
            )

            return DocumentState(
                doc_uuid=golden_trip.annotations.doc_uuid,
                page_uuids=golden_trip.annotations.page_uuids,
                rm_files=golden_trip.annotations.rm_files,
                has_annotations=len(golden_trip.annotations.rm_files) > 0,
            )
        else:
            # Legacy phase format: find golden_native phase
            if not self._phases:
                raise RuntimeError("No test loaded - call load_test() or start_test() first")

            golden_phase = None
            for phase in self._phases:
                if phase.phase_name == "golden_native":
                    golden_phase = phase
                    break

            if golden_phase is None:
                raise FileNotFoundError(
                    f"No golden_native phase in testdata for test '{self._current_test_id}'. "
                    "Re-run with --online -s to record golden ground truth."
                )

            golden_uuid = (
                golden_phase.device_state.get("doc_uuid") if golden_phase.device_state else None
            )
            if not golden_uuid:
                raise RuntimeError("Golden phase has no doc_uuid in device_state")

            page_uuids = golden_phase.device_state.get("page_uuids", [])
            rm_files = golden_phase.rm_files or {}

            self.bench.ok(
                f"Loaded golden phase: {len(rm_files)} .rm file(s) (doc_uuid: {golden_uuid[:8]})"
            )

            return DocumentState(
                doc_uuid=golden_uuid,
                page_uuids=page_uuids,
                rm_files=rm_files,
                has_annotations=len(rm_files) > 0,
            )

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

        state = DocumentState(
            doc_uuid=doc_uuid,
            page_uuids=page_uuids,
            rm_files=rm_files,
            has_annotations=has_annotations,
        )

        # Validate anchor offsets in synced .rm files
        self._validate_rm_anchors(state, context="after sync")

        return state

    def _validate_rm_anchors(self, state: DocumentState, context: str = "unknown") -> None:
        """Validate that TreeNodeBlock anchors are within page text bounds.

        This catches the device error "anchor=X for group=Y is not present in text"
        during test replay instead of requiring device re-recording.

        Args:
            state: Document state with .rm files to validate
            context: Description of when validation is happening (for error messages)

        Raises:
            AssertionError: If any anchor is out of range
        """
        # Import here to avoid circular imports
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "tools"))
        from rmlib.validator import validate_rm_bytes

        errors: list[str] = []
        for page_uuid, rm_data in state.rm_files.items():
            result = validate_rm_bytes(rm_data, source_name=f"{page_uuid[:8]}.rm")
            if not result.is_valid:
                for error in result.errors:
                    errors.append(f"{page_uuid[:8]}: {error}")

        if errors:
            error_msg = (
                f"ANCHOR VALIDATION FAILED ({context}):\n"
                f"The following anchors are invalid (device would show 'anchor is not present in text'):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
            raise AssertionError(error_msg)

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

    def _get_cached_rm_files_as_dict(self) -> dict[str, bytes]:
        """Get cached rm files as a dict.

        Returns:
            Dict of page_uuid -> rm_bytes
        """
        rm_files: dict[str, bytes] = {}
        for rm_path in self.workspace.get_cached_rm_files():
            rm_files[rm_path.stem] = rm_path.read_bytes()
        return rm_files

    def _download_rm_files_to_cache(self, doc_uuid: str) -> None:
        """Download rm files from cloud and save to cache directory.

        This ensures we can capture the rm files that were uploaded,
        even though the sync process doesn't save uploaded files to cache.

        Clears the cache directory first to avoid mixing files from
        different sync operations.

        Also stores page order in self._cached_page_order for later use.

        Args:
            doc_uuid: Document UUID
        """
        from rock_paper_sync.rm_cloud_client import RmCloudClient
        from rock_paper_sync.rm_cloud_sync import RmCloudSync

        client = RmCloudClient(base_url=self.workspace.cloud_url)
        sync = RmCloudSync(base_url=self.workspace.cloud_url, client=client)

        try:
            page_uuids = sync.get_existing_page_uuids(doc_uuid)
            if page_uuids:
                # Store page order for diagnostic saving
                self._cached_page_order = page_uuids

                # Clear and recreate cache directory to avoid stale files
                cache_dir = self.workspace.cache_dir / "annotations" / doc_uuid
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)
                cache_dir.mkdir(parents=True, exist_ok=True)

                # Download rm files directly to cache
                downloaded = sync.download_page_rm_files(doc_uuid, page_uuids, cache_dir)
                count = sum(1 for p in downloaded if p and p.exists())
                self.bench.observe(f"Downloaded {count} rm file(s) to cache")
        except Exception as e:
            self.bench.warn(f"Failed to download rm files to cache: {e}")

    # =========================================================================
    # Recording Phase Methods (no-op for offline mode)
    # =========================================================================

    def begin_phase(self, phase_id: int, phase_name: str, description: str = "") -> bool:
        """No-op for offline mode.

        Recording phases are only used during online recording. In offline
        replay, all phases are executed.

        Args:
            phase_id: Ignored
            phase_name: Ignored
            description: Ignored

        Returns:
            Always True (execute all phases)
        """
        return True

    def end_phase(self) -> None:
        """No-op for offline mode.

        Recording phases are only saved during online recording.
        """
        pass

    @property
    def current_phase(self) -> int | None:
        """Get current phase number.

        Returns:
            Always None in offline mode
        """
        return None

    @property
    def resume_phase(self) -> int | None:
        """Get phase to resume from.

        Returns:
            Always None in offline mode
        """
        return None

    # =========================================================================
    # Comparison Methods
    # =========================================================================

    def compare_to_golden(
        self,
        test_rm_files: dict[str, bytes],
        test_id: str | None = None,
        tolerance_px: float = 5.0,
        visual: bool = True,
        debug_dir: "Path | None" = None,
    ) -> None:
        """Assert test_rm_files match the golden trip for this test."""
        from pathlib import Path

        from .comparison import assert_highlights_match
        from .phase import debug_on_failure
        from .visual_comparison import assert_rm_files_match_visually

        tid = test_id or self._current_test_id
        if not tid:
            raise AssertionError("No test_id: call start_test() before compare_to_golden()")

        golden = self.testdata_store.get_golden(tid)
        if golden is None or golden.annotations is None:
            raise AssertionError(f"No golden trip found for '{tid}'")
        golden_rm = golden.annotations.rm_files

        _debug_dir = debug_dir or (Path(__file__).parent.parent / "debug_images")

        with debug_on_failure(test_rm_files, golden_rm, _debug_dir, f"{tid}_highlights"):
            assert_highlights_match(test_rm_files, golden_rm, tolerance_px=tolerance_px)

        if visual:
            with debug_on_failure(test_rm_files, golden_rm, _debug_dir, f"{tid}_visual"):
                assert_rm_files_match_visually(test_rm_files, golden_rm)

    def compare_trips(
        self,
        from_trip: int,
        to_trip: int,
        test_id: str | None = None,
        tolerance_px: float = 5.0,
    ) -> None:
        """Assert annotation positions are consistent between two stored trips."""
        from .comparison import assert_highlights_match

        tid = test_id or self._current_test_id
        if not tid:
            raise AssertionError("No test_id: call start_test() before compare_trips()")

        trip_a = self.testdata_store.get_trip(tid, from_trip)
        trip_b = self.testdata_store.get_trip(tid, to_trip)

        if trip_a is None or trip_a.annotations is None:
            raise AssertionError(f"Trip {from_trip} not found for '{tid}'")
        if trip_b is None or trip_b.annotations is None:
            raise AssertionError(f"Trip {to_trip} not found for '{tid}'")

        assert_highlights_match(
            trip_a.annotations.rm_files,
            trip_b.annotations.rm_files,
            tolerance_px=tolerance_px,
        )
