# Coordinate System Discovery - Executive Summary

**Date:** 2025-12-12
**Status:** ✅ COMPLETED AND INTEGRATED

## What We Discovered

**Document coordinates use reMarkable 2's 226 DPI**, not the calculated 291.4 DPI the code assumed.

This was empirically validated by generating calibration rulers at different DPI values and measuring them with a physical ruler (Pixel 6a phone, 71.8mm width) on the Paper Pro Move device.

## The Problem

The codebase assumed document coordinates scaled from physical pixels:
```python
DOC_PPI_Y = PHYSICAL_PPI * (PAGE_HEIGHT / PHYSICAL_HEIGHT)
         = 264 * (1872 / 1696)
         = 291.4  # ❌ WRONG
```

This caused **30% error** in all physical measurements.

## The Solution

Document coordinates are at **reMarkable 2's 226 DPI** regardless of device:
```python
document_ppi = 226  # ✅ CORRECT
```

This ensures cross-device compatibility - the same .rm file works correctly on all reMarkable devices.

## What Was Updated

### ✅ Core Infrastructure (`layout/device.py`)

**Added:**
- `document_ppi` field to `DeviceGeometry` class
- `mm_to_doc_pixels()` and `doc_pixels_to_mm()` conversion methods
- Corrected documentation about coordinate systems

**Updated:**
- `PAPER_PRO_MOVE.document_ppi = 226` (empirically validated)
- Fixed comments about scaling (uniform 1.168×, not non-uniform)

**Device-agnostic design:**
- Other devices can specify their own `document_ppi` if different
- Fallback calculation available for backwards compatibility
- Clear separation of document vs physical coordinate systems

### 📝 Documentation

**Created:**
1. `COORDINATE_SYSTEMS.md` - Detailed technical analysis
2. `COORDINATE_CALIBRATION_RESULTS.md` - Implementation guide
3. `COORDINATE_SYSTEM_SUMMARY.md` - This file

**Updated:**
4. `layout/device.py` - Inline documentation with empirical findings

### 🧪 Calibration Tools

**Created:**
- `tools/calibration/generate_geometry_calibration.py` - Generates test rulers
- `tools/calibration/inspect_rm_file.py` - Inspects .rm file structure
- `tests/record_replay/fixtures/calibration_geometry.rm` - Validated test file

## How to Use

### For Physical Measurements

```python
from rock_paper_sync.layout.device import PAPER_PRO_MOVE

geometry = PAPER_PRO_MOVE

# Convert mm to document pixels
ruler_length = geometry.mm_to_doc_pixels(71.8)  # Returns ~639.7 doc pixels

# Convert document pixels to mm
physical_size = geometry.doc_pixels_to_mm(640)  # Returns ~71.8 mm
```

### For New Devices

When adding support for a new reMarkable device:

```python
NEW_DEVICE = DeviceGeometry(
    # Physical screen specs
    physical_width=...,
    physical_height=...,
    physical_ppi=...,

    # Document coordinate system
    # If compatible with existing .rm files, use 226 (reMarkable 2)
    # If device uses a new format, set to appropriate value
    document_ppi=226,  # Or device-specific value

    # Other layout parameters...
)
```

## Key Concepts

### Three Coordinate Spaces

1. **Document Space** (1404×1872 @ 226 DPI)
   - Used in .rm file format
   - Device-independent "virtual" coordinates
   - Ensures cross-device compatibility

2. **Viewport Space** (~1404×1443 visible)
   - What's actually shown on screen
   - Accounts for UI chrome (toolbars, etc.)
   - Device-specific

3. **Physical Space** (954×1696 @ 264 PPI for Paper Pro Move)
   - Actual hardware pixels
   - Device-specific

### Scaling

Document → Physical: `scale = physical_ppi / document_ppi`
- Paper Pro Move: 264 / 226 = **1.168×**
- Uniform in both X and Y (no distortion)

## Next Steps

### Recommended Actions

1. **Review generator.py**
   - Check for hardcoded DPI values (291.4, 388.5)
   - Replace with `geometry.document_ppi`

2. **Review font_metrics.py**
   - Ensure font calculations use correct DPI
   - Validate with empirical measurements

3. **Add Tests**
   - Unit tests for `mm_to_doc_pixels()` conversions
   - Integration tests with known physical measurements

### Future Devices

When reMarkable releases new devices:

1. Add new `DeviceGeometry` constant
2. Set `document_ppi` based on compatibility requirements
3. Empirically validate with calibration tools
4. Update documentation

## References

- **Empirical testing:** `tools/calibration/`
- **Technical details:** `docs/COORDINATE_SYSTEMS.md`
- **Implementation:** `src/rock_paper_sync/layout/device.py`
- **reMarkable 2 specs:** 1872×1404 @ 226 DPI
- **Paper Pro Move specs:** 1696×954 @ 264 PPI

---

**Contributors:** Discovered and validated through systematic empirical testing on 2025-12-12.
