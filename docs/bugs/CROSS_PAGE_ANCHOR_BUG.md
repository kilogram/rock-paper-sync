# Cross-Page TreeNodeBlock Anchor Bug

**Status: FIXED** (Phase 1-4 complete)

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

## Future Architecture: AnchorContext

The Phase 1 and Phase 2 fixes address immediate bugs but don't solve the fundamental fragility of character offset anchoring. A more robust architecture has been designed:

**See**: `docs/ANNOTATION_ARCHITECTURE_V2.md`

Key concepts:
- **AnchorContext**: Multi-signal stable identifier (text + context + structure + spatial)
- **DiffAnchor**: Anchor relative to unchanged text (survives edits)
- **DocumentModel**: Document-level abstraction with pages as projections
- **ContextResolver**: Unified resolution using preserved `HeuristicTextAnchor`

This architecture will eliminate the class of bugs where coordinate systems are mixed, by making anchoring explicit and multi-layered rather than relying on fragile character offsets.

## Phase 3: Scene Tree Structure Missing (2025-12-21)

**Status: FIXED**

### Symptoms

Device logs show errors when loading a document with cross-page strokes:
```
Dec 16 01:25:37 rm.scene.tree  Unable to find node with id=0:11, but it should be present
Dec 16 01:25:37 rm.crdt.sequence  - left not found 2:957 - insert at beginning
```

Strokes that moved cross-page disappear on the device, even though:
- TreeNodeBlocks are present and correctly anchored
- SceneLineItemBlocks (strokes) are present and reference the correct parent
- All blocks pass rmscene serialization

### Root Cause (Confirmed)

The CRDT scene graph requires **three blocks** for each stroke group, not two:

```
SceneTreeBlock (declares node in scene tree)
  - tree_id: CrdtId(2, 956)       # The TreeNodeBlock being declared
  - node_id: CrdtId(0, 0)         # Update marker
  - is_update: True
  - parent_id: CrdtId(0, 11)      # Layer 1

TreeNodeBlock (2:956)
  - node_id: CrdtId(2, 956)
  - anchor_id: CrdtId(1, 845)     # Text position

SceneGroupItemBlock (links to layer's sequence)
  - parent_id: CrdtId(0, 11)      # Layer 1
  - item_id: CrdtId(2, 957)       # This item's ID
  - left_id: CrdtId(0, 0)         # CRDT sequence predecessor (reset for cross-page)
  - right_id: CrdtId(0, 0)        # CRDT sequence successor (reset for cross-page)
  - value: CrdtId(2, 956)         # Points to TreeNodeBlock

SceneLineItemBlock (stroke data)
  - parent_id: CrdtId(2, 956)     # References TreeNodeBlock
```

When we moved a stroke cross-page, we were:
1. ✅ Copying the TreeNodeBlock (with reanchored anchor_id)
2. ✅ Copying the SceneLineItemBlock (stroke data)
3. ✅ Copying the SceneGroupItemBlock (links node to layer)
4. ❌ **NOT copying the SceneTreeBlock** that declares the node in the scene tree

Without the SceneTreeBlock, the device can't find the TreeNodeBlock in the scene tree hierarchy, so it logs "Unable to find node with id=0:11" and fails to render the strokes.

### Discovery Process

1. **Device logs** showed "Unable to find node with id=0:11" - this is Layer 1, not a stroke node
2. **rm_inspector tool** was enhanced with `--mode scene-graph` to debug block structure
3. **Analysis** of working device-native files revealed the SceneTreeBlock pattern:
   ```
   [5] SceneTreeBlock: tree_id=CrdtId(2, 929), parent_id=CrdtId(0, 11)
   [13] TreeNodeBlock: node_id=CrdtId(2, 929)
   [19] SceneGroupItemBlock: parent_id=CrdtId(0, 11), value=CrdtId(2, 929)
   ```

### Fix Implementation

1. **OldPageData** (`preserver.py`):
   - Added `scene_tree_blocks_by_tree_id: dict[Any, Any]` field
   - Extract SceneTreeBlocks during page data extraction

2. **RoutingDecision** (`preserver.py`):
   - Added `scene_tree_block: Any | None` field
   - Extract scene_tree_block in `_route_single_annotation()`

3. **Extraction** (`preserver.py`):
   - Both `extract_old_page_data()` and `_extract_page_data_internal()` now extract SceneTreeBlocks
   - Build `scene_tree_blocks_by_tree_id` mapping keyed by `tree_id`

4. **Context Building** (`preserver.py`):
   - 4-tuple structure: `(TreeNodeBlock, offset, SceneGroupItemBlock, SceneTreeBlock)`
   - Pass all four elements through the routing pipeline

5. **Injection** (`generator.py`):
   - Create NEW SceneTreeBlock for each cross-page TreeNodeBlock:
     ```python
     new_scene_tree_block = SceneTreeBlock(
         tree_id=node_id,
         node_id=CrdtId(0, 0),
         is_update=True,
         parent_id=CrdtId(0, 11),  # Layer 1
     )
     ```
   - Create NEW SceneGroupItemBlock with reset left_id/right_id:
     ```python
     new_scene_group_item = SceneGroupItemBlock(
         parent_id=CrdtId(0, 11),  # Layer 1
         item=CrdtSequenceItem(
             item_id=scene_group_item.item.item_id,
             left_id=CrdtId(0, 0),   # Reset - no predecessor
             right_id=CrdtId(0, 0),  # Reset - no successor
             deleted_length=0,
             value=node_id,
         ),
     )
     ```
   - Inject in order: TreeNodeBlock → SceneTreeBlock → SceneGroupItemBlock

### Key Files Changed

- `src/rock_paper_sync/annotations/preserver.py`:
  - Added `scene_tree_blocks_by_tree_id` to OldPageData
  - Added `scene_tree_block` to RoutingDecision
  - Extract SceneTreeBlocks in both extraction methods
  - 4-tuple structure for tree_nodes

- `src/rock_paper_sync/generator.py`:
  - Updated tuple unpacking for 4-element structure
  - Inject SceneTreeBlock before SceneGroupItemBlock
  - Create new blocks with proper CRDT IDs

- `tools/analysis/rm_inspector.py`:
  - Added `--mode scene-graph` for debugging scene tree structure
  - Shows block order, parent relationships, and validates parent_id references

### Verification

After the fix:
```
=== Scene Graph Debug ===
[ 4] SceneTreeBlock: tree_id=CrdtId(0, 11), parent_id=CrdtId(0, 1)
[ 5] SceneTreeBlock: tree_id=CrdtId(2, 299), parent_id=CrdtId(0, 11)  # Cross-page stroke
[ 8] TreeNodeBlock: node_id=CrdtId(0, 11) label='Layer 1'
[ 9] TreeNodeBlock: node_id=CrdtId(2, 299) anchor=CrdtId(0, 281474976710655)
[11] SceneGroupItemBlock: parent_id=CrdtId(0, 11), value=CrdtId(2, 299)

✓ All SceneGroupItemBlock parent_ids exist
```

Device logs no longer show "Unable to find node" errors, and strokes render correctly.

### Remaining Issue (Addressed in Phase 3.5)

Strokes were being anchored to wrong text because AnchorContext was computed from Y position instead of the TreeNodeBlock's actual anchor.

---

## Phase 3.5: Anchor Resolution from TreeNodeBlock (2025-12-21)

**Status: FIXED**

### Symptoms

Strokes on the device appeared on the wrong page or at the wrong position, even though the TreeNodeBlock was correctly injected. For example, margin notes near "Write margin note here:" were being anchored to text like "Yet another paragraph" because that's where their Y coordinate mapped to.

### Root Cause

When extracting annotations in `DocumentModel.from_rm_files()`, the `AnchorContext` was computed from the stroke's **Y position** alone:

```python
# OLD: Y-position based anchor (WRONG)
abs_y = text_origin_y + center_y + 60
anchor = AnchorContext.from_y_position(abs_y, page_text, layout_ctx)
```

This ignored the TreeNodeBlock's `anchor_id`, which contains the **actual character offset** where the stroke is anchored. Stroke Y coordinates are RELATIVE to their TreeNodeBlock anchor, not to the page origin.

### Fix

Use the TreeNodeBlock's `anchor_id.part2` (character offset) to create the AnchorContext:

```python
# NEW: Use TreeNodeBlock's actual anchor
if tree_node and hasattr(tree_node, "group") and tree_node.group:
    g = tree_node.group
    if hasattr(g, "anchor_id") and g.anchor_id and g.anchor_id.value:
        anchor_val = g.anchor_id.value
        if anchor_val.part2 != END_OF_DOC_ANCHOR_MARKER:
            anchor_char_offset = anchor_val.part2

            # Create anchor from actual text position
            anchor = AnchorContext.from_text_span(
                page_text,
                anchor_char_offset,
                min(anchor_char_offset + 50, len(page_text)),
            )
```

### Key Changes

**`document_model.py` - `from_rm_files()` (lines ~774-830):**

1. Extract `anchor_char_offset` from TreeNodeBlock's `anchor_id.part2`
2. Skip sentinel anchors (`END_OF_DOC_ANCHOR_MARKER = 281474976710655`)
3. Create AnchorContext from the actual character offset, not Y position
4. Preserve Y hint for page routing fallback

### Remaining Issue (Addressed in Phase 4)

Strokes rendered on the device but their relative X positions were wrong. See Phase 4 below.

---

## Phase 4: Stroke Relative Positioning (2025-12-21)

**Status: FIXED**

### Symptoms

After Phase 3, strokes appeared on the correct page but their **relative X positions were wrong**. Multiple strokes that should appear together were "shredded" - scattered horizontally across the page.

Debug output showed all TreeNodeBlocks getting the same `anchor_id`:
```
TreeNodeBlock node_id=2-929: anchor_id=715, anchor_x=28.73
TreeNodeBlock node_id=2-935: anchor_id=715, anchor_x=105.97
TreeNodeBlock node_id=2-944: anchor_id=715, anchor_x=121.42
TreeNodeBlock node_id=2-950: anchor_id=715, anchor_x=152.33
```

Original anchors were different (837, 842, 843, 845) but after reanchoring they all became 715.

### Root Cause

The reanchoring logic searched for each stroke's anchor TEXT on the target page and set `anchor_id` to where that text was found. Since nearby strokes had overlapping anchor text (50-char spans starting at close offsets), `page_text.find()` returned the **same position** for all of them.

This destroyed the **relative spacing** between strokes:
- Original: 837, 842, 843, 845 (spacing: 5, 1, 2)
- After reanchoring: 715, 715, 715, 715 (spacing: 0, 0, 0)

### Fix: Delta-Based Reanchoring

Instead of setting each TreeNodeBlock to an independently-calculated position, use a **delta approach**:

1. Find where the FIRST TreeNodeBlock's anchor text appears on the target page
2. Calculate `offset_delta = target_position - original_anchor_id`
3. Apply the **same delta** to ALL TreeNodeBlocks

This preserves relative spacing:
- Original: 837, 842, 843, 845
- Delta: 715 - 837 = -122
- Result: 715, 720, 721, 723 (spacing preserved!)

### Key Changes

**`generator.py` - Round-trip generation (lines ~2297-2340):**

```python
# Compute offset_delta from FIRST TreeNodeBlock
offset_delta = 0
if unique_tree_nodes:
    first_tree_node, first_target_offset, _, _ = unique_tree_nodes[0]
    if hasattr(first_tree_node, "group") and first_tree_node.group:
        g = first_tree_node.group
        if hasattr(g, "anchor_id") and g.anchor_id and g.anchor_id.value:
            original_anchor = g.anchor_id.value.part2
            # Only compute delta for non-sentinel anchors
            if original_anchor != END_OF_DOC_ANCHOR_MARKER:
                offset_delta = first_target_offset - original_anchor

# Apply delta to ALL TreeNodeBlocks (preserves relative spacing)
for tree_node, _, scene_group_item, scene_tree_block in unique_tree_nodes:
    reanchored_node = self._update_tree_node_anchor(tree_node, offset_delta)
    modified_blocks.append(reanchored_node)
```

### Verification

After the fix, strokes maintain their relative positions:
```
INJECTING 5 unique TreeNodeBlocks (from 7 total) with offset_delta=-122
```

Device testing confirmed strokes appear correctly positioned relative to each other.

---

## Postmortem: Why This Bug Took So Long to Fix

**Timeline**: This bug spanned multiple debugging sessions across several weeks, with three distinct phases of fixes before strokes finally rendered on the device.

### What Made This Hard

#### 1. Implicit Knowledge About rmscene Block Relationships

The reMarkable v6 format uses a CRDT-based scene graph where strokes require **multiple interdependent blocks**:

```
SceneTreeBlock  →  TreeNodeBlock  →  SceneGroupItemBlock  →  SceneLineItemBlock
   (declares)        (anchors)          (links to layer)       (stroke data)
```

This relationship was never explicitly documented. We discovered it incrementally:
- Phase 1: Realized TreeNodeBlock anchors matter
- Phase 2: Realized SceneGroupItemBlock links nodes to layers
- Phase 3: Realized SceneTreeBlock declares nodes in the tree

Each discovery came from device log analysis and reverse engineering, not from understanding the format upfront.

#### 2. No "Stroke Bundle" Abstraction

The code treated each block type independently:
- `TreeNodeBlock` extracted in one place
- `SceneGroupItemBlock` extracted in another
- `SceneTreeBlock` not extracted at all (until Phase 3)

There was no single abstraction representing "a complete stroke with all its scene graph dependencies." This meant:
- Easy to forget a required block when moving strokes
- No compile-time or test-time enforcement of completeness
- Each phase fixed one missing piece without addressing the structural gap

#### 3. Scattered Extraction/Injection Logic

Block extraction happened in multiple places:
- `document_model.py`: DocumentAnnotation extraction
- `preserver.py`: OldPageData extraction (two methods!)
- `generator.py`: Direct block reading

Block injection also happened in multiple places:
- `generator.py`: Round-trip path (~line 2165)
- `generator.py`: From-scratch path (~line 2305)

This scatter meant:
- Fixes had to be applied in multiple locations
- Easy to miss a code path
- No single source of truth for "how to move a stroke"

#### 4. Incremental Symptom-Driven Fixes

Each phase addressed symptoms rather than root cause:

| Phase | Symptom | Fix | Underlying Issue |
|-------|---------|-----|------------------|
| 1 | Wrong page matching | Reorder .rm files | No document-level model |
| 2 | Invalid anchors | Track char offsets | Mixed coordinate systems |
| 3 | Missing nodes | Inject SceneTreeBlock | No stroke bundle abstraction |

The fixes were correct but didn't address why these bugs kept appearing.

#### 5. Testing at the Wrong Abstraction Level

Tests validated:
- ✅ Annotation counts preserved
- ✅ Highlights detected by text content
- ❌ Scene graph structure validity
- ❌ Block relationship integrity

The test harness couldn't catch "SceneTreeBlock missing" because it counted annotations, not scene graph nodes. We only caught this via device logs.

#### 6. Reverse-Engineering Overhead

Each debugging session required:
1. Sync to real device
2. Wait for xochitl to process
3. SSH to fetch logs
4. Parse cryptic error messages ("Unable to find node with id=0:11")
5. Correlate with generated .rm files
6. Hypothesis → code change → repeat

The `device_iteration.py` tool helped, but the feedback loop was still slow compared to unit tests.

### Structural Gaps in Current Architecture

#### Gap 1: No Schema for "Valid Injectable Stroke"

**Current state**: Code implicitly assumes various blocks are needed, discovered through trial and error.

**Needed**: Explicit type/schema defining what constitutes a complete stroke:
```python
@dataclass
class StrokeBundle:
    """All blocks required to render a stroke on the device."""
    scene_tree_block: SceneTreeBlock      # Declares node in tree
    tree_node_block: TreeNodeBlock        # Anchors to text
    scene_group_item: SceneGroupItemBlock # Links to layer
    line_items: list[SceneLineItemBlock]  # Actual strokes
```

#### Gap 2: No Single Extraction/Injection Point

**Current state**: Extract in preserver.py, inject in generator.py, with multiple code paths.

**Needed**: Single abstraction that handles both:
```python
class StrokeMigrator:
    def extract(rm_file: Path) -> list[StrokeBundle]: ...
    def inject(bundles: list[StrokeBundle], target_blocks: list) -> list: ...
```

#### Gap 3: No Scene Graph Validation

**Current state**: rmscene serializes anything, device silently fails.

**Needed**: Validation layer that checks:
- Every SceneGroupItemBlock.value has a corresponding TreeNodeBlock
- Every TreeNodeBlock has a corresponding SceneTreeBlock
- All parent_id references resolve to existing nodes
- Anchor offsets are within text bounds

#### Gap 4: No Document-Level Model

**Current state**: Process page-by-page, reconstruct relationships ad-hoc.

**Needed**: Document model that understands:
- Which strokes belong to which text ranges
- How strokes should move when text moves
- Complete dependency graph for each annotation

### Recommendations

1. **Create StrokeBundle abstraction** that encapsulates all required blocks
2. **Centralize extraction/injection** in a single module
3. **Add scene graph validation** to test harness and pre-sync checks
4. **Document rmscene block relationships** in RMSCENE_FINDINGS.md
5. **Add structural tests** that validate scene graph integrity, not just counts

### Lessons Learned

1. **Understand the format before writing code**: Time spent reverse-engineering upfront saves debugging time later
2. **Create abstractions for related concepts**: If blocks always appear together, model them together
3. **Test at the right level**: Count-based tests miss structural issues
4. **Single source of truth**: Scattered code paths multiply bug surface area
5. **Fast feedback loops matter**: device_iteration.py was essential but came late
