# Annotation System Architecture

**Status**: Production-ready with pluggable handler architecture

## Overview

The annotation system provides a composable architecture for handling different annotation types (highlights, handwritten strokes, future: sketches, diagrams) with:
- **Protocol-based handlers** for extensibility
- **Handler-specific state management** (each handler owns its state schema)
- **Generic corrections system** (type-agnostic)
- **Shared coordinate transformation utilities**

## Architecture

```
[.rm files] → [AnnotationProcessor] → [Handlers] → [Markdown output]
      ↓              ↓                      ↓              ↓
  rmscene     Routes by type      detect/map/render   HTML comments
                                  + state/corrections    + OCR blocks
```

### Core Components

**`AnnotationHandler` Protocol** (`core/protocol.py`):
- Interface for pluggable annotation processors
- Required: `detect()`, `map()`, `create_anchor()`, `extract_from_markdown()`
- Optional: `relocate()` (only for content-based repositioning like highlights)
- Handlers are stateless processors (state persisted separately)
- Handlers may import rmscene and coordinate_transformer directly for type-specific operations

**`AnnotationProcessor`** (`core/processor.py`):
- Orchestrates multiple handlers
- Routes annotations to appropriate handler by type
- Manages handler registration and initialization

**Handler Implementations** (`handlers/`):
- `HighlightHandler`: Text selections, HTML comment rendering
- `StrokeHandler`: Handwriting with OCR, coordinate transformation

### Coordinate Systems

reMarkable v6 uses multiple coordinate spaces that require transformation:

1. **Absolute coordinates**: Items parented to root layer `CrdtId(0, 11)`
2. **Text-relative coordinates**: Items parented to text layers (other CrdtIds)

Different annotation types use different transformation strategies:
- **Highlights**: Simple text-relative (`absolute_y = text_origin_y + native_y`)
- **Strokes**: Dual-anchor system with 60px offset for negative Y coordinates

See [docs/STROKES.md](docs/STROKES.md) and [docs/HIGHLIGHTS.md](docs/HIGHLIGHTS.md) for details.

### Corrections System

**Generic, type-agnostic** corrections work for all annotation types:

**`CorrectionManager`** (`core/corrections.py`):
- Stores corrections with multi-strategy matching (image hash, content hash, position)
- Correction kinds: `text_edit`, `replacement`, `type_change`, `format_change`
- Versioning support with `supersedes_id` chain

**Matching Strategies** (priority order):
1. Image hash: Visual fingerprint of annotation rendering
2. Content hash: Hash of annotation data
3. Position key: Document + paragraph + offset
4. Annotation ID: Direct match (fragile, lowest priority)

**Type Changes**: Annotations can be reclassified between types (e.g., stroke → drawing), enabling different rendering on next sync.

## Handler Deep Dives

- **[Strokes](docs/STROKES.md)**: Coordinate transformation, clustering, OCR integration
- **[Highlights](docs/HIGHLIGHTS.md)**: Text-based matching, HTML rendering

## Code Organization

```
annotations/
├── README.md              # This file - architecture overview
├── core_types.py          # Core data structures (Annotation, Point, Rectangle, etc.)
├── core/
│   ├── protocol.py        # AnnotationHandler Protocol
│   ├── processor.py       # AnnotationProcessor orchestrator
│   ├── data_types.py      # AnnotationInfo summary
│   └── corrections.py     # Generic corrections system
├── handlers/
│   ├── highlight_handler.py
│   └── stroke_handler.py
├── common/
│   └── text_extraction.py # Shared utilities (RmTextBlock, etc.)
└── docs/
    ├── STROKES.md         # Stroke-specific details
    └── HIGHLIGHTS.md      # Highlight-specific details
```

## Adding New Annotation Types

To add a new annotation type (e.g., sketches, diagrams):

1. **Implement `AnnotationHandler` Protocol**:
   ```python
   class SketchHandler:
       @property
       def annotation_type(self) -> str:
           return "sketch"

       def detect(self, rm_file_path) -> list[Annotation]:
           # Extract sketch annotations

       def map(self, annotations, markdown_blocks, rm_file_path, layout_context) -> dict:
           # Map sketches to paragraphs

       def create_anchor(self, annotation, paragraph_text, paragraph_index, page_num) -> AnnotationAnchor:
           # Create stable anchor for matching across syncs

       def extract_from_markdown(self, paragraph, config) -> list[ExtractedAnnotation]:
           # Extract annotations from rendered markdown
   ```

2. **Register with `AnnotationProcessor`**:
   ```python
   processor = AnnotationProcessor()
   processor.register_handler(SketchHandler())
   ```

The system automatically routes annotations to the correct handler and applies type-specific processing.

## Key Principles

1. **Separation of Concerns**:
   - Coordinate transformation: Pure math operations (`coordinate_transformer.py`)
   - Annotation mapping: Position → paragraph index (handler `map()`)
   - Content anchoring: Stable identifiers for annotation matching (`create_anchor()`)
   - Annotation migration: Content-aware relocation (`relocate()`, `AnnotationMerger`)

2. **Pure Functions**:
   - Coordinate transformers are stateless and deterministic
   - Easy to test, compose, and reason about

3. **Extensibility**:
   - Add new annotation types without modifying existing code
   - Protocol ensures handlers implement required interface
   - Type changes support cross-handler workflows

## Architecture: AnchorContext (V2) — PRODUCTION

The annotation system uses content-based anchoring via `AnchorContext` and `DocumentModel`.
This replaced the fragile character-offset approach that was prone to bugs when content changed.

**Implementation**: `document_model.py` — used by `generator.py:generate_document()`

### Key Concepts

| Concept | Description |
|---------|-------------|
| **AnchorContext** | Multi-signal stable identifier (content hash, fuzzy text, context before/after) |
| **DocumentModel** | Document-level abstraction; pages are projections, not primary structure |
| **ContextResolver** | Fuzzy matching with explicit confidence scores |
| **DiffAnchor** | Anchor relative to unchanged text for edit resilience |

### How It Works

```
[Old .rm files] → DocumentModel.from_rm_files()
                        ↓
              [Old DocumentModel]
                        ↓
              AnnotationMerger.merge(old_model, new_model)
                        ↓
              [MergeResult with migrated annotations]
                        ↓
              project_to_pages()
                        ↓
              [PageProjection list] → .rm file generation
```

### Benefits

1. **Edit resilience**: Survives content rewrites, not just insertions/deletions
2. **Natural cross-page**: Page boundaries are projections, not special cases
3. **Multi-signal matching**: Falls back gracefully through resolution strategies
4. **Explicit failures**: No hidden fallback paths that mask bugs

**See**: `docs/ANNOTATION_ARCHITECTURE_V2.md` for detailed design documentation.

## Debugging

### Known Issues

See `docs/bugs/CROSS_PAGE_ANCHOR_BUG.md` for documentation of anchor-related bugs and fixes.

### Anchor Validation

The test harness includes anchor validation that catches out-of-bounds anchors:

```python
# In tests/record_replay/harness/offline.py
def _validate_rm_anchors(state: DocumentState, context: str) -> None:
    """Validate TreeNodeBlock anchors are within page text bounds."""
```

Use `tools/rmlib/validator.py` for standalone anchor validation:

```bash
uv run python -m tools.rmlib.validator path/to/file.rm --verbose
```

## Testing

Integration tests validate annotation preservation through round-trip sync:

- `tests/record_replay/test_highlights.py` - Highlight annotation preservation
- `tests/record_replay/test_pen_colors.py` - Stroke color preservation
- `tests/record_replay/test_pen_tools.py` - Different pen tool preservation
- `tests/record_replay/test_pen_widths.py` - Stroke width preservation
- `tests/record_replay/test_ocr_handwriting.py` - OCR with coordinate transformation

Tests use device-captured `.rm` files to ensure real-world accuracy.

## References

### Internal Documentation
- [Strokes](docs/STROKES.md) - Dual-anchor coordinates, clustering, OCR
- [Highlights](docs/HIGHLIGHTS.md) - Text matching, rendering

### Related Modules
- `coordinate_transformer.py` - Coordinate space transformations
- `generator.py` - Document generation with annotation preservation
- `ocr/integration.py` - OCR processing pipeline

### External Resources
- [rmscene library](https://github.com/ricklupton/rmscene) - reMarkable v6 file parsing
- reMarkable Paper Pro Display: 2160×1620 @ 229 DPI (content area: ~1404×1872)
