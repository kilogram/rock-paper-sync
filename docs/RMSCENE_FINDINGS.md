# rmscene Library - Findings and Limitations

This document summarizes the findings from integrating the rmscene library (v0.7.0) for generating reMarkable v6 format files.

## Overview

The rmscene library provides Python bindings for reading and writing reMarkable's binary .rm files (v6 format, firmware 3.0+). It supports both reading existing files and creating new ones with text content.

## Key Components

### Core Modules

1. **`rmscene.scene_items`** - Data structures for scene elements:
   - `Text` - Positioned text blocks with CRDT sequences
   - `Line` - Pen strokes
   - `Group` - Grouping elements
   - `GlyphRange` - Highlighted text regions
   - `ParagraphStyle` - Text styling enum (BASIC, PLAIN, HEADING, BOLD, BULLET, etc.)

2. **`rmscene.write_blocks`** - Serialization to binary format:
   - `write_blocks(buffer, blocks)` - Writes block iterator to BytesIO buffer

3. **`rmscene.read_blocks`** - Parsing binary format:
   - `read_blocks(buffer)` - Reads blocks from BytesIO buffer

4. **`rmscene.crdt_sequence`** - CRDT (Conflict-free Replicated Data Type) sequences:
   - `CrdtSequence` - Stores text as CRDT for collaborative editing
   - `CrdtId` - Unique identifiers for CRDT operations

### Helper Functions

- **`simple_text_document(text, author_uuid=None)`** - Generates all necessary blocks for a basic text document
  - Returns: Iterator of blocks (AuthorIdsBlock, MigrationInfoBlock, PageInfoBlock, SceneTreeBlock, RootTextBlock, TreeNodeBlock, SceneGroupItemBlock)
  - Limitation: Only creates a single text block, no custom positioning

## Implementation Approach

### What We Use (Current)

We now use **custom scene tree construction** with optimized text width for proper display on the Paper Pro:

```python
def generate_rm_file(self, page: RemarkablePage) -> bytes:
    # Combine all text items into a single text block
    combined_text = "\n".join(item.text for item in page.text_items)

    # Generate blocks manually with custom text width (750px)
    blocks = [
        AuthorIdsBlock(...),
        MigrationInfoBlock(...),
        PageInfoBlock(...),
        SceneTreeBlock(...),
        RootTextBlock(
            value=si.Text(
                items=CrdtSequence([...]),
                styles={...},
                pos_x=-375.0,  # Centered
                pos_y=234.0,
                width=750.0,   # Optimized for 1.0x display
            ),
        ),
        # ... additional blocks
    ]
```

**Why this approach?**
- ✅ Displays at 1.0x zoom on Paper Pro (vs 0.8x with default 936px width)
- ✅ No user configuration needed for margins/layout
- ✅ Generates valid v6 files that reMarkable devices can read
- ✅ Works with rmscene's experimental write API
- ✅ Preserves text content accurately

**Note**: Previously used `simple_text_document()` which defaulted to 936px width,
causing the device to zoom out to 0.8x to fit content. Custom scene tree construction
allows precise control over text width.

### What We Haven't Used (Yet)

Advanced positioning and formatting capabilities are available but not fully utilized:

1. **Multiple Text items per page** - Currently we combine all text into one block. We could create separate `Text` items for better layout control and precise positioning of individual elements.

2. **Inline formatting** (bold, italic) - See limitations below.

**What We Now Use:**

✅ **Custom text width** - We set `width=750.0` for optimal 1.0x display on Paper Pro
✅ **Custom positioning** - We set `pos_x=-375.0` (centered) and `pos_y=234.0` for proper placement

## Key Findings

### ✅ What Works Well

1. **Basic text generation** - Plain text documents are fully supported
2. **Multi-line text** - Newlines are preserved correctly
3. **Round-trip validation** - Generated files can be read back with `read_blocks()`
4. **File structure** - The complete v6 file structure is correctly generated:
   - Header: `reMarkable .lines file, version=6`
   - Multiple block types (8 blocks for simple documents)
   - Valid binary serialization

### ⚠️ Limitations and Workarounds

#### 1. Inline Formatting (Bold/Italic)

**Limitation**: reMarkable's native format uses **paragraph-level** styling, not character-level formatting.

**Evidence**:
- `ParagraphStyle` enum has values like `BOLD`, `ITALIC`, `HEADING`
- These apply to entire paragraphs, not inline ranges
- `GlyphRange` is for highlighting (not bold/italic)

**Our Approach**:
- ✅ Preserve formatting in `TextItem.formatting` list for future use
- ✅ Store plain text with formatting metadata
- ❌ Don't attempt to render inline bold/italic (not supported)

**Future Possibilities**:
- Split paragraphs with different formatting into separate `Text` items
- Use different `ParagraphStyle` values for each item
- Would require manual scene tree construction

#### 2. Custom Text Positioning

**Status**: ✅ **Now Implemented**

We manually construct the scene tree to set custom width and positioning:

```python
RootTextBlock(
    block_id=CrdtId(0, 0),
    value=si.Text(
        items=CrdtSequence([...]),
        styles={...},
        pos_x=-375.0,  # Centered on page
        pos_y=234.0,   # Standard top position
        width=750.0,   # Optimized for 1.0x display
    ),
)
```

**Benefits**:
- ✅ Control over text width for proper display zoom
- ✅ Centered positioning on the page
- ✅ Automatic - no user configuration needed

**Future Enhancement**:
- Create multiple Text items per page for precise element positioning
- Use calculated positions from `blocks_to_text_items()` (currently stored but unused)

#### 3. Write API is Experimental

**Status**: rmscene's write capabilities are marked as experimental.

**Implications**:
- ✅ Works reliably for basic use cases (confirmed by tests)
- ⚠️ API may change in future versions
- ⚠️ Limited documentation
- ⚠️ Edge cases may not be handled

**Mitigation**:
- Pin to specific rmscene version (0.7.0+)
- Comprehensive test coverage (32 tests)
- Round-trip validation in tests

#### 4. No Font/Size Control

**Limitation**: reMarkable handles font and size at the UI level, not in the .rm file.

**Impact**:
- Cannot specify font family
- Cannot specify font size
- Text scale is set in `.content` file (document-level, not per-text-item)

**Workaround**:
- Use `.content` metadata `textScale` field (default: 1.0)
- Users can adjust on device

## Block Structure

A complete .rm file contains these blocks (in order):

1. **AuthorIdsBlock** - Author UUID mapping
2. **MigrationInfoBlock** - Migration version info
3. **PageInfoBlock** - Page-level metadata
4. **SceneTreeBlock** - Scene tree structure
5. **RootTextBlock** - Root text content container
6. **TreeNodeBlock(s)** - Tree hierarchy nodes
7. **SceneGroupItemBlock** - Scene item grouping

Generated file size: ~350-400 bytes for minimal text, scales with content.

## Performance

- **Generation speed**: Very fast (~0.001s per page in tests)
- **File size**: Efficient binary format
- **Memory**: Minimal - uses iterators where possible

## Validation

We validate generated files by:

1. **Header check**: Files start with `reMarkable .lines file, version=6`
2. **Round-trip parsing**: `read_blocks()` can parse generated files
3. **Block count**: Correct number of blocks present
4. **Content preservation**: Text is accurately stored

## Recommendations

### Current Implementation ✅

**Custom scene tree construction with optimized width**
- ✅ Displays at 1.0x zoom on Paper Pro
- ✅ Simple, reliable, well-tested
- ✅ Automatic - no user configuration needed
- ✅ Sufficient for basic text conversion with proper display

### For Phase 2 (Future)

Consider enhanced scene tree construction for:
- Multiple text items per page (precise element positioning)
- Per-element width and positioning based on `blocks_to_text_items()` calculations
- Paragraph-level formatting (BOLD, HEADING styles)
- Better handling of complex documents

Would require:
- Creating separate Text items for each block
- Managing multiple CrdtSequence instances
- Extensive testing on actual device

## Testing

### Coverage

- **metadata.py**: 100% coverage (23 tests)
- **generator.py**: 99.27% coverage (32 tests)
- **Total**: 55 tests, 99.33% overall coverage

### Test Categories

1. **Unit tests** - Individual functions (pagination, line estimation, etc.)
2. **Integration tests** - Full pipeline (parse → generate → write)
3. **Round-trip tests** - Generate and parse back
4. **Validation tests** - File structure correctness

### Manual Testing Needed

⚠️ **IMPORTANT**: Generated files should be tested on an actual reMarkable device:

1. Transfer files to device via Syncthing
2. Verify documents appear in UI
3. Check text rendering
4. Test pagination
5. Verify multi-page documents
6. Check special characters and Unicode

## Known Issues

None currently. The implementation works reliably for basic text documents.

## Resources

- **rmscene GitHub**: https://github.com/ricklupton/rmscene
- **rmscene PyPI**: https://pypi.org/project/rmscene/
- **reMarkable format docs**: Limited public documentation
- **Related tools**: rmc (reMarkable console tool using rmscene)

## Conclusion

The rmscene library provides a solid foundation for generating reMarkable v6 files. While it has limitations (especially around inline formatting and custom positioning), it's perfectly suitable for Phase 1 of the project.

Our implementation prioritizes:
- **Reliability** over features
- **Text preservation** over visual fidelity
- **Simplicity** over complexity

This allows us to get markdown content onto reMarkable devices quickly and correctly, with room for future enhancements.
