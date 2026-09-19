# reMarkable Coordinate Systems

## Problem Statement

The Paper Pro Move uses a document coordinate system inherited from the reMarkable 2, but has a different physical screen resolution and aspect ratio. We need to understand how document coordinates map to physical screen pixels to draw geometrically accurate calibration rulers.

## Hard Facts

### reMarkable 2 (Source Device)
- **Resolution**: 1872 × 1404 pixels
- **PPI**: 226
- **Display**: 10.3" diagonal
- **Physical size**: 210.4mm (H) × 157.8mm (W)
- **Aspect ratio**: 1872/1404 = 1.333 (4:3)

### Paper Pro Move (Target Device)
- **Resolution**: 1696 × 954 pixels
- **PPI**: 264
- **Display**: 7.3" diagonal
- **Physical size**: 163.2mm (H) × 91.8mm (W)
- **Aspect ratio**: 1696/954 = 1.778 (16:9)

### Document Canvas (from code)
- **Dimensions**: 1872 × 1404 doc pixels
- **Origin**: (0, 0) at top-center
- **Y-axis**: Positive downward
- **X-axis**: ±X from center

## The Aspect Ratio Problem

The document canvas (4:3) must be mapped onto a physical screen (16:9) with different dimensions. This requires either:
1. **Cropping** - Show only part of the document
2. **Letterboxing** - Add margins (black bars)
3. **Non-uniform scaling** - Distort geometry

## Current Code Assumptions

```python
# From tools/calibration/generate_geometry_calibration.py
PHYSICAL_WIDTH = 954   # pixels (horizontal in portrait)
PHYSICAL_HEIGHT = 1696  # pixels (vertical in portrait)
PAGE_WIDTH = 1404      # doc pixels
PAGE_HEIGHT = 1872     # doc pixels

DOC_SCALE_X = PAGE_WIDTH / PHYSICAL_WIDTH   # 1.472
DOC_SCALE_Y = PAGE_HEIGHT / PHYSICAL_HEIGHT  # 1.104

DOC_PPI_X = PHYSICAL_PPI * DOC_SCALE_X  # 388.6
DOC_PPI_Y = PHYSICAL_PPI * DOC_SCALE_Y  # 291.4
```

This assumes **non-uniform scaling** (different X and Y scale factors), which would cause geometric distortion.

## Three Possible Mapping Scenarios

### Scenario 1: Uniform Scale to Fit Height (Horizontal Crop)
```
Scale factor: 1696 / 1872 = 0.906
Physical pixels per doc pixel: 0.906
DOC_PPI_Y = 264 / 0.906 = 291.4 ✓
DOC_PPI_X = 264 / 0.906 = 291.4

Visible doc width: 954 / 0.906 = 1053 pixels (75% of 1404)
Cropped horizontally: ±175 pixels from edges
```

**Implications:**
- Text area (x = -375 to +375) would be fully visible ✓
- Content beyond ±526 would be off-screen
- No geometric distortion ✓
- DOC_PPI same in X and Y ✓

### Scenario 2: Uniform Scale to Fit Width (Vertical Margins)
```
Scale factor: 954 / 1404 = 0.679
Physical pixels per doc pixel: 0.679
DOC_PPI_X = 264 / 0.679 = 388.6 ✓
DOC_PPI_Y = 264 / 0.679 = 388.6

Visible doc height: 1696 / 0.679 = 2498 pixels (exceeds 1872)
Vertical margins: (2498 - 1872) × 0.679 = 425 physical pixels
Top/bottom black bars: 212 pixels each
```

**Implications:**
- Entire document width visible ✓
- Large vertical margins (black bars)
- No geometric distortion ✓
- DOC_PPI same in X and Y ✓

### Scenario 3: Non-uniform Scaling (Code's Assumption)
```
X scale factor: 954 / 1404 = 0.679
Y scale factor: 1696 / 1872 = 0.906
DOC_PPI_X = 264 / 0.679 = 388.6 ✓
DOC_PPI_Y = 264 / 0.906 = 291.4 ✓

Entire document visible (no cropping, no margins)
```

**Implications:**
- Entire document visible ✓
- **Geometric distortion** ✗ (circles become ellipses)
- Different PPI in X and Y ✗
- Matches code calculations ✓

## Observations from User Testing

1. Bottom border at y=1400 appears "well above" the bottom of physical screen
   - Suggests viewport doesn't extend to full PAGE_HEIGHT (1872)
   - Consistent with Scenario 1 (horizontal crop) if there's also vertical viewport limitation

2. Content below certain Y coordinate is cut off but remains in document
   - User confirmed this is intentional for paragraph overflow
   - Suggests viewport cropping in Y direction

3. Ruler measurements showed discrepancy with DOC_PPI_Y = 291.4
   - Empirical measurement suggested effective PPI ≈ 223.3
   - Indicates viewport scaling may not match code assumptions

## Questions to Resolve

1. **Which mapping scenario is actually used?**
   - Does rmscene handle this, or is it device firmware?
   - Is there official documentation?

2. **What is the actual viewport range?**
   - Y-axis: What range of [0, 1872] is visible?
   - X-axis: What range of [-702, +702] is visible?

3. **Is there a transformation layer?**
   - Does rmscene provide viewport/camera concepts?
   - Is scaling handled transparently?

## rmscene Library Findings

### SceneInfo Block

The rmscene library (v0.7.0) has a `SceneInfo` block (type 0x0D) that includes an optional `paper_size` field:

```python
class SceneInfo(Block):
    current_layer: LwwValue[CrdtId]
    background_visible: Optional[LwwValue[bool]]
    root_document_visible: Optional[LwwValue[bool]]
    paper_size: Optional[tuple[int, int]]  # Added in v0.7.0
```

**This may be the key to understanding coordinate systems!**

The `paper_size` field could indicate:
1. The target device resolution
2. The viewport dimensions
3. The document canvas size

### Questions to Answer Empirically

1. **What is the `paper_size` value in our generated documents?**
   - Check calibration_geometry.rm
   - Check documents created by reMarkable devices

2. **Does `paper_size` match device resolution?**
   - reMarkable 2: Expected (1404, 1872) or (1872, 1404)?
   - Paper Pro Move: Expected (954, 1696) or (1696, 954)?

3. **How does the device use `paper_size`?**
   - Does it determine viewport bounds?
   - Does it affect coordinate scaling?

## SOLUTION - Empirically Validated ✅

### The Answer: Document Coordinates Are Always at 226 DPI

Through systematic testing with physical rulers, we determined:

**Document coordinate system:**
- Uses reMarkable 2's resolution: **226 DPI (virtual DPI)**
- This is FIXED regardless of target device or `paper_size` setting
- Document canvas: 1872 × 1404 pixels (from reMarkable 2)

**Rendering on Paper Pro Move:**
- Physical screen: 1696 × 954 pixels at 264 DPI
- Scale factor: 264 / 226 = **1.168 physical pixels per doc pixel**
- Viewport shows ~1443 doc pixels vertically (after UI chrome)

**For physical measurements:**
- Use **DOC_PPI = 226** for all coordinate calculations
- To convert mm to doc pixels: `mm / 25.4 × 226`
- To convert doc pixels to mm: `doc_pixels / 226 × 25.4`

### Test Results

| DOC_PPI | Ruler Length | Pixel 6a Coverage | Result |
|---------|--------------|-------------------|---------|
| 291.4 (code assumption) | 823 px | ~5.5cm / 7.18cm | Too long (30%) |
| 223.3 (viewport theory) | 631 px | ~5.5cm / 7.18cm | Close but short |
| 264 (1:1 theory) | 746 px | ~6.2cm / 7.18cm | Too long (16%) |
| **226 (reMarkable 2)** | **639 px** | **~7.18cm / 7.18cm** | ✅ **Accurate!** |

### Key Findings

1. **`paper_size` does NOT control coordinate mapping**
   - Setting `paper_size=(954, 1696)` had no effect on rendering
   - It may be metadata only or used for other purposes

2. **Document coordinates inherit from reMarkable 2**
   - All .rm files use 226 DPI coordinate system
   - This ensures cross-device compatibility

3. **Viewport is cropped, not scaled uniformly**
   - Physical screen: 1696 pixels - UI chrome ≈ 253 pixels = ~1443 available
   - Shows doc pixels ~0 to ~1443 (bottom of canvas is off-screen)
   - Content below y≈1443 exists but is not visible without scrolling

4. **No geometric distortion**
   - Pixels are square in document space (226 DPI in both X and Y)
   - Uniform scaling to physical screen (1.168× in both directions)

### Implications for rock-paper-sync

**For generator.py:**
- Document coordinates should use 226 DPI for physical accuracy
- Current DOC_PPI_Y = 291.4 is INCORRECT (causes 30% error)
- Should be updated to 226 DPI

**For layout calculations:**
- Text positioning uses document coordinates (226 DPI)
- Physical measurements need 226 DPI conversion
- Viewport cutoff at ~1443 doc pixels is expected behavior

## References

- reMarkable 2 specs: 1872×1404 @ 226 DPI
- Paper Pro Move specs: 1696×954 @ 264 DPI
- Document canvas: Inherited from reMarkable 2 coordinate system
- rmscene library: https://github.com/ricklupton/rmscene
- rmscene v0.7.0: Added `paper_size` field to `SceneInfo` block
