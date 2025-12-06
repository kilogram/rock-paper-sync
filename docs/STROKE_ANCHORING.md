# Stroke Anchoring in reMarkable .rm Files

This document describes how handwritten stroke annotations are anchored to text in reMarkable v6 .rm files, and the implications for re-anchoring strokes when document text is modified.

## Overview

When a user draws strokes (handwritten notes, annotations) near text in a reMarkable document, the device creates a spatial relationship between the strokes and the text. This relationship is encoded through **TreeNodeBlocks** which define anchor points in the text content.

Understanding this anchoring mechanism is critical for preserving stroke positions when document text is modified externally (e.g., syncing updated markdown content).

## TreeNodeBlock Structure

Each stroke belongs to a parent layer defined by a `TreeNodeBlock`. The TreeNodeBlock contains:

```
TreeNodeBlock
├── group.node_id        # CrdtId identifying this layer
├── group.anchor_id      # LwwValue<CrdtId> - text anchor reference
│   └── value.part2      # Character offset into RootTextBlock text
├── group.anchor_origin_x # LwwValue<float> - X offset from anchor position
└── ... other fields
```

### anchor_id.part2 - Character Offset

The `anchor_id.value.part2` field is a **character offset** (0-indexed) into the document's text content (stored in `RootTextBlock`).

Example:
```
Text: "Hello world. This is a test document."
       0123456789...

anchor_id.part2 = 13  → Points to 'T' in "This"
```

The anchor position determines the **baseline Y coordinate** for strokes in this layer. The Y position is computed by:
1. Using the layout engine to find which line contains character N
2. Computing Y = TEXT_POS_Y + (line_number × LINE_HEIGHT)

### anchor_origin_x - X Offset

The `anchor_origin_x` field is an X offset from the text position at the anchor point.

- **Negative values** (e.g., -56.4): Stroke is to the LEFT of text (left margin note)
- **Positive values** (e.g., 131.9): Stroke is to the RIGHT of text origin

The absolute X position of a stroke is: `anchor_origin_x + stroke.native_x`

### Special Value: End of Document

The value `281474976710655` (0xFFFFFFFFFFFF) in `anchor_id.part2` indicates an anchor at the **end of the document**. This is used for strokes drawn below all text content ("implicit paragraphs").

## Stroke Coordinate Spaces

Strokes have **native coordinates** relative to their parent layer's anchor:

```
Absolute Position = Anchor Position + Native Position

absolute_x = anchor_origin_x + native_x
absolute_y = anchor_y + native_y

where anchor_y = layout_engine.offset_to_position(anchor_id.part2).y
```

## The Anchor Drift Problem

When document text is modified (characters inserted or deleted), anchor_id character offsets become **stale**:

### Example: Inserting Text

**Before (Phase 2):**
```
Text: "First paragraph. Second paragraph with annotation."
                       ^
                       anchor_id.part2 = 17
                       Points to 'S' in "Second"
```

**After inserting "NEW TEXT. " at position 0 (Phase 3):**
```
Text: "NEW TEXT. First paragraph. Second paragraph with annotation."
                  ^
                  anchor_id.part2 = 17 (UNCHANGED!)
                  Now points to 'i' in "First" - WRONG TEXT!
```

The anchor now points to completely different text. The stroke appears anchored to "First paragraph" instead of "Second paragraph".

### Real-World Case Study

From our test case (`stroke_reanchor`), stroke 29 exhibited this exact problem:

| Phase | anchor_id.part2 | Text at offset | Actual content |
|-------|-----------------|----------------|----------------|
| Phase 2 (original) | 106 | Character 106 | "...content **i**s inserted above..." |
| Phase 3 (our output) | 106 (stale) | Character 106 | "...next to TH**I**S paragraph..." |
| Phase 4 (golden) | 166 | Character 166 | "...content **i**s inserted above..." |

The inserted text was 60 characters, and 106 + 60 = 166. The device correctly updated the anchor; our code did not.

## Device Behavior vs Our Original Approach

### Device Behavior (Correct)

When text is modified on the device, it:
1. **Updates TreeNodeBlock anchor_ids** to track the original text content
2. Strokes automatically remain near their intended text
3. May create new TreeNodeBlocks with new CrdtIds

### Our Original Approach (Incorrect)

We attempted to compensate for text changes by:
1. **Preserving** TreeNodeBlocks with stale anchor_ids
2. Computing Y deltas based on paragraph movement
3. Applying Y deltas to stroke native coordinates

**Why this failed:**
- The anchor Y position is computed from the (stale) anchor_id
- When the file is read, the anchor points to wrong text
- Our Y delta cannot fully compensate for the anchor drift
- X position was not adjusted at all

### The Math

Let's trace through stroke 29:

**Original (Phase 2):**
- anchor_y = 208.0 (from char 106 in old layout)
- native_y = 14.1
- absolute_y = 208.0 + 14.1 = 222.1

**Our output (Phase 3):**
- anchor_y = 265.0 (char 106 now maps to different Y due to text change!)
- native_y = 14.1 + 114.0 (we added Y delta) = 128.1
- absolute_y = 265.0 + 128.1 = 393.1

**Golden (Phase 4):**
- anchor_y = 436.0 (from char 166 in new layout)
- native_y = -36.1
- absolute_y = 436.0 + (-36.1) = 399.9

We got 393.1 instead of 399.9 - close but not exact. And the X position was completely wrong (-66 vs 133).

## Correct Approach: Update anchor_ids

The fix is to **update TreeNodeBlock anchor_ids** when text changes:

### Algorithm

1. Before modifying text, record each TreeNodeBlock's anchor_id and the text content at that offset
2. After modifying text, find where that text content now exists (using content matching or offset delta)
3. Update each TreeNodeBlock's anchor_id.part2 to the new offset
4. Leave stroke native coordinates unchanged

### Simple Case: Pure Insertion

If text is only inserted (no deletions or modifications):
```python
def update_anchor_ids(tree_node_blocks, insertion_offset, inserted_length):
    for block in tree_node_blocks:
        if block.group.anchor_id.value.part2 >= insertion_offset:
            block.group.anchor_id.value.part2 += inserted_length
```

### General Case: Content Matching

For arbitrary text changes, use content-based matching:
1. Extract a snippet of text around each anchor point in the old document
2. Find that snippet in the new document
3. Update anchor_id to the new offset

This is similar to how we handle highlight re-anchoring.

## anchor_origin_x Considerations

The `anchor_origin_x` value encodes the stroke's horizontal position relative to the text:

- Left margin notes: anchor_origin_x ≈ -50 to -100
- Inline annotations: anchor_origin_x ≈ 0
- Right margin notes: anchor_origin_x ≈ +100 to +200

When text reflows significantly (e.g., a paragraph moves from the beginning to the end of a line), the anchor_origin_x may need adjustment. This is an area for future investigation.

## Implementation

### Solution: Update anchor_ids in Round-Trip Generation

The fix is implemented in `_generate_rm_file_roundtrip()` which modifies existing .rm files:

1. **Extract old text** from the original RootTextBlock
2. **Compute offset delta** between old and new text using content matching
3. **Update TreeNodeBlock anchor_ids** by adding the delta to each anchor_id.part2
4. **Preserve stroke native coordinates** unchanged

```python
# In generator.py: _generate_rm_file_roundtrip()

# Compute anchor offset delta for TreeNodeBlock anchor_id updates
anchor_offset_delta = self._compute_anchor_offset_delta(old_text, combined_text)

# Update TreeNodeBlock anchor_ids to track text content
elif block_type == "TreeNodeBlock":
    if anchor_offset_delta != 0:
        modified_block = self._update_tree_node_anchor(block, anchor_offset_delta)
        modified_blocks.append(modified_block)
```

### Key Insight: Don't Apply Y Deltas to Strokes

The critical insight is that we do NOT need to modify stroke native coordinates when text changes. By updating the TreeNodeBlock anchor_ids:

- The anchor Y position automatically points to the correct line
- Stroke native_y remains unchanged
- absolute_y = (new anchor_y) + native_y = correct position

Applying Y deltas to native coordinates would double-count the movement.

### LwwValue Immutability

rmscene uses frozen dataclasses. To modify TreeNodeBlock anchor_ids:

```python
from dataclasses import replace

# Create new objects with modified values
new_anchor_value = CrdtId(old_anchor.value.part1, new_offset)
new_anchor_lww = LwwValue(timestamp=old_anchor.timestamp, value=new_anchor_value)
new_group = replace(block.group, anchor_id=new_anchor_lww)
new_block = replace(block, group=new_group)
```

## Implementation Status

### Completed

- [x] `ParentAnchorResolver` class extracts per-parent anchor positions
- [x] Stroke clustering uses correct absolute coordinates
- [x] TreeNodeBlock anchor_id update on text change
- [x] Offset delta calculation via content matching

### Future Work

- [ ] Content-based anchor matching for complex edits (deletions, reordering)
- [ ] anchor_origin_x adjustment for significant reflow cases

### Files

- `src/rock_paper_sync/coordinate_transformer.py` - ParentAnchorResolver, anchor extraction
- `src/rock_paper_sync/generator.py` - TreeNodeBlock anchor_id updates in `_generate_rm_file_roundtrip()`
- `tools/analysis/stroke_visualizer.py` - Debugging tool for stroke positions

## Open Questions

1. **How is anchor_origin_x computed?** Is it the X position of the character within its line, or derived from stroke position at creation time?

2. **Handling deleted text?** If the text a stroke was anchored to is deleted, where should the stroke go?

3. **Multi-line strokes?** If a stroke spans multiple lines (e.g., a bracket), how is it anchored?

## References

- `docs/RMSCENE_FINDINGS.md` - Layout engine calibration, line height discovery
- `src/rock_paper_sync/annotations/docs/STROKES.md` - Stroke coordinate transformation
- rmscene library source code for TreeNodeBlock structure
