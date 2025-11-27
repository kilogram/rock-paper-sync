# Highlight Annotations: Text-Based Matching and Simple Coordinates

**Annotation Type**: Text selections (Glyph blocks in .rm files)
**Handler**: `HighlightHandler` (`handlers/highlight_handler.py`)

## Overview

Highlights are the most reliable annotation type because they include the actual selected text content. This allows for robust text-based matching instead of relying solely on coordinates.

## Coordinate Transformation

Highlights use **simple text-relative coordinates** without the dual-anchor complexity of strokes.

```python
# Simple transformation (no 60px offset)
absolute_y = text_origin_y + native_y
absolute_x = text_origin_x + native_x

# Constants
text_origin_y = RootTextBlock.value.pos_y  # 94.0
text_origin_x = RootTextBlock.value.pos_x  # -375.0
```

**Why simpler than strokes?**
- Highlights are always anchored to text origin (top of text area)
- No negative Y coordinate space
- No per-parent anchor_origin_x variations (highlights track entire selections, not individual character positions)

## Matching Strategy

### Primary: Text Content Matching

Highlights include the actual highlighted text, enabling robust matching:

```python
# Extract highlighted text from annotation
highlight_text = annotation.highlight.text.strip().lower()

# Match against markdown blocks
for idx, md_block in enumerate(markdown_blocks):
    if highlight_text in md_block.text.lower():
        paragraph_index = idx
        break
```

**Advantages**:
- Robust to layout changes
- Works even if document content is reformatted
- No coordinate transformation errors

### Fallback: Y-Position Matching

If text content is unavailable, fall back to position-based matching:

```python
# Transform to absolute coordinates
anno_y_absolute = text_origin_y + annotation.bounding_box.y

# Find closest paragraph by Y position
for idx, md_block in enumerate(markdown_blocks):
    distance = abs(anno_y_absolute - md_block.page_y_start)
    # ... select minimum distance
```

## Rendering

Highlights are rendered as **HTML comments** in markdown:

```markdown
<!-- Highlights: selected text here -->
Original paragraph text.
```

**Multiple highlights in one paragraph**:
```markdown
<!-- Highlights: first selection | second selection | third selection -->
Paragraph with multiple highlighted portions.
```

**Rendering code**:
```python
def render(self, paragraph_index, matches, original_content):
    highlight_texts = [anno.highlight.text for anno in matches]
    highlights_str = " | ".join(highlight_texts)
    comment = f"<!-- Highlights: {highlights_str} -->"
    return f"{comment}\n{original_content}"
```

## State Management

Highlights track **text hashes** to detect when highlighted content changes.

### Schema

```sql
CREATE TABLE highlight_state (
    document_id TEXT NOT NULL,
    annotation_id TEXT NOT NULL,
    text_hash TEXT,           -- Hash of highlighted text
    highlighted_text TEXT,    -- Original text content
    last_seen TIMESTAMP,
    PRIMARY KEY (document_id, annotation_id)
);
```

### Usage

```python
# Store highlight state
handler.store_state(
    db_connection,
    document_id=doc_id,
    annotation_id=highlight_id,
    state_data={
        "text_hash": hash_text(highlight.text),
        "highlighted_text": highlight.text,
    }
)

# Load cached state
cached = handler.load_state(db_connection, doc_id, highlight_id)
if cached and cached["text_hash"] == current_hash:
    # Highlight unchanged, skip reprocessing
    pass
```

**Change detection**:
- If text hash differs: Highlighted text was edited on device
- If annotation_id missing: Highlight was deleted
- If new annotation_id: New highlight created

## Comparison with Strokes

| Feature | Highlights | Strokes |
|---------|-----------|---------|
| Coordinates | Simple text-relative | Dual-anchor with 60px offset |
| Matching | Text content (reliable) | Spatial clustering + OCR |
| Processing | Instant | Requires OCR service |
| State | Text hash only | Image hash, OCR text, confidence |
| Rendering | HTML comments | OCR text blocks |

## Key Modules

**Handler**:
- `handlers/highlight_handler.py` - HighlightHandler implementation

**Shared Utilities**:
- `coordinate_transformer.py` - CoordinateTransformer.to_absolute()
- `common/text_extraction.py` - extract_text_blocks_from_rm()

## Testing

**Integration Tests**:
- `tests/record_replay/test_highlights.py` - Highlight preservation through sync

Tests use device-captured `.rm` files to ensure real-world accuracy.

## References

- Main architecture: [../README.md](../README.md)
- Strokes comparison: [STROKES.md](STROKES.md)
