"""Document-level annotation model for reMarkable documents.

This module implements the V2 annotation architecture based on AnchorContext.
The key insight is that annotations should be anchored to CONTENT, not POSITIONS.

Architecture:
    [Old .rm files] -> DocumentModel.from_rm_files()
           |
           v
    [Old DocumentModel with annotations]
           |
           +-- [New Markdown] -> DocumentModel.from_markdown()
           |          |
           |          v
           |   [New DocumentModel (no annotations)]
           |          |
           v          v
    old_model.migrate_annotations_to(new_model)
           |
           v
    [New DocumentModel with migrated annotations]
           |
           v
    new_model.project_to_pages()
           |
           v
    [List of PageProjection] -> .rm file generation
"""

from __future__ import annotations

import difflib
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import rmscene

from .core_types import HeuristicTextAnchor, Point, StrokeData
from .scene_graph import SceneGraphIndex, StrokeBundle

if TYPE_CHECKING:
    from rock_paper_sync.annotations.merging import AnnotationMerger
    from rock_paper_sync.layout import DeviceGeometry, LayoutContext, WordWrapLayoutEngine
    from rock_paper_sync.parser import ContentBlock

logger = logging.getLogger(__name__)


# =============================================================================
# Core Types: AnchorContext and DiffAnchor
# =============================================================================


def _normalize_text(text: str) -> str:
    """Normalize text for hashing (lowercase, collapse whitespace)."""
    return " ".join(text.lower().split())


def _content_hash(text: str) -> str:
    """Compute content hash for normalized text."""
    normalized = _normalize_text(text)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


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
    """

    stable_before: str  # Unchanged text before target
    stable_before_hash: str  # Hash for fast matching
    stable_after: str  # Unchanged text after target
    stable_after_hash: str  # Hash for fast matching
    offset_from_before: int  # Characters after stable_before ends
    offset_from_after: int  # Characters before stable_after starts

    @classmethod
    def from_text_span(
        cls,
        full_text: str,
        start: int,
        end: int,
        context_size: int = 50,
    ) -> DiffAnchor:
        """Create DiffAnchor from a text span.

        Args:
            full_text: Complete document text
            start: Start offset of target span
            end: End offset of target span
            context_size: Characters of stable text to capture
        """
        # Get stable text before
        before_start = max(0, start - context_size)
        stable_before = full_text[before_start:start]

        # Get stable text after
        after_end = min(len(full_text), end + context_size)
        stable_after = full_text[end:after_end]

        return cls(
            stable_before=stable_before,
            stable_before_hash=_content_hash(stable_before),
            stable_after=stable_after,
            stable_after_hash=_content_hash(stable_after),
            offset_from_before=0,  # Immediately after stable_before
            offset_from_after=0,  # Immediately before stable_after
        )

    def resolve_in(self, new_text: str) -> tuple[int, int] | None:
        """Find target span in new text using stable anchors.

        Returns (start, end) or None if stable anchors not found.
        """
        # Find stable_before in new_text
        before_pos = new_text.find(self.stable_before)
        if before_pos == -1:
            # Try fuzzy match
            before_pos = self._fuzzy_find(self.stable_before, new_text)
            if before_pos == -1:
                return None

        start = before_pos + len(self.stable_before) + self.offset_from_before

        # Find stable_after in new_text (search after start)
        after_pos = new_text.find(self.stable_after, start)
        if after_pos == -1:
            # Try fuzzy match
            after_pos = self._fuzzy_find(self.stable_after, new_text, start)
            if after_pos == -1:
                return None

        end = after_pos - self.offset_from_after

        if start <= end and 0 <= start <= len(new_text) and 0 <= end <= len(new_text):
            return (start, end)
        return None

    def _fuzzy_find(self, needle: str, haystack: str, start: int = 0) -> int:
        """Fuzzy find needle in haystack starting at position."""
        if len(needle) < 10:
            return -1

        # Use SequenceMatcher to find best match
        matcher = difflib.SequenceMatcher(None, needle, haystack[start:])
        match = matcher.find_longest_match(0, len(needle), 0, len(haystack) - start)

        if match.size >= len(needle) * 0.7:
            return start + match.b
        return -1


@dataclass
class AnchorContext:
    """A stable anchor point in document space.

    Represents "the thing this annotation is attached to" using multiple
    signals that together survive content edits.

    This replaces raw character offsets with a multi-signal identifier:
    - content_hash: Fast exact matching
    - text_content: Fuzzy matching
    - context_before/after: Context for disambiguation
    - paragraph_index: Structural hint
    - y_position_hint: Spatial fallback
    - diff_anchor: Edit-resilient anchoring
    """

    # Primary Identification
    content_hash: str  # Hash of normalized content
    text_content: str  # Actual text for fuzzy matching

    # Structural Position
    paragraph_index: int | None = None
    section_path: tuple[str, ...] = ()

    # Contextual Anchoring
    context_before: str = ""  # ~50 chars before
    context_after: str = ""  # ~50 chars after

    # Spatial Hints
    line_range: tuple[int, int] | None = None
    y_position_hint: float | None = None
    page_hint: int | None = None

    # Diff-Based Stability
    diff_anchor: DiffAnchor | None = None

    @classmethod
    def from_text_span(
        cls,
        full_text: str,
        start: int,
        end: int,
        paragraph_index: int | None = None,
        y_position: float | None = None,
    ) -> AnchorContext:
        """Create AnchorContext from a text span.

        Args:
            full_text: Complete document text
            start: Start character offset
            end: End character offset
            paragraph_index: Optional paragraph index
            y_position: Optional Y coordinate hint
        """
        text_content = full_text[start:end]
        context_before = full_text[max(0, start - 50) : start]
        context_after = full_text[end : end + 50]

        return cls(
            content_hash=_content_hash(text_content),
            text_content=text_content,
            paragraph_index=paragraph_index,
            context_before=context_before,
            context_after=context_after,
            y_position_hint=y_position,
            diff_anchor=DiffAnchor.from_text_span(full_text, start, end),
        )

    @classmethod
    def from_y_position(
        cls,
        y_position: float,
        full_text: str,
        layout: LayoutContext,
        paragraph_index: int | None = None,
    ) -> AnchorContext:
        """Create AnchorContext from a Y position (for strokes).

        Uses layout engine to find which text region the Y position
        corresponds to, then builds full context.
        """
        # Find approximate offset for this Y position
        offset = layout.position_to_offset(0, y_position)
        offset = max(0, min(offset, len(full_text) - 1))

        # Find paragraph boundaries
        para_start = full_text.rfind("\n", 0, offset)
        para_start = para_start + 1 if para_start != -1 else 0
        para_end = full_text.find("\n", offset)
        para_end = para_end if para_end != -1 else len(full_text)

        text_content = full_text[para_start:para_end]
        context_before = full_text[max(0, para_start - 50) : para_start]
        context_after = full_text[para_end : para_end + 50]

        return cls(
            content_hash=_content_hash(text_content),
            text_content=text_content,
            paragraph_index=paragraph_index,
            context_before=context_before,
            context_after=context_after,
            y_position_hint=y_position,
            diff_anchor=DiffAnchor.from_text_span(full_text, para_start, para_end),
        )

    def similarity_to(self, other: AnchorContext) -> float:
        """Calculate similarity score between contexts.

        Weights: text_content (0.5) + context (0.3) + structure (0.15) + spatial (0.05)
        """
        score = 0.0

        # Text content similarity (0.5)
        if self.content_hash == other.content_hash:
            score += 0.5
        else:
            text_ratio = difflib.SequenceMatcher(
                None, self.text_content, other.text_content
            ).ratio()
            score += 0.5 * text_ratio

        # Context similarity (0.3)
        before_ratio = difflib.SequenceMatcher(
            None, self.context_before, other.context_before
        ).ratio()
        after_ratio = difflib.SequenceMatcher(None, self.context_after, other.context_after).ratio()
        score += 0.3 * (before_ratio + after_ratio) / 2

        # Structural similarity (0.15)
        if self.paragraph_index is not None and other.paragraph_index is not None:
            if self.paragraph_index == other.paragraph_index:
                score += 0.15
            else:
                # Diminishing score for nearby paragraphs
                distance = abs(self.paragraph_index - other.paragraph_index)
                score += 0.15 * max(0, 1 - distance / 10)

        # Spatial similarity (0.05)
        if self.y_position_hint is not None and other.y_position_hint is not None:
            y_distance = abs(self.y_position_hint - other.y_position_hint)
            # ~57px per line, normalize
            score += 0.05 * max(0, 1 - y_distance / 500)

        return score


@dataclass
class ResolvedAnchorContext:
    """Result of resolving an AnchorContext in a document."""

    start_offset: int
    end_offset: int
    confidence: float  # 0.0 to 1.0
    match_type: Literal["exact", "fuzzy", "diff_anchor", "spatial"]
    target_paragraph_index: int | None = None


# =============================================================================
# Context Resolver
# =============================================================================


class ContextResolver:
    """Resolves AnchorContext across document versions.

    Integrates HeuristicTextAnchor for fuzzy matching - this class
    is preserved and promoted from the current implementation.
    """

    def __init__(
        self,
        context_window: int = 50,
        fuzzy_threshold: float = 0.8,
    ):
        self._heuristic = HeuristicTextAnchor(
            context_window=context_window,
            fuzzy_threshold=fuzzy_threshold,
        )
        self.fuzzy_threshold = fuzzy_threshold

    def resolve(
        self,
        context: AnchorContext,
        old_text: str,
        new_text: str,
        old_layout: LayoutContext | None = None,
        new_layout: LayoutContext | None = None,
    ) -> ResolvedAnchorContext | None:
        """Resolve context in new document.

        Strategy (in order of preference):
        1. Exact content hash match - highest confidence
        2. Fuzzy match with HeuristicTextAnchor - content + context windows
        3. Diff anchor resolution - stable neighbor text
        4. Spatial fallback - Y position + structure hints
        """
        # Strategy 1: Exact Hash Match
        hash_matches = self._find_by_hash(context.content_hash, context.text_content, new_text)
        if len(hash_matches) == 1:
            start, end = hash_matches[0]
            return ResolvedAnchorContext(
                start_offset=start,
                end_offset=end,
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

        # Strategy 2: Fuzzy Match with HeuristicTextAnchor
        old_position = (0.0, context.y_position_hint or 0.0)
        anchor = self._heuristic.find_anchor(context.text_content, old_text, old_position)

        new_offset = self._heuristic.resolve_anchor(anchor, new_text)
        if new_offset is not None and anchor.confidence >= self.fuzzy_threshold:
            return ResolvedAnchorContext(
                start_offset=new_offset,
                end_offset=new_offset + len(context.text_content),
                confidence=anchor.confidence,
                match_type="fuzzy",
            )

        # Strategy 3: Diff Anchor
        if context.diff_anchor:
            span = context.diff_anchor.resolve_in(new_text)
            if span:
                return ResolvedAnchorContext(
                    start_offset=span[0],
                    end_offset=span[1],
                    confidence=0.6,
                    match_type="diff_anchor",
                )

        # Strategy 4: Spatial Fallback
        if context.y_position_hint is not None and new_layout:
            spatial_match = self._resolve_by_spatial(context, new_layout, new_text)
            if spatial_match:
                return ResolvedAnchorContext(
                    start_offset=spatial_match[0],
                    end_offset=spatial_match[1],
                    confidence=0.4,
                    match_type="spatial",
                )

        return None

    def _find_by_hash(
        self, content_hash: str, text_content: str, full_text: str
    ) -> list[tuple[int, int]]:
        """Find all spans matching content hash."""
        matches = []
        start = 0
        while True:
            pos = full_text.find(text_content, start)
            if pos == -1:
                break
            # Verify hash matches
            found_text = full_text[pos : pos + len(text_content)]
            if _content_hash(found_text) == content_hash:
                matches.append((pos, pos + len(text_content)))
            start = pos + 1
        return matches

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
            before = text[max(0, start - len(context.context_before)) : start]
            after = text[end : end + len(context.context_after)]

            before_ratio = difflib.SequenceMatcher(None, before, context.context_before).ratio()
            after_ratio = difflib.SequenceMatcher(None, after, context.context_after).ratio()
            score = (before_ratio + after_ratio) / 2

            if score > best_score:
                best_score = score
                best_candidate = (start, end)

        return best_candidate if best_score > 0.5 else None

    def _resolve_by_spatial(
        self,
        context: AnchorContext,
        layout: LayoutContext,
        text: str,
    ) -> tuple[int, int] | None:
        """Resolve using spatial position hints."""
        if context.y_position_hint is None:
            return None

        # Find offset at this Y position
        offset = layout.position_to_offset(0, context.y_position_hint)
        offset = max(0, min(offset, len(text) - 1))

        # Find paragraph boundaries
        para_start = text.rfind("\n", 0, offset)
        para_start = para_start + 1 if para_start != -1 else 0
        para_end = text.find("\n", offset)
        para_end = para_end if para_end != -1 else len(text)

        return (para_start, para_end)


# =============================================================================
# Document Model Types
# =============================================================================


@dataclass
class Paragraph:
    """A paragraph of content in the document."""

    content: str
    paragraph_type: Literal["heading", "paragraph", "list_item", "code_block"]
    heading_level: int | None = None
    list_level: int | None = None

    # Position in document (set during construction)
    char_start: int = 0
    char_end: int = 0
    paragraph_index: int = 0


@dataclass
class HighlightData:
    """Highlight-specific annotation data."""

    highlighted_text: str
    color: int
    rectangles: list[tuple[float, float, float, float]]  # (x, y, w, h) per line


@dataclass
class DocumentAnnotation:
    """An annotation in document space (page-agnostic).

    Annotations exist at the document level. Page boundaries are
    determined during projection, not when defining the annotation.

    For strokes, the `as_stroke_bundle` property provides access to a
    StrokeBundle that groups all the CRDT blocks needed for the stroke.
    """

    annotation_id: str
    annotation_type: Literal["stroke", "highlight"]

    # What is this annotation attached to?
    anchor_context: AnchorContext

    # The annotation data itself
    stroke_data: StrokeData | None = None
    highlight_data: HighlightData | None = None

    # Original device representation (for coordinate updates)
    original_rm_block: Any = None
    original_tree_node: Any = None
    original_scene_group_item: Any = (
        None  # SceneGroupItemBlock that links TreeNodeBlock to scene graph
    )
    original_scene_tree_block: Any = (
        None  # SceneTreeBlock that declares TreeNodeBlock in scene tree
    )

    # Spatial cluster membership (for grouped stroke migration)
    cluster_id: str | None = None

    # Source page index (for page-aware clustering)
    source_page_idx: int | None = None

    @property
    def as_stroke_bundle(self) -> StrokeBundle | None:
        """Get a StrokeBundle for this annotation (strokes only).

        Returns a StrokeBundle containing all the CRDT blocks needed to
        represent this stroke on the device. Returns None for highlights
        or if the TreeNodeBlock is missing.

        Note: The returned bundle contains only this annotation's stroke.
        Multiple annotations may share the same TreeNodeBlock in the original
        document; use SceneGraphIndex.from_blocks() + StrokeBundle.from_index()
        for complete bundles.
        """
        if self.annotation_type != "stroke":
            return None
        if not self.original_tree_node:
            return None

        # Get node_id from TreeNodeBlock
        tree_node = self.original_tree_node
        if not hasattr(tree_node, "group") or not tree_node.group:
            return None
        node_id = tree_node.group.node_id

        # Build stroke list (just this annotation's stroke)
        strokes = [self.original_rm_block] if self.original_rm_block else []

        return StrokeBundle(
            node_id=node_id,
            tree_node=tree_node,
            scene_tree=self.original_scene_tree_block,
            scene_group_item=self.original_scene_group_item,
            strokes=strokes,
        )


@dataclass
class MigrationReport:
    """Report of annotation migration results."""

    migrations: list[tuple[DocumentAnnotation, DocumentAnnotation, ResolvedAnchorContext]] = field(
        default_factory=list
    )
    orphans: list[DocumentAnnotation] = field(default_factory=list)

    def add_migration(
        self,
        old_annotation: DocumentAnnotation,
        new_annotation: DocumentAnnotation,
        resolution: ResolvedAnchorContext,
    ) -> None:
        self.migrations.append((old_annotation, new_annotation, resolution))

    def add_orphan(self, annotation: DocumentAnnotation) -> None:
        self.orphans.append(annotation)

    @property
    def success_rate(self) -> float:
        total = len(self.migrations) + len(self.orphans)
        return len(self.migrations) / total if total > 0 else 1.0

    @property
    def average_confidence(self) -> float:
        if not self.migrations:
            return 0.0
        return sum(r.confidence for _, _, r in self.migrations) / len(self.migrations)


@dataclass
class PageProjection:
    """A page as rendered from DocumentModel.

    This is a VIEW, not source of truth. Used for .rm generation.
    """

    page_index: int
    page_uuid: str

    # Content on this page
    paragraphs: list[Paragraph] = field(default_factory=list)
    content_blocks: list[ContentBlock] = field(
        default_factory=list
    )  # Original blocks for .rm generation
    page_text: str = ""

    # Annotations projected to this page
    annotations: list[DocumentAnnotation] = field(default_factory=list)

    # Layout info
    text_origin_y: float = 0.0

    # Character offset range in full document
    doc_char_start: int = 0
    doc_char_end: int = 0


# =============================================================================
# Document Model
# =============================================================================


@dataclass
class DocumentModel:
    """Document-level view of content and annotations.

    This is THE source of truth for annotation preservation.
    Pages are derived from this model via projection.
    """

    paragraphs: list[Paragraph] = field(default_factory=list)
    content_blocks: list[ContentBlock] = field(
        default_factory=list
    )  # Original blocks for pagination
    full_text: str = ""
    annotations: list[DocumentAnnotation] = field(default_factory=list)

    # Layout configuration
    geometry: DeviceGeometry | None = None
    lines_per_page: int = 33
    allow_paragraph_splitting: bool = False

    @classmethod
    def from_rm_files(
        cls,
        rm_files: list[Path],
        geometry: DeviceGeometry,
    ) -> DocumentModel:
        """Extract document model from existing .rm files.

        Reads all pages, extracts text and annotations, builds unified view.
        """
        from rock_paper_sync.layout import LayoutContext, TextAreaConfig

        all_paragraphs: list[Paragraph] = []
        all_annotations: list[DocumentAnnotation] = []
        full_text_parts: list[str] = []
        current_char_offset = 0

        for page_idx, rm_path in enumerate(rm_files):
            if not rm_path or not rm_path.exists():
                continue

            try:
                with open(rm_path, "rb") as f:
                    blocks = list(rmscene.read_blocks(f))
            except Exception as e:
                logger.warning(f"Failed to read {rm_path}: {e}")
                continue

            # Extract text and tree nodes
            page_text = ""
            text_origin_y = geometry.text_pos_y

            # Build scene graph index for efficient block lookups
            scene_index = SceneGraphIndex.from_blocks(blocks)

            # Extract page text from RootTextBlock
            for block in blocks:
                if "RootText" in type(block).__name__:
                    text_data = block.value
                    text_origin_y = text_data.pos_y

                    text_parts = []
                    for item in text_data.items.sequence_items():
                        if hasattr(item, "value") and isinstance(item.value, str):
                            text_parts.append(item.value)
                    page_text = "".join(text_parts)
                    break

            if not page_text:
                continue

            # Build layout context for this page
            layout_ctx = LayoutContext.from_text(
                page_text,
                use_font_metrics=True,
                config=TextAreaConfig(
                    text_width=geometry.text_width,
                    text_pos_x=geometry.text_pos_x,
                    text_pos_y=text_origin_y,
                ),
            )

            # Extract paragraphs
            para_texts = page_text.split("\n")
            para_offset = 0
            for para_idx, para_text in enumerate(para_texts):
                if para_text.strip():
                    para_start = current_char_offset + para_offset
                    para_end = para_start + len(para_text)

                    paragraph = Paragraph(
                        content=para_text,
                        paragraph_type="paragraph",
                        char_start=para_start,
                        char_end=para_end,
                        paragraph_index=len(all_paragraphs),
                    )
                    all_paragraphs.append(paragraph)

                para_offset += len(para_text) + 1  # +1 for \n

            # Extract annotations
            for block in blocks:
                block_type = type(block).__name__

                if "Line" in block_type:
                    # Stroke annotation
                    line = block.item.value if hasattr(block, "item") else None
                    if line is None or not hasattr(line, "points") or not line.points:
                        continue

                    # Convert to Point objects for unified stroke representation
                    points = [
                        Point(x=p.x, y=p.y, pressure=getattr(p, "pressure", 100))
                        for p in line.points
                    ]
                    if not points:
                        continue

                    y_coords = [p.y for p in points]
                    center_y = sum(y_coords) / len(y_coords)

                    # Get tree node for this stroke using scene graph index
                    parent_id = getattr(block, "parent_id", None)
                    tree_node = scene_index.tree_nodes.get(parent_id) if parent_id else None

                    # Compute absolute Y position from TreeNodeBlock anchor
                    # Stroke Y coordinates are RELATIVE to the anchor position, not the page origin
                    # The TreeNodeBlock's anchor_id.part2 is the character offset in page text
                    abs_y = text_origin_y + center_y + 60  # Default fallback
                    anchor_char_offset = None

                    if tree_node and hasattr(tree_node, "group") and tree_node.group:
                        g = tree_node.group
                        if hasattr(g, "anchor_id") and g.anchor_id and g.anchor_id.value:
                            anchor_val = g.anchor_id.value
                            # anchor_id.part2 is the character offset (unless it's the sentinel)
                            if anchor_val.part2 != 281474976710655:  # Not END_OF_DOC sentinel
                                anchor_char_offset = anchor_val.part2
                                # Get Y position of the anchor text
                                if layout_ctx and anchor_char_offset < len(page_text):
                                    _, anchor_y = layout_ctx.offset_to_position(anchor_char_offset)
                                    # Stroke Y is relative to anchor Y
                                    abs_y = anchor_y + center_y

                    # Create anchor context - use anchor_char_offset if available
                    if anchor_char_offset is not None and anchor_char_offset < len(page_text):
                        # Use the TreeNodeBlock's anchor directly
                        anchor = AnchorContext.from_text_span(
                            page_text,
                            anchor_char_offset,
                            min(anchor_char_offset + 50, len(page_text)),
                        )
                        # Preserve Y hint for page routing
                        anchor = AnchorContext(
                            content_hash=anchor.content_hash,
                            text_content=anchor.text_content,
                            paragraph_index=anchor.paragraph_index,
                            context_before=anchor.context_before,
                            context_after=anchor.context_after,
                            y_position_hint=abs_y,
                            diff_anchor=anchor.diff_anchor,
                        )
                    else:
                        # Fallback to Y-position based anchor
                        anchor = AnchorContext.from_y_position(
                            abs_y, page_text, layout_ctx, paragraph_index=None
                        )
                        anchor = AnchorContext(
                            content_hash=anchor.content_hash,
                            text_content=anchor.text_content,
                            paragraph_index=anchor.paragraph_index,
                            context_before=anchor.context_before,
                            context_after=anchor.context_after,
                            y_position_hint=abs_y,
                            diff_anchor=anchor.diff_anchor,
                        )

                    # Get SceneGroupItemBlock and SceneTreeBlock using scene graph index
                    scene_group_item = None
                    scene_tree_block = None
                    if tree_node and hasattr(tree_node, "group") and tree_node.group:
                        node_id = tree_node.group.node_id
                        scene_group_item = scene_index.scene_group_items.get(node_id)
                        scene_tree_block = scene_index.scene_trees.get(node_id)

                    # Build stroke data with bounding box
                    xs = [p.x for p in points]
                    ys = [p.y for p in points]
                    bbox = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

                    stroke_data = StrokeData(
                        points=points,
                        color=line.color.value if hasattr(line.color, "value") else line.color,
                        tool=line.tool.value if hasattr(line.tool, "value") else line.tool,
                        thickness=getattr(line, "thickness_scale", 2.0),
                        bounding_box=bbox,
                    )

                    annotation = DocumentAnnotation(
                        annotation_id=str(block.item.item_id)
                        if hasattr(block, "item")
                        else str(id(block)),
                        annotation_type="stroke",
                        anchor_context=anchor,
                        stroke_data=stroke_data,
                        original_rm_block=block,
                        original_tree_node=tree_node,
                        original_scene_group_item=scene_group_item,
                        original_scene_tree_block=scene_tree_block,
                        source_page_idx=page_idx,  # Track source page for clustering
                    )
                    all_annotations.append(annotation)

                elif "Glyph" in block_type:
                    # Highlight annotation
                    glyph = block.item.value if hasattr(block, "item") else None
                    if glyph is None or not hasattr(glyph, "rectangles"):
                        continue

                    highlight_text = getattr(glyph, "text", "") or ""
                    if not highlight_text:
                        continue

                    # Create anchor context from highlighted text
                    # Use rectangle positions to find the correct occurrence when text appears multiple times
                    rects = glyph.rectangles
                    text_offset = -1

                    if rects and layout_ctx:
                        # Use the rectangle X,Y to find which occurrence was highlighted
                        first_rect = rects[0]
                        rect_y = first_rect.y
                        rect_x = first_rect.x

                        # Find all occurrences of the highlight text
                        candidates = []
                        search_start = 0
                        while True:
                            pos = page_text.find(highlight_text, search_start)
                            if pos == -1:
                                break
                            candidates.append(pos)
                            search_start = pos + 1

                        if len(candidates) == 1:
                            text_offset = candidates[0]
                        elif len(candidates) > 1:
                            # Disambiguate using rectangle position
                            # Find which candidate has position closest to rect_x, rect_y
                            best_offset = candidates[0]
                            best_distance = float("inf")

                            for candidate_offset in candidates:
                                # Get position of this candidate
                                cand_x, cand_y = layout_ctx.offset_to_position(candidate_offset)
                                # Calculate distance from rectangle position
                                # Y is more important for line matching
                                distance = abs(cand_y - rect_y) * 2 + abs(cand_x - rect_x)
                                if distance < best_distance:
                                    best_distance = distance
                                    best_offset = candidate_offset

                            text_offset = best_offset
                    else:
                        # No rectangles or layout - fallback to simple find
                        text_offset = page_text.find(highlight_text)

                    if text_offset != -1:
                        anchor = AnchorContext.from_text_span(
                            page_text,
                            text_offset,
                            text_offset + len(highlight_text),
                        )
                    else:
                        # Fallback to Y position
                        if rects:
                            avg_y = sum(r.y + r.h / 2 for r in rects) / len(rects)
                            abs_y = text_origin_y + avg_y
                            anchor = AnchorContext.from_y_position(abs_y, page_text, layout_ctx)
                        else:
                            continue

                    rectangles = [(r.x, r.y, r.w, r.h) for r in glyph.rectangles if hasattr(r, "x")]

                    highlight_data = HighlightData(
                        highlighted_text=highlight_text,
                        color=glyph.color.value if hasattr(glyph.color, "value") else glyph.color,
                        rectangles=rectangles,
                    )

                    annotation = DocumentAnnotation(
                        annotation_id=str(block.item.item_id)
                        if hasattr(block, "item")
                        else str(id(block)),
                        annotation_type="highlight",
                        anchor_context=anchor,
                        highlight_data=highlight_data,
                        original_rm_block=block,
                        source_page_idx=page_idx,  # Track source page for relocation
                    )
                    all_annotations.append(annotation)

            full_text_parts.append(page_text)
            current_char_offset += len(page_text) + 1  # +1 for page separator

        full_text = "\n".join(full_text_parts)

        model = cls(
            paragraphs=all_paragraphs,
            full_text=full_text,
            annotations=all_annotations,
            geometry=geometry,
        )

        # Assign spatial clusters to stroke annotations
        model._assign_stroke_clusters()

        return model

    @classmethod
    def from_paragraphs(
        cls,
        paragraphs: list[Paragraph],
        geometry: DeviceGeometry | None = None,
    ) -> DocumentModel:
        """Create document model from paragraph list.

        Used when generating from markdown - no annotations yet.
        """
        # Build full text
        text_parts = []
        current_offset = 0
        for para in paragraphs:
            para.char_start = current_offset
            para.char_end = current_offset + len(para.content)
            text_parts.append(para.content)
            current_offset = para.char_end + 1  # +1 for \n

        full_text = "\n".join(text_parts)

        return cls(
            paragraphs=paragraphs,
            full_text=full_text,
            annotations=[],
            geometry=geometry,
        )

    @classmethod
    def from_content_blocks(
        cls,
        blocks: list[ContentBlock],
        geometry: DeviceGeometry,
        allow_paragraph_splitting: bool = False,
    ) -> DocumentModel:
        """Create document model from ContentBlocks (parsed markdown).

        This is the primary constructor for new documents from markdown.
        Converts ContentBlocks to Paragraphs while preserving original blocks
        for pagination.

        Args:
            blocks: ContentBlocks from parsed markdown
            geometry: Device geometry for layout
            allow_paragraph_splitting: If True, split long paragraphs across pages
        """
        from rock_paper_sync.parser import BlockType

        paragraphs: list[Paragraph] = []
        text_parts: list[str] = []
        current_offset = 0

        for block in blocks:
            # Map BlockType to paragraph_type
            if block.type == BlockType.HEADER:
                para_type: Literal["heading", "paragraph", "list_item", "code_block"] = "heading"
            elif block.type == BlockType.LIST_ITEM:
                para_type = "list_item"
            elif block.type == BlockType.CODE_BLOCK:
                para_type = "code_block"
            else:
                para_type = "paragraph"

            para = Paragraph(
                content=block.text,
                paragraph_type=para_type,
                heading_level=block.level if block.type == BlockType.HEADER else None,
                list_level=block.level if block.type == BlockType.LIST_ITEM else None,
                char_start=current_offset,
                char_end=current_offset + len(block.text),
                paragraph_index=len(paragraphs),
            )
            paragraphs.append(para)
            text_parts.append(block.text)
            current_offset = para.char_end + 1  # +1 for \n

        full_text = "\n".join(text_parts)

        return cls(
            paragraphs=paragraphs,
            content_blocks=blocks,
            full_text=full_text,
            annotations=[],
            geometry=geometry,
            lines_per_page=geometry.lines_per_page,
            allow_paragraph_splitting=allow_paragraph_splitting,
        )

    def _assign_stroke_clusters(self) -> None:
        """Assign cluster IDs to spatially-related stroke annotations.

        Uses KDTree-based proximity clustering with DEFAULT_CLUSTER_THRESHOLD.
        Clusters are assigned during from_rm_files() and used by both:
        - Annotation reanchoring (migrate_annotations_to)
        - OCR processing (get_annotation_clusters)
        """
        import uuid

        from .common.spatial import DEFAULT_CLUSTER_THRESHOLD, KDTreeProximityStrategy

        # Get stroke annotations with valid stroke_data, grouped by source page
        # Strokes on different pages should NEVER be in the same cluster
        strokes_by_page: dict[int | None, list[tuple[int, DocumentAnnotation]]] = {}
        for i, anno in enumerate(self.annotations):
            if anno.annotation_type == "stroke" and anno.stroke_data:
                page_idx = anno.source_page_idx
                strokes_by_page.setdefault(page_idx, []).append((i, anno))

        if not strokes_by_page:
            return

        total_clusters = 0
        total_strokes = 0

        # Cluster strokes separately for each source page
        strategy = KDTreeProximityStrategy(distance_threshold=DEFAULT_CLUSTER_THRESHOLD)
        for page_idx, stroke_annos in strokes_by_page.items():
            total_strokes += len(stroke_annos)

            if len(stroke_annos) < 2:
                continue  # Need at least 2 strokes to form a cluster

            strokes = [anno.stroke_data for _, anno in stroke_annos]
            clusters = strategy.cluster(strokes)

            # Assign cluster IDs (only for actual clusters with >1 member)
            for cluster_indices in clusters:
                if len(cluster_indices) > 1:
                    cluster_id = str(uuid.uuid4())[:8]
                    for idx in cluster_indices:
                        anno_idx, _ = stroke_annos[idx]
                        self.annotations[anno_idx].cluster_id = cluster_id
                    total_clusters += 1

        logger.debug(
            f"Assigned {total_clusters} clusters to {total_strokes} stroke annotations "
            f"across {len(strokes_by_page)} pages"
        )

    def get_annotation_clusters(self) -> list[list[DocumentAnnotation]]:
        """Get annotations grouped by cluster_id.

        Returns a list of annotation clusters. Each cluster is a list of
        DocumentAnnotation objects that should be processed together.
        Unclustered annotations are returned as single-element lists.

        Used by both OCR processing and annotation reanchoring.
        """
        clusters: dict[str, list[DocumentAnnotation]] = {}
        unclustered: list[DocumentAnnotation] = []

        for anno in self.annotations:
            if anno.cluster_id:
                clusters.setdefault(anno.cluster_id, []).append(anno)
            else:
                unclustered.append(anno)

        # Return multi-annotation clusters + single-annotation "clusters"
        result = list(clusters.values())
        result.extend([[a] for a in unclustered])
        return result

    def migrate_annotations_to(
        self,
        new_model: DocumentModel,
        merger: AnnotationMerger | None = None,
    ) -> tuple[DocumentModel, MigrationReport]:
        """Migrate annotations from this model to new content.

        The core operation for annotation preservation. This is a convenience
        facade that delegates to AnnotationMerger.

        For explicit dependency injection (e.g., testing), pass a custom merger:
            merger = AnnotationMerger(resolver=mock_resolver)
            merged, report = old_model.migrate_annotations_to(new_model, merger=merger)

        Args:
            new_model: Target document model (without annotations)
            merger: Optional AnnotationMerger instance. If None, creates a
                default merger with default ContextResolver.

        Returns:
            Tuple of (merged DocumentModel with annotations, MigrationReport)
        """
        from rock_paper_sync.annotations.merging import (
            AnnotationMerger,
            MergeContext,
        )

        if merger is None:
            merger = AnnotationMerger(resolver=ContextResolver())

        context = MergeContext(old_model=self, new_model=new_model)
        result = merger.merge(context)

        return result.merged_model, result.report

    def project_to_pages(
        self,
        page_uuids: list[str] | None = None,
        layout_engine: WordWrapLayoutEngine | None = None,
    ) -> list[PageProjection]:
        """Project document to pages for .rm file generation.

        This is where page boundaries are determined. Annotations
        flow to correct pages based on their anchor position.

        Uses block-based pagination with:
        - Header orphan prevention (headers near bottom start new page)
        - Atomic block placement (blocks don't split mid-way)
        - Proper annotation routing by character offset

        Args:
            page_uuids: Optional list of page UUIDs to reuse
            layout_engine: Optional layout engine for line estimation (created if not provided)
        """
        from rock_paper_sync.layout import WordWrapLayoutEngine

        if not self.geometry:
            raise ValueError("DocumentModel requires geometry for page projection")

        # Create layout engine if not provided
        if layout_engine is None:
            layout_engine = WordWrapLayoutEngine.from_geometry(
                self.geometry,
                use_font_metrics=True,
            )

        # If no content blocks, return empty pages
        if not self.content_blocks:
            page_uuid = page_uuids[0] if page_uuids else str(id(0))
            return [
                PageProjection(
                    page_index=0,
                    page_uuid=page_uuid,
                    paragraphs=[],
                    content_blocks=[],
                    page_text="",
                    text_origin_y=self.geometry.text_pos_y,
                    doc_char_start=0,
                    doc_char_end=0,
                )
            ]

        # Use shared paginator for consistent pagination with generator
        from rock_paper_sync.layout import ContentPaginator

        paginator = ContentPaginator(
            layout_engine=layout_engine,
            lines_per_page=self.lines_per_page,
            allow_paragraph_splitting=self.allow_paragraph_splitting,
        )
        page_block_lists = paginator.paginate(self.content_blocks)

        # Build PageProjections from paginated blocks
        pages: list[PageProjection] = []
        doc_char_offset = 0

        for page_idx, page_blocks in enumerate(page_block_lists):
            page_uuid = (
                page_uuids[page_idx]
                if page_uuids and page_idx < len(page_uuids)
                else str(id(page_idx))
            )

            # Build page text from blocks
            page_text = "\n".join(block.text for block in page_blocks)

            # Calculate character range
            doc_char_start = doc_char_offset
            doc_char_end = doc_char_start + len(page_text)

            # Find paragraphs on this page
            page_paragraphs = [
                p
                for p in self.paragraphs
                if p.char_start < doc_char_end and p.char_end > doc_char_start
            ]

            page = PageProjection(
                page_index=page_idx,
                page_uuid=page_uuid,
                paragraphs=page_paragraphs,
                content_blocks=page_blocks,
                page_text=page_text,
                text_origin_y=self.geometry.text_pos_y,
                doc_char_start=doc_char_start,
                doc_char_end=doc_char_end,
            )
            pages.append(page)
            doc_char_offset = doc_char_end + 1  # +1 for page separator

        # Assign annotations to pages (done after all pages are created)
        # IMPORTANT: Strokes in the same cluster (same parent TreeNodeBlock) must go to the same page
        # We use cluster_id for grouped strokes, and parent_id as fallback

        # Helper to determine target page for an annotation
        def _determine_page_for_annotation(
            annotation: DocumentAnnotation,
        ) -> int | None:
            anchor = annotation.anchor_context
            anno_start = self._find_anchor_position(anchor)

            if anno_start is None:
                return None

            target_page_idx = None

            # For strokes with Y-position hints, check if they should spill to next page
            if (
                annotation.annotation_type == "stroke"
                and anchor.y_position_hint is not None
                and self.geometry
            ):
                # Find which page the anchor text is on
                text_page_idx = None
                for idx, page in enumerate(pages):
                    if page.doc_char_start <= anno_start < page.doc_char_end:
                        text_page_idx = idx
                        break

                if text_page_idx is not None:
                    page_height = 1870  # Device-specific constant
                    if anchor.y_position_hint > page_height * 0.9 and text_page_idx + 1 < len(
                        pages
                    ):
                        target_page_idx = text_page_idx + 1
                    else:
                        target_page_idx = text_page_idx

            if target_page_idx is None:
                # For highlights and strokes without Y hints, use character offset
                for idx, page in enumerate(pages):
                    if page.doc_char_start <= anno_start < page.doc_char_end:
                        target_page_idx = idx
                        break

            return target_page_idx

        # Group stroke annotations by cluster_id
        # Clusters are assigned in _assign_stroke_clusters() using spatial proximity
        # ALL strokes in a cluster MUST go to the same page - never split clusters
        stroke_clusters: dict[str, list[DocumentAnnotation]] = {}
        unclustered_strokes: list[DocumentAnnotation] = []
        non_stroke_annotations: list[DocumentAnnotation] = []

        for annotation in self.annotations:
            if annotation.annotation_type == "stroke":
                if annotation.cluster_id:
                    stroke_clusters.setdefault(annotation.cluster_id, []).append(annotation)
                else:
                    unclustered_strokes.append(annotation)
            else:
                non_stroke_annotations.append(annotation)

        # Route stroke clusters to pages (entire cluster goes to same page)
        for cluster_id, cluster_strokes in stroke_clusters.items():
            # Find the stroke with the LOWEST Y position (cluster leader) to determine page
            leader = min(
                cluster_strokes,
                key=lambda a: a.anchor_context.y_position_hint
                if a.anchor_context.y_position_hint is not None
                else float("inf"),
            )

            target_page_idx = _determine_page_for_annotation(leader)

            if target_page_idx is not None:
                # Assign ALL strokes in this cluster to the same page
                for annotation in cluster_strokes:
                    pages[target_page_idx].annotations.append(annotation)

                logger.warning(
                    f"STROKE CLUSTER ({cluster_id}) -> page {target_page_idx}: "
                    f"{len(cluster_strokes)} strokes, leader_y={leader.anchor_context.y_position_hint}"
                )
            else:
                logger.warning(
                    f"Could not find page for stroke cluster {cluster_id} "
                    f"({len(cluster_strokes)} strokes)"
                )

        # Route unclustered strokes individually (single-stroke "clusters")
        for annotation in unclustered_strokes:
            target_page_idx = _determine_page_for_annotation(annotation)
            if target_page_idx is not None:
                pages[target_page_idx].annotations.append(annotation)
                logger.debug(
                    f"UNCLUSTERED STROKE -> page {target_page_idx}: "
                    f"y={annotation.anchor_context.y_position_hint}"
                )

        # Route non-stroke annotations individually
        for annotation in non_stroke_annotations:
            anchor = annotation.anchor_context
            anno_start = self._find_anchor_position(anchor)

            if anno_start is None:
                logger.warning(
                    f"Could not find anchor position for {annotation.annotation_type}: "
                    f"{anchor.text_content[:30] if anchor.text_content else 'N/A'}..."
                )
                continue

            target_page_idx = _determine_page_for_annotation(annotation)

            if target_page_idx is not None:
                pages[target_page_idx].annotations.append(annotation)
                logger.debug(
                    f"{annotation.annotation_type.upper()} -> page {target_page_idx}: "
                    f"anno_start={anno_start}"
                )

        return pages

    def _find_anchor_position(self, anchor: AnchorContext) -> int | None:
        """Find character position of an anchor in the document."""
        # Try exact match first
        pos = self.full_text.find(anchor.text_content)
        if pos != -1:
            return pos

        # Try diff anchor
        if anchor.diff_anchor:
            span = anchor.diff_anchor.resolve_in(self.full_text)
            if span:
                return span[0]

        return None
