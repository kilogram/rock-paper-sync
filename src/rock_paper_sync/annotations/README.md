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
- Methods: `detect()`, `map()`, `render()`, `get_position()`, state management
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

### State Management

Each handler manages its own state schema via Protocol methods:

```python
class AnnotationHandler(Protocol):
    def init_state_schema(self, db_connection) -> None:
        """Initialize handler-specific state tables."""

    def store_state(self, db_connection, document_id, annotation_id, state_data):
        """Store handler-specific state."""

    def load_state(self, db_connection, document_id, annotation_id):
        """Load handler-specific state."""
```

**Examples**:
- `HighlightHandler`: Tracks text hashes for change detection
- `StrokeHandler`: Caches OCR results with image hashes and confidence scores

The common `StateManager` provides database connection; handlers define their own schemas.

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

       def map(self, annotations, markdown_blocks, rm_file_path) -> dict:
           # Map sketches to paragraphs

       def render(self, paragraph_index, matches, content) -> str:
           # Render as SVG embedded in markdown

       def init_state_schema(self, db_connection):
           # Create sketch-specific state tables

       # ... other Protocol methods
   ```

2. **Register with `AnnotationProcessor`**:
   ```python
   processor = AnnotationProcessor(db_path)
   processor.register_handler(SketchHandler())
   ```

3. **Define Handler-Specific State** (optional):
   - Create state tables in `init_state_schema()`
   - Store/load state via Protocol methods

The system automatically routes annotations to the correct handler and applies type-specific processing.

## Key Principles

1. **Separation of Concerns**:
   - Coordinate transformation: Pure math operations (`coordinate_transformer.py`)
   - Annotation mapping: Position → paragraph index (handler `map()`)
   - Rendering: Markdown generation (handler `render()`)
   - State: Handler-specific persistence (Protocol methods)

2. **Pure Functions**:
   - Coordinate transformers are stateless and deterministic
   - Easy to test, compose, and reason about

3. **Extensibility**:
   - Add new annotation types without modifying existing code
   - Protocol ensures handlers implement required interface
   - Type changes support cross-handler workflows

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
