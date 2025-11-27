# Stroke Annotations: Coordinates, Clustering, and OCR

**Annotation Type**: Hand-drawn pen/pencil strokes (Line blocks in .rm files)
**Handler**: `StrokeHandler` (`handlers/stroke_handler.py`)

## Overview

Stroke annotations require three processing stages:
1. **Coordinate Transformation**: Convert native coords → absolute page coords
2. **Clustering**: Group nearby strokes into words/phrases
3. **OCR**: Extract text from clustered stroke images

## 1. Coordinate Transformation

### The Problem

reMarkable v6 stores strokes in **two different coordinate spaces** depending on the sign of Y:

- **Positive Y**: Relative to text origin (top of text area)
- **Negative Y**: Relative to baseline + line height

Additionally, each text line has a different X anchor point.

### The Solution

```python
# X transformation: Use per-parent anchor_origin_x
absolute_x = native_x + anchor_origin_x  # From TreeNodeBlock

# Y transformation: 60px offset for negative Y
if stroke_center_y >= 0:
    absolute_y = text_origin_y + native_y
else:
    absolute_y = text_origin_y + 60 + native_y  # Typography-based offset
```

**Key Constants**:
- `text_origin_y = 94.0` - From RootTextBlock.pos_y
- `anchor_origin_x` - Per-parent from TreeNodeBlock.anchor_origin_x
- `60px offset` = LINE_HEIGHT (35) + BASELINE_OFFSET (25)

### Data Structures

**RootTextBlock**:
```python
text_origin_x = RootTextBlock.value.pos_x  # -375.0
text_origin_y = RootTextBlock.value.pos_y  #   94.0
text_width = RootTextBlock.value.width     #  750.0
```

**TreeNodeBlock** (parent layers):
```python
node_id = TreeNodeBlock.group.node_id
anchor_id = TreeNodeBlock.group.anchor_id
anchor_origin_x = TreeNodeBlock.group.anchor_origin_x  # Varies per parent!
```

**Note**: There is NO `anchor_origin_y` field. Y positioning uses implicit rules (positive/negative).

### Transformation Algorithm

```python
from rock_paper_sync.coordinate_transformer import (
    extract_text_origin,
    build_parent_anchor_map,
    CoordinateTransformer,
)

# Extract transformation components
text_origin = extract_text_origin(rm_file)
parent_anchor_map = build_parent_anchor_map(rm_file)
transformer = CoordinateTransformer(
    text_origin_x=text_origin.x,
    text_origin_y=text_origin.y,
)

# Transform stroke to absolute coordinates
for stroke in strokes:
    anchor_x = parent_anchor_map[stroke.parent_id].x
    bbox = stroke.bounding_box
    stroke_center_y = bbox.y + bbox.h / 2

    # CRITICAL: Apply same offset to ALL points in stroke
    if stroke_center_y >= 0:
        y_offset = text_origin.y
    else:
        y_offset = text_origin.y + 60

    for point in stroke.points:
        point.x += anchor_x
        point.y += y_offset
```

**Critical**: Apply the same Y offset to ALL points in a stroke. Per-point offsets cause shape distortion!

### Why 60px?

Through empirical testing:
```
60px = LINE_HEIGHT + BASELINE_OFFSET
     = 35px + 25px
     ≈ 1.7 × LINE_HEIGHT
```

This reflects reMarkable's text rendering engine where strokes can anchor either to:
- Text block top (positive Y)
- Writing baseline + line height (negative Y)

## 2. Spatial Clustering

After coordinate transformation, strokes must be grouped into logical units (words, phrases).

### Clustering Algorithm

**Method**: Graph-based connected components with distance threshold

```python
def cluster_annotations(annotations: list[Annotation]) -> list[list[Annotation]]:
    """Group strokes by spatial proximity."""
    # 1. Build proximity graph
    distance_threshold = 50.0  # pixels
    graph = {i: [] for i in range(len(annotations))}

    for i in range(len(annotations)):
        for j in range(i + 1, len(annotations)):
            distance = euclidean_distance(
                annotations[i].bounding_box.center,
                annotations[j].bounding_box.center
            )
            if distance < distance_threshold:
                graph[i].append(j)
                graph[j].append(i)

    # 2. Find connected components (DFS)
    visited = set()
    clusters = []
    for i in range(len(annotations)):
        if i not in visited:
            cluster = dfs(graph, i, visited)
            clusters.append([annotations[idx] for idx in cluster])

    return clusters
```

**Parameters**:
- `distance_threshold = 50px` - Maximum distance between connected strokes
- Euclidean distance between bounding box centers

**Result**: Each cluster represents a word/phrase/marginal note.

### Cluster → Paragraph Mapping

Once clustered, map each cluster to a markdown paragraph using bounding box overlap:

```python
from rock_paper_sync.ocr.paragraph_mapper import SpatialOverlapMapper

mapper = SpatialOverlapMapper()
paragraph_index = mapper.map_cluster_to_paragraph(
    cluster_bbox,      # Cluster's combined bounding box
    markdown_blocks,   # Parsed markdown content blocks
    rm_text_blocks,    # Text blocks from .rm file
)
```

**Strategies**:
1. **Spatial overlap**: Bounding box intersection with markdown blocks
2. **Text matching**: Fuzzy match to handle layout changes
3. **Context fallback**: Use surrounding text

## 3. OCR Integration

### Pipeline

```
Strokes → Transform → Cluster → Render → OCR → Markdown
   ↓          ↓          ↓         ↓      ↓        ↓
.rm file  absolute   groups    images  text   OCR blocks
         coords
```

### Rendering to Images

Each cluster is rendered as a PNG image:

```python
def render_cluster(cluster: list[Annotation]) -> tuple[bytes, BoundingBox]:
    """Render stroke cluster to PNG for OCR."""
    # Calculate combined bounding box
    min_x, min_y, max_x, max_y = calculate_bounds(cluster)

    # Create image canvas
    width = int(max_x - min_x) + padding
    height = int(max_y - min_y) + padding
    image = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(image)

    # Draw each stroke
    for annotation in cluster:
        for i in range(len(annotation.stroke.points) - 1):
            p1 = annotation.stroke.points[i]
            p2 = annotation.stroke.points[i + 1]
            draw.line(
                [(p1.x - min_x, p1.y - min_y),
                 (p2.x - min_x, p2.y - min_y)],
                fill='black',
                width=stroke_width
            )

    # Encode as PNG
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue(), BoundingBox(min_x, min_y, width, height)
```

### OCR Request

```python
from rock_paper_sync.ocr.protocol import OCRRequest, ParagraphContext

request = OCRRequest(
    image_data=png_bytes,
    annotation_uuid=cluster_id,
    bounding_box=cluster_bbox,
    context=ParagraphContext(
        document_id=doc_id,
        paragraph_index=para_idx,
        paragraph_text=original_text,
        preceding_text=prev_paragraph,
        following_text=next_paragraph,
    )
)

# Send to OCR service
results = ocr_service.recognize_batch([request])
```

### OCR Service

**Runpods Implementation** (`ocr/runpods.py`):
- Cloud-based inference using TrOCR model
- Base model: `microsoft/trocr-base-handwritten`
- Optional LoRA fine-tuning for improved accuracy
- Beam search decoding (beam_size=5)

**Request Flow**:
```
Client → HTTP POST /run → Runpods → TrOCR → Result
   ↓                                            ↓
OCRRequest                                  OCRResult
```

### Markdown Integration

OCR results are inserted as special comment blocks:

```markdown
# Document

Original paragraph text.

<!-- RPS:OCR:annotation-uuid:confidence-0.95:model-v1 -->
Recognized handwritten text
<!-- /RPS:OCR -->

More content...
```

**Marker Format**:
- `annotation-uuid`: Unique identifier for correction tracking
- `confidence`: OCR confidence score (0.0 - 1.0)
- `model-v1`: Model version for tracking changes

### Corrections Workflow

When users edit OCR text, corrections are detected and stored:

```python
from rock_paper_sync.ocr.corrections import CorrectionManager

# Detect correction
original_text = extract_from_marker(markdown)
current_text = extract_from_content(markdown)

if current_text != original_text:
    # Store correction
    correction_manager.store_correction(
        annotation_id=uuid,
        original_text=original_text,
        corrected_text=current_text,
        confidence=original_confidence,
    )
```

Corrections are used for:
- Improving future OCR accuracy (fine-tuning data)
- Tracking model performance
- User feedback loop

## State Management

`StrokeHandler` manages OCR-specific state:

**Schema**:
```sql
CREATE TABLE stroke_ocr_state (
    document_id TEXT NOT NULL,
    annotation_id TEXT NOT NULL,
    image_hash TEXT,          -- Visual fingerprint for matching
    ocr_text TEXT,            -- Recognized text
    confidence REAL,          -- OCR confidence score
    model_version TEXT,       -- Model version used
    last_processed TIMESTAMP,
    PRIMARY KEY (document_id, annotation_id)
);

CREATE INDEX idx_stroke_image_hash ON stroke_ocr_state(image_hash);
```

**Usage**:
```python
# Store OCR result
handler.store_state(
    db_connection,
    document_id=doc_id,
    annotation_id=stroke_id,
    state_data={
        "image_hash": hash_image(png_bytes),
        "ocr_text": recognized_text,
        "confidence": 0.95,
        "model_version": "trocr-v1",
    }
)

# Load cached result
cached = handler.load_state(db_connection, doc_id, stroke_id)
if cached and cached["image_hash"] == current_hash:
    return cached["ocr_text"]  # Skip re-processing
```

## Key Modules

**Coordinate Transformation**:
- `coordinate_transformer.py` - Pure transformation functions
- `CoordinateTransformer` - Main transformer class
- `extract_text_origin()`, `build_parent_anchor_map()` - Extraction utilities

**Clustering**:
- `ocr/integration.py:_cluster_annotations_by_proximity()` - Spatial clustering
- `ocr/paragraph_mapper.py` - Cluster → paragraph mapping

**OCR**:
- `ocr/protocol.py` - Service interface and data types
- `ocr/runpods.py` - Runpods service implementation
- `ocr/markers.py` - Markdown marker management
- `ocr/corrections.py` - Correction detection and storage

**Handler**:
- `handlers/stroke_handler.py` - StrokeHandler implementation

## Testing

**Integration Tests**:
- `tests/record_replay/test_pen_colors.py` - Color preservation
- `tests/record_replay/test_pen_tools.py` - Tool type preservation
- `tests/record_replay/test_pen_widths.py` - Width preservation
- `tests/record_replay/test_ocr_handwriting.py` - OCR with coordinates

Tests use device-captured `.rm` files to validate real-world accuracy.

## References

- Main architecture: [../README.md](../README.md)
- Highlights comparison: [HIGHLIGHTS.md](HIGHLIGHTS.md)
- OCR server setup: `../../../docs/OCR_SYSTEM.md`
