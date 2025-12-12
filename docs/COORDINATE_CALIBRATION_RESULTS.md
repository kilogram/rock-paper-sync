# Coordinate System Calibration Results

**Date:** 2025-12-12
**Status:** ✅ VALIDATED - Empirically confirmed with physical measurements

## Executive Summary

Through systematic empirical testing with physical rulers, we discovered that **all reMarkable .rm files use a coordinate system at 226 DPI** (reMarkable 2's resolution), regardless of the target device.

**Critical finding:** The current code assumption of DOC_PPI_Y = 291.4 is **INCORRECT** and causes **30% measurement error**. All coordinate calculations should use **226 DPI**.

## The Discovery

### Testing Methodology
1. Generated calibration ruler in .rm format at various DPI settings
2. Uploaded to Paper Pro Move device
3. Measured physical ruler against Pixel 6a phone (71.8mm width)
4. Compared expected vs. actual measurements

### Test Results

| DOC_PPI | Calculation Basis | Ruler Accuracy | Error |
|---------|-------------------|----------------|-------|
| 291.4 | Code assumption (PAGE_HEIGHT/PHYSICAL_HEIGHT × 264) | 30% too long | ❌ |
| 223.3 | Viewport scaling theory | Close but slightly short | ~ |
| 264 | 1:1 mapping theory | 16% too long | ❌ |
| **226** | **reMarkable 2 PPI** | **Accurate** | ✅ |

### The Answer

**Document coordinates are ALWAYS at 226 DPI (reMarkable 2's resolution).**

This is true regardless of:
- Target device (Paper Pro Move, reMarkable 2, etc.)
- `paper_size` setting in `SceneInfo` block
- Physical screen resolution (264 PPI vs 226 PPI)

## How It Works

### Coordinate System Hierarchy

```
┌─────────────────────────────────────────────────┐
│ Document Canvas (reMarkable 2 format)          │
│ - Size: 1404 × 1872 doc pixels                 │
│ - DPI: 226 (virtual/logical DPI)               │
│ - Origin: (0,0) at top-center                  │
│ - Coordinate space is FIXED, device-independent│
└─────────────────────────────────────────────────┘
                      ↓
                 (scaling)
                      ↓
┌─────────────────────────────────────────────────┐
│ Physical Device (Paper Pro Move)                │
│ - Screen: 954 × 1696 physical pixels           │
│ - DPI: 264                                      │
│ - Scale: 1.168× (264/226)                      │
│ - UI chrome: ~253 pixels (toolbar/margins)     │
└─────────────────────────────────────────────────┘
                      ↓
                  (viewport)
                      ↓
┌─────────────────────────────────────────────────┐
│ Visible Viewport                                │
│ - Shows: ~1443 doc pixels vertically           │
│ - Content below y≈1443 is off-screen           │
│ - No horizontal cropping (full 1404 pixels)    │
└─────────────────────────────────────────────────┘
```

### Key Formulas

**Convert physical mm to document pixels:**
```python
doc_pixels = mm / 25.4 * 226
```

**Convert document pixels to physical mm:**
```python
mm = doc_pixels / 226 * 25.4
```

**Physical screen scaling (for reference):**
```python
physical_pixels = doc_pixels * (264 / 226)  # 1.168× scaling
```

## Code Fixes Status

### ✅ layout/device.py (COMPLETED 2025-12-12)

**Updated:**
- Added `document_ppi` field to `DeviceGeometry`
- Set `PAPER_PRO_MOVE.document_ppi = 226` (empirically validated)
- Added `mm_to_doc_pixels()` and `doc_pixels_to_mm()` helper methods
- Corrected documentation comments about coordinate systems
- Removed incorrect assumptions about non-uniform scaling

**Changes:**
```python
# PAPER_PRO_MOVE now includes:
document_ppi=226,  # reMarkable 2's DPI - used for ALL .rm files

# New conversion methods:
geometry.mm_to_doc_pixels(71.8)  # Returns ~639.7 doc pixels
geometry.doc_pixels_to_mm(640)   # Returns ~71.8 mm
```

**Impact:**
- Provides correct DPI value for all layout calculations
- Device-independent abstraction ready for other devices
- Fallback to old calculation if document_ppi not set (backwards compatible)

### 🔍 generator.py (NEEDS REVIEW)

**Check for:**
- Uses of hardcoded DPI values (291.4, 388.5)
- Should use `geometry.document_ppi` instead
- Physical measurement calculations

**Impact:**
- Current code may cause 30% error if using wrong DPI
- Text positioning, layout calculations all affected

### 🔍 font_metrics.py (NEEDS REVIEW)

**Review needed:**
- Font size calculations
- Line height measurements
- Any physical dimension conversions
- Should use `geometry.document_ppi` for accuracy

## Additional Findings

### `paper_size` Field in `SceneInfo`

**Tested:** Setting `paper_size=(954, 1696)` for Paper Pro Move
**Result:** No effect on coordinate mapping or rendering
**Conclusion:** `paper_size` is metadata only or used for other purposes

### Viewport Characteristics

- **Visible height:** ~1443 doc pixels (empirically measured)
- **UI chrome:** ~253 physical pixels (toolbar/margins)
- **Available content area:** 1696 - 253 = 1443 physical pixels
- **Mapping:** 1443 doc pixels → 1443 physical pixels (after scaling)

### Cross-Device Compatibility

The 226 DPI coordinate system ensures:
- Same .rm file works on all devices
- Content maintains physical size across devices
- Layout preserved regardless of screen resolution

## Migration Path

### Phase 1: Fix Core Constants (CRITICAL)
```python
# In generator.py or shared constants file
REMARKABLE_2_PPI = 226  # Document coordinate system DPI
DOC_PPI = REMARKABLE_2_PPI  # Use this for ALL physical measurements
```

### Phase 2: Update Calculations
1. Replace all uses of DOC_PPI_Y = 291.4 with DOC_PPI = 226
2. Remove DOC_SCALE_X and DOC_SCALE_Y (incorrect model)
3. Use uniform 226 DPI for both X and Y directions

### Phase 3: Validation
1. Run existing tests with new DPI
2. Generate test documents and verify on device
3. Check line spacing, margins, text positioning

### Phase 4: Documentation
1. Update code comments
2. Document coordinate system in developer guide
3. Add migration notes for existing issues

## References

- Empirical testing: `tools/calibration/generate_geometry_calibration.py`
- Detailed analysis: `docs/COORDINATE_SYSTEMS.md`
- Test results: `tests/record_replay/fixtures/calibration_geometry.rm`
- reMarkable 2 specs: 1872×1404 @ 226 DPI
- Paper Pro Move specs: 1696×954 @ 264 DPI

## Authors

Discovered through systematic empirical testing on 2025-12-12.
