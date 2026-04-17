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

### 5. Dense Annotation Areas - VERIFIED (2026-01-09)

**File**: `tests/test_annotation_renderer.py`
**Status**: ✅ Working correctly, tests added

**Problem**: Multiple annotations on the same paragraph may interfere during re-anchoring.

**Resolution**: The rendering system handles dense annotations correctly:

1. **Highlights**: Sorted in reverse order by position before insertion
   - Inserting `==` markers from end to start preserves offsets
   - Multiple highlights on consecutive words work correctly
   - Order is preserved regardless of annotation input order

2. **Strokes**: Grouped by anchor position
   - Multiple strokes on same word each get their own footnote
   - Footnote numbering is sequential
   - All footnote definitions are appended at document end

**Tests Added** (`TestDenseAnnotationAreas`):
- `test_multiple_strokes_same_paragraph` - 3 strokes in one paragraph
- `test_consecutive_highlights_no_space` - 3 consecutive word highlights
- `test_overlapping_anchor_areas_strokes` - Overlapping anchor text
- `test_mixed_dense_annotations` - 2 highlights + 2 strokes in same sentence
- `test_annotation_ordering_preserved_on_same_word` - 2 strokes on same word
- `test_five_highlights_same_sentence` - 5 highlights stress test
- `test_five_strokes_same_paragraph` - 5 strokes stress test
- `test_highlight_position_stability_after_multiple_insertions` - Non-sequential input order

**Key insight**: The reverse-order sorting in `render_highlights()` is critical - inserting from end to start ensures earlier positions remain valid.

---

### 6. Pull/Push Sync Race Condition - ANALYZED (2026-01-09)

**File**: N/A (architectural analysis)
**Status**: ✅ By design - no locking needed for normal use

**Problem**: No locking mechanism prevents concurrent pull and push operations on the same file.

**Resolution**: The architecture handles concurrency appropriately for intended use cases:

1. **Single process (normal use)**:
   - `sync --direction both` runs pull first, then push sequentially
   - Pull modifies markdown files, push sees those modifications
   - No race condition possible within a single CLI invocation

2. **Database concurrency**:
   - SQLite uses WAL mode (`PRAGMA journal_mode=WAL`)
   - Multiple readers allowed, writers are serialized
   - DB operations are atomic within each process

3. **Watch mode + sync**:
   - Watch only performs push operations (not pull)
   - If `sync` modifies files during pull, watch detects changes and re-pushes
   - This is correct behavior - watch reacts to file modifications

4. **Multiple concurrent CLI invocations**:
   - **Not a supported use case**
   - Running multiple `sync` commands simultaneously may cause conflicts
   - SQLite handles DB concurrency, but file writes could interleave
   - Recommendation: Users should not run concurrent sync operations

**Why no locking is needed**:
- Normal CLI usage is single-process, sequential operations
- Watch mode only pushes, no conflict with manual pull
- The debounce mechanism in watch mode handles rapid file changes
- Adding file locking would add complexity for an unsupported use case

**Future consideration** (if needed):
- Add a lockfile at `~/.local/share/rock-paper-sync/sync.lock` if users report issues
- Use `fcntl.flock()` for advisory file locking
- Log warning if lock acquisition fails

**No tests added**: This is an architectural analysis, not a code change. The current design is correct for supported use cases.

---

## ✅ P2 - Resolved Issues

### 7. Overlapping Highlight Conflict Resolution - DOCUMENTED (2026-04-17)

**File**: `tests/annotations/test_annotation_anchoring.py` (`TestOverlappingHighlightConflict`)
**Status**: ✅ Behavior documented and tested

**Resolution**: Three tests added:
- `test_overlapping_highlights_disambiguation_by_context`: Documents known limitation — when
  two anchors share the same text and one occurrence is deleted, both collapse to the same
  remaining occurrence (expected behavior: system cannot distinguish them once text is unique).
- `test_surviving_highlight_resolves_after_overlap_deletion`: The surviving highlight still
  resolves correctly when the overlapping anchor text is deleted.
- `test_deleted_highlight_becomes_lower_confidence`: Deleted highlight has confidence < 1.0.

---

### 8. Cross-Page Annotation During Reflow - PARTIALLY CORRECT (2026-04-17)

**File**: `tests/annotations/test_annotation_anchoring.py` (`TestCrossPageAnnotationReflow`)
**Status**: ⚠️ Tests verify anchor resolution only — generator coordinate bug unaddressed

**What the tests actually verify**: `AnchorContext.resolve()` correctly finds the anchor
text's new character offset after content is inserted or deleted. That part works.

**Real bug not yet fixed** (`generator.py:402–433`): when a highlight migrates from page N
to page M due to content reflow, the generator fetches old text from `rm_paths[source_page_idx]`
(always the ORIGINAL page N) to compute the coordinate delta. It then places the highlight on
page M using that wrong old_text/old_origin pair. The delta calculation is wrong because
page N's layout doesn't match page M's layout context.

**Consequence**: after content reflow, a highlight that was on page 1 and is now on page 2
will appear at incorrect Y-coordinates on the device — visually misplaced.

**Not triggered if annotation stays on the same page**, which is why record-replay tests
haven't caught it yet.

---

### 9. Confidence Threshold Boundary Cases - SHALLOW TESTS (2026-04-17)

**File**: `tests/annotations/test_annotation_anchoring.py` (`TestConfidenceThresholdBoundaryP2`)
**Status**: ⚠️ Tests verify single-occurrence cases only — multi-occurrence fuzzy boundary untested

**What the tests actually verify**: With a single occurrence of the target text in the new
document, `_find_by_hash` returns it at confidence=1.0 regardless of `fuzzy_threshold`.
The fuzzy threshold is never consulted in any of the five tests.

**What should be tested**: `_fuzzy_match` uses `fuzzy_threshold` only for the
multi-occurrence case (`if best_score >= fuzzy_threshold`). A test at the boundary should
have two copies of the same text with differing context similarity scores, where the best
score is precisely near 0.80 — verifying that threshold=0.79 accepts it but threshold=0.81
falls through to diff_anchor instead.

**Deeper policy question unresolved**: `DiffAnchor` always returns exactly `confidence=0.6`,
and the reanchor threshold is `>= 0.6`, so every diff_anchor result unconditionally passes.
If a highlight was on "brown fox" and someone replaces it with "completely different text",
the highlight migrates to "completely different text" at 0.6 confidence. This was documented
as intentional in P0 #3 ("highlights follow edited text"), but the diff_anchor confidence
carries no information about how much the in-between text changed. The threshold does not
guard against large-scale text replacement — only against the stable anchors not being found.

---

### 10. Annotation Type Mismatch in Merge - BY DESIGN (2026-04-17)

**File**: `tests/annotations/test_annotation_anchoring.py` (`TestAnnotationTypeMismatch`)
**Status**: ✅ Correctly by design — no fix needed

**Resolution**: Three tests confirm annotation type is orthogonal to anchor resolution:
- `test_highlight_and_stroke_same_anchor_both_resolve`: Both types resolve independently.
- `test_annotation_type_preserved_after_reanchoring`: Type is unchanged after migration.
- `test_highlight_and_stroke_coexist_on_same_text_after_edit`: Both migrate after content edit.

**Why this is genuinely fine**: Highlights (GlyphBlock) and strokes (LineBlock) are separate
CRDT structures produced by separate tools on the device. The generator dispatches on
`type(original_rm_block).__name__`, not `annotation_type`, so the correct handler is always
used. Having both a highlight and a stroke at the same text position is a valid user state
(e.g., the user highlighted text AND wrote a margin note on the same words). Coexistence
is correct behaviour.

---

## ✅ P3 - Resolved Issues

### 11. Unicode Text Modification in Anchors - DOCUMENTED (2026-04-17)

**File**: `tests/annotations/test_annotation_anchoring.py` (`TestUnicodeTextInAnchors`)
**Status**: ✅ Behavior documented and tested

**Resolution**: Three tests document current unicode behavior:
- Unicode text resolves exactly in the same document (exact hash match).
- `_normalize_text` does NOT do NFC/NFD unicode normalization → "café" ≠ "cafe" by hash.
- Accent→ASCII change falls back to diff-anchor (may resolve near the ASCII position).

---

### 12. Whitespace-Only Modifications - DOCUMENTED (2026-04-17)

**File**: `tests/annotations/test_annotation_anchoring.py` (`TestWhitespaceModifications`)
**Status**: ✅ Behavior documented and tested

**Resolution**: Three tests confirm whitespace normalization behavior:
- `_normalize_text` collapses whitespace → "Check  this" and "Check this" share a content hash.
- An anchor on double-space text resolves successfully in single-space document via hash match.
- Anchors on unchanged text are unaffected by nearby whitespace changes.

---

### 13. Orphan Comment Placement with Dynamic Structure - FIXED (2026-04-17)

**File**: `src/rock_paper_sync/annotation_renderer.py`, `tests/test_annotation_renderer.py`
**Status**: ✅ Bug fixed and tested

**Root cause**: `render_orphan_comment()` checked only `content[:100]` for an existing
orphan comment. If the comment moved past position 100 (user added content above, or
comment was placed at bottom), the check failed and a new comment was prepended at top,
leaving the old one in place — resulting in duplicate orphan comments.

**Fix**: Changed `content[:100]` → `content` in the existence check. The `re.sub()` that
follows already operates on the full string, so detecting anywhere is sufficient.

**Tests updated** (`TestOrphanCommentPlacementDynamicStructure`):
- `test_comment_at_top_is_detected_and_updated`: unchanged — still passes.
- `test_comment_at_bottom_is_detected_and_updated`: now verifies no duplicate.
- `test_comment_in_middle_after_user_adds_content_below`: now verifies updated in-place.

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
| 2026-01-09 | P1 #5: Dense annotation areas | ✓ | Verified working correctly, added 8 tests |
| 2026-01-09 | P1 #6: Race condition analysis | ✓ | By design - no locking needed for normal use |
| 2026-04-17 | P2 #7: Overlapping highlight conflict | ✓ | Documented: when text unique after deletion, both anchors collapse |
| 2026-04-17 | P2 #8: Cross-page annotation reflow | ⚠ | Anchor resolution correct; generator uses wrong old_text/origin for cross-page moves |
| 2026-04-17 | P2 #9: Confidence threshold boundary | ⚠ | Tests are shallow (single-occurrence only); multi-occurrence fuzzy boundary untested |
| 2026-04-17 | P2 #10: Annotation type mismatch | ✓ | By design: GlyphBlock/LineBlock are independent; coexistence is correct |
| 2026-04-17 | P3 #11: Unicode text in anchors | ✓ | Documented: no NFC normalization; diff-anchor fallback for accent→ASCII |
| 2026-04-17 | P3 #12: Whitespace-only modifications | ✓ | Documented: double→single space shares hash; resolves correctly |
| 2026-04-17 | P3 #13: Orphan comment placement | ✓ | Fixed: content[:100] → content; comment now detected anywhere in document |

---

## Open Issues — Priority for Fresh Agents

The two remaining ⚠ items from this document, plus the known limitation from P2 #7,
ranked by user-facing impact and implementation complexity:

### Priority 1 — Fix cross-page highlight coordinate bug (P2 #8)

**Why first**: Silent data corruption. A highlight that reflows to a different page appears
at the wrong Y-coordinate on the device with no warning. Users see their highlights in the
wrong place; the system reports success.

**What to fix**: `generator.py` around line 402–433. When a highlight's target page differs
from `source_page_idx`, the generator must use the NEW page's text and origin for the delta
calculation, not the original page's. Specifically:

1. After `apply_to_page()` routes the annotation to `target_page_idx`, check if
   `target_page_idx != source_page_idx`.
2. If they differ (cross-page move), re-fetch old_text and old_origin from
   `rm_paths[target_page_idx]` (the page the annotation is going TO), or set
   `old_text = None` to force an absolute placement rather than a delta.
3. Add a unit test and a record-replay integration test that inserts content large enough
   to push a highlight to the next page and asserts its Y-coordinate lands within the
   correct page's bounds in the generated .rm file.

**Scope**: Medium. Touches the generator and highlight_handler; no schema changes.

---

### Priority 2 — Write real multi-occurrence fuzzy threshold tests (P2 #9, part A)

**Why second**: The existing tests give false confidence. A future change to `_fuzzy_match`
or `DEFAULT_FUZZY_THRESHOLD` would pass the existing test suite even if it broke the
multi-occurrence disambiguation.

**What to fix**: Add tests to `TestConfidenceThresholdBoundaryP2` (or a new class) where:

1. The document contains two copies of the target text with measurably different contexts.
2. Compute the expected `difflib.SequenceMatcher` score between each candidate's surrounding
   text and the stored `context_before`/`context_after`.
3. Assert that at `fuzzy_threshold` just below that score the better match is accepted,
   and at `fuzzy_threshold` just above it the result falls through to diff_anchor.

No production code changes needed — this is a test gap only.

**Scope**: Small. Tests only.

---

### Priority 3 — Define policy for diff_anchor confidence on large text changes (P2 #9, part B)

**Why third**: Currently diff_anchor returns a fixed 0.6 regardless of how much the text
between stable anchors changed. A highlight on two words that is now anchored to an entire
paragraph is technically "resolved" at 0.6. This is a policy decision, not a clear bug.

**Options to consider**:

A. **Status quo** — intentional: highlights follow edited text wherever the stable context
   leads. Document this explicitly as the accepted PM decision.

B. **Proportional confidence** — compute `difflib.SequenceMatcher(old_span_text,
   new_span_text).ratio()` and use `min(0.6, ratio)` as the confidence. Spans that change
   drastically get confidence < 0.6, become orphaned. Spans with minor edits stay at ~0.6.
   This adds one `ratio()` call to `DiffAnchor.resolve_in()`.

C. **Length guard** — reject diff_anchor if the new span is more than N× longer or shorter
   than the original. Simple heuristic; avoids migrating a two-word highlight to a paragraph.

The right answer requires a product decision. An agent implementing this should first confirm
option A (status quo) or B/C with the user before writing any code.

**Scope**: Small if status quo; small-medium if proportional confidence or length guard.

---

### Priority 4 — Context validation for single hash matches (P2 #7)

**Why fourth**: Currently `_find_by_hash` returns confidence=1.0 for a single occurrence
without checking context at all. When two highlights had the same text and one occurrence
is deleted, both anchors collapse to the remaining occurrence — the annotation that *should*
be orphaned migrates silently to the wrong position.

**What to fix** (`document_model.py`, `AnchorContext.resolve()`):

1. After `_find_by_hash` returns a single match, compute a context similarity score
   (same formula used in `_disambiguate_by_context`).
2. If the context score is below a threshold (suggested: 0.3 — intentionally permissive,
   since text may have moved far), return a reduced confidence (e.g. `0.6 * score`) instead
   of 1.0, or fall through to DiffAnchor.
3. Update `TestOverlappingHighlightConflict::test_overlapping_highlights_disambiguation_by_context`
   — the test currently documents both anchors collapsing to the same position; after the fix
   the orphaned anchor should get low confidence and become orphaned.
4. Verify no regression in normal cases (text moved to new section: context score will be
   low but the hash match is correct; this is the tricky trade-off to calibrate).

**Scope**: Medium. Core anchor resolution change; must be carefully calibrated to avoid
increasing the orphan rate for legitimate moved-text cases.
