# Architecture Consolidation

This document describes the annotation system architecture consolidation effort, including the target architecture, migration phases, and key abstractions.

## Problem Statement

The annotation system evolved organically, resulting in:
- **Triple stroke representation**: `Stroke`, `StrokeData`, `StrokeBundle` with overlapping concerns
- **Fragmented merge logic**: Spread across `DocumentModel`, `RemarkableGenerator`, and `SyncEngine`
- **Duplicate anchor types**: `AnchorContext`, `AnnotationAnchor`, `HeuristicTextAnchor`
- **Disabled features**: Reflow detection disabled due to layout model limitations

## Target Architecture

### Core Abstractions

#### 1. Stroke (CRDT-aware)

A single stroke that knows its own CRDT structure. Located in `annotations/stroke.py`.

```python
@dataclass
class Stroke:
    """A single stroke that knows its own CRDT structure."""
    stroke_id: CrdtId
    points: list[Point]
    bounding_box: tuple[float, float, float, float]
    color: int
    tool: int
    thickness: float

    # CRDT blocks (this stroke's serialization structure)
    tree_node: TreeNodeBlock
    scene_tree: SceneTreeBlock
    scene_group_item: SceneGroupItemBlock
    line_block: SceneLineItemBlock

    def to_rm_blocks(self) -> list[Block]:
        """Serialize this stroke's CRDT blocks in correct order."""
        ...
```

**Key insight**: Each stroke knows its own CRDT parent structure. This prevents the TreeNodeBlock duplication bugs we previously encountered.

#### 2. StrokeCluster (Spatial Grouping)

A spatial grouping of strokes that moves as a unit when content changes. Located in `annotations/stroke_cluster.py`.

```python
@dataclass
class StrokeCluster:
    """Spatial grouping of strokes - moves as a unit when context changes.

    Examples: margin note, underlines on a sentence, circled word.
    A cluster may contain strokes with DIFFERENT TreeNodeBlocks.
    """
    cluster_id: str
    strokes: list[Stroke]
    bounding_box: tuple[float, float, float, float]  # Combined
    anchor: AnchorContext  # Where it belongs in document

    @classmethod
    def from_rm_file(cls, rm_path: Path) -> list[StrokeCluster]:
        """Extract stroke clusters from .rm file using spatial clustering."""
        ...

    def to_rm_blocks(self) -> list[Block]:
        """Serialize all strokes' CRDT blocks."""
        return [b for s in self.strokes for b in s.to_rm_blocks()]
```

**Key insight**: Spatial grouping is NOT the same as CRDT grouping. A margin note (cluster) may contain strokes with different CRDT parents, but they should move together when content reflows.

#### 3. AnchorContext (Where annotation belongs)

Already exists in `document_model.py`. Represents where an annotation belongs in document context, supporting both text-anchored (highlights) and spatially-anchored (strokes) annotations.

```python
@dataclass
class AnchorContext:
    """Identifies where an annotation belongs in document context."""
    # Primary anchor
    char_offset: int | None           # For text-anchored annotations
    y_position: float | None          # For spatially-anchored annotations

    # Context for resolution
    text_content: str                 # Nearby text for matching
    context_before: str               # Text before annotation
    context_after: str                # Text after annotation

    # Fallback hints
    paragraph_index: int | None
    page_hint: int | None
```

**Deprecates**: `AnnotationAnchor` (common/anchors.py), `HeuristicTextAnchor` (core_types.py)

### Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| **Handlers** | CREATE `AnchorContext` (type-specific signals) |
| **ContextResolver** | RESOLVE anchors across document versions (type-agnostic) |
| **AnnotationMerger** | EXCLUSIVELY owns three-way merge |
| **DocumentGenerator** | Receives merged annotations, generates .rm files |

### Three-Way Merge Flow

```
                    ┌─────────────────┐
                    │  SnapshotStore  │
                    │  (old markdown) │
                    └────────┬────────┘
                             │
    ┌────────────────────────┼────────────────────────┐
    │                        ▼                        │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────┐ │
    │  │ Old Markdown│  │ New Markdown│  │Device RM│ │
    │  └──────┬──────┘  └──────┬──────┘  └────┬────┘ │
    │         │                │               │      │
    │         └────────────────┼───────────────┘      │
    │                          ▼                      │
    │               ┌──────────────────┐              │
    │               │ AnnotationMerger │              │
    │               │  (exclusive owner)│              │
    │               └────────┬─────────┘              │
    │                        │                        │
    │         ┌──────────────┴──────────────┐        │
    │         ▼                              ▼        │
    │  ┌─────────────┐              ┌─────────────┐  │
    │  │  Handlers   │              │ContextResolver│ │
    │  │(create anchor)│            │(resolve anchor)│ │
    │  └─────────────┘              └─────────────┘  │
    │                        │                        │
    │                        ▼                        │
    │              ┌──────────────────┐               │
    │              │MergedAnnotations │               │
    │              └────────┬─────────┘               │
    │                       │                         │
    └───────────────────────┼─────────────────────────┘
                            ▼
                  ┌──────────────────┐
                  │DocumentGenerator │
                  │ (no merge logic) │
                  └──────────────────┘
```

**Simplifying fact**: Annotations are made on reMarkable files generated from the OLD vault snapshot. This means:
1. Device annotations are always relative to OLD layout
2. We can resolve OLD→NEW using the text diff
3. No ambiguity about which version the annotation belongs to

## Migration Phases

### Phase 1: Dead Code Cleanup (COMPLETE)

**Status**: Done

Changes:
- Deleted `preserve_strokes_in_scene()` from `core_types.py` (was NotImplementedError stub)
- Wired `cleanup_old_snapshots()` into `sync_all_changed()` in `converter.py`
- Documented reflow detection limitation in `highlight_handler.py`
- Cleaned dead exports from `annotations/__init__.py`

### Phase 2: Create Stroke + StrokeCluster

**Status**: In Progress

Create the two distinct stroke abstractions:

1. **Create `annotations/stroke.py`**
   - New `Stroke` class with CRDT blocks
   - Factory method to create from SceneLineItemBlock

2. **Create `annotations/stroke_cluster.py`**
   - New `StrokeCluster` class for spatial grouping
   - Spatial clustering algorithm
   - Factory method to load from .rm file

3. **Refactor `StrokeHandler`**
   - `detect(rm_path) -> list[StrokeCluster]`
   - `create_anchor_context(cluster, page_text) -> AnchorContext`
   - `serialize(clusters) -> list[Block]`
   - **NO `resolve()`** - resolution in ContextResolver

4. **Deprecate old types**
   - Mark `Stroke` (old), `StrokeData`, `StrokeBundle` as deprecated

### Phase 3: Consolidate Context Resolution

Keep `ContextResolver` as the central resolution abstraction. Consolidate:

1. **Deprecate `HeuristicTextAnchor`** - merge functionality into `AnchorContext`
2. **Deprecate `AnnotationAnchor`** - use `AnchorContext` consistently
3. **Extract `CrdtService`** - CRDT ID generation and block cloning from generator.py

Update handler protocol:
- `create_anchor()` → `create_anchor_context()` returning `AnchorContext`
- Inject `ContextResolver` as service to handlers that need it

### Phase 4: Create AnnotationMerger

Create `AnnotationMerger` that EXCLUSIVELY owns three-way merge:

```python
@dataclass
class MergeContext:
    """All inputs for three-way merge."""
    old_markdown: str              # From SnapshotStore (pre-sync)
    new_markdown: str              # Current file content
    device_rm_files: list[Path]    # .rm files from device
    geometry: DeviceGeometry       # Layout parameters

@dataclass
class MergedAnnotations:
    """Output of three-way merge."""
    annotations: list[DocumentAnnotation]  # With updated anchors
    orphans: list[DocumentAnnotation]       # Could not resolve
    migration_report: MigrationReport       # Confidence scores

class AnnotationMerger:
    """EXCLUSIVELY owns three-way merge logic."""

    def __init__(
        self,
        context_resolver: ContextResolver,
        handlers: list[AnnotationHandler],
    ): ...

    def merge(self, context: MergeContext) -> MergedAnnotations:
        """Three-way merge:
        1. Load annotations from device_rm_files (handlers.detect)
        2. Create AnchorContext for each (handlers.create_anchor_context)
        3. Resolve each anchor OLD→NEW (context_resolver.resolve)
        4. Return updated annotations with new positions
        """
```

Wire into `SyncEngine.sync_file()`:
```python
old_snapshot = self.state.snapshots.get_file_snapshot(vault, path)
old_markdown = old_snapshot.decode() if old_snapshot else ""

merger = AnnotationMerger(context_resolver, handlers)
merged = merger.merge(MergeContext(
    old_markdown=old_markdown,
    new_markdown=clean_content,
    device_rm_files=existing_rm_files,
    geometry=self.config.layout.get_device_geometry(),
))

# Pass to generator - NO annotation logic in generator
rm_doc = self.generator.generate_document(
    md_doc, parent_uuid, existing_uuid, existing_page_uuids,
    merged_annotations=merged.annotations,
)
```

### Phase 5: Simplify DocumentGenerator

Refactor generator to receive MergedAnnotations:
- Remove annotation migration logic from generator
- Remove duplicate pagination (keep inline, remove from DocumentModel)
- DocumentModel becomes pure data container
- Generator receives: content blocks + MergedAnnotations

## Files Reference

### Phase 1 (Complete)
- `annotations/core_types.py` - Deleted `preserve_strokes_in_scene()`
- `annotations/__init__.py` - Cleaned exports
- `converter.py` - Added snapshot cleanup call
- `annotations/handlers/highlight_handler.py` - Documented reflow limitation

### Phase 2 (In Progress)
- CREATE: `annotations/stroke.py`
- CREATE: `annotations/stroke_cluster.py`
- UPDATE: `annotations/handlers/stroke_handler.py`
- DEPRECATE: `annotations/core_types.py` (old Stroke, StrokeData)
- DEPRECATE: `annotations/scene_graph.py` (StrokeBundle)

### Phase 3
- DEPRECATE: `annotations/core_types.py` (HeuristicTextAnchor)
- DEPRECATE: `annotations/common/anchors.py` (AnnotationAnchor)
- CREATE: `annotations/services/crdt_service.py`
- UPDATE: `annotations/core/protocol.py`

### Phase 4
- CREATE: `annotations/merger.py`
- UPDATE: `converter.py`
- UPDATE: `snapshots.py`

### Phase 5
- UPDATE: `generator.py`
- UPDATE: `document_model.py`
- UPDATE: `annotations/README.md`

## Handler Interface

The handler protocol remains stable but with clarified responsibilities:

```python
@runtime_checkable
class AnnotationHandler(Protocol):
    @property
    def annotation_type(self) -> str:
        """Discriminator for 'is this yours?' pattern."""
        ...

    def detect(self, rm_file_path: Path) -> list[Annotation]:
        """Extract annotations from .rm file."""
        ...

    def create_anchor_context(
        self,
        annotation: Annotation,
        page_text: str,
        layout_context: LayoutContext,
    ) -> AnchorContext:
        """Create anchor context with type-specific signals.

        Highlights: text content, character offset
        Strokes: Y-position, bounding box
        """
        ...

    def serialize(self, annotations: list[Annotation]) -> list[Block]:
        """Serialize annotations to CRDT blocks."""
        ...
```

**Note**: Handlers do NOT have a `resolve()` method. Resolution is centralized in `ContextResolver`.

## Known Limitations

### Reflow Detection (highlight_handler.py:598-616)

Reflow detection is currently disabled because the layout model's paragraph spacing doesn't match device rendering. The model joins text_blocks with '\n' creating artificial line breaks.

To properly fix this, we would need:
1. Per-paragraph Y origins from the layout model
2. Character offsets relative to paragraph, not document
3. AnnotationMerger to handle cross-paragraph highlight migration

For now, the delta-based approach works well enough for most cases.

## Testing Requirements

- All existing tests must pass after each phase
- New tests per phase:
  - Phase 2: StrokeCluster roundtrip (read rm → create clusters → serialize → compare)
  - Phase 3: Service isolation tests
  - Phase 4: Three-way merge scenarios (old changed, new changed, both changed)
  - Phase 5: Full sync with MergedAnnotations input
- Record/replay tests validate real device behavior
