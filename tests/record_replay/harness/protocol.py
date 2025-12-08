"""Device interaction protocol for online and offline testing.

Defines the abstract interface for device interaction that works both
with real devices (online mode) and emulated replay (offline mode).

Uses typing.Protocol for structural subtyping to ensure all implementations
(OnlineDevice, OfflineEmulator) adhere to the same interface contract.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


def derive_test_id(fixture_path: Path) -> str:
    """Derive test_id from fixture filename.

    Fixture files follow the pattern: test_{name}.md
    The test_id is extracted as {name}.

    Args:
        fixture_path: Path to the fixture markdown file

    Returns:
        Test identifier derived from filename

    Examples:
        >>> derive_test_id(Path("fixtures/test_highlights.md"))
        'highlights'
        >>> derive_test_id(Path("fixtures/test_full_integration.md"))
        'full_integration'
    """
    stem = fixture_path.stem  # e.g., "test_highlights"
    if stem.startswith("test_"):
        return stem[5:]  # Remove "test_" prefix
    return stem  # If no prefix, use stem as-is


@dataclass
class DocumentState:
    """State of a document on the reMarkable cloud.

    Attributes:
        doc_uuid: Unique identifier for the document
        page_uuids: List of page UUIDs in document order
        rm_files: Mapping of page_uuid -> .rm file bytes
        has_annotations: Whether any pages have annotations
    """

    doc_uuid: str
    page_uuids: list[str] = field(default_factory=list)
    rm_files: dict[str, bytes] = field(default_factory=dict)
    has_annotations: bool = False


@runtime_checkable
class DeviceInteractionProtocol(Protocol):
    """Protocol for device interaction in online and offline modes.

    This protocol defines the interface for interacting with a reMarkable device,
    allowing the same test code to work in two modes:

    1. **Online Mode**: Real device connected, user prompted for actions.
       Testdata is automatically captured for later replay.

    2. **Offline Mode**: No device needed, pre-recorded .rm files are
       injected into rmfakecloud to simulate device sync.

    Implementations:
        - OnlineDevice: Records real device interactions
        - OfflineEmulator: Replays pre-recorded testdata

    Type Safety:
        This is a typing.Protocol, so type checkers will verify that
        implementations match this interface at static analysis time.
        The @runtime_checkable decorator also enables isinstance() checks.

    Example:
        def test_annotations(device: DeviceInteractionProtocol):
            # Works identically in online and offline mode
            doc_uuid = device.upload_document(workspace.test_doc)
            state = device.wait_for_annotations(doc_uuid)
            assert state.has_annotations
    """

    def upload_document(self, markdown_path: Path) -> str:
        """Upload a markdown document to the cloud.

        In online mode: runs sync to upload document.
        In offline mode: runs sync to upload document to rmfakecloud.

        Args:
            markdown_path: Path to the markdown file

        Returns:
            Document UUID assigned by the cloud
        """
        ...

    def wait_for_annotations(self, doc_uuid: str, timeout: float = 300.0) -> DocumentState:
        """Wait for annotations to be synced from device.

        In online mode: prompts user to annotate on device, then syncs.
        In offline mode: injects pre-recorded .rm files into cloud.

        Args:
            doc_uuid: Document UUID to wait for
            timeout: Maximum time to wait in seconds (online mode only)

        Returns:
            Document state with annotations
        """
        ...

    def trigger_sync(self) -> None:
        """Trigger a sync operation.

        In online mode: runs rock-paper-sync sync command.
        In offline mode: runs sync against rmfakecloud.
        """
        ...

    def get_document_state(self, doc_uuid: str) -> DocumentState:
        """Get current state of a document from the cloud.

        Args:
            doc_uuid: Document UUID to query

        Returns:
            Current document state
        """
        ...

    def unsync_vault(self, vault_name: str | None = None) -> tuple[int, int]:
        """Unsync entire vault from cloud.

        Removes all documents and folders from the cloud, deleting them
        and clearing the state database.

        In online mode: Runs actual unsync command against cloud.
        In offline mode: Simulates unsync using testdata expectations.

        Args:
            vault_name: Vault to unsync (default: first vault in config)

        Returns:
            Tuple of (files_removed_from_state, files_deleted_from_cloud)

        Raises:
            RuntimeError: If unsync operation fails
        """
        ...

    def get_remaining_folders(self, vault_name: str | None = None) -> list[tuple[str, str]]:
        """Get folders still tracked in state after operations.

        Useful for verifying that empty folders were deleted during unsync.

        Args:
            vault_name: Vault to query (default: first vault in config)

        Returns:
            List of (folder_path, folder_uuid) tuples still in state
        """
        ...

    def start_test(self, test_id: str, description: str = "") -> None:
        """Begin a test, enabling artifact capture.

        Called by test harness at the start of each test.

        Args:
            test_id: Unique identifier for this test run
            description: Human-readable test description (used in online mode)
        """
        ...

    def start_test_for_fixture(self, fixture_path: Path, description: str = "") -> str:
        """Begin a test, deriving test_id from fixture path.

        This is the preferred way to start tests as it ensures test_id
        matches the fixture, avoiding redundant recordings.

        Args:
            fixture_path: Path to the fixture markdown file
            description: Human-readable test description

        Returns:
            The derived test_id

        Example:
            test_id = device.start_test_for_fixture(
                fixtures_dir / "test_highlights.md",
                description="Highlight annotations"
            )
            # test_id will be "highlights"
        """
        ...

    def end_test(self, test_id: str) -> None:
        """End a test, finalizing artifact capture.

        Called by test harness at the end of each test.
        If this method is called, the test is assumed to have succeeded.
        Failed tests will raise exceptions before reaching this point.

        Args:
            test_id: Test identifier (same as start_test)
        """
        ...

    def observe_result(self, message: str = "") -> None:
        """Pause for user to observe result on device before cleanup.

        In online mode: Prompts user to view device and press Enter to continue.
        In offline mode: No-op (no observation needed).

        Call this after syncing up modified markdown to let the user verify
        the changes look correct on the device before the test proceeds to cleanup.

        Args:
            message: Optional message describing what to observe
        """
        ...

    def compare_with_golden(
        self,
        doc_uuid: str,
        markdown_path: Path,
        observation: str,
        golden_prompt: str,
    ) -> tuple[DocumentState, DocumentState]:
        """Upload golden document and compare side-by-side with re-anchored document.

        Combines observe_result and upload_golden_document into a single step,
        allowing the user to flip between both documents on the device to
        compare annotation positions visually.

        In online mode:
        1. Uploads fresh golden document (separate UUID)
        2. Prompts user to observe re-anchored doc and annotate golden doc
        3. Captures both states for comparison

        In offline mode:
        1. Loads pre-recorded re-anchored state from testdata
        2. Loads pre-recorded golden state from testdata

        Args:
            doc_uuid: UUID of the re-anchored document to observe
            markdown_path: Path to the (already modified) markdown document
            observation: What to observe in the re-anchored document
            golden_prompt: Instructions for annotating the golden document

        Returns:
            Tuple of (reanchored_state, golden_state)
        """
        ...

    def capture_phase(self, phase_name: str, action: str = "capture") -> None:
        """Manually capture a phase at the current state.

        In online mode: Saves current vault and device state as a named phase.
        In offline mode: No-op (testdata is pre-recorded).

        Use this after trigger_sync() or any operation to capture intermediate states.

        Args:
            phase_name: Name for this phase (e.g., "post_modification")
            action: Action description for the phase metadata
        """
        ...

    def upload_golden_document(self, markdown_path: Path, prompt: str) -> DocumentState:
        """Upload a fresh document for device-native ground truth capture.

        This enables comparison between our re-anchored highlights and what the
        device would produce if the user highlighted text at its final position.

        Workflow:
        1. Upload markdown_path as a NEW document (separate from main test doc)
        2. In online mode: prompt user to create highlights matching the prompt
        3. Capture the resulting .rm files as "golden_native" phase
        4. Return state for comparison with re-anchored output

        In online mode: Uploads fresh doc, prompts user, captures golden phase.
        In offline mode: Loads pre-recorded golden phase from testdata.

        Args:
            markdown_path: Path to the (already modified) markdown document
            prompt: Instructions for user (e.g., "Highlight 'target', 'bottom'")

        Returns:
            DocumentState with device-native annotations for comparison

        Example:
            # After re-anchoring flow completes
            golden_state = device.upload_golden_document(
                workspace.test_doc,  # Already modified
                prompt="Highlight 'target', 'bottom', 'cross line'"
            )
            assert_highlights_match(
                reanchored_state.rm_files,
                golden_state.rm_files,
                tolerance_px=5.0
            )
        """
        ...

    def cleanup(self) -> None:
        """Cleanup after test completion.

        In online mode: Prompts user to confirm device cleanup is complete.
        In offline mode: Silent cleanup, no user interaction required.

        This is called during test teardown to ensure device state is cleaned up.
        """
        ...


# Backward compatibility aliases
DeviceProtocol = DeviceInteractionProtocol
DeviceInteractionManager = DeviceInteractionProtocol
