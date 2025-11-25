"""OCR integration with sync flow.

Handles OCR processing during document sync, including:
- Processing annotations for OCR
- Generating OCR markers
- Detecting corrections
- Storing results

For details on stroke coordinate transformation, see docs/STROKE_ANCHORING.md
"""

import io
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from rock_paper_sync.annotations import (
    Annotation,
    AnnotationType,
    read_annotations,
)
from rock_paper_sync.annotation_mapper import extract_text_blocks_from_rm
from rock_paper_sync.coordinate_transformer import (
    extract_text_origin,
    build_parent_anchor_map,
    CoordinateTransformer,
    is_text_relative,
    NEGATIVE_Y_OFFSET,
)
from rock_paper_sync.ocr.corrections import CorrectionManager
from rock_paper_sync.ocr.factory import create_ocr_service
from rock_paper_sync.ocr.markers import (
    AnnotationInfo,
    add_ocr_markers,
    extract_paragraph_index_mapping,
    strip_ocr_markers,
    _hash_text,
)
from rock_paper_sync.ocr.paragraph_mapper import (
    ParagraphMapper,
    SpatialOverlapMapper,
)
from rock_paper_sync.ocr.protocol import (
    BoundingBox,
    OCRRequest,
    OCRServiceError,
    ParagraphContext,
)
from rock_paper_sync.parser import ContentBlock, parse_content

if TYPE_CHECKING:
    from rock_paper_sync.config import OCRConfig
    from rock_paper_sync.state import StateManager
    from rmscene.tagged_block_common import CrdtId

logger = logging.getLogger("rock_paper_sync.ocr.integration")


class OCRProcessor:
    """Handles OCR processing during sync."""

    def __init__(
        self,
        config: "OCRConfig",
        state_manager: "StateManager",
    ) -> None:
        """Initialize OCR processor.

        Args:
            config: OCR configuration
            state_manager: State manager for database access
        """
        self.config = config
        self.state_manager = state_manager
        self.correction_manager = CorrectionManager(config.cache_dir, state_manager)

        # Initialize paragraph mapper (can be swapped with vision model later)
        self.paragraph_mapper: ParagraphMapper = SpatialOverlapMapper()

        # Lazy-init service
        self._service = None

    @property
    def service(self):
        """Get OCR service (lazy initialization)."""
        if self._service is None:
            self._service = create_ocr_service(self.config)
        return self._service

    def cleanup(self) -> None:
        """Clean up resources held by the processor.

        Must be called when the processor is no longer needed to release
        HTTP connections and other resources held by the OCR service.
        """
        if self._service is not None:
            if hasattr(self._service, 'close'):
                self._service.close()
            self._service = None
            logger.debug("OCR processor cleaned up")

    def process_annotations(
        self,
        vault_name: str,
        obsidian_path: str,
        markdown_content: str,
        annotation_map: dict[int, AnnotationInfo],
        rm_files: list[Path],
        paragraph_texts: list[str],
    ) -> str:
        """Process annotations for OCR and generate marked content.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file
            markdown_content: Current markdown content
            annotation_map: Map of paragraph index to annotation info
            rm_files: List of .rm files with annotations
            paragraph_texts: List of paragraph texts by index

        Returns:
            Markdown content with OCR markers added
        """
        logger.info(
            f"process_annotations called: vault={vault_name}, path={obsidian_path}, "
            f"annotation_map={len(annotation_map)} paragraphs, rm_files={len(rm_files)} files"
        )
        logger.debug(f"annotation_map keys: {list(annotation_map.keys())}")
        logger.debug(f"rm_files: {[str(f) for f in rm_files]}")

        if not annotation_map:
            logger.debug("No annotation_map, returning markdown unchanged")
            return markdown_content

        # First, check for corrections in existing OCR blocks
        corrections, conflicts = self.correction_manager.process_markdown_file(
            vault_name, obsidian_path, markdown_content
        )

        if corrections:
            logger.info(
                f"Detected {len(corrections)} OCR corrections in {vault_name}:{obsidian_path}"
            )

        if conflicts:
            logger.warning(
                f"Detected {len(conflicts)} conflicts in {vault_name}:{obsidian_path} "
                f"(original text edited)"
            )
            # TODO: Handle conflicts - for now, just log

        # Parse markdown content into blocks for text matching
        markdown_blocks = parse_content(markdown_content)
        logger.debug(f"Parsed markdown into {len(markdown_blocks)} content blocks")

        # Extract annotation images from .rm files
        annotation_images = self._extract_annotation_images(rm_files, annotation_map, markdown_blocks)

        if not annotation_images:
            logger.debug("No annotation images extracted")
            return markdown_content

        # Build OCR requests
        requests = []
        uuid_to_image_hash: dict[str, str] = {}  # Track image hashes for efficient lookup
        for para_idx, (annotation, images) in annotation_images.items():
            para_text = paragraph_texts[para_idx] if para_idx < len(paragraph_texts) else ""

            for img_data, bbox, annotation_uuid in images:
                context = ParagraphContext(
                    document_id=f"{vault_name}:{obsidian_path}",
                    page_number=0,  # TODO: Get actual page number
                    paragraph_index=para_idx,
                    paragraph_text=para_text,
                )

                requests.append(OCRRequest(
                    image=img_data,
                    annotation_uuid=annotation_uuid,
                    bounding_box=bbox,
                    context=context,
                ))

                # Store image for potential corrections
                image_hash = self.correction_manager.store_annotation_image(
                    img_data, annotation_uuid
                )
                # Build mapping for efficient lookup later
                uuid_to_image_hash[annotation_uuid] = image_hash

        if not requests:
            return markdown_content

        # Run OCR
        logger.info(f"Running OCR on {len(requests)} annotations")
        try:
            results = self.service.recognize_batch(requests)
        except OCRServiceError as e:
            # Expected service failures (network, timeout, etc.)
            logger.warning(f"OCR service unavailable, skipping OCR: {e}")
            return markdown_content
        except Exception as e:
            # Unexpected errors - log with stack trace and re-raise
            logger.error(f"Unexpected OCR error: {e}", exc_info=True)
            raise

        # Group results by paragraph
        ocr_results: dict[int, tuple[AnnotationInfo, list[str]]] = {}

        for result in results:
            para_idx = result.context.paragraph_index
            annotation = annotation_map.get(para_idx)

            if not annotation:
                continue

            if para_idx not in ocr_results:
                ocr_results[para_idx] = (annotation, [])

            # Only include results above confidence threshold
            if result.confidence >= self.config.confidence_threshold:
                ocr_results[para_idx][1].append(result.text)

                # Store OCR result in state
                self.state_manager.update_ocr_result(
                    vault_name=vault_name,
                    obsidian_path=obsidian_path,
                    annotation_uuid=result.annotation_uuid,
                    paragraph_index=para_idx,
                    ocr_text=result.text,
                    ocr_text_hash=_hash_text(result.text),
                    original_text_hash=_hash_text(result.context.paragraph_text),
                    image_hash=uuid_to_image_hash[result.annotation_uuid],
                    confidence=result.confidence,
                    model_version=result.model_version,
                )

        # Add OCR markers to content
        if ocr_results:
            marked_content = add_ocr_markers(markdown_content, ocr_results)
            logger.info(f"Added OCR markers for {len(ocr_results)} paragraphs")
            return marked_content

        return markdown_content

    def _extract_annotation_images(
        self,
        rm_files: list[Path],
        annotation_map: dict[int, AnnotationInfo],
        markdown_blocks: list[ContentBlock] | None = None,
    ) -> dict[int, tuple[AnnotationInfo, list[tuple[bytes, BoundingBox, str]]]]:
        """Extract annotation images from .rm files using the new architecture.

        New flow:
        1. Extract all annotations from .rm files
        2. Cluster annotations spatially (words, phrases, marginalia)
        3. Map each cluster to a paragraph using bounding box overlap
        4. Render clusters to images

        Args:
            rm_files: List of .rm files
            annotation_map: Map of paragraph index to annotation info
            markdown_blocks: Optional list of markdown content blocks for matching

        Returns:
            Dict mapping paragraph index to (annotation_info, list of (image_data, bbox, uuid))
        """
        result: dict[int, tuple[AnnotationInfo, list[tuple[bytes, BoundingBox, str]]]] = {}

        logger.debug(f"Extracting images from {len(rm_files)} .rm files for {len(annotation_map)} annotated paragraphs")

        for rm_file in rm_files:
            if not rm_file.exists():
                logger.warning(f".rm file not found: {rm_file}")
                continue

            logger.debug(f"Processing .rm file: {rm_file}")

            try:
                # Extract annotations from .rm file
                annotations = read_annotations(rm_file)
                if not annotations:
                    logger.debug(f"No annotations found in {rm_file}")
                    continue

                logger.debug(f"Found {len(annotations)} annotations in {rm_file}")

                # Extract text blocks for position mapping
                rm_text_blocks, text_origin_y = extract_text_blocks_from_rm(rm_file)
                logger.debug(f"Extracted {len(rm_text_blocks)} text blocks, text_origin_y={text_origin_y}")

                # Extract text origin X from RootTextBlock
                text_origin_x = self._get_text_origin_x(rm_file)
                logger.debug(f"Text origin: x={text_origin_x}, y={text_origin_y}")

                # Build parent_id → (anchor_x, baseline_y) mapping via anchor system
                # Different parent_ids (CrdtId(2, xxx)) represent different text LINES,
                # each with its own anchor origin in X and Y. We extract per-parent
                # anchor origins from TreeNodeBlock anchor_ids and anchor_origin_x.
                parent_anchor_map = self._build_parent_baseline_map(rm_file)
                logger.debug(f"Built anchor origin map for {len(parent_anchor_map)} parent IDs")

                # Transform annotations to absolute coordinates using per-parent anchor origins
                # This ensures strokes from different text lines are positioned correctly
                annotations_absolute = self._transform_annotations_to_absolute(
                    annotations, parent_anchor_map, text_origin_x, text_origin_y
                )
                logger.debug(f"Transformed {len(annotations)} annotations to absolute coordinates")

                # STAGE 1: Cluster annotations spatially (using absolute coordinates)
                clusters = self._cluster_annotations_by_proximity(annotations_absolute)
                logger.debug(f"Clustered {len(annotations_absolute)} annotations into {len(clusters)} spatial groups")

                # STAGE 2: Map each cluster to a paragraph using bounding boxes
                for cluster_idx, cluster in enumerate(clusters):
                    # Render cluster (already in absolute coordinates after transformation)
                    image_data, cluster_bbox = self._render_annotations_to_image(cluster)

                    if not image_data:
                        logger.warning(f"No image data rendered for cluster {cluster_idx}")
                        continue

                    logger.debug(
                        f"Cluster {cluster_idx}: absolute bbox y={cluster_bbox.y:.1f}, h={cluster_bbox.height:.1f}"
                    )

                    # Map cluster to paragraph using spatial overlap
                    # Cluster bbox is already in absolute coordinates
                    para_idx = self.paragraph_mapper.map_cluster_to_paragraph(
                        cluster_bbox,
                        markdown_blocks or [],
                        rm_text_blocks,
                    )

                    if para_idx is None:
                        logger.debug(
                            f"Cluster {cluster_idx} (bbox: x={cluster_bbox.x:.1f}, y={cluster_bbox.y:.1f}) "
                            f"could not be mapped to any paragraph"
                        )
                        continue

                    # Verify paragraph is in annotation_map
                    if para_idx not in annotation_map:
                        logger.warning(
                            f"Cluster {cluster_idx} mapped to paragraph {para_idx}, "
                            f"but paragraph not in annotation_map"
                        )
                        continue

                    # Store image for this paragraph
                    annotation_info = annotation_map[para_idx]
                    annotation_uuid = str(uuid.uuid4())

                    if para_idx not in result:
                        result[para_idx] = (annotation_info, [])

                    result[para_idx][1].append((image_data, cluster_bbox, annotation_uuid))

                    logger.info(
                        f"Rendered cluster {cluster_idx+1}/{len(clusters)} "
                        f"with {len(cluster)} annotations → paragraph {para_idx}"
                    )

            except Exception as e:
                logger.error(f"Failed to extract annotations from {rm_file}: {e}", exc_info=True)
                continue

        logger.info(f"Extracted annotation images for {len(result)} paragraphs")
        return result

    def _cluster_annotations_by_proximity(
        self,
        annotations: list[Annotation],
        distance_threshold: float = 60.0,
    ) -> list[list[Annotation]]:
        """Cluster annotations using 2D Euclidean distance and connected components.

        This approach treats clustering as a graph problem:
        1. Each annotation is a node
        2. Nodes are connected if their Euclidean distance < threshold
        3. Clusters are the connected components of this graph

        This naturally handles 2D spatial distribution without separate
        horizontal/vertical thresholds, making it more robust to handwriting
        irregularities.

        Args:
            annotations: List of annotations to cluster
            distance_threshold: Maximum Euclidean distance for annotations to be
                              in same cluster (default: 60px works well for keeping
                              words together while separating different writing areas)

        Returns:
            List of annotation clusters
        """
        import math
        from collections import defaultdict

        if not annotations:
            return []

        # Extract bounding boxes and calculate centers
        # Filter out annotations without valid geometry to avoid corrupt clustering
        centers = []
        valid_annotations = []

        for ann in annotations:
            center = None

            if ann.type == AnnotationType.STROKE and ann.stroke:
                bbox = ann.stroke.bounding_box
                center = (bbox.x + bbox.w / 2, bbox.y + bbox.h / 2)
            elif ann.type == AnnotationType.HIGHLIGHT and ann.highlight:
                if ann.highlight.rectangles:
                    rect = ann.highlight.rectangles[0]
                    center = (rect.x + rect.w / 2, rect.y + rect.h / 2)

            if center is not None:
                centers.append(center)
                valid_annotations.append(ann)
            else:
                logger.warning(
                    f"Skipping annotation with no valid geometry: type={ann.type}"
                )

        # Use valid_annotations for the rest of clustering
        annotations = valid_annotations
        if not annotations:
            return []

        if len(annotations) == 1:
            return [annotations]

        # Build adjacency graph using Euclidean distance
        n = len(annotations)
        graph = defaultdict(list)

        for i in range(n):
            cx_i, cy_i = centers[i]
            for j in range(i + 1, n):
                cx_j, cy_j = centers[j]

                # Euclidean distance between centers
                distance = math.sqrt((cx_j - cx_i) ** 2 + (cy_j - cy_i) ** 2)

                logger.debug(
                    f"Annotation {i}→{j}: distance={distance:.1f} (threshold={distance_threshold})"
                )

                if distance < distance_threshold:
                    graph[i].append(j)
                    graph[j].append(i)
                    logger.debug(f"  → Connected (distance < threshold)")

        # Find connected components using DFS
        visited = set()
        clusters = []

        for i in range(n):
            if i not in visited:
                # Start new cluster
                cluster_indices = []
                stack = [i]

                while stack:
                    node = stack.pop()
                    if node not in visited:
                        visited.add(node)
                        cluster_indices.append(node)
                        # Add all connected neighbors to stack
                        stack.extend(graph[node])

                # Convert indices to annotations
                cluster = [annotations[idx] for idx in cluster_indices]
                clusters.append(cluster)
                logger.debug(
                    f"Created cluster {len(clusters)} with {len(cluster)} annotation(s)"
                )

        return clusters

    def _get_text_origin_x(self, rm_file: Path) -> float:
        """Extract text origin X coordinate from RootTextBlock.

        Args:
            rm_file: Path to .rm file

        Returns:
            X coordinate of text origin (default -375.0 if not found)
        """
        origin = extract_text_origin(rm_file)
        return origin.x

    def _build_parent_baseline_map(self, rm_file: Path) -> dict["CrdtId", tuple[float, float]]:
        """Build mapping of parent_ids to (X, Y) anchor origins via anchor system.

        Uses the coordinate_transformer module for extraction.

        Args:
            rm_file: Path to .rm file

        Returns:
            Dictionary mapping parent_id (CrdtId) to (anchor_x, baseline_y) tuple
        """
        anchor_map = build_parent_anchor_map(rm_file)
        # Convert AnchorOrigin objects to tuples for backwards compatibility
        return {pid: (origin.x, origin.y) for pid, origin in anchor_map.items()}

    def _get_parent_origin_offset(
        self, parent_id: "CrdtId", parent_anchor_map: dict["CrdtId", tuple[float, float]],
        text_origin_x: float, text_origin_y: float
    ) -> tuple[float, float]:
        """Get the (X, Y) anchor origin offset for a specific parent ID.

        Different parent_ids represent different text lines/formatting blocks,
        each with its own anchor origin in both X and Y. This returns the
        appropriate origin for transformation.

        Args:
            parent_id: The parent layer ID from the annotation
            parent_anchor_map: Mapping of parent_ids to (anchor_x, baseline_y)
            text_origin_x: The base text X origin (fallback)
            text_origin_y: The base text Y origin (fallback)

        Returns:
            (X, Y) origin tuple to use for this parent's coordinates
        """
        # Use parent-specific anchor origin if available, otherwise fall back to text origin
        return parent_anchor_map.get(parent_id, (text_origin_x, text_origin_y))

    def _transform_annotations_to_absolute(
        self,
        annotations: list[Annotation],
        parent_anchor_map: dict["CrdtId", tuple[float, float]],
        text_origin_x: float,
        text_origin_y: float,
    ) -> list[Annotation]:
        """Transform annotations from native coordinate space to absolute page coordinates.

        Uses coordinate_transformer module for transformations. See docs/STROKE_ANCHORING.md.

        Args:
            annotations: List of annotations (possibly in mixed coordinate spaces)
            parent_anchor_map: Mapping of parent_ids to (anchor_x, baseline_y) tuples
            text_origin_x: X offset of text layer origin (fallback)
            text_origin_y: Y offset of text layer origin (fallback)

        Returns:
            List of annotations with all coordinates transformed to absolute space
        """
        from copy import deepcopy
        from rock_paper_sync.annotations import Point, Rectangle

        transformed = []

        for ann in annotations:
            # Check if annotation is in text-relative space
            if not is_text_relative(ann.parent_id):
                # Already in absolute coordinates
                transformed.append(ann)
                continue

            # Need to transform to absolute coordinates
            if ann.type == AnnotationType.STROKE and ann.stroke:
                # Create a copy and transform stroke points
                ann_copy = deepcopy(ann)

                # Get parent-specific anchor origin (X, Y)
                anchor_x, anchor_y = self._get_parent_origin_offset(
                    ann.parent_id, parent_anchor_map, text_origin_x, text_origin_y
                )

                # Calculate stroke's center Y to determine which coordinate space
                bbox = ann_copy.stroke.bounding_box
                stroke_center_y = bbox.y + bbox.h / 2

                # Determine Y offset based on coordinate space
                # Positive Y: relative to text origin (top of text area)
                # Negative Y: relative to baseline + line height
                y_offset = NEGATIVE_Y_OFFSET if stroke_center_y < 0 else 0

                # Apply same offset to ALL points in stroke (preserves shape)
                transformed_points = [
                    Point(
                        x=point.x + anchor_x,
                        y=text_origin_y + y_offset + point.y,
                    )
                    for point in ann_copy.stroke.points
                ]

                # Update stroke with transformed points
                ann_copy.stroke.points = transformed_points

                # Recalculate bounding box with new coordinates
                if transformed_points:
                    min_x = min(p.x for p in transformed_points)
                    max_x = max(p.x for p in transformed_points)
                    min_y = min(p.y for p in transformed_points)
                    max_y = max(p.y for p in transformed_points)
                    ann_copy.stroke.bounding_box = Rectangle(
                        x=min_x, y=min_y, w=max_x - min_x, h=max_y - min_y
                    )

                transformed.append(ann_copy)
                logger.debug(
                    f"Transformed text-relative annotation: Y offset +{text_origin_y}"
                )
            else:
                # Non-stroke annotations (highlights, etc.) - keep as-is for now
                transformed.append(ann)

        return transformed

    def _render_annotations_to_image(
        self,
        annotations: list[Annotation],
    ) -> tuple[bytes, BoundingBox]:
        """Render annotations to a PNG image.

        Args:
            annotations: List of annotations to render

        Returns:
            Tuple of (PNG image data, bounding box)
        """
        if not annotations:
            return b"", BoundingBox(0, 0, 0, 0)

        # Calculate combined bounding box
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")

        for annotation in annotations:
            if annotation.type == AnnotationType.STROKE and annotation.stroke:
                bbox = annotation.stroke.bounding_box
                min_x = min(min_x, bbox.x)
                min_y = min(min_y, bbox.y)
                max_x = max(max_x, bbox.x + bbox.w)
                max_y = max(max_y, bbox.y + bbox.h)
            elif annotation.type == AnnotationType.HIGHLIGHT and annotation.highlight:
                for rect in annotation.highlight.rectangles:
                    min_x = min(min_x, rect.x)
                    min_y = min(min_y, rect.y)
                    max_x = max(max_x, rect.x + rect.w)
                    max_y = max(max_y, rect.y + rect.h)

        # Handle case where no valid bounds were found
        if min_x == float("inf"):
            return b"", BoundingBox(0, 0, 0, 0)

        # Add padding
        padding = 10
        min_x -= padding
        min_y -= padding
        max_x += padding
        max_y += padding

        width = int(max_x - min_x)
        height = int(max_y - min_y)

        # Create image with white background
        img = Image.new("RGB", (max(width, 1), max(height, 1)), color="white")
        draw = ImageDraw.Draw(img)

        # Render each annotation
        for annotation in annotations:
            if annotation.type == AnnotationType.STROKE and annotation.stroke:
                self._draw_stroke(draw, annotation.stroke, min_x, min_y)
            elif annotation.type == AnnotationType.HIGHLIGHT and annotation.highlight:
                self._draw_highlight(draw, annotation.highlight, min_x, min_y)

        # Convert to PNG bytes
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        image_data = buffer.getvalue()

        bbox = BoundingBox(
            x=min_x,
            y=min_y,
            width=float(width),
            height=float(height),
        )

        return image_data, bbox

    def _draw_stroke(self, draw: ImageDraw.Draw, stroke, offset_x: float, offset_y: float) -> None:
        """Draw a stroke on the image.

        Args:
            draw: PIL ImageDraw object
            stroke: Stroke object with points
            offset_x: X offset to subtract
            offset_y: Y offset to subtract
        """
        if len(stroke.points) < 2:
            return

        # Map color codes to RGB
        color_map = {
            0: "black",      # Black
            1: "gray",       # Grey
            2: "white",      # White (will be invisible on white bg)
            3: "yellow",     # Yellow highlighter
            4: "green",      # Green
            5: "pink",       # Pink
            6: "blue",       # Blue
            7: "red",        # Red
        }
        color = color_map.get(stroke.color, "black")

        # Draw lines between consecutive points
        points = [
            (p.x - offset_x, p.y - offset_y)
            for p in stroke.points
        ]

        # Calculate line width based on thickness
        line_width = max(1, int(stroke.thickness * 2))

        draw.line(points, fill=color, width=line_width)

    def _draw_highlight(self, draw: ImageDraw.Draw, highlight, offset_x: float, offset_y: float) -> None:
        """Draw a highlight on the image.

        Args:
            draw: PIL ImageDraw object
            highlight: Highlight object with rectangles
            offset_x: X offset to subtract
            offset_y: Y offset to subtract
        """
        # Map color codes to semi-transparent colors
        color_map = {
            3: (255, 255, 0, 128),    # Yellow
            4: (0, 255, 0, 128),      # Green
            5: (255, 192, 203, 128),  # Pink
            6: (173, 216, 230, 128),  # Light blue
        }
        color = color_map.get(highlight.color, (255, 255, 0, 128))

        for rect in highlight.rectangles:
            x1 = rect.x - offset_x
            y1 = rect.y - offset_y
            x2 = x1 + rect.w
            y2 = y1 + rect.h

            # Draw semi-transparent rectangle
            draw.rectangle([x1, y1, x2, y2], fill=color[:3])

    def strip_ocr_markers(self, markdown_content: str) -> str:
        """Strip OCR markers from content before generating document.

        Args:
            markdown_content: Markdown with OCR markers

        Returns:
            Clean markdown without OCR markers
        """
        return strip_ocr_markers(markdown_content)

    def get_stats(self, vault_name: str | None = None) -> dict:
        """Get OCR processing statistics.

        Args:
            vault_name: Optional vault name filter

        Returns:
            Dictionary with stats
        """
        stats = self.state_manager.get_ocr_correction_stats()
        return {
            "corrections_pending": stats["pending"],
            "corrections_total": stats["total"],
            "datasets_created": stats["datasets"],
        }
