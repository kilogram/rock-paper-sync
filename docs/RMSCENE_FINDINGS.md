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

## Block Ordering Requirements (Critical)

**IMPORTANT**: The reMarkable device requires strict block ordering. Blocks must appear
in a specific sequence or the device will fail with errors like:

```
rm.scene.tree  Unable to find node with id=0:11, but it should be present
rm.scene.tree  Unable to find node with id=2:299, but it should be present
```

### Required Block Order

The device expects blocks in this exact order:

```
1. Header blocks (must come first):
   - AuthorIdsBlock
   - MigrationInfoBlock
   - PageInfoBlock
   - SceneInfo (if present)

2. ALL SceneTreeBlocks (declarations):
   - System nodes first: tree_id=0:11 (Layer 1)
   - User nodes: tree_id=2:xxx (annotation nodes)

3. RootTextBlock (text content)

4. ALL TreeNodeBlocks (definitions):
   - System nodes first: node_id=0:1 (root), node_id=0:11 (Layer 1)
   - User nodes: node_id=2:xxx (annotation nodes)

5. ALL SceneGroupItemBlocks (hierarchy links):
   - System links first: value=0:11 (links Layer 1 to root)
   - User links: value=2:xxx (links annotations to Layer 1)

6. ALL annotation blocks (at the end):
   - SceneLineItemBlock (strokes)
   - SceneGlyphItemBlock (highlights)
```

### Why Order Matters

The device processes blocks sequentially. When it encounters a reference to a node:
- `SceneTreeBlock.parent_id` references parent in scene tree
- `SceneGroupItemBlock.parent_id` references the parent group
- `SceneGroupItemBlock.value` references the TreeNodeBlock it links
- `SceneLineItemBlock.parent_id` references the TreeNodeBlock for the stroke

If these references point to nodes that haven't been declared/defined yet,
the device fails with "Unable to find node" errors.

### Implementation

We use `_reorder_blocks_for_device()` to ensure correct ordering before serialization:

```python
def _reorder_blocks_for_device(self, blocks: list) -> list:
    """Reorder blocks to match device-expected format."""
    header_blocks = []
    scene_tree_blocks = []
    root_text_block = None
    tree_node_blocks = []
    scene_group_item_blocks = []
    annotation_blocks = []

    # Categorize by type...
    # Reconstruct in correct order...
```

This is called in both the roundtrip and from-scratch generation paths.

### CRDT IDs: Understanding part1 and part2

**CRITICAL**: `CrdtId.part2` is NOT a character offset. It is a CRDT sequence number.

A `CrdtId(part1, part2)` consists of:
- `part1`: Author/namespace identifier (see "System Nodes vs User Nodes" below)
- `part2`: Sequence number within that author's CRDT operations

**Common Bug Pattern** (AVOID THIS):
```python
# ❌ WRONG - part2 is NOT a character offset!
for crdt_id, style in text_data.styles.items():
    char_offset = crdt_id.part2  # BUG: This is a sequence number, not position!
    paragraph_styles[char_offset] = style

# ❌ WRONG - Same bug for anchor resolution
for anchor in anchors:
    char_offset = anchor.crdt_id.part2  # BUG!
```

**Correct Approach** - Build a CRDT ID → character offset map:
```python
# ✅ CORRECT - Build mapping from CRDT IDs to actual character offsets
crdt_to_char: dict[CrdtId, int] = {}
char_offset = 0
for item in text_data.items.sequence_items():
    if hasattr(item, "value") and isinstance(item.value, str):
        text = item.value
        item_id = item.item_id
        # Each character gets a CRDT ID based on item_id + position in string
        for i in range(len(text)):
            char_crdt_id = CrdtId(item_id.part1, item_id.part2 + i)
            crdt_to_char[char_crdt_id] = char_offset + i
        char_offset += len(text)

# Now use the mapping to resolve CRDT IDs to character positions
for crdt_id, style in text_data.styles.items():
    if crdt_id in crdt_to_char:
        actual_char_offset = crdt_to_char[crdt_id]
        paragraph_styles[actual_char_offset] = style
```

**Why this matters**: CRDT sequence numbers are unique identifiers for collaborative
editing operations, not positions. A document could have CRDT IDs like:
- `CrdtId(1, 16)` → character 0
- `CrdtId(1, 17)` → character 1
- `CrdtId(1, 100)` → character 5 (if characters were inserted non-sequentially)

The mapping between CRDT IDs and character positions depends on how the document
was edited and in what order operations were applied.

### System Nodes vs User Nodes

Node IDs follow a convention based on `CrdtId.part1`:
- `part1 == 0`: System nodes (0:1 root, 0:11 Layer 1, 0:13 layer group)
- `part1 == 1`: Generator-created (text blocks, formatting)
- `part1 == 2`: User-created (annotations, strokes, highlights)

**Critical**: System nodes must NEVER be excluded during roundtrip filtering.
Only user nodes (part1 == 2) should be tracked for cross-page migration.

### Scene Graph Relationships

Each stroke requires FOUR interdependent blocks:

```
SceneTreeBlock(tree_id=2:xxx)       - Declares node exists in scene tree
     ↓
TreeNodeBlock(node_id=2:xxx)        - Defines node properties (anchor to text)
     ↓
SceneGroupItemBlock(value=2:xxx)    - Links node to Layer 1 (parent_id=0:11)
     ↓
SceneLineItemBlock(parent_id=2:xxx) - Actual stroke data
```

If any of these blocks are missing or incorrectly ordered, the stroke will
not render on the device ("disappears").

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

## Layout Engine Calibration (2025-11-30)

### Line Height Discovery

When highlights are created on a reMarkable device and then text is modified (e.g., inserting
paragraphs), the highlight rectangles must be repositioned. This requires an accurate model
of how the device renders text.

**Problem**: Initial `LINE_HEIGHT` values (35px, then 50px) caused highlights to appear at
wrong Y positions after text modifications. A 45.2px error was observed for highlights that
should have shifted down by ~4 lines.

**Root Cause Analysis**:

We created fresh highlights on the device and measured their actual pixel positions:

| Highlight | Character Position | Device X | Device Y |
|-----------|-------------------|----------|----------|
| "target"  | char 77           | -218.9   | 205.8    |
| "bottom"  | char 210          | -378.4   | 436.2    |

From word-wrap analysis:
- "target" is on display line 2
- "bottom" is on display line 6

Calculating effective line height from device data:
```
From target: (205.8 - 94.0) / 2 = 55.9px
From bottom: (436.2 - 94.0) / 6 = 57.0px
From delta:  (436.2 - 205.8) / 4 = 57.6px
Average: ~57px per line
```

**Calibrated Values**:

| Parameter | Old Value | New Value | Source |
|-----------|-----------|-----------|--------|
| `LINE_HEIGHT` | 35px | 57px | Device highlight Y positions |
| `avg_char_width` | 15px | 15px | Unchanged (50-51 chars/line observed) |
| `TEXT_WIDTH` | 750px | 750px | Unchanged |
| `TEXT_POS_Y` | 94px | 94px | Unchanged (from RootTextBlock.pos_y) |

**Highlight Rectangle Structure**:

The highlight rect height is 44.4px, less than the 57px line height. This suggests:
- Text line height: 57px total
- Highlight box: 44.4px (covers text portion)
- Inter-line gap: ~12.6px (spacing between lines)

### CRDT Anchoring (Firmware 3.6+)

Highlights store their text anchor position in `extra_value_data` as CRDT offsets:

```
Field 15 (tag 0x7F): Start CrdtId
  - author_id (varint)
  - position = base_id + char_offset (varint)

Field 17 (tag 0x8F): End marker
  - Fixed prefix: 01 01
  - end_position (varint)
```

Where `base_id` is typically 16 (from RootTextBlock's CrdtSequenceItem.item_id.part2).

**Key Finding**: CRDT offsets alone do NOT control display position. The device uses
the rectangle coordinates for rendering. CRDT offsets may be used for text selection
or editing operations, but visual positioning requires correct rectangle coordinates.

### Word-Wrap Algorithm Bug Fix (2025-11-30)

When repositioning highlights after text modifications, the layout engine must calculate
line breaks to determine the (x, y) position of text. A bug in the `calculate_line_breaks`
algorithm caused highlight rectangles to be positioned incorrectly after text shifts.

**Problem**: Highlights appeared 2+ characters to the left of where they should be after
text was inserted before the highlighted word (X-shift scenario).

**Root Cause**:

The word-wrap algorithm double-counted spaces:

```python
# Bug: space counted twice
space_needed = 1 if line_length > 0 else 0  # (1) Pre-word space
if line_length + space_needed + word_length > chars_per_line:
    ...
line_length += space_needed + word_length  # Adds space_needed
...
if text[pos] == " ":
    line_length += 1  # (2) Post-word space - DOUBLE COUNT!
```

This caused line breaks to occur ~10 characters too early (36 chars vs 50 chars on a
50-char-wide line), which meant the layout model calculated wrong X positions.

**Fix**: Remove the duplicate `space_needed` logic since trailing spaces are already
tracked when consumed after each word.

**Impact**: Tests relying on old (buggy) layout engine behavior may need their testdata
re-recorded. The `test_markdown_modifications` test requires re-recording for this reason.

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
