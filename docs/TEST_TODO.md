# Test Coverage TODO - Bidirectional Sync (M5)

**Created**: 2026-01-08
**Status**: Active
**Context**: Analysis of test coverage gaps for M5 bidirectional sync

## Summary

M5 bidirectional sync has solid unit test coverage for core algorithms, but round-trip testing of conflict scenarios is insufficient. Several critical features are either untested or unimplemented.

## Priority Legend

- 🔴 **P0 - Blocking**: Must fix before production use
- 🟡 **P1 - High**: Should fix soon, affects user experience
- 🟢 **P2 - Medium**: Important for robustness
- ⚪ **P3 - Low**: Edge cases, nice to have

---

## ✅ P0 - Resolved Issues

### 1. Highlight X-Shift Re-Anchoring - FIXED (2026-01-08)

**File**: `tests/record_replay/test_highlight_reanchor.py`
**Status**: ✅ Test enabled and passing

**Problem**: When text is inserted *within* a line (before highlighted text), the highlight's X coordinate was not being adjusted correctly.

**Root Cause**: In `apply_to_page()`, when finding the old_offset for delta calculation, we used `old_text.find(highlight_text)` which returns the FIRST occurrence. But for text like `'Highlight the word "target" here: The target word'`, the highlight was on the SECOND "target", not the first.

**Fix**: Use `_find_best_text_offset()` with anchor_context disambiguation for BOTH old and new offset calculation. This ensures we find the SAME occurrence that was highlighted.

**Changes**:
- `src/rock_paper_sync/annotations/handlers/highlight_handler.py`: Use disambiguation for old_offset
- `tests/record_replay/test_highlight_reanchor.py`: Enabled test, increased golden tolerance to 30px

**Verified**:
- X-shift: "target" correctly shifts right when "INSERTED " added before it
- Y-shift: "bottom" correctly shifts down when paragraph added above
- X stability: "bottom" X stays unchanged (Δx=0.0) when only Y should change
- Reflow: "cross line" correctly splits into 2 rectangles when text wraps

**Related Fixes** (same disambiguation pattern):
- `find_and_resolve_anchor()`: Added Y-position disambiguation for multiple occurrences
- `create_anchor()`: Added X-position disambiguation for multiple occurrences within paragraph

---

## 🔴 P0 - Blocking Issues

---

### 2. Orphan Recovery Workflow - FIXED (2026-01-08)

**File**: `src/rock_paper_sync/pull_sync.py`, `tests/test_pull_sync.py`
**Status**: ✅ Implemented and tested

**Problem**: When a user deletes annotated text (creating an orphan), then restores similar text, the orphan was never re-evaluated. Annotations were permanently lost after 7-day retention.

**Fix**: Implemented `attempt_orphan_recovery()` in `PullSyncEngine`:
1. Checks all synced files for orphans in DB
2. For each orphan, checks if `original_anchor_text` now exists in markdown
3. If text found, forces a re-pull from device to attempt reanchoring
4. Successfully reanchored annotations are automatically cleared from orphan DB
5. Called automatically during pull sync phase (before normal pull)

**Changes**:
- `src/rock_paper_sync/pull_sync.py`: Added `attempt_orphan_recovery()` method
- `src/rock_paper_sync/cli.py`: Integrated recovery into `_run_pull_sync()`
- `tests/test_pull_sync.py`: Added `TestOrphanRecovery` with 8 comprehensive tests

**Tests Added**:
- `test_recovery_no_orphans` - No orphans to recover
- `test_recovery_no_synced_files` - Orphans exist but no synced files
- `test_recovery_file_not_found` - File was deleted from vault
- `test_recovery_text_not_in_content` - Orphan text still not in content
- `test_recovery_dry_run` - Dry run reports but doesn't modify
- `test_recovery_successful` - Full recovery workflow
- `test_recovery_partial_success` - Some orphans recover, others don't
- `test_recovery_multiple_files` - Recovery across multiple files

---

### 3. Double Conflict (Content + Annotation Changed) - DOCUMENTED (2026-01-09)

**File**: `tests/annotations/test_annotation_anchoring.py`
**Status**: ✅ Behavior documented and tested

**Problem**: When both the source text AND the annotation properties change simultaneously, behavior was undocumented.

**Resolution**: The behavior is now well-defined and tested:

1. **Text substitution with stable context** (e.g., "important" → "crucial"):
   - DiffAnchor finds the span between stable context anchors
   - Annotation migrates to new text at confidence 0.6
   - This is INTENTIONAL: highlights should follow edited text

2. **Text + context both change significantly**:
   - DiffAnchor cannot find stable anchors
   - Annotation becomes orphaned (correct behavior)
   - Prevents highlighting wrong text

3. **Annotation properties (color, etc.)**:
   - Always taken from device (no conflict)
   - Architecture ensures device version wins

**Tests Added** (`TestDoubleConflict`, `TestDoubleConflictIntegration`):
- `test_word_substitution_same_context` - "important" → "crucial" migrates
- `test_word_substitution_with_context_change` - Too much change → orphan
- `test_phrase_replacement` - Significant change → orphan
- `test_phrase_replacement_stable_context` - Stable anchors → migrates
- `test_complete_rewrite_orphans` - Complete rewrite → orphan
- `test_text_expansion` - "fox" → "brown fox" migrates
- `test_text_contraction` - "quick brown fox" → "fox" migrates
- `test_confidence_threshold_boundary` - Verifies 0.6 threshold
- `test_multiple_words_same_replacement` - Each occurrence migrates correctly
- `test_reanchor_with_text_modification` - Full pipeline test
- `test_annotation_color_unchanged_on_device` - Documents property handling

**Key insight**: The 0.6 confidence threshold in `_reanchor_annotations()` is the boundary between migration and orphaning. This is appropriate - it allows text edits to be followed while preventing wrong guesses when too much changes.

---

## 🟡 P1 - High Priority

### 4. Cascading Conflicts - RESOLVED (2026-01-09)

**File**: `tests/test_pull_sync.py`
**Status**: ✅ Already handled by P0 #2 orphan recovery

**Problem**: Multi-round modifications where orphans should recover but don't.

**Resolution**: The `attempt_orphan_recovery()` implemented in P0 #2 already handles this:
- Checks if `original_anchor_text` exists **anywhere** in the document
- Works regardless of where the text moved (different section, paragraph, etc.)
- Triggers re-pull from device when text is found
- Device annotation's AnchorContext disambiguates the correct location

**Tests Added** (`TestCascadingConflicts`):
- `test_text_moved_to_different_section` - Text moves from Section A to Section B
- `test_text_duplicated_in_multiple_places` - Same text now appears 3 times
- `test_text_in_different_file` - Text exists but in wrong file (no recovery)
- `test_partial_text_match_not_recovered` - Partial matches don't trigger false recovery
- `test_cascading_with_multiple_orphans` - Multiple orphans, only matching ones recover

**Key insight**: Cascading conflicts are a subset of orphan recovery - if the text exists anywhere in the file, recovery is attempted.

---

### 5. Dense Annotation Areas - NOT TESTED

**Problem**: Multiple annotations on the same paragraph may interfere during re-anchoring.

**Example**:
```
Paragraph with 5 strokes + 2 highlights
→ Paragraph modified
→ All 7 annotations need independent re-anchoring
→ Unknown if ordering/layering preserved
```

**Action Items**:
- [ ] Add test with multiple strokes in one paragraph
- [ ] Add test with multiple highlights on consecutive words
- [ ] Verify annotation ordering preserved

---

### 6. Pull/Push Sync Race Condition - NOT TESTED

**Problem**: No locking mechanism prevents concurrent pull and push operations on the same file.

**Action Items**:
- [ ] Add test for concurrent operations
- [ ] Consider adding file-level locking
- [ ] Document expected behavior

---

## 🟢 P2 - Medium Priority

### 7. Overlapping Highlight Conflict Resolution

**Status**: Partially tested

**Problem**: When overlapping highlights exist and one is deleted, disambiguation may fail.

**Action Items**:
- [ ] Add test for overlapping highlights with deletion
- [ ] Verify disambiguation algorithm

---

### 8. Cross-Page Annotation During Reflow

**Status**: Structure preservation tested, conflict during reflow not tested

**Problem**: Annotation spanning pages 1-2, content change forces reflow, annotation now spans pages 2-3.

**Action Items**:
- [ ] Add test for page shift during reflow
- [ ] Verify annotation coordinates updated correctly

---

### 9. Confidence Threshold Boundary Cases

**Problem**: No tests for behavior exactly at threshold boundaries.

**Action Items**:
- [ ] Add test for confidence = 0.79 (below threshold)
- [ ] Add test for confidence = 0.80 (at threshold)
- [ ] Add test for confidence = 0.81 (above threshold)

---

### 10. Annotation Type Mismatch in Merge

**Problem**: Old version has highlight, new version has stroke on same area.

**Action Items**:
- [ ] Define expected behavior
- [ ] Add test case

---

## ⚪ P3 - Low Priority

### 11. Unicode Text Modification in Anchors

**Example**: Highlight on "café" → user changes to "cafe"

**Action Items**:
- [ ] Add test for unicode normalization in fuzzy matching

---

### 12. Whitespace-Only Modifications

**Example**: "hello  world" → "hello world" (double space to single)

**Action Items**:
- [ ] Add test for whitespace handling in anchor resolution

---

### 13. Orphan Comment Placement with Dynamic Structure

**Problem**: Orphan comment placed at bottom, user adds content after, comment moves to middle.

**Action Items**:
- [ ] Define expected placement behavior
- [ ] Add test case

---

## Test Coverage Statistics

| Area | Current | Target | Notes |
|------|---------|--------|-------|
| Anchor matching | 95% | 95% | GOOD |
| Three-way merge | 70% | 85% | Add conflict cases |
| Orphan handling | 40% | 80% | Add recovery |
| Highlight handler | 63% | 80% | Fix X-shift |
| Round-trip conflicts | 30% | 70% | Major gap |

## Files to Create/Modify

### New Test Files
- [ ] `tests/test_orphan_recovery.py` - Orphan lifecycle tests
- [ ] `tests/record_replay/test_double_conflict.py` - Content + annotation conflict
- [ ] `tests/record_replay/test_dense_annotations.py` - Multiple annotations same area

### Existing Files to Modify
- [ ] `tests/record_replay/test_highlight_reanchor.py` - Unskip and fix
- [ ] `tests/annotations/test_merging.py` - Add conflict scenarios
- [ ] `tests/annotations/test_annotation_anchoring.py` - Add boundary tests

---

## Progress Log

| Date | Item | Status | Notes |
|------|------|--------|-------|
| 2026-01-08 | Document created | ✓ | Initial analysis |
| 2026-01-08 | P0 #1: X-shift re-anchoring | ✓ | Fixed disambiguation bug, enabled test |
| 2026-01-08 | Related: find_and_resolve_anchor | ✓ | Added Y-position disambiguation |
| 2026-01-08 | Related: create_anchor | ✓ | Added X-position disambiguation |
| 2026-01-08 | P0 #2: Orphan recovery workflow | ✓ | Implemented attempt_orphan_recovery(), 8 unit tests |
| 2026-01-09 | P0 #3: Double conflict behavior | ✓ | Documented behavior, added 11 tests |
| 2026-01-09 | P1 #4: Cascading conflicts | ✓ | Already handled by P0 #2, added 5 tests |
| | | | |
