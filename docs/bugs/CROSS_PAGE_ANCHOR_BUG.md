# Cross-Page TreeNodeBlock Anchor Bug

**Status: FIXED** (2025-12-07)

## Summary

When strokes move from one page to another during sync, their TreeNodeBlock anchors are incorrectly calculated, causing the device to silently drop the strokes.

## Symptoms

Device logs show errors like:
```
anchor=1:809 for group=2:772 is not present in text
anchor=1:806 for group=2:763 is not present in text
```

Strokes that were visible before sync disappear after sync. The strokes are present in the .rm files but the device refuses to render them.

## Root Cause

When a stroke moves cross-page, its TreeNodeBlock anchor should be recalculated as an **absolute offset** into the target page's text. Instead, the anchor appears to be calculated using a **delta** from the source page.

### Observed Behavior

**Input (source .rm file):**
- Source page text length: 746 chars
- Two strokes on the same page:
  - `CrdtId(2, 763)`: anchor=635
  - `CrdtId(2, 772)`: anchor=638

**Output (after sync):**
- Strokes moved to different pages:
  - Page 1 (746 chars): `CrdtId(2, 763)`: anchor=600 ✓
  - Page 2 (947 chars): `CrdtId(2, 772)`: anchor=839 ✗

### The Math That Reveals the Bug

For the stroke that moved to Page 2:
- Original anchor: 638
- Output anchor: 839
- Delta: 839 - 638 = **201**

Page text length difference:
- New page text length: 947
- Old page text length: 746
- Difference: 947 - 746 = **201**

The output anchor equals `original_anchor + (new_page_text_len - old_page_text_len)`.

This is wrong. The anchor should point to a meaningful character position in the target page's text, not be shifted by an arbitrary delta.

## Why Validation Misses This

The anchor 839 passes bounds checking (839 < 947), so naive validation that only checks `anchor <= page_text_len` reports it as "VALID".

However, the anchor is **semantically invalid** - it points to the wrong text content. The device performs deeper validation and rejects the stroke.

## Code Location

The bug is in `src/rock_paper_sync/generator.py`:

1. **Cross-page stroke detection** (lines ~700-850): `_preserve_annotations` calculates `target_char_offset` for strokes moving to different pages

2. **TreeNodeBlock reanchoring** (lines ~1368-1418): `_reanchor_tree_node_for_cross_page` creates a new TreeNodeBlock with the calculated anchor

3. **Cross-page injection** (lines ~1886-1892): The reanchored TreeNodeBlocks are injected into the target page

The `target_char_offset` calculation appears to be incorrect when strokes move cross-page.

## Expected Behavior

When a stroke moves from Page A to Page B:

1. Identify which text block on Page B corresponds to the stroke's target location (by Y-position or content matching)
2. Calculate the character offset to that text block within Page B's RootTextBlock
3. Use that absolute offset as the new anchor

The anchor should represent: "this stroke is anchored to character position X in this page's text" - a page-local value, not derived from the source page.

## Reproduction

1. Create a document with strokes on a later page
2. Modify the markdown to add text that causes pagination to shift
3. Run sync with the original .rm files
4. Observe that strokes on pages that shifted have incorrect anchors
5. Upload to device - strokes disappear

## Test Data

The bug can be reproduced with the multi-trip test data:
- Input: `tests/record_replay/testdata/multi_trip/phases/phase_2_phase_2/rm_files/`
- Markdown: `tests/record_replay/testdata/multi_trip/phases/phase_2_phase_2/vault_snapshot/document.md`

## Fix Strategy

1. **Add semantic anchor validation**: Check that anchor points to expected text content, not just bounds
2. **Fix `target_char_offset` calculation**: Ensure it computes a true page-local offset
3. **Add tracing**: Instrument anchor calculations to enable debugging
4. **Add regression test**: Verify anchors are semantically correct after cross-page moves

## Solution Implemented (Phase 1: Cross-Page Ordering)

The first root cause was that `.rm` files were being passed to `_preserve_annotations()` in UUID-sorted order, but the code assumed they were in NEW document page order. When document content reorganizes across pages, these orders differ.

### Phase 1 Fix

Added `_match_rm_files_to_pages()` method in `generator.py` that:

1. Extracts text content from each `.rm` file
2. Extracts text content from each new page
3. Matches each new page to the best-matching `.rm` file using Jaccard word similarity
4. Returns a reordered list where `matched_rm_files[i]` corresponds to `pages[i]`

This ensures that when processing page N, we use the `.rm` file whose content matches page N, regardless of filename order.

## Additional Bug Discovered (Phase 2: Same-Page Text Changes)

**Status: FIXED** (2025-12-08)

### Symptoms

Device logs show the same anchor errors, but for strokes that stayed on the SAME page where text content changed:
```
anchor=1:845 for group=2:962 is not present in text
anchor=1:842 for group=2:947 is not present in text
anchor=1:836 for group=2:929 is not present in text
```

Page text length: 791 chars, but anchors reference 836, 842, 845 (all > 791).

### Root Cause

The `_calculate_tree_node_offset()` function calculated character offsets using **cumulative sums** of filtered TextBlock content. However:

1. Empty paragraphs are skipped during TextBlock extraction (`if paragraph.strip()`)
2. The RootTextBlock in .rm files contains the FULL page text including empty paragraphs
3. TreeNodeBlock anchors reference positions in the FULL RootTextBlock text
4. Cumulative calculation from filtered blocks produces wrong offsets

Additionally, same-page strokes weren't being reanchored at all - only cross-page strokes triggered the TreeNodeBlock recalculation.

### Phase 2 Fix

1. **Added `char_start`/`char_end` fields to `TextBlock`** (`core_types.py`):
   - Track actual character positions in the full page text
   - Set during extraction from both old .rm files and new page generation

2. **Updated offset extraction** (`preserver.py`, `generator.py`):
   - `_extract_old_page_data()`: Store actual offsets when creating TextBlocks
   - `blocks_to_text_items()`: Store actual offsets when generating pages
   - `_extract_text_blocks_from_rm()`: Store actual offsets when reading .rm files

3. **Updated anchor calculation** (`preserver.py`):
   - `_calculate_tree_node_offset()`: Use `char_start`/`char_end` instead of cumulative sums
   - `_route_single_annotation()`: Use actual offsets for anchor-based routing

4. **Added same-page reanchoring** (`preserver.py`):
   - Detect when page text length changes (even if stroke stays on same page)
   - Recalculate TreeNodeBlock anchor for same-page strokes
   - Add reanchored TreeNodeBlocks to context for reinsertion

5. **Added anchor validation to test harness** (`tests/record_replay/harness/offline.py`):
   - `_validate_rm_anchors()`: Validates anchors during test replay
   - Catches anchor errors before device upload
   - Uses `tools/rmlib/validator.py` for validation

6. **Added sentinel anchor detection** (`tools/rmlib/validator.py`):
   - Skip validation for `END_OF_DOC_ANCHOR_MARKER` (0xFFFFFFFFFFFF)
   - These are intentionally large anchors for margin notes

### Key Changes (Phase 2)

- `src/rock_paper_sync/annotations/core_types.py`:
  - Added `char_start: int | None` and `char_end: int | None` to `TextBlock`

- `src/rock_paper_sync/annotations/preserver.py`:
  - `_extract_old_page_data()`: Set char_start/char_end on TextBlocks
  - `_route_single_annotation()`: Use actual offsets for routing decisions
  - `_calculate_tree_node_offset()`: Use char_start/char_end instead of cumulative sums
  - `_process_routing_decision()`: Handle same-page reanchoring

- `src/rock_paper_sync/generator.py`:
  - `blocks_to_text_items()`: Set char_start/char_end on TextBlocks
  - `_extract_text_blocks_from_rm()`: Set char_start/char_end on TextBlocks

- `tools/rmlib/validator.py`:
  - Added `END_OF_DOC_ANCHOR_MARKER` constant
  - Skip sentinel anchors in validation

- `tests/record_replay/harness/offline.py`:
  - Added `_validate_rm_anchors()` method
  - Called after sync to catch anchor errors during replay

### Test Coverage

- `tests/annotations/test_tree_node_anchor_validity.py`: 4 tests validating anchor correctness
- `tests/record_replay/test_cross_page_reanchor.py`: End-to-end test with golden comparison

### Architectural Insight

The core issue was **mixing coordinate systems**:
- TreeNodeBlock anchors use positions in the **full RootTextBlock text** (includes all whitespace)
- TextBlock extraction **filters** empty paragraphs
- Cumulative offset calculations assumed filtered blocks mapped directly to full text positions

The fix establishes a single source of truth: each TextBlock now knows its **exact** position in the full page text via `char_start`/`char_end`, eliminating the need for cumulative calculations that can drift.
