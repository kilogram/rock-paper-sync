# Renderer Coordinate Model

This document describes how `tools/rmlib/renderer.py` transforms reMarkable .rm file coordinates to rendered PNG images. Understanding these transformations is critical for validating annotation positioning.

## Overview

The renderer converts .rm binary files to PNG images for visual comparison testing. It must accurately position:
- **Text** - The document content from RootTextBlock
- **Highlights** - Yellow highlight rectangles (SceneGlyphItemBlock)
- **Strokes** - Handwritten annotations (SceneLineItemBlock)

Each element type has a different coordinate model.

## Page Coordinate System

```
┌────────────────────────────────────────┐
│                                        │
│  Origin: (0, 0) at top-left            │
│  Width: 1404 pixels                    │
│  Height: 1872 pixels                   │
│  DPI: 226 (reMarkable 2 document DPI)  │
│                                        │
│  PAGE_CENTER_X = 702                   │
│  Text X coordinates are center-relative│
│  (negative = left of center)           │
│                                        │
└────────────────────────────────────────┘
```

## Text Rendering

### RootTextBlock Properties

The RootTextBlock contains text positioning data:

```python
text_data.pos_x   # X offset from page center (typically -375.0)
text_data.pos_y   # Y offset from page top (typically 234.0)
text_data.width   # Text width for word wrapping (typically 750.0)
```

### Critical: Use text_width from .rm File

**Problem discovered (2025-12-29):** We were calculating text width from page dimensions:
```python
# WRONG: Calculated ~1027px, causing incorrect word wrapping
max_text_width = self.width - page_x - 50
```

**Solution:** Extract text_width directly from RootTextBlock:
```python
# CORRECT: Use device-stored value (750px typical)
text_width = text_data.width if hasattr(text_data, "width") else None
```

**Why this matters:**
- Word wrapping determines which character is on which line
- Wrong word wrapping = wrong Y positions for all annotations
- Device stores the exact width it uses for rendering

### Text Y Position Calculation

Text lines are rendered at Y positions:
```
Line 0: Y = text_origin_y                    = 234.0
Line 1: Y = text_origin_y + LINE_HEIGHT      = 291.0
Line 2: Y = text_origin_y + 2 * LINE_HEIGHT  = 348.0
...
```

## Highlight Rendering (SceneGlyphItemBlock)

Highlights use **absolute coordinates** stored directly in the .rm file:

```python
glyph = block.item.value
for rect in glyph.rectangles:
    # rect.x is center-relative
    page_x = PAGE_CENTER_X + rect.x
    page_y = rect.y  # Absolute Y, no transformation needed

    # Draw rectangle at (page_x, page_y) with dimensions (rect.w, rect.h)
```

**Key insight:** Highlight rectangles don't need coordinate transformation - the device stores their absolute page positions.

## Stroke Rendering (SceneLineItemBlock)

Strokes use **anchor-relative coordinates** that require transformation.

### Stroke Coordinate Model

```
Stroke Position = Anchor Position + Native Position

page_x = anchor_x + stroke.point.x
page_y = anchor_y + stroke.point.y + BASELINE_OFFSET
```

Where:
- `anchor_x` = PAGE_CENTER_X + TreeNodeBlock.anchor_origin_x
- `anchor_y` = Y position of text line containing anchor character
- `stroke.point.x/y` = Native coordinates from SceneLineItemBlock

### Anchor Y Calculation

Each stroke has a parent TreeNodeBlock that anchors it to text:

1. TreeNodeBlock contains `anchor_id` pointing to a character offset
2. We map character offsets to Y positions using word-wrap layout
3. The anchor Y is the Y position of the line containing that character

```python
# Build char_offset -> Y position map
char_to_y = {}
current_y = text_origin_y
for paragraph in text.split("\n"):
    line_breaks = engine.calculate_line_breaks(paragraph, text_width)
    for break_pos in line_breaks:
        # All chars on this line have same Y
        for char in range(break_pos, end_pos):
            char_to_y[current_offset + char] = current_y
        current_y += LINE_HEIGHT
```

### Critical: Baseline Offset (STROKE_BASELINE_OFFSET = 20)

**Problem discovered (2025-12-29):** Handwritten annotations appeared "half a line too high" - descenders should touch the line they were written on.

**Root cause:**
- Device anchors strokes to the text **baseline**
- Our char_to_y map stores the **top** of each text line
- Need offset to convert from line-top to baseline

**Solution:** Add 20px baseline offset to stroke anchor Y:
```python
# Add baseline offset: device anchors to baseline, char_to_y stores line top
page_y = char_to_y[anchor.char_offset] + STROKE_BASELINE_OFFSET
```

**Calibration method:**
1. Find a stroke with a descender (like 'g' in handwriting)
2. The descender should touch underscores on the text line
3. Adjust offset until stroke descender Y ≈ underscore Y
4. Result: 20px offset (font ascent is ~34px, underscore starts at baseline)

## Line Height Values

### Critical: Device Coordinates Use 57px Line Height

**Problem discovered (2025-12-29):** We were using 68px line height (calibrated for thumbnail visual comparison at 264/226 PPI scaling).

**Solution:** Use 57px for coordinate calculations:

```python
# Device coordinate system uses 57px line height
BODY_LINE_HEIGHT = 57   # NOT 68 (which was for thumbnail scaling)
HEADING_LINE_HEIGHT = 87  # ~1.53× body (57 × 1.53)
```

**Why 68px was wrong:**
- 68px = 57px × (264 DPI / 226 DPI) = thumbnail scaling factor
- Annotation coordinates in .rm files use the original 57px line height
- Using 68px caused Y positions to be ~20% off by the bottom of the page

### Historical Note: Line Height Values

| Value | Purpose | Status |
|-------|---------|--------|
| 57px | Device coordinate system (anchor line height) | **CORRECT** |
| 68px | Thumbnail visual comparison (PPI scaled) | Used for thumbnail overlay tests only |
| 35px | Initial guess | Deprecated |
| 50px | Second guess | Deprecated |

## Summary of Constants

```python
# Page dimensions (reMarkable 2 document format)
DEFAULT_WIDTH = 1404
DEFAULT_HEIGHT = 1872
PAGE_CENTER_X = 702.0  # 1404 / 2

# Text origin defaults (from device)
TEXT_ORIGIN_X = -375.0  # 375px left of center
TEXT_ORIGIN_Y = 234.0   # Start 234px from top

# Line heights for coordinate calculations
BODY_LINE_HEIGHT = 57      # Device anchor line height
HEADING_LINE_HEIGHT = 87   # ~1.53× body

# Stroke baseline offset
STROKE_BASELINE_OFFSET = 20  # Shift anchor Y to text baseline
```

## Debugging Coordinate Issues

### Symptoms and Causes

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| Highlights horizontally misaligned | Wrong word wrapping | Check text_width from RootTextBlock |
| Highlights vertically misaligned | Wrong line height | Use 57px, not 68px |
| Strokes appear "too high" | Missing baseline offset | Add STROKE_BASELINE_OFFSET |
| All annotations shift over page | Wrong text_origin extraction | Check pos_x, pos_y from RootTextBlock |

### Debug Rendering Technique

Add colored lines to visualize anchor positions:

```python
# Red line at anchor Y
draw.line([(0, anchor_y), (1404, anchor_y)], fill=(255, 0, 0), width=2)

# Yellow line at stroke descender
descender_y = anchor_y + stroke_lowest_y
draw.line([(0, descender_y), (1404, descender_y)], fill=(255, 255, 0), width=2)
```

## Adapting to Device Changes

If reMarkable changes device parameters, update these in priority order:

1. **text_width** - Check RootTextBlock.width on new device
2. **line height** - Create test document, measure highlight Y delta between lines
3. **baseline offset** - Write text with descenders, measure offset to underscore
4. **text origin** - Check RootTextBlock.pos_x and pos_y

### Calibration Test

Create a test document with:
1. Multiple lines of text (to measure line height)
2. Highlight on a known word (to verify Y positioning)
3. Handwritten annotation with descenders touching underscores (to verify baseline offset)
4. Compare device thumbnail to renderer output

## Related Documentation

- `docs/COORDINATE_SYSTEMS.md` - Page coordinate fundamentals (226 DPI)
- `docs/STROKE_ANCHORING.md` - How strokes anchor to text
- `docs/RMSCENE_FINDINGS.md` - Line height calibration history
- `tools/rmlib/renderer.py` - Implementation with detailed comments
