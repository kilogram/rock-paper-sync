"""Handler for stroke annotations (Line blocks).

Strokes are hand-drawn annotations with point coordinates. They use the
dual-anchor coordinate transformation system and require OCR for text
extraction.

Characteristics:
- Hand-drawn pen/pencil annotations
- Stored as sequences of (x, y) points
- Use dual-anchor Y transformation:
  - Positive Y: relative to text origin
  - Negative Y: relative to baseline + line height (60px offset)
- Per-parent X anchors from TreeNodeBlocks
- Require OCR to extract text content

For coordinate transformation details, see docs/STROKE_ANCHORING.md.

Example (traditional interface):
    handler = StrokeHandler(ocr_processor)
    annotations = handler.detect(rm_file_path)
    mappings = handler.map(annotations, markdown_blocks, rm_file_path)

Example (cluster-based interface for migration):
    handler = StrokeHandler()
    clusters = handler.detect_clusters(rm_file_path)  # Extract with CRDT context
    migrated = handler.migrate_clusters(clusters, old_text, new_text, resolver)
    blocks = handler.serialize_for_page(migrated, page_projection)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from rock_paper_sync.annotations import Annotation, AnnotationType, read_annotations
from rock_paper_sync.annotations.common.spatial import find_nearest_paragraph_by_y
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm
from rock_paper_sync.annotations.core.data_types import (
    ExtractedAnnotation,
    RenderConfig,
)
from rock_paper_sync.annotations.document_model import AnchorContext
from rock_paper_sync.coordinate_transformer import (
    CoordinateTransformer,
    build_parent_anchor_map,
    extract_text_origin,
    is_text_relative,
)

if TYPE_CHECKING:
    from typing import Any

    from rmscene import TreeNodeBlock

    from rock_paper_sync.annotations.document_model import (
        AnchorContext,
        PageProjection,
    )
    from rock_paper_sync.annotations.services.crdt_service import CrdtService
    from rock_paper_sync.annotations.stroke_cluster import StrokeCluster
    from rock_paper_sync.layout import DeviceGeometry, LayoutContext
    from rock_paper_sync.ocr.integration import OCRProcessor

logger = logging.getLogger(__name__)


class StrokeHandler:
    """Handler for stroke annotations with OCR integration.

    Implements AnnotationHandler Protocol for strokes using coordinate
    transformation and OCR processing. Strokes require special handling
    because they use the dual-anchor coordinate system.
    """

    def __init__(self, ocr_processor: OCRProcessor | None = None):
        """Initialize stroke handler.

        Args:
            ocr_processor: Optional OCR processor for text extraction
        """
        self.ocr_processor = ocr_processor

    @property
    def annotation_type(self) -> str:
        """Return unique identifier for strokes."""
        return "stroke"

    def detect(self, rm_file_path: Path) -> list[Annotation]:
        """Extract stroke annotations from .rm file.

        Args:
            rm_file_path: Path to reMarkable v6 .rm file

        Returns:
            List of Annotation objects with type=STROKE
        """
        all_annotations = read_annotations(rm_file_path)
        strokes = [anno for anno in all_annotations if anno.type == AnnotationType.STROKE]
        logger.debug(f"Detected {len(strokes)} strokes in {rm_file_path.name}")
        return strokes

    def map(
        self,
        annotations: list[Annotation],
        markdown_blocks: list,
        rm_file_path: Path,
        layout_context: LayoutContext | None = None,
    ) -> dict[int, list[Annotation]]:
        """Map strokes to markdown paragraphs using coordinate transformation.

        Uses dual-anchor Y transformation for accurate positioning:
        - Positive Y strokes: text_origin_y + native_y
        - Negative Y strokes: text_origin_y + NEGATIVE_Y_OFFSET + native_y

        When layout_context is provided, can also use position_to_offset() for
        more accurate content-based anchoring (similar to highlights).

        Args:
            annotations: List of stroke annotations
            markdown_blocks: List of markdown content blocks
            rm_file_path: Path to .rm file for coordinate extraction
            layout_context: Optional layout context for position calculations.
                When provided, enables position_to_offset() for content-based
                anchoring using the shared layout infrastructure.

        Returns:
            Dict mapping paragraph_index -> list of matching annotations
        """
        mappings: dict[int, list[Annotation]] = {}

        # Extract coordinate transformation components
        text_origin = extract_text_origin(rm_file_path)
        parent_anchor_map = build_parent_anchor_map(rm_file_path)
        transformer = CoordinateTransformer(
            text_origin_x=text_origin.x,
            text_origin_y=text_origin.y,
        )

        # Extract text blocks for position references
        rm_text_blocks, _ = extract_text_blocks_from_rm(rm_file_path)

        for annotation in annotations:
            if not annotation.stroke or not annotation.stroke.bounding_box:
                logger.warning(
                    f"Stroke annotation {annotation.annotation_id[:8]}... missing bounding box"
                )
                continue

            bbox = annotation.stroke.bounding_box
            native_y = bbox.y

            # Transform to absolute coordinates using proper dual-anchor system
            anchor_x = text_origin.x
            if annotation.parent_id and annotation.parent_id in parent_anchor_map:
                anchor_x = parent_anchor_map[annotation.parent_id].x

            # Calculate stroke center Y to determine coordinate space
            stroke_center_y = bbox.y + bbox.h / 2

            # Apply coordinate transformation
            if is_text_relative(annotation.parent_id):
                # Text-relative: use dual-anchor transform
                absolute_x, absolute_y = transformer.to_absolute(
                    native_x=bbox.x,
                    native_y=native_y,
                    parent_id=annotation.parent_id,
                    anchor_x=anchor_x,
                    stroke_center_y=stroke_center_y,
                )
            else:
                # Already absolute
                absolute_x = bbox.x
                absolute_y = native_y

            paragraph_index = None

            # Strategy 1: Use layout context for position-to-offset mapping (if available)
            # This enables content-based anchoring similar to highlights
            if layout_context is not None:
                try:
                    # Convert stroke position to approximate character offset
                    char_offset = layout_context.position_to_offset(absolute_x, absolute_y)

                    # Find which paragraph contains this offset
                    cumulative_offset = 0
                    for idx, md_block in enumerate(markdown_blocks):
                        block_length = len(md_block.text) + 1  # +1 for newline
                        if cumulative_offset <= char_offset < cumulative_offset + block_length:
                            paragraph_index = idx
                            logger.debug(
                                f"Mapped stroke via layout context: "
                                f"pos=({absolute_x:.1f}, {absolute_y:.1f}) "
                                f"→ offset={char_offset} → paragraph {idx}"
                            )
                            break
                        cumulative_offset += block_length
                except Exception as e:
                    logger.debug(f"Layout context mapping failed: {e}, falling back to Y-position")

            # Strategy 2: Fall back to Y-position matching
            if paragraph_index is None:
                # Find closest paragraph by Y position using common utility
                # NOTE: Requires page_y_start attribute on ContentBlock
                # See issue #5 for pagination metadata persistence implementation
                paragraph_index = find_nearest_paragraph_by_y(absolute_y, markdown_blocks)

            if paragraph_index is not None:
                if paragraph_index not in mappings:
                    mappings[paragraph_index] = []
                mappings[paragraph_index].append(annotation)
            else:
                logger.warning(
                    f"Cannot map stroke {annotation.annotation_id[:8]}: "
                    "pagination metadata (page_y_start) not available (see issue #5). "
                    "Strokes require Y-position matching which needs persisted pagination data."
                )

        return mappings

    def create_anchor(
        self,
        annotation: Annotation,
        paragraph_text: str,
        paragraph_index: int,
        page_num: int = 0,  # noqa: ARG002
    ) -> AnchorContext:
        """Create anchor from stroke annotation for matching and correction detection.

        Args:
            annotation: Stroke annotation from detect()
            paragraph_text: Full text of the matched paragraph
            paragraph_index: Index of paragraph in markdown
            page_num: Page number (default: 0)

        Returns:
            AnchorContext with spatial anchor using Y position hint
        """
        if not annotation.stroke or not annotation.stroke.bounding_box:
            raise ValueError("Stroke annotation missing bounding box")

        bbox = annotation.stroke.bounding_box
        center_y = bbox.y + bbox.h / 2

        # For strokes, anchor to the entire paragraph with Y position hint
        # This provides spatial anchoring without requiring OCR text upfront
        return AnchorContext.from_text_span(
            full_text=paragraph_text,
            start=0,
            end=len(paragraph_text),
            paragraph_index=paragraph_index,
            y_position=center_y,
        )

    def relocate(
        self,
        block,
        old_text: str,
        new_text: str,
        old_origin: tuple[float, float],
        new_origin: tuple[float, float],
        layout_engine,
        geometry,
        crdt_base_id: int | None = None,
    ):
        """Relocate stroke annotation block (pass-through for coordinates).

        Strokes use anchor-relative positioning via TreeNodeBlocks, NOT absolute
        coordinates. The SceneLineItemBlock coordinates are relative to the anchor
        and don't need transformation. The actual relocation happens via
        TreeNodeBlock reanchoring (see reanchor_tree_node method).

        This pass-through preserves the original pixel-perfect stroke coordinates
        while the TreeNodeBlock anchor is updated separately to position the
        stroke correctly on the new page.

        Args:
            block: Raw rmscene SceneLineItemBlock
            old_text: Page text before modification (unused for coordinates)
            new_text: Page text after modification (unused for coordinates)
            old_origin: Origin of old text block (unused)
            new_origin: Origin of new text block (unused)
            layout_engine: Layout engine (unused)
            geometry: Device geometry (unused)
            crdt_base_id: CRDT base ID (unused)

        Returns:
            Block unchanged - stroke positioning is via TreeNodeBlock anchor
        """
        # Stroke coordinates are relative to their TreeNodeBlock anchor.
        # The anchor is updated via reanchor_tree_node(), not here.
        # Returning the block unchanged preserves pixel-perfect stroke paths.
        return block

    def reanchor_tree_node(
        self,
        tree_node: TreeNodeBlock,
        anchor_context: AnchorContext,
        page_text: str,
        geometry: DeviceGeometry | None = None,
    ) -> tuple[TreeNodeBlock, int]:
        """Reanchor a TreeNodeBlock to a new position in page text.

        This method owns the anchor resolution logic for strokes. Given a
        TreeNodeBlock and its anchor context, it finds the correct character
        offset in the page text and creates a reanchored TreeNodeBlock.

        Args:
            tree_node: Original TreeNodeBlock with anchor to update
            anchor_context: Context with text content for anchor resolution
            page_text: Text content of the target page
            geometry: Optional device geometry for Y-position fallback

        Returns:
            Tuple of (reanchored_tree_node, target_offset)
        """
        from rock_paper_sync.annotations.services.crdt_service import CrdtService

        # Calculate target offset in page text using full anchor resolution
        target_offset = self._calculate_anchor_offset(anchor_context, page_text, geometry)

        # Create reanchored tree node via CRDT service
        crdt_service = CrdtService()
        reanchored_node = crdt_service.clone_tree_node_with_anchor(tree_node, target_offset)

        anchor_text = anchor_context.text_content or ""
        logger.debug(
            f"Reanchored TreeNodeBlock: target_offset={target_offset}, "
            f"anchor_text='{anchor_text[:30]}...'"
        )

        return reanchored_node, target_offset

    def _calculate_anchor_offset(
        self,
        anchor_context: AnchorContext,
        page_text: str,
        geometry: DeviceGeometry | None = None,
    ) -> int:
        """Calculate the character offset for a stroke anchor in page text.

        Uses multiple strategies to find the best position:
        1. Direct text match - find anchor text in page
        2. Context match - use surrounding context for disambiguation
        3. Diff anchor resolution - use diff-based anchor if available
        4. Y-position hint fallback - use layout to approximate position
        5. Paragraph index fallback - use relative position

        Args:
            anchor_context: Anchor information with text and context
            page_text: Text content of the target page
            geometry: Optional device geometry for Y-position fallback

        Returns:
            Character offset where stroke should be anchored
        """
        from rock_paper_sync.transform import find_all_occurrences

        anchor_text = anchor_context.text_content

        # Strategy 1: Direct text match
        if anchor_text:
            occurrences = find_all_occurrences(page_text, anchor_text)

            if len(occurrences) == 1:
                # Unique match - use it
                return occurrences[0]

            if len(occurrences) > 1:
                # Multiple matches - use context to disambiguate
                if anchor_context.context_before:
                    for occ in occurrences:
                        start = max(0, occ - len(anchor_context.context_before))
                        if page_text[start:occ].endswith(anchor_context.context_before):
                            return occ
                # Fall back to first occurrence
                return occurrences[0]

        # Strategy 2: Diff anchor resolution
        if anchor_context.diff_anchor:
            span = anchor_context.diff_anchor.resolve_in(page_text)
            if span:
                return span[0]

        # Strategy 3: Y-position hint fallback (requires geometry)
        if anchor_context.y_position_hint is not None and geometry is not None:
            try:
                from rock_paper_sync.layout import LayoutContext, TextAreaConfig

                # Create layout context for this page
                layout_ctx = LayoutContext.from_text(
                    page_text,
                    use_font_metrics=True,
                    config=TextAreaConfig(
                        text_width=geometry.text_width,
                        text_pos_x=geometry.text_pos_x,
                        text_pos_y=geometry.text_pos_y,
                    ),
                )

                # Convert Y position to approximate offset
                offset = layout_ctx.position_to_offset(0, anchor_context.y_position_hint)
                offset = max(0, min(offset, len(page_text) - 1))

                logger.debug(
                    f"Using Y-position fallback for stroke anchor: "
                    f"y={anchor_context.y_position_hint:.1f} -> offset={offset}"
                )
                return offset
            except Exception as e:
                logger.debug(f"Y-position fallback failed: {e}")

        # Strategy 4: Paragraph index fallback
        if anchor_context.paragraph_index is not None:
            offset = 0
            lines = page_text.split("\n")
            for i, line in enumerate(lines):
                if i >= anchor_context.paragraph_index:
                    return offset
                offset += len(line) + 1  # +1 for newline
            return offset

        # Default: anchor at document start
        logger.warning(
            f"Could not resolve anchor for '{anchor_text[:30] if anchor_text else '<no text>'}...', "
            f"using offset 0"
        )
        return 0

    def extract_from_markdown(
        self,
        paragraph: str,
        config: RenderConfig,
    ) -> list[ExtractedAnnotation]:
        """Extract strokes/OCR text from markdown based on rendering style.

        Supports two rendering styles:
        - footnote: text[^1] with footnote containing metadata
        - comment: <!-- OCR: text -->

        Args:
            paragraph: Markdown paragraph text (may include footnotes)
            config: Rendering configuration

        Returns:
            List of extracted stroke/OCR annotations
        """
        extracted = []

        if config.stroke_style == "footnote":
            # Pattern: captures text before footnote marker
            # Example: "Handwritten text[^1]" -> "Handwritten text"
            pattern = r"([^\[\n]+)\[\^\d+\]"

            for match in re.finditer(pattern, paragraph):
                text = match.group(1).strip()
                if text:  # Skip empty matches
                    extracted.append(
                        ExtractedAnnotation(
                            text=text,
                            annotation_type="stroke",
                            start_offset=match.start(),
                            end_offset=match.end(),
                        )
                    )

        elif config.stroke_style == "comment":
            # Pattern: <!-- OCR: text -->
            pattern = r"<!-- OCR: (.+?) -->"

            for match in re.finditer(pattern, paragraph):
                extracted.append(
                    ExtractedAnnotation(
                        text=match.group(1),
                        annotation_type="stroke",
                        start_offset=match.start(),
                        end_offset=match.end(),
                    )
                )

        else:
            logger.warning(f"Unknown stroke style: {config.stroke_style}")
            return []

        logger.debug(
            f"Extracted {len(extracted)} strokes from paragraph " f"(style={config.stroke_style})"
        )

        return extracted

    # =========================================================================
    # Cluster-based Interface (for migration)
    # =========================================================================

    def detect_clusters(
        self,
        rm_file_path: Path,
        distance_threshold: float = 80.0,
    ) -> list[StrokeCluster]:
        """Extract stroke clusters from .rm file with full CRDT context.

        This is the cluster-based interface for annotation migration. It returns
        StrokeCluster objects that preserve CRDT block references for serialization.

        Args:
            rm_file_path: Path to reMarkable v6 .rm file
            distance_threshold: Maximum distance between stroke centers for clustering
                              (default: 80px)

        Returns:
            List of StrokeCluster objects with CRDT context
        """
        from rock_paper_sync.annotations.stroke_cluster import StrokeCluster

        clusters = StrokeCluster.from_rm_file(rm_file_path, distance_threshold)
        logger.debug(f"Detected {len(clusters)} stroke cluster(s) from {rm_file_path.name}")
        return clusters

    def migrate_clusters(
        self,
        clusters: list[StrokeCluster],
        old_text: str,
        new_text: str,
        fuzzy_threshold: float = 0.8,
        crdt_service: CrdtService | None = None,
    ) -> list[StrokeCluster]:
        """Migrate stroke clusters to a new document version.

        Uses AnchorContext.resolve() to find new anchor positions for each cluster.
        Clusters that cannot be resolved are dropped with a warning.

        Args:
            clusters: List of StrokeCluster objects from detect_clusters()
            old_text: Full text of the original document version
            new_text: Full text of the new document version
            fuzzy_threshold: Minimum similarity for fuzzy matching (0.0-1.0)
            crdt_service: Optional CRDT service for reanchoring (creates one if not provided)

        Returns:
            List of migrated StrokeCluster objects with updated anchors
        """
        from rock_paper_sync.annotations.services.crdt_service import (
            CrdtService as CrdtServiceClass,
        )

        if crdt_service is None:
            crdt_service = CrdtServiceClass()

        migrated: list[StrokeCluster] = []

        for cluster in clusters:
            if not cluster.anchor:
                # No anchor context - cannot migrate
                logger.warning(
                    f"Cluster {cluster.cluster_id[:8]}... has no anchor, skipping migration"
                )
                continue

            # Resolve anchor in new text
            resolved = cluster.anchor.resolve(
                old_text,
                new_text,
                fuzzy_threshold=fuzzy_threshold,
            )

            if resolved is None:
                logger.warning(
                    f"Cluster {cluster.cluster_id[:8]}... could not be resolved in new text, "
                    f"dropping annotation"
                )
                continue

            # Update bundles with new anchor offset
            new_anchor_offset = resolved.start_offset
            migrated_bundles = []
            for bundle in cluster.bundles:
                migrated_bundle = crdt_service.reanchor_bundle(bundle, new_anchor_offset)
                migrated_bundles.append(migrated_bundle)

            # Update strokes to reference new bundles
            new_strokes = []
            bundle_map = {
                (b.node_id.part1, b.node_id.part2): migrated_bundles[i]
                for i, b in enumerate(cluster.bundles)
            }
            for stroke in cluster.strokes:
                if stroke.bundle:
                    key = (stroke.bundle.node_id.part1, stroke.bundle.node_id.part2)
                    if key in bundle_map:
                        # Create new stroke with updated bundle reference
                        from rock_paper_sync.annotations.stroke import Stroke

                        new_stroke = Stroke(
                            stroke_id=stroke.stroke_id,
                            points=stroke.points,
                            bounding_box=stroke.bounding_box,
                            color=stroke.color,
                            tool=stroke.tool,
                            thickness=stroke.thickness,
                            tree_node_id=stroke.tree_node_id,
                            line_block=stroke.line_block,
                            bundle=bundle_map[key],
                        )
                        new_strokes.append(new_stroke)
                    else:
                        new_strokes.append(stroke)
                else:
                    new_strokes.append(stroke)

            # Create migrated cluster with updated anchor
            from rock_paper_sync.annotations.stroke_cluster import StrokeCluster

            migrated_cluster = StrokeCluster(
                cluster_id=cluster.cluster_id,
                strokes=new_strokes,
                bounding_box=cluster.bounding_box,
                anchor=self._update_anchor_context(cluster.anchor, resolved),
            )
            migrated_cluster._bundles = migrated_bundles
            migrated.append(migrated_cluster)

            logger.debug(
                f"Migrated cluster {cluster.cluster_id[:8]}... "
                f"from offset {cluster.anchor.paragraph_index} "
                f"to offset {new_anchor_offset} "
                f"(confidence={resolved.confidence:.2f}, type={resolved.match_type})"
            )

        logger.info(f"Migrated {len(migrated)}/{len(clusters)} clusters to new document version")
        return migrated

    def _update_anchor_context(
        self,
        old_anchor: AnchorContext,
        resolved: Any,  # AnchorResolution
    ) -> AnchorContext:
        """Update AnchorContext with new resolved position.

        Args:
            old_anchor: Original anchor context
            resolved: Resolved anchor from AnchorContext.resolve()

        Returns:
            Updated AnchorContext
        """
        from rock_paper_sync.annotations.document_model import AnchorContext

        return AnchorContext(
            content_hash=old_anchor.content_hash,
            text_content=old_anchor.text_content,
            paragraph_index=None,  # Will be recalculated on next detection
            section_path=old_anchor.section_path,
            context_before=old_anchor.context_before,
            context_after=old_anchor.context_after,
            line_range=None,  # Will be recalculated
            y_position_hint=old_anchor.y_position_hint,
            page_hint=old_anchor.page_hint,
            diff_anchor=old_anchor.diff_anchor,
        )

    def serialize_for_page(
        self,
        clusters: list[StrokeCluster],
        page_projection: PageProjection,
        crdt_service: CrdtService | None = None,
    ) -> list[Any]:
        """Serialize stroke clusters for a specific page.

        Filters clusters that belong to this page based on their anchor offset,
        prepares bundles for page injection, and returns raw rmscene blocks.

        Args:
            clusters: List of StrokeCluster objects
            page_projection: Page projection containing offset range
            crdt_service: Optional CRDT service for bundle preparation

        Returns:
            List of rmscene blocks ready for writing to .rm file
        """
        from rock_paper_sync.annotations.services.crdt_service import (
            CrdtService as CrdtServiceClass,
        )

        if crdt_service is None:
            crdt_service = CrdtServiceClass()

        blocks: list[Any] = []
        page_start = page_projection.doc_char_start
        page_end = page_projection.doc_char_end

        for cluster in clusters:
            # Determine if cluster belongs to this page
            # Use the first bundle's anchor offset as representative
            cluster_offset = None
            for bundle in cluster.bundles:
                if bundle.anchor_offset is not None:
                    cluster_offset = bundle.anchor_offset
                    break

            if cluster_offset is None:
                # No anchor - skip
                logger.debug(f"Cluster {cluster.cluster_id[:8]}... has no anchor, skipping")
                continue

            if not (page_start <= cluster_offset < page_end):
                # Not on this page
                continue

            # Prepare each bundle for page injection
            for bundle in cluster.bundles:
                prepared_bundle = crdt_service.prepare_bundle_for_page(bundle)
                blocks.extend(prepared_bundle.to_raw_blocks())

        logger.debug(
            f"Serialized {len(blocks)} blocks for page {page_projection.page_index} "
            f"(offset range {page_start}-{page_end})"
        )
        return blocks
