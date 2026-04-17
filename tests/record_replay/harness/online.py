"""Online device handler for recording real device interactions.

Session-level tracking prevents redundant re-recording when multiple tests
share the same fixture within a single pytest run.

Records test artifacts by prompting user to perform actions on a physical
reMarkable device or rmfakecloud simulation, then capturing the resulting
annotations.

Trip-Based Recording:
    - Trip 1: Upload document → user annotates → capture annotations
    - Trip 2+: Modify vault → sync → user annotates → capture
    - Golden: Fresh upload for device-native ground truth comparison

Directory Structure:
    testdata/{test_id}/
    ├── trips/
    │   ├── 1/vault/           # Initial vault state
    │   ├── 1/annotations/     # User annotations (for replay)
    │   ├── 1/_diagnostic/     # Debug data (uploaded_rm, etc.)
    │   ├── 2/vault/           # Modified vault state
    │   ├── 2/annotations/
    │   └── golden/annotations/
    └── manifest.json

Usage:
    device = OnlineDevice(workspace, testdata_store, bench)
    device.start_test("pen_colors", description="Write text in different colors")

    doc_uuid = device.upload_document(workspace.test_doc)
    # User sees prompt: "Please annotate document on device, then press Enter"

    state = device.wait_for_annotations(doc_uuid)
    device.end_test("pen_colors")
    # Testdata saved to tests/testdata/pen_colors/
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .protocol import DeviceInteractionManager, DocumentState, derive_test_id
from .testdata import TestdataStore

if TYPE_CHECKING:
    from .logging import Bench
    from .workspace import WorkspaceManager


# Session-level tracking of recorded test_ids to prevent redundant re-recording
# when multiple tests share the same fixture
_session_recorded_tests: set[str] = set()


class TestdataExistsError(Exception):
    """Raised when testdata already exists and should be reused.

    This exception signals that the test should skip recording and use
    the existing testdata. It's caught by the test framework to handle
    gracefully (e.g., by skipping the test or using offline mode).
    """

    def __init__(self, test_id: str, test_dir: Path) -> None:
        self.test_id = test_id
        self.test_dir = test_dir
        super().__init__(f"Testdata already recorded this session: {test_id}")


class OnlineDevice(DeviceInteractionManager):
    """Record real device interactions as testdata.

    Prompts user to perform actions on a physical reMarkable device,
    captures the resulting annotations and vault state, and saves them
    as replayable testdata using trip-based format.

    Requirements:
    - Physical reMarkable device OR rmfakecloud simulation
    - Configured cloud credentials
    - User to perform actions on device when prompted

    Usage:
        device = OnlineDevice(workspace, testdata_store, bench)
        device.start_test("test_id", description="Test description")

        doc_uuid = device.upload_document(workspace.test_doc)
        # Displays: "Please annotate on device, then press Enter"

        state = device.wait_for_annotations(doc_uuid)
        device.end_test("test_id")
    """

    def __init__(
        self,
        workspace: "WorkspaceManager",
        testdata_store: TestdataStore,
        bench: "Bench",
        resume_from_phase: int | None = None,
    ) -> None:
        """Initialize online device recorder.

        Args:
            workspace: Workspace manager for sync operations (provides cloud_url)
            testdata_store: Store for saving testdata artifacts
            bench: Bench utilities for logging
            resume_from_phase: Phase number to resume from (None = fresh start)
        """
        self.workspace = workspace
        self.testdata_store = testdata_store
        self.bench = bench

        # Recording state (trip-based)
        self._current_test_id: str | None = None
        self._current_description: str | None = None
        self._current_trip: int = 1  # 1-indexed trip number
        self._trips_recorded: list[int] = []
        self._has_golden: bool = False
        self._doc_uuid: str | None = None

        # Recording phase state (for resumption)
        self._resume_from_phase: int | None = resume_from_phase
        self._current_phase: int | None = None
        self._current_phase_name: str | None = None
        self._cached_rm_files: dict[str, bytes] = {}
        self._cached_page_uuids: list[str] = []

    def start_test(self, test_id: str, description: str = "") -> None:
        """Begin recording a test.

        Creates directory structure and prepares to capture artifacts.
        If testdata was already recorded in THIS SESSION (same fixture used
        by another test), reuses it instead of re-recording.

        If resuming from a phase, restores state from that phase instead
        of creating fresh directories.

        Args:
            test_id: Unique test identifier
            description: Human-readable test description
        """
        self._current_test_id = test_id
        self._current_description = description
        self._current_trip = 1
        self._trips_recorded = []
        self._has_golden = False
        self._doc_uuid = None
        self._current_phase = None
        self._current_phase_name = None

        test_dir = self.testdata_store.base_dir / test_id

        # Handle resumption
        if self._resume_from_phase is not None:
            phase_state = self.testdata_store.load_recording_phase(test_id, self._resume_from_phase)
            if phase_state is None:
                available = self.testdata_store.get_recording_phases(test_id)
                if available:
                    phase_list = ", ".join(str(p["phase_id"]) for p in available)
                    raise RuntimeError(
                        f"Cannot resume from phase {self._resume_from_phase}: not found. "
                        f"Available phases: {phase_list}"
                    )
                else:
                    raise RuntimeError(
                        f"Cannot resume from phase {self._resume_from_phase}: "
                        f"no recording phases exist for test '{test_id}'. "
                        "Run test without --resume-from-phase first to create phases."
                    )

            # Restore state from phase
            self._restore_from_phase(phase_state)
            self.bench.ok(
                f"Resumed from phase {self._resume_from_phase}: "
                f"{phase_state['phase_name']} (trip {self._current_trip})"
            )
            if description:
                self.bench.info(f"Description: {description}")
            return

        # Check if this test_id was already recorded in this session
        if test_id in _session_recorded_tests:
            self.bench.ok(f"Reusing testdata recorded earlier this session: {test_id}")
            raise TestdataExistsError(test_id, test_dir)

        # Clean up any existing testdata from previous sessions (but keep recording_phases)
        if test_dir.exists():
            # Preserve recording_phases directory
            recording_phases_dir = test_dir / "recording_phases"
            recording_phases_backup = None
            if recording_phases_dir.exists():
                import tempfile

                recording_phases_backup = Path(tempfile.mkdtemp()) / "recording_phases"
                shutil.copytree(recording_phases_dir, recording_phases_backup)

            shutil.rmtree(test_dir)
            self.bench.info(f"Cleaned up existing testdata: {test_id}")

            # Restore recording_phases if it existed
            if recording_phases_backup:
                test_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(recording_phases_backup, recording_phases_dir)
                shutil.rmtree(recording_phases_backup.parent)  # Clean up temp dir

        # Track this test_id BEFORE creating directory, so if we fail mid-recording,
        # other tests sharing this fixture will still skip (and not wipe partial data)
        _session_recorded_tests.add(test_id)

        # Create fresh testdata directory with trips structure
        (test_dir / "trips").mkdir(parents=True, exist_ok=True)

        self.bench.ok(f"Started recording: {test_id}")
        if description:
            self.bench.info(f"Description: {description}")

    def _restore_from_phase(self, phase_state: dict) -> None:
        """Restore workspace and recording state from a saved phase.

        Args:
            phase_state: Phase state dict from load_recording_phase()
        """
        # Restore recording state
        self._current_trip = phase_state.get("current_trip", 1)
        self._trips_recorded = list(phase_state.get("trips_recorded", []))
        self._doc_uuid = phase_state.get("doc_uuid")
        self._cached_rm_files = phase_state.get("rm_files", {})
        self._cached_page_uuids = phase_state.get("page_uuids", [])

        # Restore vault from phase
        vault_path = phase_state.get("vault_path")
        if vault_path and vault_path.exists():
            workspace_dir = self.workspace.workspace_dir

            # Clear workspace (preserve .state, .cache, logs, config, .test_config)
            for item in workspace_dir.iterdir():
                if item.name not in [".state", ".cache", "logs", "config.toml", ".test_config"]:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)

            # Copy files from vault snapshot
            for item in vault_path.iterdir():
                if item.is_file():
                    shutil.copy(item, workspace_dir / item.name)
                elif item.is_dir():
                    shutil.copytree(item, workspace_dir / item.name)

            self.bench.ok(f"Restored vault from phase {phase_state['phase_id']}")

    def start_test_for_fixture(self, fixture_path: Path, description: str = "") -> str:
        """Begin recording a test, deriving test_id from fixture path.

        This is the preferred way to start tests as it ensures test_id
        matches the fixture, avoiding redundant recordings.

        Args:
            fixture_path: Path to the fixture markdown file
            description: Human-readable test description

        Returns:
            The derived test_id
        """
        test_id = derive_test_id(fixture_path)
        self.start_test(test_id, description)
        return test_id

    def upload_document(self, markdown_path: Path) -> str:
        """Upload document and capture Trip 1 vault state.

        Saves:
        - Trip 1 vault: Initial vault state (for replay)
        - Trip 1 diagnostic: .rm files we uploaded (debug only)

        Args:
            markdown_path: Path to markdown file

        Returns:
            Document UUID

        Raises:
            RuntimeError: If sync fails
        """
        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Save Trip 1 vault state (before sync, for replay)
        self.testdata_store.save_trip_vault(
            self._current_test_id,
            trip_number=1,
            vault_dir=self.workspace.workspace_dir,
        )
        self.bench.ok("Trip 1: Saved initial vault state")

        # Run sync to upload document
        self.workspace.run_sync("Upload document")

        doc_uuid = self.workspace.get_document_uuid()
        if not doc_uuid:
            self.bench.error("Document UUID not found after sync")
            self.bench.observe(f"Workspace dir: {self.workspace.workspace_dir}")
            self.bench.observe(f"State dir: {self.workspace.state_dir}")
            if self.workspace.state_dir.exists():
                for item in sorted(self.workspace.state_dir.rglob("*"))[:20]:
                    self.bench.observe(f"  {item.relative_to(self.workspace.state_dir)}")
            raise RuntimeError("Document UUID not found after sync")

        self._doc_uuid = doc_uuid
        self.bench.ok(f"Uploaded document: {doc_uuid}")

        # Download fresh rm files from cloud to capture what we uploaded (diagnostic)
        self._download_rm_files_to_cache(doc_uuid)
        uploaded_rm = self._get_cached_rm_files_as_dict()
        if uploaded_rm:
            self.testdata_store.save_trip_diagnostic(
                self._current_test_id,
                trip_number=1,
                diagnostic_name="online/uploaded_rm",
                rm_files=uploaded_rm,
            )
            self.bench.observe(f"Trip 1: Saved diagnostic ({len(uploaded_rm)} uploaded .rm files)")

        # Note: Removed redundant "Document uploaded and syncing..." prompt
        # The user will be prompted in wait_for_annotations() with clear instructions

        return doc_uuid

    def wait_for_annotations(self, doc_uuid: str, timeout: float = 300.0) -> DocumentState:
        """Wait for user to annotate on device, then capture trip annotations.

        Displays user prompt and waits for them to press Enter after
        completing annotations on the physical device. Then waits for the
        device to sync annotations back to cloud before downloading.

        Args:
            doc_uuid: Document UUID
            timeout: Maximum time to wait for annotations to appear (in seconds)

        Returns:
            Document state with captured annotations

        Raises:
            RuntimeError: If sync fails or timeout waiting for annotations
        """
        import time

        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Prompt user to annotate with clear trip-based instruction
        self.bench.prompt_user(
            f"Trip {self._current_trip}: Annotate document",
            f"Please annotate document on device (doc_uuid: {doc_uuid[:8]})",
            "Make sure device syncs annotations back to cloud.",
            "Press Enter when done annotating...",
        )

        # Run multiple syncs with small delays to allow device to sync annotations
        annotations_found = False
        start_time = time.time()
        attempt = 0
        max_attempts = 5

        while not annotations_found and attempt < max_attempts:
            attempt += 1
            elapsed = time.time() - start_time

            if elapsed > timeout:
                self.bench.warn(f"Timeout waiting for annotations (waited {elapsed:.1f}s)")
                break

            # Run sync
            sync_desc = f"Sync (attempt {attempt}/{max_attempts})"
            self.workspace.run_sync(sync_desc)

            # Check if annotations were downloaded
            state = self.get_document_state(doc_uuid)
            if state.has_annotations:
                annotations_found = True
                self.bench.ok(
                    f"Trip {self._current_trip}: Annotations found after {attempt} sync(s)"
                )
                break

            if attempt < max_attempts:
                self.bench.observe("No annotations yet, waiting 5s before retry...")
                time.sleep(5)

        if not annotations_found:
            self.bench.warn(f"No annotations captured after {attempt} sync attempts")
            self.bench.observe(f"Cache dir: {self.workspace.cache_dir}")
            if self.workspace.cache_dir.exists():
                cache_contents = list(self.workspace.cache_dir.rglob("*"))
                self.bench.observe(f"Cache contains {len(cache_contents)} items")
            self.bench.warn("Common causes:")
            self.bench.warn("1. Device annotations not synced back to cloud yet")
            self.bench.warn("2. No annotations were actually made on the device")
            self.bench.warn("3. rmfakecloud may not support annotation syncing")

        # Get final state and save trip annotations
        state = self.get_document_state(doc_uuid)
        self.testdata_store.save_trip_annotations(
            self._current_test_id,
            trip_number=self._current_trip,
            rm_files=state.rm_files,
            doc_uuid=doc_uuid,
            page_uuids=state.page_uuids,
        )
        self._trips_recorded.append(self._current_trip)
        self.bench.ok(f"Trip {self._current_trip}: Saved {len(state.rm_files)} annotation file(s)")

        self._current_trip += 1

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
                self.testdata_store.save_trip_diagnostic(
                    self._current_test_id,
                    trip_number=self._current_trip,
                    diagnostic_name="online/uploaded_rm",
                    rm_files=uploaded_rm,
                )
                self.bench.observe(
                    f"Trip {self._current_trip}: Saved diagnostic "
                    f"({len(uploaded_rm)} uploaded .rm files)"
                )

    def capture_phase(self, phase_name: str, action: str = "capture") -> None:
        """Manually capture vault state for next trip.

        Use this after modifying markdown to save the vault state before
        the next annotation cycle.

        Args:
            phase_name: Name for this capture (for logging)
            action: Action description (for logging)
        """
        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Save vault state for the current trip
        self.testdata_store.save_trip_vault(
            self._current_test_id,
            trip_number=self._current_trip,
            vault_dir=self.workspace.workspace_dir,
        )
        self.bench.ok(f"Trip {self._current_trip}: Saved vault state ({phase_name})")

    def get_document_state(self, doc_uuid: str) -> DocumentState:
        """Get current document state by downloading fresh from cloud.

        Downloads .rm files directly from reMarkable cloud to ensure we get
        the latest version after any sync operations that may have
        adjusted annotation positions.

        Args:
            doc_uuid: Document UUID

        Returns:
            Document state with fresh .rm files from cloud
        """
        import tempfile

        from rock_paper_sync.rm_cloud_client import RmCloudClient
        from rock_paper_sync.rm_cloud_sync import RmCloudSync

        rm_files: dict[str, bytes] = {}
        page_uuids: list[str] = []

        # Create cloud client and sync instance (cloud_url from workspace config)
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
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                downloaded_files = sync.download_page_rm_files(doc_uuid, page_uuids, temp_path)
                for i, rm_path in enumerate(downloaded_files):
                    if rm_path and rm_path.exists():
                        page_uuid = page_uuids[i]
                        rm_files[page_uuid] = rm_path.read_bytes()

        has_annotations = len(rm_files) > 0

        if has_annotations:
            self.bench.observe(f"Found {len(rm_files)} .rm file(s)")

        return DocumentState(
            doc_uuid=doc_uuid,
            page_uuids=page_uuids,
            rm_files=rm_files,
            has_annotations=has_annotations,
        )

    def unsync_vault(self, vault_name: str | None = None) -> tuple[int, int]:
        """Unsync vault from cloud.

        Args:
            vault_name: Vault to unsync

        Returns:
            Tuple of (files_removed, files_deleted)
        """
        # Implementation would call actual unsync
        return (0, 0)

    def get_remaining_folders(self, vault_name: str | None = None) -> list[tuple[str, str]]:
        """Get remaining folders after operations.

        Args:
            vault_name: Vault to query

        Returns:
            List of (folder_path, folder_uuid) tuples
        """
        return []

    def end_test(self, test_id: str) -> None:
        """Finalize test recording.

        Saves manifest and completes testdata capture.

        Note: Session tracking happens in start_test(), not here, so that
        even if a test fails mid-recording, subsequent tests sharing the
        same fixture will skip (and not wipe the partial data).

        Args:
            test_id: Test identifier
        """
        if not self._current_test_id:
            return

        # Save manifest with trip information
        self.testdata_store.save_trip_manifest(
            self._current_test_id,
            description=self._current_description or "",
            doc_uuid=self._doc_uuid or "",
            trips_recorded=self._trips_recorded,
            has_golden=self._has_golden,
        )

        trips_str = ", ".join(str(t) for t in self._trips_recorded)
        golden_str = " + golden" if self._has_golden else ""
        self.bench.ok(f"Recording complete: {test_id} (trips: {trips_str}{golden_str})")

        self._current_test_id = None
        self._current_description = None
        self._current_trip = 1
        self._trips_recorded = []
        self._has_golden = False
        self._doc_uuid = None

    def observe_result(self, message: str = "") -> None:
        """Pause for user to observe result on device.

        Prompts user to view the synced content on their device and approve
        that it looks correct before proceeding to cleanup/unsync.

        Args:
            message: Optional message describing what to observe
        """
        default_msg = "Please observe the result on your device."
        observe_msg = message if message else default_msg

        self.bench.prompt_user(
            "📱 OBSERVE RESULT",
            observe_msg,
            "Verify the synced content looks correct on your device.",
            "Press Enter when you're ready to continue...",
        )

    def compare_with_golden(
        self,
        doc_uuid: str,
        markdown_path: Path,
        observation: str,
        golden_prompt: str,
    ) -> tuple[DocumentState, DocumentState]:
        """Upload golden document and let user compare both side-by-side.

        Creates a fresh golden document (different UUID) so user can flip
        between the re-anchored document and device-native ground truth
        on the device, comparing annotation positions visually.

        Args:
            doc_uuid: UUID of the re-anchored document to observe
            markdown_path: Path to the (already modified) markdown document
            observation: What to observe in the re-anchored document
            golden_prompt: Instructions for annotating the golden document
                (e.g., "Highlight 'target' and 'bottom' at their new positions")

        Returns:
            Tuple of (reanchored_state, golden_state)
        """
        import time

        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Create golden document with "_golden" suffix
        golden_path = markdown_path.parent / f"{markdown_path.stem}_golden.md"
        golden_path.write_text(markdown_path.read_text())

        self.bench.info("Uploading golden document for side-by-side comparison")

        # Run sync to upload the golden document
        self.workspace.run_sync("Upload golden document")

        # Get the golden document UUID (should be different from main doc)
        golden_uuid = self._get_golden_document_uuid()
        if not golden_uuid:
            raise RuntimeError("Golden document UUID not found after sync")

        self.bench.ok(f"Golden document uploaded: {golden_uuid[:8]}...")

        # Download fresh rm files from cloud
        self._download_rm_files_to_cache(golden_uuid)

        # Combined prompt: observe + annotate golden
        self.bench.prompt_user(
            "COMPARE & ANNOTATE GOLDEN",
            "",
            "Two documents are now on device:",
            f"  • RE-ANCHORED: {doc_uuid[:8]}... (original document)",
            f"  • GOLDEN:      {golden_uuid[:8]}... (fresh '_golden' document)",
            "",
            "OBSERVE the re-anchored document:",
            f"  {observation}",
            "",
            "ANNOTATE the golden document:",
            f"  {golden_prompt}",
            "",
            "Flip between documents to compare annotation positions.",
            "Press Enter when golden annotations are synced...",
        )

        # Wait for golden annotations with multiple sync attempts
        annotations_found = False
        max_attempts = 5

        for attempt in range(1, max_attempts + 1):
            self.workspace.run_sync(f"Golden sync (attempt {attempt})")

            state = self.get_document_state(golden_uuid)
            if state.has_annotations:
                annotations_found = True
                self.bench.ok(f"Golden annotations found after {attempt} sync(s)")
                break

            if attempt < max_attempts:
                self.bench.observe("No golden annotations yet, waiting 5s...")
                time.sleep(5)

        if not annotations_found:
            self.bench.warn("No golden annotations captured - proceeding anyway")

        # Get final states for both documents
        reanchored_state = self.get_document_state(doc_uuid)
        golden_state = self.get_document_state(golden_uuid)

        # Save golden annotations
        self.testdata_store.save_trip_annotations(
            self._current_test_id,
            trip_number=0,  # 0 = golden
            rm_files=golden_state.rm_files,
            doc_uuid=golden_uuid,
            page_uuids=golden_state.page_uuids,
        )
        self._has_golden = True
        self.bench.ok(f"Golden: Saved {len(golden_state.rm_files)} annotation file(s)")

        # Clean up golden file from vault (but keep in testdata)
        if golden_path.exists():
            golden_path.unlink()

        return reanchored_state, golden_state

    def upload_golden_document(self, markdown_path: Path, prompt: str) -> DocumentState:
        """Upload a fresh document for device-native ground truth capture.

        Creates a separate document (different UUID) for the user to annotate
        at the final text positions, enabling comparison with re-anchored output.

        Note: Consider using compare_with_golden() instead for a combined
        observe + golden workflow.

        Args:
            markdown_path: Path to the (already modified) markdown document
            prompt: Instructions for user (e.g., "Highlight 'target', 'bottom'")

        Returns:
            DocumentState with device-native annotations
        """
        import time

        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Create golden document with "_golden" suffix
        golden_path = markdown_path.parent / f"{markdown_path.stem}_golden.md"
        golden_path.write_text(markdown_path.read_text())

        self.bench.info("GOLDEN: Uploading fresh document for ground truth")

        # Run sync to upload the golden document
        self.workspace.run_sync("Upload golden document")

        # Get the golden document UUID (should be different from main doc)
        golden_uuid = self._get_golden_document_uuid()
        if not golden_uuid:
            raise RuntimeError("Golden document UUID not found after sync")

        self.bench.ok(f"GOLDEN: Uploaded document: {golden_uuid}")

        # Download fresh rm files from cloud
        self._download_rm_files_to_cache(golden_uuid)

        # Prompt user to annotate with specific instructions
        self.bench.prompt_user(
            "GOLDEN: Annotate for ground truth",
            f"Annotate the GOLDEN document (uuid: {golden_uuid[:8]})",
            f"Instructions: {prompt}",
            "",
            "IMPORTANT: Annotate '_golden' document, not the original!",
            "Press Enter when annotations are synced...",
        )

        # Wait for annotations with multiple sync attempts
        annotations_found = False
        max_attempts = 5

        for attempt in range(1, max_attempts + 1):
            self.workspace.run_sync(f"Golden sync (attempt {attempt})")

            state = self.get_document_state(golden_uuid)
            if state.has_annotations:
                annotations_found = True
                self.bench.ok(f"GOLDEN: Annotations found after {attempt} sync(s)")
                break

            if attempt < max_attempts:
                self.bench.observe("No golden annotations yet, waiting 5s...")
                time.sleep(5)

        if not annotations_found:
            self.bench.warn("GOLDEN: No annotations captured - proceeding anyway")

        # Get final state and save as golden trip
        state = self.get_document_state(golden_uuid)
        self.testdata_store.save_trip_annotations(
            self._current_test_id,
            trip_number=0,  # 0 = golden
            rm_files=state.rm_files,
            doc_uuid=golden_uuid,
            page_uuids=state.page_uuids,
        )
        self._has_golden = True
        self.bench.ok(f"GOLDEN: Saved {len(state.rm_files)} annotation file(s)")

        # Clean up golden file from vault (but keep in testdata)
        if golden_path.exists():
            golden_path.unlink()

        return state

    def _get_golden_document_uuid(self) -> str | None:
        """Get UUID of the golden document from state.

        Returns:
            Golden document UUID, or None if not found
        """
        from rock_paper_sync.state import StateManager

        state_db = self.workspace.state_dir / "state.db"
        if not state_db.exists():
            return None

        state_manager = StateManager(state_db)
        # Look for document with _golden suffix
        for entry in state_manager.get_all_synced_files():
            if "_golden" in entry.obsidian_path:
                return entry.remarkable_uuid
        return None

    def cleanup(self) -> None:
        """Cleanup after test with user confirmation.

        The unsync operation has already completed by the time this is called.
        This prompt allows the user to inspect device state before finalizing.
        """
        self.bench.prompt_user(
            "Cleanup complete.",
            "Inspect device state if needed.",
            "Press Enter to finalize...",
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
    # Recording Phase Methods (for test resumption)
    # =========================================================================

    def begin_phase(self, phase_id: int, phase_name: str, description: str = "") -> bool:
        """Begin a named recording phase.

        Marks the start of a phase. If resuming, skips phases that have
        already been recorded (phases before the resume point).

        Args:
            phase_id: Phase number (1-indexed)
            phase_name: Human-readable phase name (e.g., "initial_upload")
            description: Phase description

        Returns:
            True if phase should be executed, False if should be skipped
        """
        # If resuming, skip phases before the resume point
        if self._resume_from_phase is not None and phase_id < self._resume_from_phase:
            self.bench.info(
                f"Skipping phase {phase_id}: {phase_name} (resuming from {self._resume_from_phase})"
            )
            return False

        self._current_phase = phase_id
        self._current_phase_name = phase_name
        self.bench.ok(f"Phase {phase_id}: {phase_name}")
        if description:
            self.bench.info(f"  {description}")

        return True

    def end_phase(self) -> None:
        """End the current recording phase and save state.

        Saves vault state and annotations for resumption. Call this at the
        end of each phase to create a resumption checkpoint.
        """
        if self._current_phase is None or not self._current_test_id:
            return

        # Get current annotations if we have a document
        rm_files: dict[str, bytes] = {}
        page_uuids: list[str] = []
        if self._doc_uuid:
            state = self.get_document_state(self._doc_uuid)
            rm_files = state.rm_files
            page_uuids = state.page_uuids

        # Save phase state
        self.testdata_store.save_recording_phase(
            test_id=self._current_test_id,
            phase_id=self._current_phase,
            phase_name=self._current_phase_name or f"phase_{self._current_phase}",
            vault_dir=self.workspace.workspace_dir,
            doc_uuid=self._doc_uuid,
            rm_files=rm_files if rm_files else None,
            page_uuids=page_uuids if page_uuids else None,
            current_trip=self._current_trip,
            trips_recorded=self._trips_recorded,
        )

        self.bench.ok(f"Saved phase {self._current_phase} checkpoint")

        self._current_phase = None
        self._current_phase_name = None

    @property
    def current_phase(self) -> int | None:
        """Get current phase number.

        Returns:
            Current phase number, or None if not in a phase
        """
        return self._current_phase

    @property
    def resume_phase(self) -> int | None:
        """Get phase to resume from (if any).

        Returns:
            Phase number to resume from, or None if starting fresh
        """
        return self._resume_from_phase

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
            raise AssertionError(
                f"No golden trip found for '{tid}'. "
                "Record a golden trip first (trip with is_golden=True)."
            )
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
