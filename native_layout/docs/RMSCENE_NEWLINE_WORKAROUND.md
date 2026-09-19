# rmscene Newline Support Workaround

## Problem

The reMarkable Paper Pro uses **format code 10** to represent newlines/paragraph breaks in `.rm` files. However, rmscene (as of the current version) does not recognize this format code and falls back to `ParagraphStyle.PLAIN`.

### Evidence

When inspecting real `.rm` files created by reMarkable devices:
```
Unrecognised text format code 10.
```

### Current rmscene ParagraphStyle Codes

```python
class ParagraphStyle(IntEnum):
    BASIC = 0
    PLAIN = 1
    HEADING = 2
    BOLD = 3
    BULLET = 4
    BULLET2 = 5
    CHECKBOX = 6
    CHECKBOX_CHECKED = 7
    # Code 10 is MISSING - this is the newline marker
```

## Impact

Without proper newline formatting:
- Multi-paragraph documents may not render with correct paragraph breaks
- Text that should be on separate lines may run together
- The reMarkable device may not preserve paragraph structure correctly

## Workaround Implementation

Since rmscene doesn't yet support format code 10, we implement a workaround in `generator.py`:

### Approach 1: Monkey Patch (Current Implementation)

```python
from enum import IntEnum
import rmscene.scene_items as si

# Extend ParagraphStyle with newline support
if not hasattr(si.ParagraphStyle, 'NEWLINE'):
    # Dynamically add NEWLINE = 10 to the enum
    si.ParagraphStyle = IntEnum(
        'ParagraphStyle',
        {**{item.name: item.value for item in si.ParagraphStyle}, 'NEWLINE': 10}
    )
```

### Approach 2: Direct Style Injection

When creating `RootTextBlock`, add style entries for each newline:

```python
# Build styles dictionary with newlines
styles = {CrdtId(0, 0): LwwValue(timestamp=CrdtId(1, 15), value=si.ParagraphStyle.PLAIN)}

# Add newline style for each \n character
text_offset = 0
for i, char in enumerate(combined_text):
    if char == '\n':
        styles[CrdtId(0, i)] = LwwValue(
            timestamp=CrdtId(1, 15),
            value=10  # Use raw int 10 instead of enum
        )

# Use in RootTextBlock
RootTextBlock(
    block_id=CrdtId(0, 0),
    value=si.Text(
        items=CrdtSequence([...]),
        styles=styles,  # Now includes newline markers
        pos_x=self.TEXT_POS_X,
        pos_y=self.TEXT_POS_Y,
        width=self.TEXT_WIDTH,
    ),
)
```

## Upstream Fix Needed

This workaround should be temporary. The proper fix is to:

1. **Submit PR to rmscene**: Add `NEWLINE = 10` to `ParagraphStyle` enum
2. **Update rmscene parser**: Recognize format code 10 when reading `.rm` files
3. **Add tests**: Verify newline formatting is preserved round-trip

### Suggested rmscene Changes

**File**: `rmscene/scene_items.py`

```python
class ParagraphStyle(IntEnum):
    """Paragraph formatting styles."""
    BASIC = 0
    PLAIN = 1
    HEADING = 2
    BOLD = 3
    BULLET = 4
    BULLET2 = 5
    CHECKBOX = 6
    CHECKBOX_CHECKED = 7
    # ADD THIS:
    NEWLINE = 10  # Paragraph break / newline marker
```

**File**: `rmscene/scene_stream.py`

Update the format code parser to handle 10 without warnings:

```python
try:
    format_type = si.ParagraphStyle(format_code)
except ValueError:
    # Only warn for truly unknown codes (not 10)
    if format_code != 10:
        _logger.warning("Unrecognised text format code %d.", format_code)
    format_type = si.ParagraphStyle.NEWLINE if format_code == 10 else si.ParagraphStyle.PLAIN
```

## Testing

After implementing the workaround, verify:

1. **Multi-paragraph documents render correctly** on reMarkable device
2. **Paragraph breaks are preserved** when syncing back from device
3. **No warnings** about unrecognized format code 10
4. **Round-trip integrity**: Upload → Annotate → Download preserves structure

### Test Document

```markdown
# Test Document

First paragraph.

Second paragraph.

Third paragraph.
```

Expected `.rm` file should have format code 10 at each newline position.

## Timeline

- **Short-term**: Use workaround in rock-paper-sync
- **Medium-term**: Submit PR to rmscene maintainers
- **Long-term**: Remove workaround once rmscene release includes NEWLINE support

## References

- rmscene library: https://github.com/ricklupton/rmscene
- reMarkable v6 format: https://docs.reMarkable.com (if available)
- This workaround: `src/rock_paper_sync/generator.py`

---

**Last Updated**: 2025-11-27
**rmscene Version**: Current (pre-NEWLINE support)
**Status**: Workaround active, upstream fix pending
