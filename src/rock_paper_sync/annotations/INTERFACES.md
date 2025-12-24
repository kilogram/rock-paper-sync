# Annotation System Interfaces

This document describes the contracts between components in the annotation system.

## Overview

The annotation pipeline flows data through several components:

```
[.rm files] → DocumentModel → AnnotationMerger → PageProjection → [.rm output]
                  ↑                  ↑                 ↓
            LayoutContext      ContextResolver    RemarkableGenerator
```

## Component Contracts

### DocumentModel

**Purpose**: Document-level abstraction for annotation storage and migration.

**Expects from callers**:
- `.rm` file paths for `from_rm_files()`
- `ContentBlock` list for `from_markdown()`
- Valid `DeviceGeometry` for layout calculations

**Provides**:
- `annotations: list[DocumentAnnotation]` - all annotations with anchors
- `project_to_pages(geometry) -> list[PageProjection]` - paginated view
- `_assign_stroke_clusters(layout_context)` - cluster strokes by content

**Data structures**:
```python
@dataclass
class DocumentAnnotation:
    annotation_type: Literal["highlight", "stroke"]
    anchor_context: AnchorContext  # Content-based anchor
    highlight: HighlightData | None
    stroke_data: StrokeData | None
    page_hint: int | None
```

---

### AnnotationMerger

**Purpose**: Three-way merge of annotations between document versions.

**Expects**:
- `MergeContext` with `old_model: DocumentModel` and `new_model: DocumentModel`
- `ContextResolver` instance for fuzzy text matching

**Provides**:
- `MergeResult` with:
  - `merged_model: DocumentModel` - new model with migrated annotations
  - `report: MigrationReport` - detailed migration status

**Contract**:
```python
merger = AnnotationMerger(resolver=ContextResolver())
context = MergeContext(old_model=old_model, new_model=new_model)
result = merger.merge(context)  # Returns MergeResult
```

---

### LayoutContext

**Purpose**: Bridge between document content and spatial positioning.

**Expects**:
- Page text content
- `DeviceGeometry` for layout parameters
- Optional text origin coordinates

**Provides to handlers**:
- `offset_to_position(offset) -> (x, y)` - character offset to page coordinates
- `position_to_offset(x, y) -> int` - page coordinates to character offset
- `get_text()` - full page text

**Creation patterns**:
```python
# From device geometry
ctx = LayoutContext.from_geometry(text, geometry)

# From .rm file (extracts text origin automatically)
ctx = LayoutContext.from_rm_file(rm_path, page_text, geometry)
```

---

### AnnotationHandler Protocol

**Purpose**: Pluggable interface for annotation type processors.

**Required methods**:

| Method | Input | Output | Purpose |
|--------|-------|--------|---------|
| `annotation_type` | - | `str` | Unique type identifier |
| `detect(rm_path)` | Path | `list[Annotation]` | Extract from .rm file |
| `map(...)` | annotations, blocks, path, context | `dict[int, list]` | Map to paragraphs |
| `create_anchor(...)` | annotation, text, idx, page | `AnnotationAnchor` | Create stable anchor |
| `extract_from_markdown(...)` | paragraph, config | `list[ExtractedAnnotation]` | Parse from markdown |

**Optional methods**:

| Method | Purpose |
|--------|---------|
| `relocate(...)` | Content-based repositioning (highlights only) |

**Handler → LayoutContext contract**:
Handlers can use `layout_context.position_to_offset()` and `offset_to_position()`
for coordinate ↔ character offset conversions.

---

### ContextResolver

**Purpose**: Fuzzy text matching across document versions.

**Expects**:
- `AnchorContext` to resolve
- Target text to search in

**Provides**:
- `resolve(context, new_text) -> ResolvedAnchorContext | None`

**Resolution strategies** (in priority order):
1. Exact hash match
2. Fuzzy text match with HeuristicTextAnchor
3. DiffAnchor resolution (stable text boundaries)
4. Spatial fallback (Y position)

---

### AnchorContext

**Purpose**: Multi-signal stable identifier for annotations.

**Signals used**:
- `content_hash` - SHA256 of normalized text (fast exact match)
- `text_content` - raw text for fuzzy matching
- `context_before/after` - surrounding text for disambiguation
- `y_position_hint` - spatial fallback
- `diff_anchor` - edit-resilient anchoring to stable text

**Factory methods**:
```python
# For text-based annotations (highlights)
anchor = AnchorContext.from_text_span(full_text, start, end)

# For spatial annotations (strokes)
anchor = AnchorContext.from_y_position(y, full_text, layout_context)
```

---

## Data Flow

### Annotation Preservation During Sync

```
1. Load old .rm files
   DocumentModel.from_rm_files(rm_paths, layout) → old_model

2. Parse new markdown
   DocumentModel.from_markdown(content_blocks, geometry) → new_model

3. Migrate annotations
   AnnotationMerger.merge(MergeContext(old_model, new_model)) → result

4. Project to pages
   result.merged_model.project_to_pages(geometry) → pages

5. Generate .rm files
   for page in pages:
       generator.generate_rm_file(page) → bytes
```

### Handler Integration

Handlers are called at specific points:

```
detect() → called during DocumentModel.from_rm_files()
           extracts raw annotations from .rm binary

map() → called during annotation processing
        associates annotations with paragraph indices

relocate() → called during regeneration (highlights only)
             adjusts coordinates when text shifts

create_anchor() → called for anchor-based matching
                  creates stable identifiers

extract_from_markdown() → called for correction detection
                          parses annotations from rendered output
```

## Error Handling

**Resolution failures**:
- `ContextResolver.resolve()` returns `None` if anchor cannot be found
- Caller decides: drop annotation, use fallback, or log warning

**Layout failures**:
- `LayoutContext` methods may return approximate values for edge cases
- Callers should validate offsets are within text bounds

**Migration failures**:
- `MigrationReport.dropped_annotations` lists annotations that couldn't migrate
- `MigrationReport.warnings` contains diagnostic messages
