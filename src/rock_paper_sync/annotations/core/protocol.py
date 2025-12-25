"""Protocol definitions for pluggable annotation handlers.

This module defines the AnnotationHandler Protocol, which enables type-safe,
extensible annotation processing without tight coupling. Each annotation type
(highlights, strokes, sketches, diagrams, etc.) implements this protocol.

Design principles:
- Use typing.Protocol for structural subtyping (not ABC)
- Handlers are pure processors (no state beyond config)
- Corrections are managed separately by CorrectionManager
- Coordinate transformation is centralized in RelocationService (not handler-specific)
- Layout context provides unified access to text positioning (shared infrastructure)
- CRDT operations delegated to CrdtService (not handler-specific)

Method categories:
- REQUIRED: annotation_type, relocation_strategy, detect(), map(), create_anchor(), extract_from_markdown()

Relocation architecture:
- Handlers declare a RelocationStrategy via the relocation_strategy property
- The actual relocation logic lives in RelocationService (annotations/relocation.py)
- This prevents the class of bugs where a handler implements relocate() as a silent no-op

For stroke handling, StrokeHandler also provides cluster-based methods:
- detect_clusters() - extract StrokeClusters with CRDT context
- migrate_clusters() - migrate clusters using ContextResolver
- serialize_for_page() - serialize clusters for specific page

Example usage (traditional interface):
    class StrokeHandler:
        @property
        def annotation_type(self) -> str:
            return "stroke"

        @property
        def relocation_strategy(self) -> RelocationStrategy:
            return RelocationStrategy.ANCHOR_DELTA

        def detect(self, rm_file_path: Path) -> list[Annotation]:
            # Extract stroke annotations from .rm file
            ...

        def map(self, annotations, markdown_blocks, rm_file_path, layout_context) -> dict:
            # Map strokes to paragraph indices using layout context
            ...

Example usage (cluster-based interface for migration):
    handler = StrokeHandler()
    clusters = handler.detect_clusters(rm_file_path)
    migrated = handler.migrate_clusters(clusters, old_text, new_text, resolver)
    blocks = handler.serialize_for_page(migrated, page_projection)
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from rock_paper_sync.annotations.relocation import RelocationStrategy

if TYPE_CHECKING:
    from rock_paper_sync.annotations.common.anchors import AnnotationAnchor
    from rock_paper_sync.annotations.core.data_types import ExtractedAnnotation, RenderConfig
    from rock_paper_sync.layout import LayoutContext


@runtime_checkable
class AnnotationHandler(Protocol):
    """Protocol for annotation type handlers.

    Each annotation type (highlights, strokes, sketches, etc.) implements
    this protocol to provide type-specific detection, mapping, and anchoring.

    Handler responsibilities:
    1. Detection: Extract annotations from .rm files
    2. Mapping: Associate annotations with markdown paragraphs
    3. Anchoring: Create stable anchors for annotation preservation
    4. Extraction: Parse annotations from rendered markdown

    Method categories:
    - REQUIRED: annotation_type, relocation_strategy, detect(), map(), create_anchor(), extract_from_markdown()

    Relocation is handled by RelocationService, not by handlers. Handlers
    declare their relocation_strategy to indicate how their annotations
    should be relocated when content changes.

    CRDT operations (ID generation, block cloning) are delegated to CrdtService,
    not handled by individual handlers.

    For stroke-specific cluster operations, see StrokeHandler which extends
    this protocol with detect_clusters(), migrate_clusters(), serialize_for_page().
    """

    @property
    def annotation_type(self) -> str:
        """Unique identifier for this annotation type.

        Returns:
            Type identifier (e.g., "highlight", "stroke", "sketch")
        """
        ...

    @property
    def relocation_strategy(self) -> RelocationStrategy:
        """Strategy for relocating annotations when content changes.

        This property is REQUIRED. Handlers must declare how their annotations
        should be relocated. The actual relocation is performed by RelocationService.

        Returns:
            RelocationStrategy enum value:
            - CONTENT_DELTA: Text anchor + layout delta (for highlights)
            - ANCHOR_DELTA: TreeNodeBlock anchor + Y adjustment (for strokes)
            - ABSOLUTE: Explicit no-op for root layer items
        """
        ...

    def detect(self, rm_file_path: Path) -> list[Any]:
        """Extract annotations of this type from .rm file.

        Args:
            rm_file_path: Path to reMarkable v6 .rm file

        Returns:
            List of annotation objects (type-specific structure)
        """
        ...

    def map(
        self,
        annotations: list[Any],
        markdown_blocks: list[Any],
        rm_file_path: Path,
        layout_context: "LayoutContext | None" = None,
    ) -> dict[int, list[Any]]:
        """Map annotations to markdown paragraph indices.

        Args:
            annotations: List of annotations from detect()
            markdown_blocks: List of markdown content blocks
            rm_file_path: Path to .rm file (for coordinate extraction)
            layout_context: Optional layout context for position calculations.
                When provided, enables accurate character-offset-based mapping
                using the shared layout infrastructure. Strokes can use
                position_to_offset() for content-based anchoring.

        Returns:
            Dict mapping paragraph_index -> list of matching annotations
        """
        ...

    def create_anchor(
        self,
        annotation: Any,
        paragraph_text: str,
        paragraph_index: int,
        page_num: int = 0,
    ) -> "AnnotationAnchor":
        """Create an anchor from an annotation for matching and correction detection.

        Anchors encapsulate all information needed to:
        - Match annotations across syncs (fuzzy matching)
        - Detect corrections from markdown edits
        - Apply corrections to RM files

        This method extracts position/content information from the annotation
        for anchor creation and matching.

        Args:
            annotation: Annotation object from detect()
            paragraph_text: Full text of the matched paragraph
            paragraph_index: Index of paragraph in markdown
            page_num: Page number (default: 0)

        Returns:
            AnnotationAnchor with all location/content information

        Example (HighlightHandler):
            anchor = self.create_anchor(
                annotation=highlight_anno,
                paragraph_text="This is the full paragraph text with highlight.",
                paragraph_index=5,
                page_num=0
            )
        """
        ...

    def extract_from_markdown(
        self,
        paragraph: str,
        config: "RenderConfig",
    ) -> list["ExtractedAnnotation"]:
        """Extract annotations from markdown based on rendering configuration.

        Each handler knows how it rendered annotations and can extract them back
        using simple pattern matching. This enables correction detection by
        comparing extracted annotations across snapshot versions.

        Args:
            paragraph: Markdown paragraph text
            config: Rendering configuration (determines patterns to match)

        Returns:
            List of extracted annotations with text and offsets

        Example (HighlightHandler with mark style):
            paragraph = "Text with <mark>highlighted part</mark> here"
            config = RenderConfig(highlight_style="mark")
            extracted = handler.extract_from_markdown(paragraph, config)
            # Returns: [ExtractedAnnotation(text="highlighted part", ...)]

        Example (StrokeHandler with footnote style):
            paragraph = "Handwritten text[^1]\\n\\n[^1]: OCR confidence 0.95"
            config = RenderConfig(stroke_style="footnote")
            extracted = handler.extract_from_markdown(paragraph, config)
            # Returns: [ExtractedAnnotation(text="Handwritten text", ...)]
        """
        ...
