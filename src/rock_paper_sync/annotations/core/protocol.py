"""Protocol definitions for pluggable annotation handlers.

This module defines the AnnotationHandler Protocol, which enables type-safe,
extensible annotation processing without tight coupling. Each annotation type
(highlights, strokes, sketches, diagrams, etc.) implements this protocol.

Design principles:
- Use typing.Protocol for structural subtyping (not ABC)
- Handlers are pure processors (no state beyond config)
- Corrections are managed separately by CorrectionManager
- Coordinate transformation is a shared utility (not handler-specific)
- Layout context provides unified access to text positioning (shared infrastructure)

Example usage:
    class StrokeHandler:
        @property
        def annotation_type(self) -> str:
            return "stroke"

        def detect(self, rm_file_path: Path) -> list[Annotation]:
            # Extract stroke annotations from .rm file
            ...

        def map(self, annotations, markdown_blocks, rm_file_path, layout_context) -> dict:
            # Map strokes to paragraph indices using layout context
            ...

        def render(self, paragraph_index, matches, content) -> str:
            # Generate OCR markdown output
            ...
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rock_paper_sync.annotations.common.anchors import AnnotationAnchor
    from rock_paper_sync.annotations.core.data_types import ExtractedAnnotation, RenderConfig
    from rock_paper_sync.layout import LayoutContext


@runtime_checkable
class AnnotationHandler(Protocol):
    """Protocol for annotation type handlers.

    Each annotation type (highlights, strokes, sketches, etc.) implements
    this protocol to provide type-specific detection, mapping, rendering,
    and state management.

    Handler responsibilities:
    1. Detection: Extract annotations from .rm files
    2. Mapping: Associate annotations with markdown paragraphs
    3. Rendering: Generate markdown output with annotation markers
    4. State: Manage handler-specific stateful concerns

    Coordinate transformation and generic corrections are handled by
    shared utilities. Handlers encapsulate type-specific state operations.
    """

    @property
    def annotation_type(self) -> str:
        """Unique identifier for this annotation type.

        Returns:
            Type identifier (e.g., "highlight", "stroke", "sketch")
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

    def render(
        self,
        paragraph_index: int,
        matches: list[Any],
        original_content: str,
    ) -> str:
        """Generate markdown output for annotations at a paragraph.

        Args:
            paragraph_index: Index of paragraph in markdown
            matches: List of annotations mapped to this paragraph
            original_content: Original paragraph text

        Returns:
            Markdown text with annotation markers/content
        """
        ...

    def init_state_schema(self, db_connection: Any) -> None:
        """Initialize handler-specific state schema in database.

        Called once at startup to ensure handler's state tables exist.
        Handlers can create their own tables for type-specific concerns.

        Example (StrokeHandler):
            CREATE TABLE stroke_ocr_cache (
                annotation_id TEXT PRIMARY KEY,
                image_hash TEXT,
                ocr_text TEXT,
                confidence REAL
            )

        Args:
            db_connection: SQLite database connection
        """
        ...

    def store_state(
        self,
        db_connection: Any,
        document_id: str,
        annotation_id: str,
        state_data: dict[str, Any],
    ) -> None:
        """Store handler-specific state for an annotation.

        Args:
            db_connection: SQLite database connection
            document_id: Document UUID
            annotation_id: Annotation identifier
            state_data: Type-specific state data to store
        """
        ...

    def load_state(
        self,
        db_connection: Any,
        document_id: str,
        annotation_id: str,
    ) -> dict[str, Any] | None:
        """Load handler-specific state for an annotation.

        Args:
            db_connection: SQLite database connection
            document_id: Document UUID
            annotation_id: Annotation identifier

        Returns:
            Type-specific state data, or None if not found
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

    def get_position(
        self,
        block: Any,
        text_origin_y: float,
    ) -> tuple[float, float] | None:
        """Get absolute (x, y) position for an annotation block.

        Encapsulates type-specific coordinate transformation logic.
        Used by AnnotationPreserver for routing decisions during
        document regeneration.

        Args:
            block: Raw rmscene annotation block (SceneGlyphItemBlock or SceneLineItemBlock)
            text_origin_y: Y coordinate of text origin from .rm file

        Returns:
            Tuple of (absolute_x, absolute_y) in page coordinates,
            or None if position cannot be determined.

        Note:
            - Highlights use simple text-relative offset
            - Strokes use dual-anchor system with NEGATIVE_Y_OFFSET for negative Y
        """
        ...

    def relocate(
        self,
        block: Any,
        old_text: str,
        new_text: str,
        old_origin: tuple[float, float],
        new_origin: tuple[float, float],
        layout_engine: Any,
        geometry: Any,
        crdt_base_id: int | None = None,
    ) -> Any:
        """Relocate annotation when content changes.

        Adjusts annotation coordinates when document text shifts. Used by
        AnnotationPreserver during document regeneration.

        Args:
            block: Raw rmscene annotation block
            old_text: Page text before modification
            new_text: Page text after modification
            old_origin: (x, y) origin of old text block
            new_origin: (x, y) origin of new text block
            layout_engine: WordWrapLayoutEngine for position calculations
            geometry: DeviceGeometry for layout parameters
            crdt_base_id: Base ID for CRDT offset calculation (highlights only)

        Returns:
            Modified block with adjusted coordinates/anchors

        Note:
            - Highlights use content-based anchoring with delta calculation
            - Strokes return block unchanged (anchor roundtrip handles them)
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
