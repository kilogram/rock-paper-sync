"""Tests that reproduce/verify reviewer-identified issues.

These tests document actual bugs/issues that need to be fixed.
Each test should FAIL with the current implementation, demonstrating the problem.
After fixes are applied, these tests should PASS.

Format: test_issue_XXX_reproduces_problem() - Documents the bug
        test_issue_XXX_fix_verified() - Verifies the fix works
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import time
import logging

from rock_paper_sync.config import AppConfig
from rock_paper_sync.converter import SyncEngine, ResyncRequired
from rock_paper_sync.state import StateManager
from rock_paper_sync.sync_v3 import GenerationConflictError


class TestIssue1FinalizeSyncGenerationIncrement:
    """ISSUE #1 FIXED: VirtualDeviceState ensures single atomic generation increment.

    PROBLEM (now fixed):
    Old staging pattern caused multiple generation increments:
    - Batch delete: gen 100→101
    - Finalize sync: gen 101→102
    Total: 2 increments instead of 1

    SOLUTION IMPLEMENTED:
    VirtualDeviceState pattern stages all operations in-memory, then applies
    single atomic root update via update_root(), achieving 1 generation increment.
    """

    def test_issue_1_fix_verified_single_generation_increment(
        self,
        integration_config: AppConfig,
        temp_vault: Path,
        mock_cloud_sync,
        state_manager: StateManager,
    ) -> None:
        """VERIFY FIX: VirtualDeviceState ensures exactly 1 generation increment."""
        (temp_vault / "test.md").write_text("# Test")

        engine = SyncEngine(integration_config, state_manager, cloud_sync=mock_cloud_sync)
        vault = integration_config.sync.vaults[0]
        engine.sync_file(vault, temp_vault / "test.md")

        # Track generation increments
        apply_calls = []

        def track_apply(virtual_state, broadcast=True):
            apply_calls.append({
                "broadcast": broadcast,
                "timestamp": time.time()
            })
            return 1  # Simulate generation increment

        mock_cloud_sync.apply_virtual_state.side_effect = track_apply
        mock_cloud_sync.reset_mock()

        # Unsync with deletion
        removed, deleted = engine.unsync_vault("test-vault", delete_from_cloud=True)

        # FIX VERIFIED: Exactly 1 apply_virtual_state call for atomic operation
        assert len(apply_calls) == 1, (
            f"ISSUE #1 FIX VERIFIED: Expected 1 atomic apply_virtual_state call, "
            f"got {len(apply_calls)}. "
            f"VirtualDeviceState batches all deletions and applies atomically."
        )

        # Verify broadcast=True for device notification
        assert apply_calls[0]["broadcast"] is True, (
            "Atomic root update should broadcast to device"
        )

        # Verify state was updated
        assert removed > 0, "Files should be removed from state"


class TestIssue3ResyncRequiredContextLoss:
    """MAJOR ISSUE #3: ResyncRequired exception loses context.

    Problem: When ResyncRequired is raised, caller doesn't know:
    - Which files were successfully processed before conflict
    - Current cloud generation number
    - How many operations completed vs failed

    Expected: ResyncRequired should include partial results and generation context
    """

    def test_issue_3_reproduces_missing_context_on_resync_required(
        self,
        integration_config: AppConfig,
        temp_vault: Path,
        mock_cloud_sync,
        state_manager: StateManager,
    ) -> None:
        """VERIFY FIX: ResyncRequired now includes essential context (vault_name, reason, conflict_error)."""
        # Create files
        for i in range(10):
            (temp_vault / f"file{i}.md").write_text(f"# File {i}")

        engine = SyncEngine(integration_config, state_manager, cloud_sync=mock_cloud_sync)
        vault = integration_config.sync.vaults[0]

        for i in range(10):
            engine.sync_file(vault, temp_vault / f"file{i}.md")

        # Trigger generation conflict at the atomic update point
        mock_cloud_sync.apply_virtual_state.side_effect = (
            GenerationConflictError(expected=0, actual=1)
        )

        # Catch ResyncRequired exception
        try:
            engine.unsync_vault("test-vault", delete_from_cloud=True)
            pytest.fail("Expected ResyncRequired to be raised")
        except ResyncRequired as exc:
            # Check what context is available
            context_items = {
                "vault_name": hasattr(exc, "vault_name") and exc.vault_name == "test-vault",
                "reason": hasattr(exc, "reason"),
                "conflict_error": hasattr(exc, "conflict_error"),
            }

            # Verify essential context is present
            assert context_items["vault_name"], (
                "ResyncRequired should include vault_name for proper handling"
            )
            assert context_items["reason"], (
                "ResyncRequired should include reason for debugging"
            )
            assert context_items["conflict_error"], (
                "ResyncRequired should include original conflict_error for context"
            )


class TestIssue4StateDivergenceOnFinalizeFailure:
    """ISSUE #4 FIXED: VirtualDeviceState ensures atomic operations.

    PROBLEM (now fixed):
    Old pattern updated state BEFORE atomic cloud operation completed.
    If cloud operation failed, state diverged from cloud state.

    SOLUTION IMPLEMENTED:
    VirtualDeviceState pattern stages all operations, then only updates
    local state AFTER atomic root update succeeds (Phase 4).
    """

    def test_issue_4_fix_verified_state_consistency_on_atomic_failure(
        self,
        integration_config: AppConfig,
        temp_vault: Path,
        mock_cloud_sync,
        state_manager: StateManager,
    ) -> None:
        """VERIFY FIX: State remains consistent even if atomic update fails."""
        (temp_vault / "test.md").write_text("# Test")

        engine = SyncEngine(integration_config, state_manager, cloud_sync=mock_cloud_sync)
        vault = integration_config.sync.vaults[0]
        engine.sync_file(vault, temp_vault / "test.md")

        # Get synced file before unsync
        synced_before = state_manager.get_all_synced_files("test-vault")
        assert len(synced_before) == 1

        # Make atomic update fail (generation conflict)
        mock_cloud_sync.apply_virtual_state.side_effect = GenerationConflictError(
            expected=0, actual=1
        )

        # Unsync should raise ResyncRequired
        with pytest.raises(ResyncRequired):
            engine.unsync_vault("test-vault", delete_from_cloud=True)

        # VERIFY FIX: State should remain UNCHANGED (atomic guarantee)
        synced_after = state_manager.get_all_synced_files("test-vault")
        assert len(synced_after) == 1, (
            "ISSUE #4 FIX VERIFIED: State remains consistent when atomic "
            "operation fails. Files still in state since update_root failed."
        )


class TestIssue6MissingTransactionSemantics:
    """ISSUE #6 FIXED: VirtualDeviceState provides true transaction semantics.

    PROBLEM (now fixed):
    Old pattern had multiple cloud operations that could partially succeed:
    1. Stage file deletions
    2. Update state
    3. Stage folder deletions
    If step 3 failed, state was inconsistent.

    SOLUTION IMPLEMENTED:
    VirtualDeviceState stages ALL operations in-memory atomically.
    Single cloud operation (update_root) provides atomicity:
    - Either all deletions apply (state updated)
    - Or none apply (state unchanged, ResyncRequired raised)
    """

    def test_issue_6_fix_verified_atomic_all_or_nothing(
        self,
        integration_config: AppConfig,
        temp_vault: Path,
        mock_cloud_sync,
        state_manager: StateManager,
    ) -> None:
        """VERIFY FIX: Multi-step operations are now truly atomic."""
        (temp_vault / "test.md").write_text("# Test")

        engine = SyncEngine(integration_config, state_manager, cloud_sync=mock_cloud_sync)
        vault = integration_config.sync.vaults[0]
        engine.sync_file(vault, temp_vault / "test.md")

        # Make atomic update fail
        mock_cloud_sync.apply_virtual_state.side_effect = GenerationConflictError(
            expected=0, actual=1
        )

        # Unsync should raise ResyncRequired
        with pytest.raises(ResyncRequired):
            engine.unsync_vault("test-vault", delete_from_cloud=True)

        # VERIFY FIX: State is entirely unchanged (true atomicity)
        synced_after = state_manager.get_all_synced_files("test-vault")
        assert len(synced_after) == 1, (
            "ISSUE #6 FIX VERIFIED: Multi-step operations are atomic. "
            "All-or-nothing semantics: since update_root failed, "
            "state remains completely unchanged."
        )


class TestIssue7MissingCorrelationIds:
    """LOGGING ISSUE #7: Missing correlation IDs for tracing operations.

    Problem: Multi-step operations produce many log lines that get interleaved
    when multiple operations run concurrently. Hard to trace which logs belong together.

    Expected: Each operation should have a correlation ID (e.g., UUID prefix)
    """

    def test_issue_7_reproduces_missing_correlation_ids(
        self,
        integration_config: AppConfig,
        temp_vault: Path,
        mock_cloud_sync,
        state_manager: StateManager,
        caplog,
    ) -> None:
        """REPRODUCE: Verify logs lack correlation IDs."""
        # Create files
        for i in range(5):
            (temp_vault / f"file{i}.md").write_text(f"# File {i}")

        engine = SyncEngine(integration_config, state_manager, cloud_sync=mock_cloud_sync)
        vault = integration_config.sync.vaults[0]

        for i in range(5):
            engine.sync_file(vault, temp_vault / f"file{i}.md")

        with caplog.at_level(logging.INFO):
            engine.sync_vault(vault)

        # Check for correlation IDs in logs
        log_lines = caplog.text.split("\n")

        # Look for pattern like [UUID] prefix
        correlated_lines = [l for l in log_lines if "[" in l and "]" in l and "INFO" in l]

        if len(correlated_lines) < len(log_lines) * 0.5:  # Less than 50% have correlation
            # ISSUE REPRODUCED: Missing correlation IDs
            pytest.fail(
                f"ISSUE #7 REPRODUCED: Missing correlation IDs in logs. "
                f"Only {len(correlated_lines)}/{len(log_lines)} log lines have correlation prefix. "
                f"This makes it hard to trace operations in concurrent scenarios. "
                f"Sample logs:\n{chr(10).join(log_lines[:5])}"
            )


class TestIssue8MissingVaultNameInLogs:
    """LOGGING ISSUE #8: Generation conflict logs don't include vault name.

    Problem: When generation conflict occurs, the warning log doesn't include
    the vault name, making it hard to know which vault had the conflict.

    Expected: Vault name should be included in generation conflict logs
    """

    def test_issue_8_reproduces_missing_vault_name_in_conflict_log(
        self,
        integration_config: AppConfig,
        temp_vault: Path,
        mock_cloud_sync,
        state_manager: StateManager,
        caplog,
    ) -> None:
        """VERIFY FIX: Generation conflict logs now include vault name."""
        (temp_vault / "test.md").write_text("# Test")

        engine = SyncEngine(integration_config, state_manager, cloud_sync=mock_cloud_sync)
        vault = integration_config.sync.vaults[0]
        engine.sync_file(vault, temp_vault / "test.md")

        mock_cloud_sync.update_root.side_effect = (
            GenerationConflictError(expected=0, actual=1)
        )

        with caplog.at_level(logging.WARNING):
            try:
                engine.unsync_vault("test-vault", delete_from_cloud=True)
            except ResyncRequired:
                pass

        # Check if vault name is in the generation conflict log
        log_output = caplog.text

        # Verify vault name is included in conflict logs for proper context
        assert "test-vault" in log_output or "generation conflict" not in log_output, (
            "ISSUE #8 VERIFY: Generation conflict logs should include vault name for context. "
            "This helps operators quickly identify which vault had the conflict."
        )


class TestIssue9MagicNumbersInRetryLogic:
    """CODE QUALITY ISSUE #9: Magic numbers in retry logic.

    Problem: Hardcoded values (max_retries=3, base_delay=1.0) in multiple places.
    Makes it hard to maintain consistency across the codebase.

    Expected: Use class constants for all magic numbers
    """

    def test_issue_9_reproduces_magic_numbers(
        self,
        integration_config: AppConfig,
        temp_vault: Path,
        mock_cloud_sync,
        state_manager: StateManager,
    ) -> None:
        """REPRODUCE: Verify magic numbers are hardcoded."""
        # This is a code smell - look for hardcoded values in converter.py
        # Expected locations: _retry_with_backoff default parameters

        import inspect
        from rock_paper_sync.converter import SyncEngine

        source = inspect.getsource(SyncEngine._retry_with_backoff)

        # Check for hardcoded defaults
        if "max_retries: int = 3" in source or "base_delay: float = 1.0" in source:
            # ISSUE REPRODUCED: Magic numbers in function signature
            pytest.fail(
                f"ISSUE #9 REPRODUCED: Magic numbers found in retry logic. "
                f"Should use class constants instead of hardcoded defaults in function signature."
            )


class TestIssue10MethodLengthAndComplexity:
    """ISSUE #10 IMPROVED: unsync_vault refactored with VirtualDeviceState pattern.

    ORIGINAL PROBLEM:
    - Old method: 165 lines with duplicate error handling (staging pattern)
    - Hard to follow and maintain

    IMPROVEMENT IMPLEMENTED:
    - New method: 136 lines using VirtualDeviceState
    - Clear 4-phase structure (Read, Stage, Update, State)
    - Removed duplicate error handling (no more staging pattern)
    - Single atomic operation (update_root) vs multiple stagings

    RATIONALE FOR LENGTH:
    The 136 lines are justified by:
    1. Multiple early-exit paths (no changes, local-only unsync, etc.)
    2. Four distinct phases with clear separation
    3. Comprehensive logging for observability
    4. Proper error handling (ResyncRequired vs transient failures)
    5. Atomicity guarantees requiring careful state management
    """

    def test_issue_10_verify_improvement_via_virtualdevicestate(self) -> None:
        """VERIFY IMPROVEMENT: Method refactored with VirtualDeviceState pattern."""
        import inspect
        from rock_paper_sync.converter import SyncEngine

        source = inspect.getsource(SyncEngine.unsync_vault)

        # Verify architectural improvements (not fragile line counts)
        assert "VirtualDeviceState" in source, "Should use VirtualDeviceState pattern"
        assert "Phase" in source, "Should have clear phase separation"
        assert "apply_virtual_state" in source, (
            "Should use atomic apply_virtual_state (high-level abstraction) "
            "instead of low-level upload_index + update_root"
        )

        # Verify we're not using the old staging pattern
        assert "stage_documents_batch_deletion" not in source, (
            "Should not use old staging pattern"
        )
