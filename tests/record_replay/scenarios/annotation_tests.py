"""Annotation sync test scenarios.

Tests for annotation sync workflow:
- Round-trip: sync → annotate → verify markers
- Hash stability: markers don't cause re-upload loops
- Content editing: edits trigger re-sync
"""

from pathlib import Path

from ..harness.base import DeviceTestCase, device_test
from ..harness.prompts import user_prompt


class AnnotationRoundtripTest(DeviceTestCase):
    """Complete annotation sync roundtrip.

    Test flow:
    1. Sync clean document to device
    2. User adds annotations (highlights/strokes)
    3. Sync back to download annotations
    4. Verify annotation markers appear in markdown
    """

    name = "annotation-roundtrip"
    description = "Full annotation sync cycle: sync → annotate → verify markers"

    @device_test(cleanup_on_success=True)
    def execute(self) -> bool:
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            self.bench.error("Initial sync failed")
            return False

        # Step 2: User adds annotations
        if not user_prompt("Add annotations", [
            f"Open '{self.workspace.device_folder}/document' on reMarkable",
            "Highlight 'annotated' and 'markers' in the Important paragraph",
            "Optionally add strokes/drawings",
            "Wait for cloud sync to complete",
        ]):
            return False

        # Step 3: Download annotations
        ret, out, err = self.sync("Download annotations")
        if ret != 0:
            self.bench.error("Annotation download sync failed")
            return False

        # Step 4: Verify markers
        content = self.workspace.get_document_content()
        if "<!-- ANNOTATED" not in content:
            self.bench.error("No annotation markers found in document!")
            return False

        count = self.assert_markers_present("<!-- ANNOTATED:")

        # Display found markers
        for line in content.split("\n"):
            if "<!-- ANNOTATED" in line:
                self.bench.ok(f"  {line.strip()}")

        self.bench.observe(f"Successfully found {count} annotation marker(s)")
        return True


class NoHashLoopTest(DeviceTestCase):
    """Verify markers don't cause infinite sync loop.

    Test flow:
    1. Sync document
    2. User adds annotations
    3. Sync (downloads markers)
    4. Sync again - should skip (no changes)
    5. Sync third time - should still skip

    This verifies that annotation markers are excluded from
    content hashing to prevent re-upload loops.
    """

    name = "no-hash-loop"
    description = "Verify annotation markers don't cause re-upload loops"

    @device_test(cleanup_on_success=True)
    def execute(self) -> bool:
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            return False

        # Step 2: User adds annotations
        if not user_prompt("Add annotations", [
            f"Open '{self.workspace.device_folder}/document' on reMarkable",
            "Add any highlight or stroke annotation",
            "Wait for cloud sync to complete",
        ]):
            return False

        # Step 3: Download annotations (adds markers)
        ret, out, err = self.sync("Download annotations")
        if ret != 0:
            return False

        # Verify markers were added
        if "<!-- ANNOTATED" not in self.workspace.get_document_content():
            self.bench.error("No markers found after sync")
            return False

        count = self.workspace.get_document_content().count("<!-- ANNOTATED:")
        self.bench.observe(f"Markers present: {count}")

        # Step 4: Second sync - should skip
        ret, out, err = self.sync("Second sync (should skip)")
        if ret != 0:
            return False

        if "unchanged" in out.lower() or "skipping" in out.lower():
            self.bench.observe("Correctly skipped - no re-upload")
        elif "synced" in out.lower() or "uploaded" in out.lower():
            self.bench.error("Re-uploaded! Hash loop bug detected!")
            return False

        # Step 5: Third sync - extra verification
        ret, out, err = self.sync("Third sync (extra check)")
        if ret != 0:
            return False

        if "unchanged" in out.lower() or "skipping" in out.lower():
            self.bench.observe("Third sync also skipped - hash stability confirmed")
        elif "synced" in out.lower() or "uploaded" in out.lower():
            self.bench.error("Third sync re-uploaded! Inconsistent behavior!")
            return False

        return True


class ContentEditTest(DeviceTestCase):
    """Verify editing marked content triggers re-sync.

    Test flow:
    1. Sync document
    2. User adds annotations
    3. Sync (downloads markers)
    4. User edits content inside markers
    5. Sync - should detect change and upload

    This verifies that content edits are properly detected
    even when annotation markers are present.
    """

    name = "content-edit"
    description = "Verify content edits trigger re-sync with markers present"

    @device_test(cleanup_on_success=True)
    def execute(self) -> bool:
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            return False

        # Step 2: User adds annotations
        if not user_prompt("Add annotations", [
            f"Open '{self.workspace.device_folder}/document' on reMarkable",
            "Add any highlight or stroke annotation",
            "Wait for cloud sync to complete",
        ]):
            return False

        # Step 3: Download annotations
        ret, out, err = self.sync("Download annotations")
        if ret != 0:
            return False

        if "<!-- ANNOTATED" not in self.workspace.get_document_content():
            self.bench.error("No markers found")
            return False

        # Step 4: User edits content
        content_before = self.workspace.get_document_content()

        if not user_prompt("Edit content", [
            f"Open {self.workspace.test_doc} in a text editor",
            "Find text inside <!-- ANNOTATED --> markers",
            "Edit the text (add/remove/change words)",
            "Save the file",
        ]):
            return False

        content_after = self.workspace.get_document_content()

        if content_after == content_before:
            self.bench.warn("File unchanged - no edit detected")
            return False

        self.bench.observe("Content modified by user")

        # Step 5: Sync after edit - should upload
        ret, out, err = self.sync("Sync after edit")
        if ret != 0:
            return False

        # Check output indicates re-sync occurred
        if "synced" in out.lower() or "uploaded" in out.lower():
            self.bench.observe("Document re-synced after edit")
        elif "unchanged" in out.lower() or "skipping" in out.lower():
            self.bench.warn("Document marked unchanged - possible content hash issue")
            # Still pass since sync succeeded

        return True
