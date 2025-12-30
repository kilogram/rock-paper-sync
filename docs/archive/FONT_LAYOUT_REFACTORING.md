# Font and Layout System Refactoring

**Date:** 2025-12-12
**Status:** PROPOSAL
**Context:** Now that we've discovered document coordinates use 226 DPI, we can properly model font rendering and word wrap

## Problem Statement

Despite empirical calibrations, we still have word wrap prediction issues. The root causes:

### 1. **Conceptual Confusion: Points vs Pixels**

```python
# font_metrics.py
FONT_POINT_SIZE = 29.5  # ← Misleading name!

def char_width(char: str, point_size: float = FONT_POINT_SIZE) -> float:
    """Get width in pixels at given point size."""  # ← Doc says "point size"
    return glyphset[char].width * point_size / units_per_em  # ← But used as pixels!
```

**The problem:**
- Variable named `FONT_POINT_SIZE` but used as direct pixel scaling
- No DPI conversion in the calculation
- Empirically calibrated to "make it work" without proper typography model

**Why it matters:**
- Makes it hard to reason about font sizes
- Difficult to validate against device specs
- Can't properly test at different zoom levels
- Hard to debug word wrap mismatches

### 2. **Indirect Calibration**

Current approach:
1. Generate document with guessed font size
2. Upload to device
3. Manually measure with ruler/highlights
4. Adjust font size until it matches
5. Repeat

**Problems:**
- Time-consuming iteration
- No way to validate intermediate steps
- Can't test different font sizes programmatically
- Hard to distinguish font size errors from layout errors

### 3. **Missing Calibration Data**

**What we have:**
- ✅ `calibration_geometry.rm` - Physical ruler (71.8mm) validates DPI
- ✅ `calibration_wrap.rm` - Word wrap boundary tests
- ⚠️ `calibration_chars.md` - Character width tests (no golden .rm yet)
- ⚠️ `calibration_structure.md` - Structure spacing (no golden .rm yet)

**What's missing:**
- ❌ Font size verification (at known point sizes)
- ❌ Proportional font width validation (per-character)
- ❌ Baseline and line height validation
- ❌ Space width validation
- ❌ Multi-line word wrap validation
- ❌ Edge cases (long words, punctuation, etc.)

## Proposed Solution

### Phase 1: Proper Typography Model

**Introduce `FontRenderer` class** that properly models DPI:

```python
@dataclass(frozen=True)
class FontRenderer:
    """Proper typographic font rendering with DPI awareness.

    This class models the relationship between:
    - Typographic points (1/72 inch)
    - Document pixels (at document_ppi)
    - Font metrics (in font units)

    Example:
        # 10pt font at 226 DPI
        renderer = FontRenderer.from_device(
            geometry=PAPER_PRO_MOVE,
            font_point_size=10.0  # Real typographic points!
        )

        width = renderer.char_width('A')  # Returns pixels at 226 DPI
    """

    font_path: Path
    font_point_size: float  # Typographic points (1/72 inch)
    document_ppi: int       # Document coordinate DPI (e.g., 226)

    # Computed
    _font: TTFont = field(init=False, repr=False)
    _cmap: dict = field(init=False, repr=False)
    _glyphset: Any = field(init=False, repr=False)
    _units_per_em: int = field(init=False, repr=False)

    @classmethod
    def from_device(
        cls,
        geometry: DeviceGeometry,
        font_point_size: float,
        font_path: Path | None = None,
    ) -> FontRenderer:
        """Create font renderer for a device.

        Args:
            geometry: Device geometry (provides document_ppi)
            font_point_size: Font size in typographic points
            font_path: Path to font file (defaults to Noto Sans)

        Returns:
            FontRenderer configured for the device
        """
        if font_path is None:
            font_path = _find_noto_sans()

        return cls(
            font_path=font_path,
            font_point_size=font_point_size,
            document_ppi=geometry.document_ppi,
        )

    @property
    def pixel_size(self) -> float:
        """Font size in document pixels.

        Converts typographic points to pixels using document DPI:
            pixel_size = point_size × document_ppi / 72

        Example:
            10pt at 226 DPI = 10 × 226 / 72 ≈ 31.4 pixels
        """
        return self.font_point_size * self.document_ppi / 72.0

    def char_width(self, char: str) -> float:
        """Get character width in document pixels.

        Args:
            char: Single character

        Returns:
            Width in document pixels
        """
        glyph_name = self._cmap.get(ord(char))
        if glyph_name and glyph_name in self._glyphset:
            return (
                self._glyphset[glyph_name].width
                * self.pixel_size
                / self._units_per_em
            )

        # Fallback to space width
        space_glyph = self._cmap.get(ord(" "))
        if space_glyph:
            return (
                self._glyphset[space_glyph].width
                * self.pixel_size
                / self._units_per_em
            )

        # Last resort
        return self.pixel_size * 0.5  # Rough average

    def text_width(self, text: str) -> float:
        """Get total width of text in document pixels."""
        return sum(self.char_width(c) for c in text)
```

**Migration:**
```python
# OLD (font_metrics.py):
FONT_POINT_SIZE = 29.5  # Confusing!
char_width('A', point_size=FONT_POINT_SIZE)

# NEW:
renderer = FontRenderer.from_device(PAPER_PRO_MOVE, font_point_size=10.0)
renderer.char_width('A')  # Clear!
```

### Phase 2: Calibrate Font Size

**Add `calibration_font_sizes.md`:**

```markdown
# Font Size Calibration

Test text at known typographic point sizes to find the device's actual font size.

## 8pt Test
The quick brown fox jumps over the lazy dog.

## 10pt Test
The quick brown fox jumps over the lazy dog.

## 12pt Test
The quick brown fox jumps over the lazy dog.

## 14pt Test
The quick brown fox jumps over the lazy dog.

## Reference String (for measurement)
mmmmmmmmmmmmmmmmmmmmmmmmm

At 10pt, 226 DPI, Noto Sans:
- 'm' width ≈ 17.7px
- 25 'm's ≈ 442px
```

**Calibration process:**
1. Generate document at 8pt, 10pt, 12pt, 14pt
2. User highlights entire reference string ("mmmm...") at each size
3. Extract highlight widths from .rm file
4. Compare against model predictions
5. Determine which point size matches device

**Result:** Discover device uses "X.Xpt" font, can now model accurately

### Phase 3: Enhanced Character Width Calibration

**Improve `calibration_chars.md`:**

```markdown
# Character Width Calibration

Highlight each character individually to validate font metrics.

## Critical Characters (affects word wrap most)

### Space Variations
| |   (1 space)
||    (2 spaces)
|||     (3 spaces)

### Narrow Characters
iiiiiiiiiiiiiiiiiiii (20 i's)
llllllllllllllllllll (20 l's)
111111111111111111111 (20 1's)

### Medium Characters
aaaaaaaaaaaaaaaaaaaa (20 a's)
eeeeeeeeeeeeeeeeeeee (20 e's)
nnnnnnnnnnnnnnnnnnnn (20 n's)

### Wide Characters
mmmmmmmmmmmmmmmmmmmm (20 m's)
wwwwwwwwwwwwwwwwwwww (20 w's)
MMMMMMMMMMMMMMMMMMMM (20 M's)
WWWWWWWWWWWWWWWWWWWW (20 W's)

## Alphabet Coverage
a b c d e f g h i j k l m n o p q r s t u v w x y z
A B C D E F G H I J K L M N O P Q R S T U V W X Y Z

## Numbers
0 1 2 3 4 5 6 7 8 9

## Common Punctuation
. , ; : ! ? ' " - – —

## Brackets and Symbols
( ) [ ] { } < > / \ | @ # $ % ^ & *
```

**Validation:**
```python
def test_char_widths_match_device(calibration_data):
    """Verify our font renderer matches device character widths."""
    renderer = FontRenderer.from_device(
        PAPER_PRO_MOVE,
        font_point_size=10.0  # From Phase 2 calibration
    )

    for char, device_width in calibration_data.items():
        model_width = renderer.char_width(char)
        error_pct = abs(model_width - device_width) / device_width * 100

        assert error_pct < 5.0, (
            f"Character '{char}': model={model_width:.1f}px, "
            f"device={device_width:.1f}px, error={error_pct:.1f}%"
        )
```

### Phase 4: Word Wrap Validation

**Enhanced `calibration_wrap.md`:**

```markdown
# Word Wrap Calibration

Highlight the FIRST and LAST character of each line to verify wrap points.

Layout width: 758.0px
Font size: 10.0pt (or whatever Phase 2 determined)

## Exact Boundary Tests

### Test 1: Gradual approach to boundary
Line 1: x mmmmmmmmmmmmmmmmmmmmmmmm y
Line 2: x mmmmmmmmmmmmmmmmmmmmmmmmm y
Line 3: x mmmmmmmmmmmmmmmmmmmmmmmmmm y
Line 4: x mmmmmmmmmmmmmmmmmmmmmmmmmmm y
Line 5: x mmmmmmmmmmmmmmmmmmmmmmmmmmmm y

Highlight: 'x' and 'y' on each line.
Expected: 'y' wraps when total width > 758px

### Test 2: Space handling at boundary
Line 1: word word word word word word end
Line 2: word word word word word word  end
Line 3: word word word word word word   end

Highlight: 'w' in first "word" and 'e' in "end"
Expected: Space before "end" determines wrap

### Test 3: Long word handling
x antidisestablishmentarianism y
x pneumonoultramicroscopicsilicovolcanoconiosis y

Highlight: 'x' and 'y'
Expected: Long word behavior (overflow vs break)

### Test 4: Punctuation at boundary
Line 1: word word word word word word, end
Line 2: word word word word word word. end
Line 3: word word word word word word! end

Highlight: 'w' in first "word" and 'e' in "end"
```

**Validation:**
```python
def test_word_wrap_matches_device(wrap_calibration_rm):
    """Verify our layout engine matches device word wrap."""
    renderer = FontRenderer.from_device(PAPER_PRO_MOVE, font_point_size=10.0)
    engine = WordWrapLayoutEngine(
        text_width=758.0,
        font_renderer=renderer,  # ← Use proper font renderer
    )

    for test_case in extract_wrap_tests(wrap_calibration_rm):
        # Get device wrap point (from highlight positions)
        device_wrapped = test_case.line1_ends_with_space

        # Get model prediction
        model_breaks = engine.calculate_line_breaks(test_case.text, 758.0)
        model_wrapped = len(model_breaks) > 1

        assert device_wrapped == model_wrapped, (
            f"Wrap mismatch for: {test_case.text[:50]}..."
            f"Device wrapped: {device_wrapped}, Model: {model_wrapped}"
        )
```

### Phase 5: Structural Spacing

**Enhanced `calibration_structure.md`:**

```markdown
# Structural Spacing Calibration

Highlight the 'x' in each line to measure vertical spacing.

## Paragraph Spacing
x First paragraph.

x Second paragraph.

x Third paragraph.

## List Spacing
x Regular text

- x List item 1
- x List item 2

x Text after list

## Heading Spacing
x Regular text

# x Heading 1

x Text after heading 1

## x Heading 2

x Text after heading 2

### x Heading 3

x Text after heading 3

## Nested Lists
- x Level 1
  - x Level 2
    - x Level 3

## Mixed Content
x Paragraph

1. x Numbered item 1
2. x Numbered item 2

- [ ] x Checkbox 1
- [x] x Checkbox 2

x Final paragraph
```

**Validation:**
```python
def test_structure_spacing_matches_device(structure_rm):
    """Verify paragraph/list spacing matches device."""
    highlights = extract_highlights(structure_rm)

    # Measure actual spacing from highlights
    para_spacing = highlights['second_para_y'] - highlights['first_para_y']
    list_indent = highlights['list_item_x'] - highlights['para_x']

    # Compare to model
    assert abs(para_spacing - geometry.line_height) < 5.0
    assert abs(list_indent - 30.0) < 5.0  # Or whatever the spec is
```

## Implementation Plan

### Step 1: Add FontRenderer class
- [ ] Create `src/rock_paper_sync/layout/font_renderer.py`
- [ ] Implement proper point → pixel conversion
- [ ] Add tests for DPI calculations
- [ ] Validate against known font metrics

### Step 2: Calibrate Font Size
- [ ] Create `calibration_font_sizes.md`
- [ ] Generate test document at 8pt, 10pt, 12pt, 14pt
- [ ] User highlights reference strings
- [ ] Extract highlight widths
- [ ] Determine device's actual font size
- [ ] Update `DeviceGeometry.font_point_size` with proper typographic value

### Step 3: Validate Character Widths
- [ ] Enhance `calibration_chars.md`
- [ ] User highlights individual characters
- [ ] Extract character widths from highlights
- [ ] Compare against `FontRenderer` predictions
- [ ] Verify error < 5% for all characters

### Step 4: Validate Word Wrap
- [ ] Enhance `calibration_wrap.md`
- [ ] Add boundary tests, space tests, long word tests
- [ ] User highlights wrap points
- [ ] Compare against `WordWrapLayoutEngine` predictions
- [ ] Fix any discrepancies

### Step 5: Validate Structure
- [ ] Enhance `calibration_structure.md`
- [ ] User highlights structure markers
- [ ] Measure spacing from highlights
- [ ] Validate against `DeviceGeometry` parameters

### Step 6: Update Layout Engine
- [ ] Modify `WordWrapLayoutEngine` to accept `FontRenderer`
- [ ] Remove hardcoded `FONT_POINT_SIZE`
- [ ] Update all font size references to use proper typography
- [ ] Migrate `font_metrics.py` to use `FontRenderer`

### Step 7: Regression Testing
- [ ] Run all calibration tests
- [ ] Verify existing documents still render correctly
- [ ] Check annotation positioning accuracy
- [ ] Validate cross-page reanchoring

## Benefits

1. **Correctness**
   - Proper typographic model (points vs pixels)
   - DPI-aware calculations
   - Testable at different font sizes

2. **Debuggability**
   - Clear separation of concerns
   - Can validate each layer independently
   - Explicit DPI conversions

3. **Maintainability**
   - Well-named variables (`font_point_size`, `pixel_size`, `document_ppi`)
   - Self-documenting code
   - Easy to add new devices

4. **Extensibility**
   - Can test different fonts
   - Can support device-specific font sizes
   - Can add zoom levels later

## Open Questions

1. **Does the device use a specific point size, or scale dynamically?**
   - Need to test at device zoom levels (1.0x, 1.5x, 2.0x)
   - Calibration will answer this

2. **Are there device-specific font customizations?**
   - Might be using modified Noto Sans
   - Might have custom hinting
   - Character width tests will reveal this

3. **How does word wrap handle edge cases?**
   - Very long words (break or overflow?)
   - Punctuation at boundaries
   - Multiple spaces
   - Wrap tests will answer this

4. **What's the baseline positioning?**
   - Current `baseline_offset = 25.0` is empirical
   - Should validate with actual baseline measurements
   - Add baseline calibration test?

## Success Criteria

- [ ] Font size specified in real typographic points (not pixel hack)
- [ ] Character width predictions within 5% of device
- [ ] Word wrap matches device 100% of the time (for test cases)
- [ ] Structural spacing within 5px of device
- [ ] All existing tests still pass
- [ ] Clear documentation of typography model

## Timeline

- **Phase 1-2:** 1 session (add FontRenderer, calibrate font size)
- **Phase 3:** 1 session (validate character widths)
- **Phase 4:** 1 session (validate word wrap)
- **Phase 5:** 1 session (validate structure)
- **Phase 6-7:** 1 session (migration and testing)

**Total: ~5 focused sessions**
