# Font Size Discovery Results

**Date:** 2025-12-12
**Status:** ✅ VALIDATED

## Key Finding

**The reMarkable Paper Pro Move uses 10.0pt font (proper typographic points).**

At 226 DPI: `10.0pt × 226 / 72 = 31.4 pixels`

## Calibration Method

1. Highlighted 20 consecutive 'i' characters on device
2. Extracted highlight rectangle width: **173.9px**
3. Accounted for highlight padding: **~10px total**
4. Adjusted width: **163.9px**
5. Compared to theoretical widths at various point sizes

## Results

| Point Size | Theoretical (20 i's) | Measured | Error | Match |
|-----------|----------------------|----------|-------|-------|
| 8.0pt | 129.6px | 163.9px | 20.9% | ✗ |
| 9.0pt | 145.8px | 163.9px | 11.0% | ✗ |
| 9.5pt | 153.9px | 163.9px | 6.1% | ~ |
| **10.0pt** | **162.0px** | **163.9px** | **1.2%** | **✓** |
| 10.5pt | 170.1px | 163.9px | 3.8% | ✓ |
| 11.0pt | 178.2px | 163.9px | 8.7% | ✗ |
| 12.0pt | 194.4px | 163.9px | 18.6% | ✗ |

**Best match: 10.0pt (1.2% error)**

## Verification

### Character 'm'
- Predicted at 10.0pt: **29.35px**
- Measured (minus padding): **~28.3px**
- **Match: ✓ GOOD** (within 1px)

### Highlight Padding Analysis

**Single character highlights** include significant padding:
- Single 'i' highlight: 22.8px
- Multi-char 'i' average: 8.7px
- **Estimated padding: ~14px per single-char highlight**

**Multi-character highlights** share padding at edges:
- 20 characters: ~10px total padding
- More accurate for measurement

## The Old Value Explained

**Current code:** `FONT_POINT_SIZE = 29.5`

This value was being used **directly as a pixel scale factor** (bypassing DPI conversion), which is why it worked despite being conceptually wrong:

```python
# OLD (what the code does):
pixel_size = 29.5  # Used directly

# NEW (proper typography):
point_size = 10.0
pixel_size = 10.0 * 226 / 72  # = 31.4 pixels
```

The old value (29.5) is close to the proper pixel size (31.4), which is why it appeared to work. But it lacked the proper typographic model.

## Implementation

### Update `DeviceGeometry`

```python
# layout/device.py
PAPER_PRO_MOVE = DeviceGeometry(
    # ... other fields ...
    font_point_size=10.0,  # CHANGED: Real typographic points (was 29.5)
    document_ppi=226,
    # ... other fields ...
)
```

### Update `font_metrics.py`

```python
# font_metrics.py

# CHANGED: Proper typographic point size
DEVICE_FONT_SIZE_PT = 10.0

def char_width(
    char: str,
    font_size_pt: float = DEVICE_FONT_SIZE_PT,
    document_ppi: int = 226,
) -> float:
    """Get character width in document pixels.

    Args:
        char: Character to measure
        font_size_pt: Font size in typographic points (1/72 inch)
        document_ppi: Document coordinate DPI

    Returns:
        Width in document pixels
    """
    # Convert points to pixels
    pixel_size = font_size_pt * document_ppi / 72.0

    # Rest of function unchanged...
```

### Update `WordWrapLayoutEngine`

```python
# layout/engine.py

if use_font_metrics:
    from rock_paper_sync.font_metrics import text_width as font_text_width

    def _width_with_dpi(text: str) -> float:
        return font_text_width(
            text,
            font_size_pt=self._geometry.font_point_size,  # Now 10.0pt
            document_ppi=self._geometry.document_ppi,     # 226
        )

    self._text_width_fn = _width_with_dpi
```

## Benefits

1. **Conceptually correct** - Points are points, pixels are pixels
2. **DPI-aware** - Proper use of 226 DPI discovery
3. **Testable** - Can validate at different point sizes
4. **Maintainable** - Self-documenting code

## Calibration Data

All calibration files captured and saved in:
- `tests/record_replay/testdata/calibration/paper_pro_move/`

**Golden files:**
- ✅ `calibration_font_sizes.rm` - Font size validation
- ✅ `calibration_chars.rm` - Individual character widths (54 highlights)
- ✅ `calibration_wrap.rm` - Word wrap boundaries
- ✅ `calibration_structure.rm` - Structural spacing (10 highlights)
- ✅ `calibration_geometry.rm` - DPI validation (71.8mm ruler)

## Next Steps

1. Update `DeviceGeometry.font_point_size` to `10.0`
2. Modify `font_metrics.py` to accept DPI parameter
3. Update `WordWrapLayoutEngine` to pass DPI
4. Run tests to verify no regressions
5. Validate word wrap predictions match device behavior

## Success Criteria

- [ ] Font size in real typographic points (10.0pt)
- [ ] DPI conversion in font_metrics (pt × DPI / 72)
- [ ] Character width predictions within 5%
- [ ] All existing tests still pass
- [ ] Word wrap matches device edge cases
