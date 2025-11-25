"""Device interaction protocol for online and offline testing.

Defines the abstract interface for device interaction that works both
with real devices (online mode) and emulated replay (offline mode).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


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


class DeviceProtocol(ABC):
    """Abstract interface for device interaction.

    This protocol abstracts the interaction with a reMarkable device,
    allowing the same test code to work in two modes:

    1. **Online Mode**: Real device connected, user prompted for actions.
       Testdata is automatically captured for later replay.

    2. **Offline Mode**: No device needed, pre-recorded .rm files are
       injected into rmfakecloud to simulate device sync.

    Example:
        class MyTest(DeviceTestCase):
            def execute(self) -> bool:
                # Works identically in online and offline mode
                doc_uuid = self.device.upload_document(self.workspace.test_doc)
                state = self.device.wait_for_annotations(doc_uuid)
                assert state.has_annotations
                return True
    """

    @abstractmethod
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

    @abstractmethod
    def wait_for_annotations(
        self, doc_uuid: str, timeout: float = 300.0
    ) -> DocumentState:
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

    @abstractmethod
    def trigger_sync(self) -> None:
        """Trigger a sync operation.

        In online mode: runs rock-paper-sync sync command.
        In offline mode: runs sync against rmfakecloud.
        """
        ...

    @abstractmethod
    def get_document_state(self, doc_uuid: str) -> DocumentState:
        """Get current state of a document from the cloud.

        Args:
            doc_uuid: Document UUID to query

        Returns:
            Current document state
        """
        ...

    # Test lifecycle methods

    def start_test(self, test_id: str) -> None:
        """Begin a test, enabling artifact capture.

        Called by test harness at the start of each test.

        Args:
            test_id: Unique identifier for this test run
        """
        pass  # Default no-op, online mode overrides

    def end_test(self, test_id: str, success: bool) -> None:
        """End a test, finalizing artifact capture.

        Called by test harness at the end of each test.

        Args:
            test_id: Test identifier (same as start_test)
            success: Whether the test passed
        """
        pass  # Default no-op, online mode overrides
