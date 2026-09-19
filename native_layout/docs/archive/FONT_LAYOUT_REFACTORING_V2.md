# Font and Layout System Refactoring (Simplified)

**Date:** 2025-12-12
**Status:** PROPOSAL
**Principle:** Keep it simple - LayoutEngine predicts layout, font metrics are just data it needs

## The Real Problem

```python
# font_metrics.py
FONT_POINT_SIZE = 29.5  # ← What does this even mean?

# Should be:
FONT_SIZE_PT = 10.0  # Typographic points
pixel_size = FONT_SIZE_PT * document_ppi / 72  # Convert to pixels
```

**That's it.** No new classes, just fix the calculation and naming.

## Simple Fix

### 1. Update `font_metrics.py` to accept DPI

```python
# font_metrics.py

# Discovered through calibration (Phase 2)
DEVICE_FONT_SIZE_PT = 10.0  # Real typographic points

def char_width(
    char: str,
    font_size_pt: float = DEVICE_FONT_SIZE_PT,
    document_ppi: int = 226,  # From DeviceGeometry
) -> float:
    """Get character width in document pixels.

    Args:
        char: Character to measure
        font_size_pt: Font size in typographic points (1/72 inch)
        document_ppi: Document coordinate DPI

    Returns:
        Width in document pixels
    """
    # Convert points to pixels: pixel_size = pt × DPI / 72
    pixel_size = font_size_pt * document_ppi / 72.0

    cmap, glyphset, units_per_em = _load_font()
    glyph_name = cmap.get(ord(char))

    if glyph_name and glyph_name in glyphset:
        return glyphset[glyph_name].width * pixel_size / units_per_em

    # Fallback...
    return pixel_size * 0.5
```

### 2. Update `WordWrapLayoutEngine` to pass DPI

```python
# layout/engine.py

class WordWrapLayoutEngine:
    def __init__(
        self,
        text_width: float,
        avg_char_width: float,
        line_height: float,
        use_font_metrics: bool = False,
        geometry: DeviceGeometry | None = None,
    ):
        self.text_width = text_width
        self.avg_char_width = avg_char_width
        self.line_height = line_height
        self.use_font_metrics = use_font_metrics
        self._geometry = geometry or DEFAULT_DEVICE
        self._text_width_fn: Callable[[str], float] | None = None

        if use_font_metrics:
            try:
                from rock_paper_sync.font_metrics import text_width as font_text_width

                # Create a wrapper that passes DPI
                def _width_with_dpi(text: str) -> float:
                    return font_text_width(
                        text,
                        font_size_pt=self._geometry.font_point_size,
                        document_ppi=self._geometry.document_ppi,
                    )

                self._text_width_fn = _width_with_dpi
            except Exception:
                self._text_width_fn = None
```

### 3. Update `DeviceGeometry.font_point_size` meaning

```python
# layout/device.py

@dataclass(frozen=True)
class DeviceGeometry:
    # ...

    # Font metrics
    # CHANGED: Now represents ACTUAL typographic points, not pixel hack
    font_point_size: float  # Typographic points (e.g., 10.0pt)

    # ...

    @property
    def font_pixel_size(self) -> float:
        """Font size in document pixels.

        Converts typographic points to pixels:
            pixels = points × document_ppi / 72
        """
        return self.font_point_size * self.document_ppi / 72.0
```

### 4. Calibrate the actual font size

Current: `font_point_size=29.5` (empirical pixel hack)
Need to find: What typographic point size does device actually use?

**Hypothesis:** Device probably uses a standard size like 10pt, 11pt, or 12pt
- 10pt @ 226 DPI = 31.4 pixels
- 11pt @ 226 DPI = 34.5 pixels
- 12pt @ 226 DPI = 37.7 pixels

The current `29.5` suggests it's NOT a standard size, OR there's additional scaling we don't know about.

## Calibration Documents Needed

### Priority 1: Font Size Discovery

**`calibration_font_sizes.md`:**
```markdown
# Font Size Calibration

Test strings at known point sizes to discover device's actual font size.

## Test String (25 m's for measurement)
mmmmmmmmmmmmmmmmmmmmmmmmm

Expected widths at 226 DPI, Noto Sans Regular:
- 8pt: ~309px (m ≈ 12.4px)
- 10pt: ~442px (m ≈ 17.7px)
- 12pt: ~531px (m ≈ 21.2px)
- 14pt: ~619px (m ≈ 24.8px)

Highlight the entire string of m's. We'll measure its width from the highlight
rectangle and compare to predictions.
```

**Process:**
1. Generate doc with test string
2. Upload, highlight the "mmmmm..." string
3. Download, extract highlight width
4. Compare: which point size prediction matches?

### Priority 2: Character Width Validation

**Enhance `calibration_chars.md`:**
- Keep existing content
- Add runs of same character for measurement:
  - `iiiiiiiiiiiiiiiiiiii` (20 i's - narrow)
  - `mmmmmmmmmmmmmmmmmmmm` (20 m's - wide)
  - `                    ` (20 spaces - critical!)

**Process:**
1. Highlight each run
2. Measure total width
3. Divide by character count
4. Compare to font_metrics predictions

### Priority 3: Word Wrap Edge Cases

**Enhance `calibration_wrap.md`:**
```markdown
# Word Wrap Calibration

Highlight the 'x' at START and 'y' at END of each test line.

## Test 1: Exact boundary (incremental)
x mmmmmmmmmmmmmmmmmmmmmmmm y
x mmmmmmmmmmmmmmmmmmmmmmmmm y
x mmmmmmmmmmmmmmmmmmmmmmmmmm y
x mmmmmmmmmmmmmmmmmmmmmmmmmmm y

Which line does 'y' wrap to next line?

## Test 2: Space at boundary
x word word word word word y
x word word word word word  y
x word word word word word   y

How many spaces before 'y' wraps?

## Test 3: Word too long for line
x supercalifragilisticexpialidocious y

Does 'y' stay on same line or wrap?

## Test 4: Punctuation
x word word word word word, y
x word word word word word. y

Does punctuation affect wrap?
```

### Priority 4: Structure Spacing

**Enhance `calibration_structure.md`:**
- Keep existing simple structure
- Add Y-position markers:
  - Highlight each 'e' character
  - Measure Y positions
  - Calculate spacing differences

## Implementation Steps

### Step 1: Discover Device Font Size (1 session)
1. Create `calibration_font_sizes.md`
2. Run calibration capture workflow
3. Measure highlight width
4. Determine actual point size used

**Expected result:**
- "Device uses 10.2pt" (or whatever)
- Update `PAPER_PRO_MOVE.font_point_size = 10.2`

### Step 2: Fix Font Metrics (30 minutes)
1. Update `font_metrics.py` to accept `document_ppi`
2. Update `WordWrapLayoutEngine` to pass DPI
3. Add `DeviceGeometry.font_pixel_size` property
4. Run existing tests - should still pass

### Step 3: Validate Character Widths (1 session)
1. Enhance `calibration_chars.md`
2. Capture golden file with highlights
3. Extract character widths
4. Compare to `char_width()` predictions
5. Debug any mismatches

### Step 4: Validate Word Wrap (1 session)
1. Enhance `calibration_wrap.md`
2. Capture edge case data
3. Test against `calculate_line_breaks()`
4. Fix any prediction errors

### Step 5: Validate Structure (if needed)
- Current structure tests passing
- Only needed if we find spacing issues

## What This Solves

✅ **Conceptual clarity** - Points are points, pixels are pixels
✅ **DPI-aware** - Proper use of 226 DPI discovery
✅ **Testable** - Can verify at each point size
✅ **Simple** - No new classes, just fix the math
✅ **Maintainable** - Self-documenting variable names

## What We'll Learn

From **Step 1** (font size calibration):
- Does device use standard point size (10pt, 12pt)?
- Or non-standard size (10.2pt, 11.7pt)?
- Any zoom/scaling we don't know about?

From **Step 3** (char width validation):
- Is our Noto Sans the exact same as device's?
- Any hinting differences?
- Which characters have largest errors?

From **Step 4** (wrap validation):
- Exact wrap width (is 758px correct?)
- Space handling algorithm
- Long word behavior
- Punctuation rules

## Success Criteria

- [ ] Font size in real typographic points
- [ ] `font_pixel_size` property for clarity
- [ ] Character width predictions within 5%
- [ ] Word wrap matches device edge cases
- [ ] All existing tests still pass
- [ ] No new classes/abstractions

## Open Questions

1. **Is the current 29.5 "close enough" to a standard size?**
   - 29.5 pixels ≈ 9.4pt @ 226 DPI
   - Maybe device uses 9.5pt or 10pt with scaling?
   - Calibration will tell us

2. **Why does empirical calibration work without DPI?**
   - Because we measured against 226 DPI output
   - But now we can MODEL it instead of guessing

3. **Is there display scaling we don't know about?**
   - Device might have 1.1× or 1.2× text scaling
   - Font size calibration will reveal this

## Bottom Line

**Before:** `FONT_POINT_SIZE = 29.5` (empirical pixel hack, works but confusing)

**After:** `font_point_size = 10.2` (real typographic points, DPI-converted)

Same behavior, but now we:
- Understand WHY it works
- Can debug issues systematically
- Can test different configurations
- Have proper abstractions
