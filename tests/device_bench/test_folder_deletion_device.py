"""On-device test for folder deletion bug.

This test runs against a real reMarkable device to capture the folder deletion
workflow as testdata. The captured testdata can then be replayed offline to
reproduce and validate the fix for the folder deletion bug.

Usage:
    # Run on real device and capture testdata
    uv run pytest tests/device_bench/test_folder_deletion_device.py -m device --device-mode=online -v

    # Replay captured testdata offline
    uv run pytest tests/device_bench/test_folder_deletion_device.py -m offline --device-mode=offline --test-artifact=folder_deletion_bug -v
"""

import pytest
from pathlib import Path


@pytest.mark.device
def test_folder_deletion_workflow(device, workspace, device_mode):
    """Test complete folder deletion workflow to capture bug scenario.

    This test:
    1. Creates a markdown file in a subfolder
    2. Syncs it to the device (creates folder + document)
    3. Waits for sync to complete
    4. Deletes the markdown file
    5. Unsyncs the vault (should delete document + folder)

    The offline emulator will capture all .rm files and API interactions.
    """
    # Skip in offline mode if no testdata loaded
    if device_mode == "offline" and not hasattr(device, '_current_test_id'):
        pytest.skip("No testdata loaded for offline replay")

    # Start test (online: captures, offline: replays)
    test_id = "folder_deletion_bug"

    if device_mode == "online":
        print(f"\n{'='*60}")
        print(f"ONLINE MODE: Capturing testdata for '{test_id}'")
        print(f"{'='*60}")
        device.start_test(test_id)
    else:
        print(f"\n{'='*60}")
        print(f"OFFLINE MODE: Replaying testdata '{test_id}'")
        print(f"{'='*60}")

    # Create a folder structure: vault/projects/document.md
    projects_dir = workspace.vault_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    test_doc = projects_dir / "document.md"
    test_doc.write_text("""# Folder Deletion Test

This document tests the folder deletion bug scenario.

## What this tests

1. Document inside a folder
2. Sync to device
3. Delete document locally
4. Unsync vault (should delete folder without 500 error)

## Expected behavior

- Document should be deleted from device
- Empty folder should be deleted from device
- No 500 errors from cloud API
""")

    print(f"\n1. Created test document: {test_doc.relative_to(workspace.vault_dir)}")

    # Sync document to device
    print(f"2. Syncing document to device...")
    doc_uuid = device.upload_document(test_doc)
    print(f"   Document UUID: {doc_uuid}")

    # Wait for sync to complete and annotations (if any)
    print(f"3. Waiting for device sync...")
    state = device.wait_for_annotations(
        doc_uuid,
        timeout=10.0,  # Short timeout, we don't expect annotations
        expect_annotations=False
    )

    print(f"   Sync complete: has_annotations={state.has_annotations}")

    if device_mode == "online":
        print(f"\n4. Document synced successfully to device!")
        print(f"   You can verify it appeared in the DeviceBench/projects folder on your device.")
        print(f"\n   Now we'll delete it and unsync to trigger folder deletion...")

        # Give user a moment to verify on device if they want
        import time
        time.sleep(2)

    # Delete the document locally
    print(f"\n5. Deleting document locally: {test_doc.name}")
    test_doc.unlink()

    # Unsync vault - this should delete document AND folder
    print(f"6. Unsyncing vault (should delete document + folder)...")

    try:
        files_removed, files_deleted = device.unsync_vault()
        print(f"   ✓ Unsync successful:")
        print(f"     - Files removed from state: {files_removed}")
        print(f"     - Files deleted from cloud: {files_deleted}")

        # Verify folder was deleted from state
        folders_remaining = device.get_remaining_folders()
        print(f"     - Folders remaining: {len(folders_remaining)}")

        if folders_remaining:
            print(f"       WARNING: {len(folders_remaining)} folders still in state:")
            for folder_path, folder_uuid in folders_remaining:
                print(f"         - {folder_path} ({folder_uuid})")

    except Exception as e:
        print(f"   ✗ Unsync FAILED with error:")
        print(f"     {type(e).__name__}: {e}")

        if "500" in str(e) and "Server Error" in str(e):
            print(f"\n   🎯 REPRODUCED: This is the folder deletion bug!")
            print(f"      The folder deletion triggered a 500 error from rmfakecloud/remarkable cloud.")

        # Re-raise to fail the test
        raise

    # End test and save testdata (online mode only)
    if device_mode == "online":
        print(f"\n7. Saving testdata...")
        success = True  # Test passed if we got here
        device.end_test(test_id, success=success)
        print(f"\n{'='*60}")
        print(f"✓ Testdata captured to: tests/device_bench/fixtures/testdata/{test_id}/")
        print(f"")
        print(f"To replay offline:")
        print(f"  uv run pytest tests/device_bench/test_folder_deletion_device.py \\")
        print(f"    -m offline --device-mode=offline --test-artifact={test_id} -v")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"✓ Offline replay completed successfully")
        print(f"{'='*60}")


@pytest.mark.device
def test_nested_folder_deletion(device, workspace, device_mode):
    """Test nested folder deletion (a/b/c structure).

    This captures the scenario where we have:
    - vault/a/b/c/document.md
    - Deleting should remove folders in order: c, b, a
    """
    test_id = "nested_folder_deletion"

    if device_mode == "offline" and not hasattr(device, '_current_test_id'):
        pytest.skip("No testdata loaded for offline replay")

    if device_mode == "online":
        print(f"\n{'='*60}")
        print(f"ONLINE MODE: Capturing testdata for '{test_id}'")
        print(f"{'='*60}")
        device.start_test(test_id)

    # Create nested structure
    nested_dir = workspace.vault_dir / "a" / "b" / "c"
    nested_dir.mkdir(parents=True, exist_ok=True)

    test_doc = nested_dir / "deep.md"
    test_doc.write_text("# Deep nested document\n\nIn a/b/c folder structure.")

    print(f"1. Created nested document: {test_doc.relative_to(workspace.vault_dir)}")

    # Sync to device
    print(f"2. Syncing to device...")
    doc_uuid = device.upload_document(test_doc)

    print(f"3. Waiting for sync...")
    device.wait_for_annotations(doc_uuid, timeout=10.0, expect_annotations=False)

    # Delete and unsync
    print(f"4. Deleting document and unsyncing...")
    test_doc.unlink()

    files_removed, files_deleted = device.unsync_vault()
    print(f"   ✓ Unsync successful: {files_removed} removed, {files_deleted} deleted")

    if device_mode == "online":
        device.end_test(test_id, success=True)
        print(f"\n✓ Testdata captured: {test_id}")


@pytest.mark.device
def test_remarkable_folder_deletion(device, workspace, device_mode):
    """Test deleting the remarkable_folder itself (vault root folder).

    This is the scenario that was failing with 500 errors:
    - Create document in vault root
    - Sync to device (creates DeviceBench folder)
    - Delete and unsync (should delete DeviceBench folder)
    """
    test_id = "remarkable_folder_deletion"

    if device_mode == "offline" and not hasattr(device, '_current_test_id'):
        pytest.skip("No testdata loaded for offline replay")

    if device_mode == "online":
        print(f"\n{'='*60}")
        print(f"ONLINE MODE: Capturing testdata for '{test_id}'")
        print(f"{'='*60}")
        device.start_test(test_id)

    # Create document in vault root
    test_doc = workspace.vault_dir / "root_document.md"
    test_doc.write_text("# Root level document\n\nDirectly in vault root.")

    print(f"1. Created root document: {test_doc.name}")

    # Sync to device
    print(f"2. Syncing to device...")
    doc_uuid = device.upload_document(test_doc)

    print(f"3. Waiting for sync...")
    device.wait_for_annotations(doc_uuid, timeout=10.0, expect_annotations=False)

    # Delete and unsync - should delete DeviceBench folder
    print(f"4. Deleting document and unsyncing (should delete DeviceBench folder)...")
    test_doc.unlink()

    try:
        files_removed, files_deleted = device.unsync_vault()
        print(f"   ✓ Unsync successful: {files_removed} removed, {files_deleted} deleted")

        folders_remaining = device.get_remaining_folders()
        if folders_remaining:
            print(f"   WARNING: {len(folders_remaining)} folders remain: {folders_remaining}")
        else:
            print(f"   ✓ All folders deleted successfully (including DeviceBench)")

    except Exception as e:
        print(f"   ✗ FAILED: {type(e).__name__}: {e}")
        if "500" in str(e):
            print(f"   🎯 REPRODUCED: DeviceBench folder deletion caused 500 error!")
        raise

    if device_mode == "online":
        device.end_test(test_id, success=True)
        print(f"\n✓ Testdata captured: {test_id}")
