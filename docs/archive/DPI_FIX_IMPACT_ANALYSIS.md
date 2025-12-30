# DPI Fix Impact Analysis

**Date:** 2025-12-12
**Context:** Following the discovery that document coordinates use 226 DPI (not 291.4 DPI)

## Executive Summary

**Good news:** The newfound DPI knowledge (226 DPI vs 291.4 DPI) **does NOT require changes** to font metrics, character positioning, or word wrap code.

**Why:** All calibrations were done **empirically against actual device output**, which inherently uses 226 DPI. The calibrations absorbed the correct DPI even though the code didn't explicitly model it.

## Detailed Analysis

### 1. Font Metrics (`font_metrics.py`)

**Current state:**
```python
FONT_POINT_SIZE = 29.5  # Recalibrated 2025-12-08
```

**Calibration history:**
- Original: 32.4 (derived from 159.5px shift / 4928 font units × 1000)
- Recalibrated: 29.5 (empirically tested against device)
- Test: "This paragraph contains the word \"" = 488.6px on device
- 29.5 gives 487.7px (within 1px) ✓
- 32.4 gave 535.7px (47px error) ✗

**DPI dependency:**
- The code uses: `glyph.width * point_size / units_per_em`
- `FONT_POINT_SIZE` is **misnamed** - it's actually a **pixel size**, not typographic points
- It's DPI-independent by design (treats point_size as pixels directly)
- The calibration was done **after** the device geometry refactor but **before** DPI discovery
- However, calibration compared against **actual device rendering** (which uses 226 DPI)

**Impact:** ✅ **NO ACTION NEEDED**
- Calibration is empirically correct for 226 DPI documents
- Works because it was tested against actual device output

**Conceptual issue:** Variable naming is misleading
- `FONT_POINT_SIZE` suggests typographic points (need DPI conversion)
- Actually used as direct pixel scaling factor (DPI-independent)
- **Recommendation:** Rename to `FONT_PIXEL_SIZE` or document clearly

### 2. Character Positioning

**Current approach:**
- Uses `geometry.char_width = 15.0` as fallback average
- Uses `font_metrics.char_width()` when `use_font_metrics=True`
- Both return pixel widths directly

**DPI dependency:**
- Character widths are in **document pixels** (coordinate system pixels)
- Document coordinates are always at 226 DPI
- Font metrics compute pixel widths that match document coordinate system

**Impact:** ✅ **NO ACTION NEEDED**
- Empirical calibration is correct

### 3. Word Wrap (`layout/engine.py`)

**Current approach:**
```python
if use_font_metrics:
    from rock_paper_sync.font_metrics import text_width as font_text_width
    self._text_width_fn = font_text_width
```

**Calibration:**
- `geometry.layout_text_width = 758.0` (device wraps at ~8px wider than text_width)
- Calibrated from cross-page annotation test (2025-12-08)

**DPI dependency:**
- All widths are in document pixels (226 DPI coordinate system)
- Layout engine operates in document coordinate space
- Empirically calibrated against actual device behavior

**Impact:** ✅ **NO ACTION NEEDED**
- Calibration is empirically correct

### 4. Line Height and Spacing

**Current values:**
```python
line_height = 57.0  # Document pixels
baseline_offset = 25.0  # Document pixels
```

**Calibration:**
- Measured from actual device highlight positioning
- All values in document coordinate space (226 DPI)

**Impact:** ✅ **NO ACTION NEEDED**
- Empirically correct

## Why the Old Wrong DPI Didn't Break Things

**The key insight:** All layout parameters were calibrated **empirically by comparing against actual device output**.

The device renders at 226 DPI, so when we measured:
- "This text" appears at 488.6px width on device
- We adjusted FONT_POINT_SIZE until our model output 487.7px
- This implicitly absorbed the 226 DPI, even though the code thought it was 291.4 DPI

**The problem the wrong DPI would have caused:**
If we had used the wrong DPI (291.4) to **calculate** font sizes or positions from physical measurements (mm → pixels), we would have gotten 30% errors.

**But we didn't!** Instead, we:
1. Measured outputs in document pixels
2. Adjusted parameters until model matched device
3. All calibrations stayed in document pixel space

## What the DPI Fix Actually Solves

The DPI discovery (226 vs 291.4) is critical for:

### ✅ Direct Physical Measurements
```python
# NOW CORRECT:
geometry.mm_to_doc_pixels(71.8)  # Returns ~640 doc pixels (accurate)

# WOULD HAVE BEEN WRONG:
# Using 291.4 DPI: 71.8mm → 829 pixels (30% too large)
```

### ✅ Future Font Size Specifications
If we wanted to specify font size as "10pt Noto Sans":
```python
# CORRECT (with new DPI):
pixel_size = 10 * 226 / 72  # = 31.4 pixels

# WOULD BE WRONG (with old DPI):
pixel_size = 10 * 291.4 / 72  # = 40.5 pixels (30% too large)
```

### ✅ Cross-Device Compatibility
New devices can now correctly specify their document coordinate DPI, ensuring layout compatibility.

## Recommendations

### 1. Clarify Variable Naming (Low Priority)

**Current:**
```python
FONT_POINT_SIZE = 29.5  # Misleading name
```

**Recommended:**
```python
# Option A: Explicit pixel size
FONT_PIXEL_SIZE = 29.5  # Direct pixel scaling factor for fonttools

# Option B: Add comment
FONT_POINT_SIZE = 29.5  # Empirical pixel scale (not typographic points)

# Option C: Use geometry (most explicit)
font_pixel_size: float = 29.5  # In DeviceGeometry
```

### 2. Add DPI to Font Metrics (Future Enhancement)

If we want proper typographic control:

```python
def char_width(
    char: str,
    font_point_size: float,  # True typographic points
    dpi: int = 226,  # Document coordinate DPI
) -> float:
    """Get character width in document pixels.

    Args:
        char: Character to measure
        font_point_size: Font size in typographic points (1/72 inch)
        dpi: Document coordinate DPI (default: 226 for reMarkable)

    Returns:
        Width in document pixels
    """
    pixel_size = font_point_size * dpi / 72
    return glyphset[glyph_name].width * pixel_size / units_per_em
```

**But this is not necessary** - current empirical approach works fine.

### 3. Document Assumptions

Add to `font_metrics.py` docstring:
```python
"""Font metrics for accurate text layout using Noto Sans.

IMPORTANT: FONT_POINT_SIZE is an empirical pixel scaling factor,
NOT a typographic point size. It was calibrated by comparing model
output against actual device rendering at 226 DPI (reMarkable 2's
document coordinate system).

All width calculations return document pixels (at 226 DPI for
reMarkable devices). No DPI conversion is needed because:
1. Document coordinates are always at 226 DPI
2. FONT_POINT_SIZE was empirically calibrated against 226 DPI output
"""
```

## Conclusion

**No code changes required.** The empirical calibration approach successfully absorbed the correct DPI (226) even when the code didn't explicitly model it.

The DPI discovery is still valuable because it:
- Fixes physical measurement conversions (mm ↔ pixels)
- Enables proper cross-device support
- Documents the actual coordinate system for future developers
- Prevents future bugs from incorrect DPI assumptions

The font metrics, character positioning, and word wrap code can remain as-is.
