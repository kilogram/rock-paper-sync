# Architecture Review: Annotation Preservation Implementation

**Date**: 2025-11-16
**Status**: Critical bugs identified and fix plan created

## Executive Summary

The annotation preservation implementation has a **critical architectural flaw**: it conflates two different coordinate systems without translation. This results in 90% of annotations (20/22) moving off-screen after sync.

**Root Cause**: reMarkable v6 files use multiple coordinate spaces based on scene tree hierarchy. Strokes use text-relative coordinates while highlights use absolute coordinates. The code treats all coordinates as if they're in the same space.

**Severity**: CRITICAL - Renders annotation preservation completely non-functional

---

## Critical Issues 🚨

### 1. Coordinate System Conflation - The Root Cause

**Location**: `generator.py:371-441` (`_adjust_annotation_block_position`, `_get_annotation_center_y`, `_extract_text_blocks_from_rm`)

**The Bug**:

reMarkable v6 files use **multiple coordinate spaces** based on scene tree hierarchy:

1. **Absolute/Page Coordinates**: Items parented to root layer (CrdtId(0, 11))
   - Highlights (Glyphs): y=251.4, y=297.0
   - Coordinates are absolute page positions

2. **Text-Relative Coordinates**: Items parented to text-specific layers (CrdtId(2, 1316))
   - Strokes (Lines): y=6.9, y=12.9
   - Coordinates are relative to TEXT_POS_Y (94.0)
   - **Actual absolute position = TEXT_POS_Y + y_relative**

**Evidence from Baseline**:
```
Baseline annotations:
- Glyph 1: y=251.4, parent_id=CrdtId(0, 11)    ✓ Absolute coordinates
- Glyph 2: y=297.0, parent_id=CrdtId(0, 11)    ✓ Absolute coordinates
- Line 1:  y=6.9,   parent_id=CrdtId(2, 1316)  ✗ Text-relative coordinates
- Line 2:  y=12.9,  parent_id=CrdtId(2, 1316)  ✗ Text-relative coordinates

Text blocks extracted:
- Block 0: y=94-129   (starts at TEXT_POS_Y)
- Block 1: y=129-164
```

**Why This Fails**:

When comparing Line 1 (y=6.9 in text-relative space) to text blocks (in absolute space), the algorithm:
1. Finds nearest block to y=6.9 → Block 0 (center 111.5)
2. Calculates offset: new_center - 111.5
3. But annotation should be at ~100.9 (94 + 6.9), not 6.9!
4. Result: **Offset is off by ~94 pixels**, causing annotations to move far off-screen

### 2. Inconsistent Coordinate Space in Text Generation vs Extraction

**Location**: `generator.py:662-728` (`blocks_to_text_items`) vs `generator.py:320-369` (`_extract_text_blocks_from_rm`)

**The Mismatch**:

When **generating** text items (new documents):
```python
# blocks_to_text_items (line 679)
y_position = float(self.layout.margin_top)  # = 100 (from config)
```

When **extracting** text blocks (old documents):
```python
# _extract_text_blocks_from_rm (line 352)
y_pos = text_data.pos_y  # = 94.0 (TEXT_POS_Y constant from rmscene)
```

**The Constants**:
```python
# generator.py:153
TEXT_POS_Y = 94.0   # Used when writing .rm files (RootTextBlock.pos_y)

# config.py:default for margin_top
margin_top = 100    # Used when calculating text item positions
```

This creates a **6-pixel coordinate offset** between generated and extracted text blocks even when the content is identical.

### 3. Broken Abstraction: Direct rmscene Block Manipulation

**Location**: `generator.py:443-473` (`_apply_y_offset_to_block`)

The code attempts to modify rmscene internal block structures directly using `hasattr` checks and string matching on type names. This is fragile and tightly coupled to rmscene internals.

**Issues**:
1. **Tight Coupling**: Code depends on internal rmscene data structures
2. **Fragile**: Uses `hasattr` checks and string matching
3. **No Abstraction**: Directly mutates third-party library objects
4. **Hidden Assumptions**: Assumes all points/rectangles use same coordinate system (FALSE!)

### 4. Missing Coordinate Space Detection

The code has **no mechanism** to determine which coordinate space an annotation uses. It treats all annotations as if they're in the same space.

**Required Information** (currently ignored):
- `parent_id` of annotation blocks (determines coordinate space)
- Scene tree hierarchy (maps parents to coordinate transforms)

---

## Additional Issues ⚠️

### 5. X-Position Adjustment Not Implemented

The current code only adjusts Y coordinates. When text reflows due to paragraph edits or line wrapping changes, annotations stay at their original X positions, appearing misaligned.

### 6. Position Mapping Assumes Content Similarity

**Location**: `annotations.py:386-450` (`calculate_position_mapping`)

The position mapping uses **content matching** to map old blocks to new blocks. This breaks when:
- Content is edited (annotations become orphaned)
- Paragraphs are split/merged
- Headings are added/removed

**Better Approach**: Use spatial proximity + content similarity + structural similarity

### 7. Inconsistent Lines Per Page (allow_paragraph_splitting Bug)

**Location**: `generator.py:509-623` (`paginate_content`)

The pagination algorithm estimates **characters** but counts **lines**. Text with varying line lengths causes inconsistent page fills.

**Impact on Annotations**: When pages have inconsistent line counts, text blocks move unpredictably between pages.

---

## Fix Implementation Plan

### Priority 1: Fix Coordinate Space Transformation (CRITICAL)

**Goal**: Make annotations stay in correct positions by handling coordinate spaces properly

**Changes Required**:

1. **Modify `_extract_text_blocks_from_rm()` to return text origin**:
```python
def _extract_text_blocks_from_rm(self, rm_file_path: Path) -> tuple[list[TextBlock], float]:
    """Extract text blocks AND text origin Y coordinate.

    Returns:
        Tuple of (text_blocks, text_origin_y)
    """
    # Store text_origin_y when found
    text_origin_y = self.TEXT_POS_Y  # default

    for block in blocks:
        if 'RootText' in type(block).__name__:
            text_origin_y = block.value.pos_y
            # ... extract text blocks ...

    return text_blocks, text_origin_y
```

2. **Add coordinate space detection to `_get_annotation_center_y()`**:
```python
def _get_annotation_center_y_absolute(self, block, text_origin_y: float) -> float | None:
    """Extract center Y in ABSOLUTE page coordinates.

    Detects coordinate space from parent_id and transforms accordingly.
    """
    # Determine if coordinates are text-relative
    is_text_relative = False
    if hasattr(block, 'parent_id'):
        # Root layer (0, 11) uses absolute coordinates
        # Other layers use text-relative coordinates
        is_text_relative = (block.parent_id != CrdtId(0, 11))

    # Extract Y in native space
    native_y = self._extract_y_from_block(block)

    # Transform to absolute if needed
    if is_text_relative:
        return text_origin_y + native_y
    else:
        return native_y
```

3. **Update `_adjust_annotation_block_position()` to use absolute coordinates**:
```python
def _adjust_annotation_block_position(
    self,
    block,
    old_text_blocks: list[TextBlock],
    new_text_blocks: list[TextBlock],
    position_map: dict[int, int],
    old_text_origin_y: float,
    new_text_origin_y: float
):
    """Adjust annotation positions using absolute coordinate space."""

    # Get annotation center in ABSOLUTE coordinates
    center_y_absolute = self._get_annotation_center_y_absolute(block, old_text_origin_y)

    # Now comparing same coordinate spaces!
    # ... find nearest block, calculate offset, apply ...
```

### Priority 2: Unify Generation/Extraction Coordinates (MAJOR)

**Goal**: Eliminate the 6-pixel offset between text generation and extraction

**Change Required**:

In `blocks_to_text_items()` line 679:
```python
# BEFORE:
y_position = float(self.layout.margin_top)  # = 100

# AFTER:
y_position = float(self.TEXT_POS_Y)  # = 94.0 (matches rmscene constant)
```

**Rationale**: TEXT_POS_Y is an rmscene constant and should not vary with user config. The margin_top config is misleading - it's not actually used for file generation positioning.

### Priority 3: Disable Paragraph Splitting (QUICK WIN)

**Goal**: Prevent inconsistent lines per page until proper text layout is implemented

**Change Required**:

In `config.py` line 69:
```python
# BEFORE:
allow_paragraph_splitting: bool = True

# AFTER:
allow_paragraph_splitting: bool = False
```

Add documentation explaining this is temporary until text layout engine is integrated.

---

## Testing Strategy

### Unit Tests Needed

```python
def test_text_relative_to_absolute():
    """Verify coordinate transformation for text-relative annotations."""
    text_origin_y = 94.0
    annotation_y_relative = 6.9

    expected_absolute = 100.9  # 94.0 + 6.9
    actual_absolute = text_origin_y + annotation_y_relative

    assert abs(expected_absolute - actual_absolute) < 0.1

def test_annotation_coordinate_space_detection():
    """Verify we detect coordinate space from parent_id."""
    # Root layer = absolute
    assert is_text_relative(CrdtId(0, 11)) == False

    # Other layers = text-relative
    assert is_text_relative(CrdtId(2, 1316)) == True

def test_position_adjustment_preserves_annotations():
    """Verify annotations stay with their text after content update."""
    # Old: annotation at y=6.9 (relative), text at y=94
    # New: text moved to y=150
    # Expected: annotation at y=6.9 (still relative to new text origin)
```

### Integration Tests

1. **Round-trip test**: Generate document → Add annotations → Update content → Verify annotations preserved
2. **Multi-coordinate-space test**: Mix absolute and relative annotations
3. **Device validation**: Transfer to reMarkable and verify visual appearance

---

## Estimated Effort

- **Priority 1 (Coordinate transformation)**: 4-6 hours
- **Priority 2 (Unify coordinates)**: 2-3 hours
- **Priority 3 (Disable splitting)**: 30 minutes
- **Testing and validation**: 2-4 hours

**Total**: 1-2 days for production-ready fix

---

## Future Work (Phase 2)

### X-Position Adjustment

Requires:
- Character-level text layout tracking
- Word boundary detection
- Bidirectional text support
- Multi-line annotation handling

**Estimated**: 1 week

### Proper Text Re-flowing

Requires:
- Text layout engine integration
- Actual line measurement (not estimation)
- Word wrapping algorithms

**Estimated**: 2-3 days

---

## References

- **rmscene library**: https://github.com/ricklupton/rmscene
- **RMSCENE_FINDINGS.md**: Documents coordinate system basics
- **SYNC_PROTOCOL.md**: Cloud sync protocol details
- **Baseline test data**: `tests/testdata/annotated_baseline/`

## Key Insights

1. **Coordinate spaces are parent-dependent**: Items parented to different scene layers use different coordinate systems
2. **TEXT_POS_Y is a constant**: Should not vary with user configuration
3. **Strokes vs Highlights use different spaces**: Strokes are text-relative, highlights are absolute
4. **Position adjustment must work in absolute space**: Convert all coordinates before comparison

---

**Review Conducted By**: architecture-reviewer agent
**Implementation Status**: Pending (Priority 1 & 2 to be implemented)
