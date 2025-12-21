# Annotation Architecture V2: AnchorContext-Based Preservation

**Status**: Production
**Date**: 2025-12-08 (designed), 2025-12-21 (production)
**Implementation**: `src/rock_paper_sync/annotations/document_model.py`
**Entry Point**: `generator.py:generate_document()` calls `DocumentModel.migrate_annotations_to()`

## Executive Summary

This document describes a revised architecture for annotation preservation in rock-paper-sync. The current system works but has fundamental limitations that make it fragile when content changes. The new architecture introduces:

1. **AnchorContext** - A multi-signal stable identifier replacing raw character offsets
2. **DiffAnchor** - Anchoring relative to unchanged text for edit resilience
3. **DocumentModel** - Document-level abstraction with pages as projections
4. **ContextResolver** - Unified resolution using fuzzy matching (HeuristicTextAnchor)

## Problem Statement

### Current Architecture Limitations

The current system anchors annotations using **character offsets** into the RootTextBlock text. This is fundamentally fragile:

```
TreeNodeBlock.anchor_id.part2 = 106  // "character 106 in page text"
```

**Why this breaks:**
- Character offsets are ephemeral - they change with any text edit
- Delta-based updates (`offset + inserted_length`) fail for rewrites
- No semantic understanding of *what* the annotation is attached to
- Page-first design requires complex cross-page routing logic

### Bug History

Two significant bugs stemmed from this architecture:

1. **Phase 1 (Cross-page ordering)**: .rm files processed in UUID order instead of page order
2. **Phase 2 (Same-page text changes)**: Cumulative offsets from filtered TextBlocks didn't match full RootTextBlock positions

Both bugs share a root cause: **mixing coordinate systems** without explicit boundaries.

### What We Want

Annotations should be anchored to **content**, not **positions**. When a user:
- Adds text above an annotation → annotation stays with its content
- Edits the text an annotation is on → annotation follows the edited content
- Rewrites a paragraph completely → we do our best to find where it went
- Deletes the annotated content → annotation is orphaned (explicit state)

## Core Abstractions

### AnchorContext

Replaces the concept of "anchor" (which was just a character offset) with a multi-signal stable identifier.

```python
@dataclass(frozen=True)
class AnchorContextSignal:
    """A single signal contributing to context identification."""
    signal_type: Literal["text_content", "text_hash", "structure", "spatial", "semantic"]
    value: Any
    weight: float = 1.0  # Contribution to matching score


@dataclass
class AnchorContext:
    """A stable anchor point in document space.

    Represents "the thing this annotation is attached to" using multiple
    signals that together survive content edits.

    Invariants:
    - Must have at least one text-based signal for content matching
    - Spatial signals are hints, not primary identifiers
    - Structure signals (paragraph index, heading level) provide coarse matching
    """

    # === Primary Identification ===
    content_hash: str  # Hash of normalized content (survives formatting changes)
    text_content: str  # Actual text for fuzzy matching

    # === Structural Position ===
    paragraph_index: int | None = None  # Index in document paragraph list
    section_path: tuple[str, ...] = ()  # e.g., ("Chapter 1", "Section 1.2")

    # === Contextual Anchoring ===
    context_before: str = ""  # ~50 chars before (for fuzzy matching)
    context_after: str = ""   # ~50 chars after (for fuzzy matching)

    # === Spatial Hints ===
    line_range: tuple[int, int] | None = None  # (start_line, end_line) in document
    y_position_hint: float | None = None  # Approximate Y coordinate
    page_hint: int | None = None  # Expected page (may change after edit)

    # === Diff-Based Stability ===
    diff_anchor: "DiffAnchor | None" = None  # Anchor relative to stable text

    # === Methods ===
    def resolve_in(self, new_document: "DocumentModel") -> "ResolvedAnchorContext | None":
        """Find where this context exists in a changed document.

        Resolution strategy (in order):
        1. Exact content hash match
        2. Fuzzy match using HeuristicTextAnchor (text + context windows)
        3. Diff anchor resolution (stable neighbor text)
        4. Spatial fallback (Y position + paragraph structure)

        Returns None if context cannot be resolved (content deleted).
        """
        ...

    def similarity_to(self, other: "AnchorContext") -> float:
        """Calculate similarity score between contexts.

        Used for matching annotations to potential target locations.
        Weights: text_content (0.5) + context (0.3) + structure (0.15) + spatial (0.05)
        """
        ...

    @classmethod
    def from_text_span(
        cls,
        full_text: str,
        start_offset: int,
        end_offset: int,
        layout: "LayoutContext | None" = None,
        paragraph_index: int | None = None,
    ) -> "AnchorContext":
        """Create AnchorContext from a text span.

        Extracts content, builds context windows, computes hash,
        and optionally adds spatial hints from layout.
        """
        ...

    @classmethod
    def from_spatial_position(
        cls,
        y_position: float,
        full_text: str,
        layout: "LayoutContext",
    ) -> "AnchorContext":
        """Create AnchorContext from a spatial position (for strokes).

        Uses layout engine to find which text region the Y position
        corresponds to, then builds full context.
        """
        ...


@dataclass
class ResolvedAnchorContext:
    """Result of resolving an AnchorContext in a document."""

    start_offset: int  # Character offset in target document
    end_offset: int
    confidence: float  # 0.0 to 1.0
    match_type: Literal["exact", "fuzzy", "diff_anchor", "spatial"]
    target_paragraph_index: int | None = None
```

### DiffAnchor

When text is edited, some portions remain unchanged. These **stable regions** provide reliable anchoring.

```python
@dataclass(frozen=True)
class DiffAnchor:
    """Anchor relative to stable (unchanged) content.

    When text is edited, DiffAnchor tracks position relative to
    the nearest unchanged text, which is more stable than absolute offsets.

    Example:
        Old: "The quick brown fox jumps over the lazy dog."
        New: "The quick red fox leaps over the lazy dog."

        Stable regions:
        - "The quick " (before change)
        - " over the lazy dog." (after change)

        An annotation on "brown fox" can be anchored as:
        - stable_before = "The quick "
        - offset_from_before = 0 (immediately after)
        - stable_after = " over the lazy dog."
        - offset_from_after = 8 (8 chars before stable_after)

        In new document, "red fox" is at:
        - Find "The quick " → ends at offset 10
        - Find " over the lazy dog." → starts at offset 18
        - Target region: [10, 18] = "red fox "
    """

    # Stable text anchors
    stable_before: str        # Unchanged text before target
    stable_before_hash: str   # Hash for fast matching
    stable_after: str         # Unchanged text after target
    stable_after_hash: str    # Hash for fast matching

    # Relative positioning
    offset_from_before: int   # Characters after stable_before ends
    offset_from_after: int    # Characters before stable_after starts

    @classmethod
    def from_diff(
        cls,
        old_text: str,
        new_text: str,
        target_span: tuple[int, int],  # (start, end) in old_text
        context_size: int = 50,
    ) -> "DiffAnchor | None":
        """Create DiffAnchor by analyzing old/new text diff.

        Uses difflib.SequenceMatcher to find stable regions, then
        builds anchor relative to nearest stable text.

        Returns None if no stable anchors can be found (complete rewrite).
        """
        import difflib

        matcher = difflib.SequenceMatcher(None, old_text, new_text)
        matching_blocks = matcher.get_matching_blocks()

        # Find stable regions before and after target
        # ... implementation details ...

    def resolve_in(self, new_text: str) -> tuple[int, int] | None:
        """Find target span in new text using stable anchors.

        1. Find stable_before in new_text
        2. Find stable_after in new_text
        3. Calculate target region from offsets

        Returns None if stable anchors not found.
        """
        before_pos = new_text.find(self.stable_before)
        if before_pos == -1:
            return None

        after_pos = new_text.find(self.stable_after, before_pos)
        if after_pos == -1:
            return None

        start = before_pos + len(self.stable_before) + self.offset_from_before
        end = after_pos - self.offset_from_after

        if start <= end and start >= 0 and end <= len(new_text):
            return (start, end)
        return None
```

### DocumentModel

The primary abstraction for annotation preservation. Pages are **projections**, not the source of truth.

```python
@dataclass
class Paragraph:
    """A paragraph of content in the document."""

    content: str
    paragraph_type: Literal["heading", "paragraph", "list_item", "code_block"]
    heading_level: int | None = None  # For headings: 1-6
    list_level: int | None = None     # For list items: nesting depth

    # Position in document (computed during model construction)
    char_start: int = 0
    char_end: int = 0

    @property
    def section_path(self) -> tuple[str, ...]:
        """Build section path from preceding headings."""
        # Computed by DocumentModel during construction
        ...


@dataclass
class DocumentAnnotation:
    """An annotation in document space (page-agnostic).

    Annotations exist at the document level. Page boundaries are
    determined during projection, not when defining the annotation.
    """

    # Identity
    annotation_id: str
    annotation_type: Literal["stroke", "highlight", "margin_note"]

    # What is this annotation attached to?
    anchor_context: AnchorContext

    # The annotation data itself (type-specific)
    data: "StrokeData | HighlightData | MarginNoteData"

    # Original device representation (for delta-based coordinate updates)
    original_rm_blocks: list[Any] | None = None  # Raw rmscene blocks
    original_device_coords: tuple[float, float] | None = None


@dataclass
class StrokeData:
    """Stroke-specific annotation data."""
    points: list[tuple[float, float, float]]  # (x, y, pressure)
    color: int
    tool: int
    thickness: float
    bounding_box: tuple[float, float, float, float]  # (x, y, w, h)


@dataclass
class HighlightData:
    """Highlight-specific annotation data."""
    highlighted_text: str
    color: int
    rectangles: list[tuple[float, float, float, float]]  # (x, y, w, h) per line


@dataclass
class DocumentModel:
    """Document-level view of content and annotations.

    This is THE source of truth for annotation preservation.
    Pages are derived from this model via projection.

    Key insight: An annotation on paragraph 5 doesn't care whether
    paragraph 5 is on page 1 or page 3 - it cares about paragraph 5.
    """

    # === Content Structure ===
    paragraphs: list[Paragraph]
    full_text: str  # Concatenated text (paragraphs joined by \n)

    # === Annotations ===
    annotations: list[DocumentAnnotation]

    # === Layout ===
    layout: "LayoutContext"
    geometry: "DeviceGeometry"

    # === Construction ===
    @classmethod
    def from_rm_files(
        cls,
        rm_files: dict[str, bytes],  # page_uuid -> .rm content
        geometry: "DeviceGeometry",
    ) -> "DocumentModel":
        """Extract document model from existing .rm files.

        1. Read all pages
        2. Extract text from RootTextBlocks
        3. Build paragraph list with char offsets
        4. Extract annotations with AnchorContext
        5. Construct unified document view
        """
        ...

    @classmethod
    def from_markdown(
        cls,
        markdown_content: str,
        geometry: "DeviceGeometry",
    ) -> "DocumentModel":
        """Create document model from markdown source.

        No annotations - this is the "new content" side of migration.
        """
        ...

    # === Annotation Migration ===
    def migrate_annotations_to(
        self,
        new_content: "DocumentModel",
    ) -> tuple["DocumentModel", "MigrationReport"]:
        """Migrate annotations from this model to new content.

        The core operation for annotation preservation:
        1. For each annotation, resolve its AnchorContext in new_content
        2. Create new annotation at resolved location
        3. Track what moved where (MigrationReport)

        Returns:
            - New DocumentModel with migrated annotations
            - Report of what moved, what was orphaned, confidence scores
        """
        resolver = ContextResolver()
        migrated = []
        report = MigrationReport()

        for annotation in self.annotations:
            resolved = resolver.resolve(
                annotation.anchor_context,
                self.full_text,
                new_content.full_text,
                self.layout,
                new_content.layout,
            )

            if resolved:
                new_annotation = self._migrate_single(annotation, resolved, new_content)
                migrated.append(new_annotation)
                report.add_migration(annotation, new_annotation, resolved)
            else:
                report.add_orphan(annotation)

        return new_content.with_annotations(migrated), report

    # === Page Projection ===
    def project_to_pages(self) -> list["PageProjection"]:
        """Project document to pages for .rm file generation.

        This is where page boundaries are determined. Annotations
        naturally flow to correct pages based on their anchor context.

        No "cross-page routing" logic - just projection.
        """
        pages = []
        lines_per_page = self.geometry.lines_per_page

        # Calculate pagination from layout
        line_breaks = self.layout.calculate_line_breaks(
            self.full_text,
            self.geometry.text_width,
        )

        # Assign paragraphs to pages
        # ... pagination logic ...

        # Assign annotations to pages based on resolved positions
        for page in pages:
            for annotation in self.annotations:
                if self._annotation_on_page(annotation, page):
                    page.annotations.append(annotation)

        return pages


@dataclass
class PageProjection:
    """A page as rendered from DocumentModel.

    This is a VIEW, not source of truth. Used for .rm generation.
    """

    page_index: int
    page_uuid: str

    # Content spans on this page
    paragraphs: list[tuple[Paragraph, int, int]]  # (paragraph, local_start, local_end)
    page_text: str  # Full text for this page's RootTextBlock

    # Annotations projected to this page
    annotations: list[DocumentAnnotation]

    # Layout
    text_origin: tuple[float, float]
    text_blocks: list["TextBlock"]  # For backward compatibility


@dataclass
class MigrationReport:
    """Report of annotation migration results."""

    migrations: list[tuple[DocumentAnnotation, DocumentAnnotation, ResolvedAnchorContext]]
    orphans: list[DocumentAnnotation]  # Annotations that couldn't be resolved

    @property
    def success_rate(self) -> float:
        total = len(self.migrations) + len(self.orphans)
        return len(self.migrations) / total if total > 0 else 1.0

    @property
    def average_confidence(self) -> float:
        if not self.migrations:
            return 0.0
        return sum(r.confidence for _, _, r in self.migrations) / len(self.migrations)
```

### ContextResolver

Unified resolution strategy integrating HeuristicTextAnchor (PRESERVED from current codebase).

```python
class ContextResolver:
    """Resolves AnchorContext across document versions.

    Integrates HeuristicTextAnchor for fuzzy matching - this class
    is preserved and promoted from the current implementation.
    """

    def __init__(
        self,
        context_window: int = 50,
        fuzzy_threshold: float = 0.8,
        use_font_metrics: bool = True,
    ):
        # HeuristicTextAnchor from current codebase - PRESERVED
        from rock_paper_sync.annotations.core_types import HeuristicTextAnchor

        self._heuristic = HeuristicTextAnchor(
            context_window=context_window,
            fuzzy_threshold=fuzzy_threshold,
        )
        self._use_font_metrics = use_font_metrics

    def resolve(
        self,
        context: AnchorContext,
        old_text: str,
        new_text: str,
        old_layout: "LayoutContext",
        new_layout: "LayoutContext",
    ) -> ResolvedAnchorContext | None:
        """Resolve context in new document.

        Strategy (in order of preference):
        1. Exact content hash match - highest confidence
        2. Fuzzy match with HeuristicTextAnchor - content + context windows
        3. Diff anchor resolution - stable neighbor text
        4. Spatial fallback - Y position + structure hints

        Returns None if context cannot be resolved (content deleted).
        """

        # === Strategy 1: Exact Hash Match ===
        hash_matches = self._find_by_hash(context.content_hash, new_text)
        if len(hash_matches) == 1:
            return ResolvedAnchorContext(
                start_offset=hash_matches[0][0],
                end_offset=hash_matches[0][1],
                confidence=1.0,
                match_type="exact",
            )
        elif len(hash_matches) > 1:
            # Multiple matches - use context to disambiguate
            best = self._disambiguate_by_context(context, hash_matches, new_text)
            if best:
                return ResolvedAnchorContext(
                    start_offset=best[0],
                    end_offset=best[1],
                    confidence=0.95,
                    match_type="exact",
                )

        # === Strategy 2: Fuzzy Match with HeuristicTextAnchor ===
        if context.y_position_hint is not None:
            old_position = old_layout.offset_to_position(
                self._estimate_offset(context, old_text)
            )
        else:
            old_position = (0.0, 0.0)

        anchor = self._heuristic.find_anchor(
            context.text_content,
            old_text,
            old_position,
        )

        new_offset = self._heuristic.resolve_anchor(anchor, new_text)
        if new_offset is not None and anchor.confidence >= self._heuristic.fuzzy_threshold:
            return ResolvedAnchorContext(
                start_offset=new_offset,
                end_offset=new_offset + len(context.text_content),
                confidence=anchor.confidence,
                match_type="fuzzy",
            )

        # === Strategy 3: Diff Anchor ===
        if context.diff_anchor:
            span = context.diff_anchor.resolve_in(new_text)
            if span:
                return ResolvedAnchorContext(
                    start_offset=span[0],
                    end_offset=span[1],
                    confidence=0.6,  # Lower confidence for diff-based
                    match_type="diff_anchor",
                )

        # === Strategy 4: Spatial Fallback ===
        if context.y_position_hint is not None:
            spatial_match = self._resolve_by_spatial(context, new_layout, new_text)
            if spatial_match:
                return ResolvedAnchorContext(
                    start_offset=spatial_match[0],
                    end_offset=spatial_match[1],
                    confidence=0.4,  # Low confidence - spatial only
                    match_type="spatial",
                )

        # Cannot resolve
        return None

    def _find_by_hash(self, content_hash: str, text: str) -> list[tuple[int, int]]:
        """Find all spans matching content hash."""
        # Implementation: sliding window hash comparison
        ...

    def _disambiguate_by_context(
        self,
        context: AnchorContext,
        candidates: list[tuple[int, int]],
        text: str,
    ) -> tuple[int, int] | None:
        """Choose best candidate using context windows."""
        best_score = 0.0
        best_candidate = None

        for start, end in candidates:
            before = text[max(0, start - len(context.context_before)):start]
            after = text[end:end + len(context.context_after)]

            score = (
                _similarity(before, context.context_before) * 0.5 +
                _similarity(after, context.context_after) * 0.5
            )

            if score > best_score:
                best_score = score
                best_candidate = (start, end)

        return best_candidate if best_score > 0.5 else None

    def _resolve_by_spatial(
        self,
        context: AnchorContext,
        layout: "LayoutContext",
        text: str,
    ) -> tuple[int, int] | None:
        """Resolve using spatial position hints."""
        if context.line_range:
            # Find paragraph at expected line
            target_line = context.line_range[0]
            offset = layout.line_to_offset(target_line)

            # Find paragraph boundaries
            para_start = text.rfind('\n', 0, offset) + 1
            para_end = text.find('\n', offset)
            if para_end == -1:
                para_end = len(text)

            return (para_start, para_end)

        return None
```

## Data Flow

### Current Flow (Page-First)

```
[Old .rm files]
    │
    ├── Page 1 ──┐
    ├── Page 2 ──┼── Extract per-page text blocks and annotations
    └── Page 3 ──┘
                 │
                 v
    ┌────────────────────────────────────────┐
    │ Build document-level position mappings │
    │ (retrofitted onto page-first design)   │
    └────────────────────────────────────────┘
                 │
                 v
    ┌────────────────────────────────────────┐
    │ Route annotations to target pages      │
    │ (complex cross-page special cases)     │
    └────────────────────────────────────────┘
                 │
                 v
    ┌────────────────────────────────────────┐
    │ Build PageAnnotationContext per page   │
    │ (mutations via side effects)           │
    └────────────────────────────────────────┘
                 │
                 v
[Generated .rm files]
```

### New Flow (Document-First)

```
[Old .rm files] ─────────────────────────────────────────────────────┐
       │                                                              │
       v                                                              │
┌──────────────────────────────────────────────────────────────────┐ │
│ DocumentModel.from_rm_files()                                     │ │
│                                                                   │ │
│ • Read ALL pages as unified document                              │ │
│ • Extract full_text from RootTextBlocks                          │ │
│ • Build Paragraph list with char offsets                         │ │
│ • Extract annotations with AnchorContext (not raw offsets)       │ │
│ • Construct LayoutContext for position calculations              │ │
└──────────────────────────────────────────────────────────────────┘ │
       │                                                              │
       v                                                              │
[Old DocumentModel]                                                   │
       │                                                              │
       │                     [New Markdown] ──────────┐              │
       │                            │                  │              │
       │                            v                  │              │
       │              ┌─────────────────────────────┐  │              │
       │              │ DocumentModel.from_markdown()│  │              │
       │              │                             │  │              │
       │              │ • Parse markdown            │  │              │
       │              │ • Build Paragraph list      │  │              │
       │              │ • No annotations yet        │  │              │
       │              └─────────────────────────────┘  │              │
       │                            │                  │              │
       │                            v                  │              │
       │                   [New DocumentModel]         │              │
       │                     (empty annotations)       │              │
       │                            │                  │              │
       ├────────────────────────────┼──────────────────┘              │
       │                            │                                 │
       v                            v                                 │
┌──────────────────────────────────────────────────────────────────┐ │
│ old_model.migrate_annotations_to(new_model)                       │ │
│                                                                   │ │
│ For each annotation:                                              │ │
│   1. resolver.resolve(annotation.anchor_context, ...)             │ │
│      • Try exact content hash match                               │ │
│      • Fall back to HeuristicTextAnchor fuzzy match              │ │
│      • Use DiffAnchor if text completely changed                 │ │
│      • Spatial fallback for unmatched                            │ │
│   2. Create new DocumentAnnotation at resolved location          │ │
│   3. Track in MigrationReport                                    │ │
└──────────────────────────────────────────────────────────────────┘ │
       │                                                              │
       v                                                              │
[New DocumentModel with migrated annotations]                         │
       │                                                              │
       v                                                              │
┌──────────────────────────────────────────────────────────────────┐ │
│ new_model.project_to_pages()                                      │ │
│                                                                   │ │
│ • Calculate pagination from layout                                │ │
│ • Assign paragraphs to pages by line count                       │ │
│ • Annotations flow to pages naturally (no "routing")             │ │
│ • Generate PageProjection for each page                          │ │
└──────────────────────────────────────────────────────────────────┘ │
       │                                                              │
       v                                                              │
[List of PageProjection]                                              │
       │                                                              │
       v                                                              │
┌──────────────────────────────────────────────────────────────────┐ │
│ Generate .rm files from PageProjections                           │ │
│                                                                   │ │
│ • Convert Paragraph to RootTextBlock text items                  │ │
│ • Convert DocumentAnnotation to device blocks:                   │ │
│   - Strokes → SceneLineItemBlock + TreeNodeBlock                 │ │
│   - Highlights → SceneGlyphItemBlock                             │ │
│ • Calculate TreeNodeBlock anchor_id from AnchorContext           │ │
│   (character offset is computed HERE, not stored)                │ │
│ • Apply coordinate transformations                               │ │
└──────────────────────────────────────────────────────────────────┘ │
       │                                                              │
       v                                                              │
[Generated .rm files] <───────────────────────────────────────────────┘
```

## What to Preserve

### 1. HeuristicTextAnchor (CRITICAL)

**Location**: `src/rock_paper_sync/annotations/core_types.py` lines 491-710

This class provides the core fuzzy matching algorithm:
- Context windows (50 chars before/after)
- Position-informed disambiguation for duplicates
- Both X and Y position hints
- Configurable fuzzy threshold

**Action**: Wrap in `ContextResolver`, do not reimplement.

### 2. LayoutContext and WordWrapLayoutEngine

**Location**: `src/rock_paper_sync/layout/`

These provide solid infrastructure:
- Font metrics for accurate positioning
- Offset-to-position and position-to-offset conversions
- Line break calculation with word wrapping

**Action**: Extend with `create_context_for_span()` method.

### 3. Handler Protocol (Simplified)

The separation between `StrokeHandler` and `HighlightHandler` is correct - these annotation types genuinely need different handling for detection and rendering.

**Action**: Remove `relocate()` and `create_anchor()` from protocol. Handlers should only handle:
- `detect()` - Find annotations in .rm files
- `get_position()` - Extract spatial position
- `render()` - Render in markdown output

### 4. Coordinate Transformer

**Location**: `src/rock_paper_sync/annotations/coordinate_transformer.py`

Still needed for Y-position transformation (dual-anchor system for negative Y).

## What to Eliminate

### 1. Raw Character Offset Storage

**Current**: `TreeNodeBlock.anchor_id.part2 = char_offset`

**New**: Store `AnchorContext`, compute offset at .rm generation time.

### 2. Page-First Routing Logic

**Current**: `AnnotationPreserver._route_single_annotation()` - 170+ lines handling cross-page

**New**: Document projection makes cross-page movement natural (not special-cased).

### 3. Duplicate Offset Calculations

**Current**: 4+ places calculate character offsets differently

**New**: `AnchorContext.from_text_span()` is the single factory.

### 4. Unused Anchor Abstractions

**Files to delete**:
- `src/rock_paper_sync/annotations/common/anchors.py` (448 lines) - `AnnotationAnchor` never consumed
- Handler `create_anchor()` methods - write-only abstractions

### 5. Fallback Paths That Hide Bugs

**Current**: Cumulative offset calculation as fallback when `char_start` missing

**New**: Fail explicitly if AnchorContext cannot be created properly.

## Implementation Phases

### Phase 1: Core Types (Non-Breaking)

1. Add `AnchorContext` and `DiffAnchor` to `core_types.py`
2. Add `AnchorContext.from_text_span()` factory method
3. Add `ContextResolver` wrapping `HeuristicTextAnchor`
4. Extend `LayoutContext` with `create_anchor_context_for_span()`
5. Write comprehensive unit tests for context resolution

**Files Modified**:
- `src/rock_paper_sync/annotations/core_types.py`
- `src/rock_paper_sync/layout/context.py`
- New: `tests/annotations/test_anchor_context.py`

### Phase 2: DocumentModel Layer

1. Add `DocumentModel`, `Paragraph`, `DocumentAnnotation` types
2. Implement `DocumentModel.from_rm_files()` extraction
3. Implement `DocumentModel.from_markdown()` construction
4. Implement `DocumentModel.project_to_pages()`
5. Run in parallel with existing flow, compare outputs

**Files Created**:
- `src/rock_paper_sync/annotations/document_model.py`
- `tests/annotations/test_document_model.py`

### Phase 3: Migration Integration

1. Implement `DocumentModel.migrate_annotations_to()`
2. Add `MigrationReport` for tracking
3. Update generator to use DocumentModel flow
4. Add integration tests with real .rm files

**Files Modified**:
- `src/rock_paper_sync/generator.py`
- `src/rock_paper_sync/annotations/document_model.py`

### Phase 4: Deprecate Old Code

1. Remove `AnnotationPreserver` routing phases
2. Remove handler `relocate()` methods
3. Delete `common/anchors.py`
4. Remove fallback offset calculations
5. Update all tests

**Files Deleted**:
- `src/rock_paper_sync/annotations/common/anchors.py`

**Files Simplified**:
- `src/rock_paper_sync/annotations/preserver.py` (989 lines → ~300 lines)
- `src/rock_paper_sync/annotations/handlers/*.py`

## Testing Strategy

### Unit Tests

1. **AnchorContext creation**: Verify context windows, hash, spatial hints
2. **DiffAnchor resolution**: Test with various edit patterns
3. **ContextResolver strategies**: Each strategy in isolation
4. **DocumentModel extraction**: Round-trip .rm files

### Integration Tests

1. **Migration accuracy**: Compare migrated positions to manual placement
2. **Cross-page movement**: Verify annotations follow content naturally
3. **Content edits**: Test rewrite, insert, delete scenarios
4. **Device validation**: Anchors pass device-side validation

### Regression Tests

1. **Existing test_cross_page_reanchor.py**: Must continue passing
2. **Golden comparison**: Rectangle positions within tolerance
3. **Anchor validation**: No out-of-bounds anchors

## Open Questions

1. **Granularity of AnchorContext**: Should we anchor to paragraphs, sentences, or arbitrary spans?
   - Recommendation: Spans (most flexible), with paragraph as structural hint

2. **DiffAnchor persistence**: Should DiffAnchors be stored, or computed on-demand?
   - Recommendation: Computed on-demand during migration (not stored in .rm)

3. **Confidence thresholds**: What confidence is "good enough" for migration?
   - Recommendation: 0.6 minimum, with orphan list for manual review below that

4. **Spatial-only strokes**: How to handle strokes in margins (no text anchor)?
   - Recommendation: Anchor to nearest paragraph + store Y offset relative to paragraph

## Appendix: Type Summary

```python
# Core Types
AnchorContext          # Multi-signal stable identifier
AnchorContextSignal    # Individual signal component
DiffAnchor             # Anchor relative to stable text
ResolvedAnchorContext  # Result of resolving in new document

# Document Model
DocumentModel          # Source of truth for document + annotations
DocumentAnnotation     # Annotation in document space
Paragraph              # Content unit
PageProjection         # Page view for .rm generation
MigrationReport        # Migration tracking

# Resolution
ContextResolver        # Unified resolution strategy
HeuristicTextAnchor    # Fuzzy matching (PRESERVED from current code)

# Data Types
StrokeData             # Stroke-specific payload
HighlightData          # Highlight-specific payload
```
