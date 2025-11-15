# Test Deletion vs Rewrite Analysis

**Current Status**: 262 passed, 21 skipped (7.4% skip rate)
**Goal**: 0% skip rate, 90%+ coverage

## Remaining 21 Skipped Tests Analysis

### Category 1: DELETE - Truly Obsolete (4 tests) ✅
**File**: test_config.py
**Reason**: Test removed `remarkable_output` field - no cloud equivalent

1. `test_validate_nonexistent_output` - Validates remarkable_output exists
2. `test_missing_remarkable_output` - Validates remarkable_output required
3. `test_remarkable_output_not_directory` - Validates remarkable_output is directory
4. `test_remarkable_output_not_writable` - Validates remarkable_output writable

**Action**: DELETE - Remove skip marks, delete test methods entirely
**Impact**: -4 tests, no replacement needed

---

### Category 2: DELETE - Filesystem Cleanup Logic (6 tests) ✅
**File**: test_generator.py - TestWriteDocumentFiles class
**Reason**: Cloud sync doesn't use local file writes, so file structure validation is obsolete

1. `test_creates_directory` - Tests filesystem directory creation
2. `test_creates_metadata_file` - Tests .metadata file written
3. `test_creates_content_file` - Tests .content file written
4. `test_creates_rm_files` - Tests .rm files written
5. `test_creates_page_metadata_files` - Tests page metadata files written
6. `test_complete_file_structure` - Tests complete file tree

**Action**: DELETE entire TestWriteDocumentFiles class
**Rationale**: The LOGIC these test (metadata structure, content structure, rm file generation) is already tested by:
- test_roundtrip.py: Tests rm file generation and rmscene parsing
- test_metadata.py: Tests metadata generation functions
- Cloud sync integration tests will validate upload behavior

**Impact**: -6 tests, covered by existing tests

---

### Category 3: DELETE & REPLACE - Integration Tests (3 tests)
**File**: test_generator.py - TestIntegration class
**Current Tests**:
1. `test_full_pipeline_simple_doc` - Tests parse → generate → write
2. `test_full_pipeline_long_doc` - Tests multi-page generation → write
3. `test_roundtrip_verification` - Tests rmscene can parse generated files

**Action**: DELETE these specific tests, ALREADY COVERED by test_roundtrip.py
**Rationale**:
- test_roundtrip.py now has 13 comprehensive tests covering all this functionality
- test_roundtrip.py tests the same logic but better (in-memory, no filesystem)
- Redundant coverage wastes test execution time

**Impact**: -3 tests, no replacement (already covered)

---

### Category 4: REWRITE - Cloud Sync Integration (8 tests) 🔄
**File**: test_integration.py
**Current Tests**:

**TestFullPipelineStubs** (3 tests):
1. `test_end_to_end_sync` - Complete sync flow
2. `test_incremental_sync` - Only changed files sync
3. `test_folder_hierarchy_creation` - Folder structure preserved

**TestDocumentUpdateFlow** (5 tests):
4. `test_file_update_preserves_uuid_end_to_end`
5. `test_multiple_updates_same_document`
6. `test_update_with_folder_move`
7. `test_concurrent_updates_different_files`
8. `test_update_state_tracking`

**Current Problem**: These use `output` fixture from integration_env which no longer exists
**Action**: REWRITE all 8 tests to use mock_cloud_sync and verify cloud upload behavior

**New Test Strategy**:
```python
def test_end_to_end_sync(temp_vault, mock_cloud_sync, state_manager, sample_config):
    """Test complete sync flow with cloud."""
    # Create markdown file
    md_file = temp_vault / "test.md"
    md_file.write_text("# Test")

    # Sync
    engine = SyncEngine(sample_config, state_manager, cloud_sync=mock_cloud_sync)
    results = engine.sync_all_changed()

    # Verify cloud upload was called
    assert mock_cloud_sync.upload_document.called
    # Verify state database updated
    assert state_manager.get_file_state("test.md") is not None
```

**Impact**: Rewrite 8 tests (~2-3 hours work), critical for cloud sync validation

---

## Summary

| Category | Action | Count | Time Estimate |
|----------|--------|-------|---------------|
| Obsolete config tests | DELETE | 4 | 5 minutes |
| Obsolete file structure tests | DELETE | 6 | 5 minutes |
| Redundant integration tests | DELETE | 3 | 5 minutes |
| Cloud sync integration tests | REWRITE | 8 | 2-3 hours |
| **TOTAL** | | **21** | **~3 hours** |

**Expected Outcome**:
- 262 passed → 270 passed (8 new tests)
- 21 skipped → 0 skipped
- Skip rate: 7.4% → 0%
- Total tests: 283 → 270 tests (-13, but better quality)

---

## Additional Tests Needed for 90% Coverage

After deleting/rewriting skipped tests, we need NEW tests for:

### Critical: sync_v3.py (currently 18% coverage)
**Missing 10-15 tests**:
1. Blob upload success
2. Blob upload with hash mismatch (error handling)
3. Index file generation (BlobEntry creation)
4. hashOfHashesV3 calculation
5. Root index update with generation field
6. Generation conflict detection (GenerationConflictError)
7. Generation conflict retry
8. Document deletion via index
9. Multi-file document upload
10. Empty document handling

**Estimate**: 3-4 hours

### Critical: rm_cloud_sync.py (currently 28% coverage)
**Missing 10-12 tests**:
1. Document upload orchestration
2. Folder creation
3. Existing page UUID retrieval
4. Document deletion
5. Error handling (network, auth, conflicts)
6. Retry logic on transient failures
7. Multiple document upload
8. Folder hierarchy validation
9. Update existing document
10. Concurrent sync operations

**Estimate**: 3-4 hours

### Moderate: rm_cloud_client.py (currently 51% coverage)
**Missing 5-7 tests**:
1. Authentication flow
2. Device registration
3. Token refresh
4. API error responses
5. Network timeout handling
6. Invalid credentials
7. Rate limiting

**Estimate**: 2 hours

---

## Implementation Plan

### Phase 1: Clean Up (30 minutes)
1. Delete 4 obsolete config tests
2. Delete TestWriteDocumentFiles class (6 tests)
3. Delete TestIntegration class (3 tests)
4. Verify tests still pass after deletions

**Result**: 262 passed, 8 skipped

### Phase 2: Rewrite Integration Tests (2-3 hours)
1. Rewrite TestFullPipelineStubs tests (3 tests)
2. Rewrite TestDocumentUpdateFlow tests (5 tests)
3. Verify all integration tests pass

**Result**: 270 passed, 0 skipped ✅

### Phase 3: Add Protocol Tests (6-8 hours)
1. Add sync_v3.py tests (10-15 tests)
2. Add rm_cloud_sync.py tests (10-12 tests)
3. Add rm_cloud_client.py tests (5-7 tests)

**Result**: ~300 passed, 90%+ coverage ✅

**Total Time**: ~10-12 hours
**Target**: 300+ tests, 0% skip, 90%+ coverage
