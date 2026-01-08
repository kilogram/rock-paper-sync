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

### 2. Orphan Recovery Workflow - NOT TESTED

**Status**: Orphan creation works, but recovery is not implemented

**Problem**: When a user deletes annotated text (creating an orphan), then restores similar text, the orphan is never re-evaluated. Annotations are permanently lost after 7-day retention.

**Example**:
```
1. User highlights "important section" on device
2. User deletes paragraph containing it in Obsidian
3. Sync creates orphan (stored in DB with 7-day TTL)
4. User undoes deletion (text restored)
5. Sync does NOT recover the orphan ← BUG
6. After 7 days, annotation lost forever
```

**Impact**: Users lose annotations permanently when undoing deletions.

**Code Locations**:
- `state.py:add_orphaned_annotation()` - creates orphans ✓
- `state.py:get_orphaned_annotations()` - retrieves orphans ✓
- `state.py:delete_orphaned_annotation()` - exists but never called for recovery
- `pull_sync.py:_record_orphans()` - creates orphans ✓
- **MISSING**: `attempt_orphan_recovery()` method

**Action Items**:
- [ ] Implement `attempt_orphan_recovery()` in PullSyncEngine
- [ ] Call it after markdown modifications
- [ ] Add unit test for recovery workflow
- [ ] Add round-trip test with device data

---

### 3. Double Conflict (Content + Annotation Changed) - NOT TESTED

**Status**: No test coverage

**Problem**: When both the source text AND the annotation properties change simultaneously, behavior is undefined.

**Example**:
```
1. Text "important" is highlighted (yellow) on device
2. User changes "important" → "crucial" in markdown
3. User also changed highlight color to red on device
4. Sync runs - what happens?
   - Fuzzy match "crucial"? (wrong text)
   - Orphan? (loses annotation)
   - Merge both changes? (not implemented)
```

**Impact**: Silent data loss or wrong text highlighted.

**Action Items**:
- [ ] Define expected behavior for double conflicts
- [ ] Add unit test for `AnchorContext.resolve()` with modified text
- [ ] Add integration test for full workflow
- [ ] Consider user notification for low-confidence matches

---

## 🟡 P1 - High Priority

### 4. Cascading Conflicts - NOT TESTED

**Problem**: Multi-round modifications where orphans should recover but don't.

**Example**:
```
1. Highlight on "Section A content"
2. Delete Section A → orphan created
3. Add Section B with text "Section A content" (same text!)
4. Orphan never re-evaluated against Section B
```

**Action Items**:
- [ ] Add test for orphan re-evaluation on new content
- [ ] Integrate with orphan recovery mechanism (P0 #2)

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
| | | | |
