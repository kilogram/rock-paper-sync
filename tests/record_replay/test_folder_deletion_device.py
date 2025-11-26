"""On-device test for folder deletion scenarios.

Tests document sync and deletion workflows with different folder structures
to verify that folder deletion doesn't cause errors.

This test runs against a real reMarkable device to capture the folder deletion
workflow as testdata. The captured testdata can then be replayed offline to
reproduce and validate the fix for the folder deletion bug.

Usage:
    # Run all folder deletion scenarios on real device
    uv run pytest tests/record_replay/test_folder_deletion_device.py -m device --device-mode=online -v

    # Replay captured testdata offline
    uv run pytest tests/record_replay/test_folder_deletion_device.py -m offline --device-mode=offline -v
"""

import pytest
from pathlib import Path


# Test scenarios: (test_id, folder_structure, file_path, folders_to_delete_after)
DELETION_SCENARIOS = [
    (
        "folder_deletion_single",
        "projects",
        "projects/document.md",
        ["projects"],
    ),
    (
        "folder_deletion_nested",
        "a/b/c",
        "a/b/c/deep.md",
        ["a/b/c", "a/b", "a"],
    ),
    (
        "folder_deletion_root",
        "",
        "root_document.md",
        [],
    ),
]


@pytest.mark.device
@pytest.mark.parametrize(
    "test_id,folder_structure,file_path,folders_to_delete_after",
    DELETION_SCENARIOS,
    ids=[s[0] for s in DELETION_SCENARIOS],
)
def test_folder_deletion_workflow(
    device, workspace, device_mode, test_id, folder_structure, file_path, folders_to_delete_after
):
    """Test complete folder deletion workflow with different folder structures.

    Parametrized test covering three scenarios:
    1. Single folder: projects/document.md
    2. Nested folders: a/b/c/deep.md
    3. Root level: root_document.md

    For each scenario:
    1. Creates markdown file(s)
    2. Syncs to device
    3. Deletes locally
    4. Unsyncs vault (should delete document + folders without errors)
    """
    # Skip in offline mode if no testdata loaded
    if device_mode == "offline" and not hasattr(device, '_current_test_id'):
        pytest.skip("No testdata loaded for offline replay")

    if device_mode == "online":
        print(f"\n{'='*60}")
        print(f"ONLINE MODE: Capturing testdata for '{test_id}'")
        print(f"{'='*60}")
        device.start_test(test_id)
    else:
        print(f"\n{'='*60}")
        print(f"OFFLINE MODE: Replaying testdata '{test_id}'")
        print(f"{'='*60}")
        workspace.vault.restore_vault("initial_state")

    # Create folder structure and file
    if folder_structure:
        workspace.vault.create_folder(folder_structure)

    # Use generic content for all scenarios
    test_doc_content = f"# {test_id}\n\nTest document for {folder_structure or 'root level'} scenario."
    workspace.vault.create_file(file_path, test_doc_content)

    test_doc = workspace.workspace_dir / file_path
    print(f"\n1. Created test document: {test_doc.relative_to(workspace.workspace_dir)}")

    # Sync document to device
    print(f"2. Syncing document to device...")
    doc_uuid = device.upload_document(test_doc)
    print(f"   Document UUID: {doc_uuid}")

    # Wait for sync to complete
    print(f"3. Waiting for device sync...")
    state = device.wait_for_annotations(doc_uuid, timeout=10.0)
    print(f"   Sync complete: has_annotations={state.has_annotations}")

    if device_mode == "online":
        print(f"\n4. Document synced successfully to device!")
        import time
        time.sleep(2)

    # Delete the document locally
    print(f"\n5. Deleting document locally: {test_doc.name}")
    workspace.vault.delete_file(file_path)

    # Delete folders in reverse order (innermost first)
    for folder_path in folders_to_delete_after:
        print(f"   Deleting folder: {folder_path}")
        workspace.vault.delete_folder(folder_path)

    # Unsync vault - should delete document AND folders
    print(f"6. Unsyncing vault (should delete document + folders)...")

    try:
        files_removed, files_deleted = device.unsync_vault()
        print(f"   ✓ Unsync successful:")
        print(f"     - Files removed from state: {files_removed}")
        print(f"     - Files deleted from cloud: {files_deleted}")

        # Verify folders were deleted from state
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
            print(f"\n   🎯 REPRODUCED: Folder deletion caused 500 error!")
            print(f"      The cloud API returned a server error during folder deletion.")

        raise

    # End test and save testdata (online mode only)
    if device_mode == "online":
        print(f"\n7. Saving testdata...")
        workspace.vault.snapshot_vault("after_deletion")
        device.end_test(test_id, success=True)
        print(f"\n{'='*60}")
        print(f"✓ Testdata captured to: tests/record_replay/fixtures/testdata/{test_id}/")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"✓ Offline replay completed successfully")
        print(f"{'='*60}")
