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

## Solution Implemented

The root cause was that `.rm` files were being passed to `_preserve_annotations()` in UUID-sorted order, but the code assumed they were in NEW document page order. When document content reorganizes across pages, these orders differ.

### The Fix

Added `_match_rm_files_to_pages()` method in `generator.py` that:

1. Extracts text content from each `.rm` file
2. Extracts text content from each new page
3. Matches each new page to the best-matching `.rm` file using Jaccard word similarity
4. Returns a reordered list where `matched_rm_files[i]` corresponds to `pages[i]`

This ensures that when processing page N, we use the `.rm` file whose content matches page N, regardless of filename order.

### Key Changes

- `src/rock_paper_sync/generator.py`:
  - Added `_match_rm_files_to_pages()` method for content-based page matching
  - Modified `generate_document()` to call `_match_rm_files_to_pages()` before annotation preservation

- `src/rock_paper_sync/annotations/document_anchors.py` (new):
  - Document-level anchoring data types for future enhancements

### Test Coverage

- `tests/annotations/test_tree_node_anchor_validity.py`: 4 tests validating anchor correctness
