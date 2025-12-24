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
- Rendered as OCR blocks in markdown

For coordinate transformation details, see docs/STROKE_ANCHORING.md.

Example (traditional interface):
    handler = StrokeHandler(ocr_processor)
    annotations = handler.detect(rm_file_path)
    mappings = handler.map(annotations, markdown_blocks, rm_file_path)
    output = handler.render(0, mappings[0], "Original paragraph")

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
from rock_paper_sync.annotations.common.anchors import AnnotationAnchor
from rock_paper_sync.annotations.common.spatial import find_nearest_paragraph_by_y
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm
from rock_paper_sync.annotations.core.data_types import (
    ExtractedAnnotation,
    RenderConfig,
)
from rock_paper_sync.coordinate_transformer import (
    NEGATIVE_Y_OFFSET,
    CoordinateTransformer,
    build_parent_anchor_map,
    extract_text_origin,
    is_text_relative,
)

if TYPE_CHECKING:
    from typing import Any

    from rock_paper_sync.annotations.document_model import (
        AnchorContext,
        ContextResolver,
        PageProjection,
    )
    from rock_paper_sync.annotations.services.crdt_service import CrdtService
    from rock_paper_sync.annotations.stroke_cluster import StrokeCluster
    from rock_paper_sync.layout import LayoutContext
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

    def render(
        self,
        paragraph_index: int,
        matches: list[Annotation],
        original_content: str,
    ) -> str:
        """Render stroke annotations as OCR blocks.

        If OCR processor is available, performs text extraction.
        Otherwise, renders as placeholder markers.

        Args:
            paragraph_index: Index of paragraph in markdown
            matches: List of stroke annotations for this paragraph
            original_content: Original paragraph text

        Returns:
            Markdown text with OCR blocks or placeholder markers
        """
        if not matches:
            return original_content

        # If no OCR processor, render as placeholder
        if not self.ocr_processor:
            num_strokes = len(matches)
            marker = f"<!-- {num_strokes} handwritten annotation(s) -->"
            return f"{marker}\n{original_content}"

        # With OCR processor, render would be handled by OCRProcessor.process_annotations
        # This is a placeholder - actual OCR rendering happens in the OCR pipeline
        # For now, just add a marker
        num_strokes = len(matches)
        marker = f"<!-- {num_strokes} stroke(s) pending OCR -->"
        return f"{marker}\n{original_content}"

    def create_anchor(
        self,
        annotation: Annotation,
        paragraph_text: str,
        paragraph_index: int,
        page_num: int = 0,
    ) -> AnnotationAnchor:
        """Create anchor from stroke annotation for matching and correction detection.

        Args:
            annotation: Stroke annotation from detect()
            paragraph_text: Full text of the matched paragraph
            paragraph_index: Index of paragraph in markdown
            page_num: Page number (default: 0)

        Returns:
            AnnotationAnchor with stroke location/content information
        """
        if not annotation.stroke or not annotation.stroke.bounding_box:
            raise ValueError("Stroke annotation missing bounding box")

        bbox = annotation.stroke.bounding_box
        center_x = bbox.x + bbox.w / 2
        center_y = bbox.y + bbox.h / 2

        # Extract context from paragraph text
        # For strokes, we don't have the actual text yet (needs OCR)
        # But we can provide surrounding paragraph context
        context_before = paragraph_text[:50] if paragraph_text else ""
        context_after = paragraph_text[-50:] if len(paragraph_text) > 50 else ""

        # Check if we have OCR text from state
        ocr_text = None
        # Note: In production, we would load state here to get OCR text
        # For now, anchors are created without OCR text initially

        return AnnotationAnchor.from_stroke(
            page_num=page_num,
            position=(center_x, center_y),
            bounding_box=(bbox.x, bbox.y, bbox.w, bbox.h),
            paragraph_index=paragraph_index,
            ocr_text=ocr_text,
            context_before=context_before,
            context_after=context_after,
        )

    def get_position(
        self,
        block,
        text_origin_y: float,
    ) -> tuple[float, float] | None:
        """Get absolute position for a stroke (Line) block.

        Strokes use the dual-anchor coordinate system:
        - Positive Y: absolute_y = text_origin_y + native_y
        - Negative Y: absolute_y = text_origin_y + NEGATIVE_Y_OFFSET + native_y

        Args:
            block: Raw rmscene SceneLineItemBlock
            text_origin_y: Y coordinate of text origin from .rm file

        Returns:
            Tuple of (absolute_x, absolute_y), or None if position cannot be determined
        """
        try:
            if not hasattr(block, "item") or not hasattr(block.item, "value"):
                return None

            value = block.item.value

            # Verify this is a Line block
            if "Line" not in type(value).__name__:
                return None

            # Extract native coordinates from points
            if not hasattr(value, "points") or not value.points:
                return None

            xs = [p.x for p in value.points if hasattr(p, "x")]
            ys = [p.y for p in value.points if hasattr(p, "y")]

            if not xs or not ys:
                return None

            native_x = sum(xs) / len(xs)
            native_y = sum(ys) / len(ys)

            # Check if text-relative
            is_text_rel = False
            if hasattr(block, "parent_id"):
                is_text_rel = is_text_relative(block.parent_id)

            # Transform to absolute coordinates using dual-anchor system
            if is_text_rel:
                # Apply NEGATIVE_Y_OFFSET for negative Y strokes
                y_offset = NEGATIVE_Y_OFFSET if native_y < 0 else 0
                absolute_y = text_origin_y + y_offset + native_y
            else:
                absolute_y = native_y

            # X coordinate doesn't need transformation for routing decisions
            absolute_x = native_x

            logger.debug(
                f"Stroke position: native_y={native_y:.1f} → absolute_y={absolute_y:.1f} "
                f"(y_offset={NEGATIVE_Y_OFFSET if native_y < 0 else 0})"
            )
            return (absolute_x, absolute_y)

        except Exception as e:
            logger.warning(f"Failed to get stroke position: {e}")
            return None

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
        """Relocate stroke annotation (pass-through).

        Strokes don't need coordinate adjustment during regeneration because:
        1. Their positions are relative to TreeNodeBlock anchors
        2. The roundtrip mechanism updates anchor_ids automatically

        Args:
            block: Raw rmscene SceneLineItemBlock
            old_text: Page text before modification (unused)
            new_text: Page text after modification (unused)
            old_origin: Origin of old text block (unused)
            new_origin: Origin of new text block (unused)
            layout_engine: Layout engine (unused)
            geometry: Device geometry (unused)
            crdt_base_id: CRDT base ID (unused)

        Returns:
            Block unchanged - anchor roundtrip handles stroke relocation
        """
        # Strokes use anchor-based positioning via TreeNodeBlocks
        # The roundtrip mechanism in generate_rm_file handles updating anchor_ids
        return block

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
        context_resolver: ContextResolver,
        crdt_service: CrdtService | None = None,
    ) -> list[StrokeCluster]:
        """Migrate stroke clusters to a new document version.

        Uses ContextResolver to find new anchor positions for each cluster.
        Clusters that cannot be resolved are dropped with a warning.

        Args:
            clusters: List of StrokeCluster objects from detect_clusters()
            old_text: Full text of the original document version
            new_text: Full text of the new document version
            context_resolver: Resolver for anchor migration
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
            resolved = context_resolver.resolve(
                cluster.anchor,
                old_text,
                new_text,
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
        resolved: Any,  # ResolvedAnchorContext
    ) -> AnchorContext:
        """Update AnchorContext with new resolved position.

        Args:
            old_anchor: Original anchor context
            resolved: Resolved anchor from ContextResolver

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
