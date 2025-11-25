"""Capture folder deletion workflow to reproduce bug.

Run this test with your real reMarkable device to capture the exact scenario
where folder deletion fails. The test will create testdata that can be replayed
offline to validate the fix.

Usage:
    # Capture testdata with real device
    uv run pytest tests/device_bench/test_folder_deletion_capture.py::test_capture_folder_deletion -v

    # The test will create a document, sync it, delete it, then unsync
    # If the folder deletion bug occurs, it will be captured in the logs
"""

import pytest
from pathlib import Path


@pytest.mark.device
def test_capture_folder_deletion(workspace, bench):
    """Capture the folder deletion workflow that triggers the bug.

    This test exercises the exact scenario from the logs:
    1. Sync a document inside a folder (creates folder structure)
    2. Delete the document
    3. Unsync the vault (should delete document + empty folders)

    If the bug occurs (500 error when deleting folder), it will be visible
    in the sync logs at workspace.log_dir / "sync.log"
    """
    print(f"\n{'='*70}")
    print(f"FOLDER DELETION BUG CAPTURE TEST")
    print(f"{'='*70}")

    # Create a document in a subfolder
    projects_dir = workspace.workspace_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    test_doc = projects_dir / "test_document.md"
    test_doc.write_text("""# Folder Deletion Test Document

This document is used to test the folder deletion bug.

## Scenario

- Document is in vault/projects/test_document.md
- After sync, should appear in DeviceBench/projects/ on device
- After unsync, both document and folder should be deleted
""")

    print(f"\n1. Created test document:")
    print(f"   {test_doc.relative_to(workspace.workspace_dir)}")

    # Sync document to device
    print(f"\n2. Syncing document to device...")
    ret, stdout, stderr = bench.run_sync(workspace.config_file)

    if ret != 0:
        print(f"   ✗ Sync failed with return code {ret}")
        print(f"   stdout: {stdout}")
        print(f"   stderr: {stderr}")
        pytest.fail("Initial sync failed")

    print(f"   ✓ Sync completed successfully")

    # Parse sync output to find document UUID
    doc_uuid = None
    for line in stdout.split('\n'):
        if "Successfully synced" in line and test_doc.stem in line:
            # Extract UUID from line like: "Successfully synced ... -> <uuid>"
            if "->" in line:
                doc_uuid = line.split("->")[-1].strip().split()[0]
                break

    if doc_uuid:
        print(f"   Document UUID: {doc_uuid}")

    # Wait for user to verify on device
    print(f"\n{'='*70}")
    print(f"📱 VERIFICATION STEP")
    print(f"{'='*70}")
    print(f"\nPlease check your reMarkable device:")
    print(f"  1. Open the 'DeviceBench' folder")
    print(f"  2. Look for the 'projects' subfolder")
    print(f"  3. Verify 'test_document' appears inside it")
    print(f"\nPress ENTER when you've verified the file exists...")
    input()
    print(f"Continuing with deletion test...")

    # Delete the document
    print(f"\n3. Deleting document from vault...")
    test_doc.unlink()
    print(f"   ✓ Document deleted: {test_doc.name}")

    # Unsync vault - this should trigger folder deletion
    print(f"\n4. Unsyncing vault (will delete document + folder from cloud)...")
    print(f"   This is where the folder deletion bug may occur...")

    ret, stdout, stderr = bench.run_unsync(workspace.config_file, delete_from_cloud=True)

    print(f"\n5. Unsync result:")
    print(f"   Return code: {ret}")

    if ret != 0:
        print(f"   ✗ Unsync FAILED")
        print(f"\n   stdout:")
        for line in stdout.split('\n'):
            print(f"     {line}")
        print(f"\n   stderr:")
        for line in stderr.split('\n'):
            print(f"     {line}")

        # Check if it's the folder deletion bug
        if "500" in stderr or "500" in stdout:
            if "Failed to delete folder" in stderr or "Failed to delete folder" in stdout:
                print(f"\n{'='*70}")
                print(f"🎯 FOLDER DELETION BUG REPRODUCED!")
                print(f"{'='*70}")
                print(f"\nThe folder deletion triggered a 500 error.")
                print(f"Check the sync log for details:")
                print(f"  {workspace.log_dir / 'sync.log'}")
                print(f"")

                # Show relevant log lines
                log_file = workspace.log_dir / "sync.log"
                if log_file.exists():
                    print(f"Recent log entries:")
                    import subprocess
                    result = subprocess.run(
                        ["tail", "-50", str(log_file)],
                        capture_output=True,
                        text=True
                    )
                    for line in result.stdout.split('\n'):
                        if any(x in line for x in ["ERROR", "Failed", "500", "folder"]):
                            print(f"  {line}")

                pytest.fail("Folder deletion bug reproduced - 500 error when deleting folder")
        else:
            pytest.fail(f"Unsync failed with return code {ret}")

    print(f"   ✓ Unsync completed successfully")
    print(f"\n   Output:")
    for line in stdout.split('\n'):
        if line.strip():
            print(f"     {line}")

    print(f"\n{'='*70}")
    print(f"✓ TEST PASSED - Folder deletion worked correctly")
    print(f"{'='*70}")

    # Print summary
    print(f"\nSummary:")
    print(f"  - Document synced: {test_doc.name}")
    print(f"  - Document deleted and unsynced")
    print(f"  - Folder 'projects' should be deleted from device")
    print(f"  - No 500 errors occurred")
    print(f"\nLogs available at: {workspace.log_dir / 'sync.log'}")


@pytest.mark.device
def test_capture_root_folder_deletion(workspace, bench):
    """Capture deletion of the DeviceBench folder itself.

    This tests the specific scenario where the vault's remarkable_folder
    (DeviceBench) is deleted when the last document is removed.
    """
    print(f"\n{'='*70}")
    print(f"ROOT FOLDER DELETION CAPTURE TEST")
    print(f"{'='*70}")

    # Create document in vault root (no subfolder)
    test_doc = workspace.workspace_dir / "root_doc.md"
    test_doc.write_text("# Root Document\n\nDirectly in vault root.")

    print(f"\n1. Created document in vault root: {test_doc.name}")

    # Sync
    print(f"\n2. Syncing...")
    ret, stdout, stderr = bench.run_sync(workspace.config_file)
    if ret != 0:
        pytest.fail(f"Sync failed: {stderr}")
    print(f"   ✓ Synced - should appear in DeviceBench/ folder on device")

    # Wait for user to verify
    print(f"\n{'='*70}")
    print(f"📱 VERIFICATION STEP")
    print(f"{'='*70}")
    print(f"\nPlease check your reMarkable device:")
    print(f"  1. Open the 'DeviceBench' folder")
    print(f"  2. Verify 'root_doc' appears inside it")
    print(f"\nPress ENTER when you've verified the file exists...")
    input()
    print(f"Continuing with deletion test...")

    # Delete and unsync
    print(f"\n3. Deleting and unsyncing...")
    test_doc.unlink()

    ret, stdout, stderr = bench.run_unsync(workspace.config_file, delete_from_cloud=True)

    if ret != 0:
        if "500" in stderr and "DeviceBench" in stderr:
            print(f"\n🎯 BUG REPRODUCED: DeviceBench folder deletion failed with 500!")
            pytest.fail("DeviceBench folder deletion bug reproduced")
        pytest.fail(f"Unsync failed: {stderr}")

    print(f"   ✓ Successfully deleted document and DeviceBench folder")
    print(f"\n✓ TEST PASSED")
