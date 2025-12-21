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

from rock_paper_sync.annotations.core.data_types import AnnotationInfo
from rock_paper_sync.ocr.corrections import CorrectionManager
from rock_paper_sync.ocr.factory import create_ocr_service
from rock_paper_sync.ocr.markers import (
    _hash_text,
    add_ocr_markers,
    strip_ocr_markers,
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

if TYPE_CHECKING:
    from rock_paper_sync.annotations.document_model import (
        DocumentAnnotation,
        DocumentModel,
        HighlightData,
        StrokeData,
    )
    from rock_paper_sync.config import OCRConfig
    from rock_paper_sync.state import StateManager

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
        cache_dir = config.cache_dir or Path.home() / ".cache" / "rock-paper-sync"
        self.correction_manager = CorrectionManager(cache_dir, state_manager)

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
            try:
                close_method = getattr(self._service, "close", None)
                if close_method is not None and callable(close_method):
                    close_method()
            except Exception as e:  # pragma: no cover
                logger.warning(f"Failed to clean up OCR service: {e}")
            finally:
                self._service = None
                logger.debug("OCR processor cleaned up")

    def process_annotations(
        self,
        vault_name: str,
        obsidian_path: str,
        markdown_content: str,
        annotation_map: dict[int, AnnotationInfo],
        document_model: "DocumentModel",
        paragraph_texts: list[str] | None = None,
    ) -> str:
        """Process annotations for OCR and generate marked content.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file
            markdown_content: Current markdown content
            annotation_map: Map of paragraph index to annotation info
            document_model: DocumentModel with pre-clustered annotations
            paragraph_texts: List of paragraph texts by index

        Returns:
            Markdown content with OCR markers added
        """
        if paragraph_texts is None:
            paragraph_texts = []

        logger.info(
            f"process_annotations called: vault={vault_name}, path={obsidian_path}, "
            f"annotation_map={len(annotation_map)} paragraphs, "
            f"document_model_annotations={len(document_model.annotations)}"
        )
        logger.debug(f"annotation_map keys: {list(annotation_map.keys())}")

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

        # Extract annotation images from DocumentModel (unified clustering)
        annotation_images = self._extract_annotation_images_from_model(
            document_model, annotation_map
        )

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

                requests.append(
                    OCRRequest(
                        image=img_data,
                        annotation_uuid=annotation_uuid,
                        bounding_box=bbox,
                        context=context,
                    )
                )

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

    def _extract_annotation_images_from_model(
        self,
        document_model: "DocumentModel",
        annotation_map: dict[int, AnnotationInfo],
    ) -> dict[int, tuple[AnnotationInfo, list[tuple[bytes, BoundingBox, str]]]]:
        """Extract annotation images from DocumentModel (unified clustering path).

        Uses pre-clustered annotations from DocumentModel. Each cluster's paragraph
        assignment comes from the anchor_context, ensuring consistency between
        annotation preservation and OCR processing.

        Args:
            document_model: DocumentModel with pre-clustered annotations
            annotation_map: Map of paragraph index to annotation info

        Returns:
            Dict mapping paragraph index to (annotation_info, list of (image_data, bbox, uuid))
        """
        result: dict[int, tuple[AnnotationInfo, list[tuple[bytes, BoundingBox, str]]]] = {}

        # Get pre-computed clusters from DocumentModel
        clusters = document_model.get_annotation_clusters()
        logger.debug(f"Processing {len(clusters)} annotation clusters from DocumentModel")

        for cluster_idx, cluster in enumerate(clusters):
            if not cluster:
                continue

            # Get paragraph index from first annotation's anchor context
            # All annotations in a cluster should have the same paragraph_index
            first_annotation = cluster[0]
            para_idx = None
            if first_annotation.anchor_context:
                para_idx = first_annotation.anchor_context.paragraph_index

            if para_idx is None:
                logger.debug(
                    f"Cluster {cluster_idx} has no paragraph_index in anchor_context, skipping"
                )
                continue

            # Verify paragraph is in annotation_map
            if para_idx not in annotation_map:
                logger.debug(
                    f"Cluster {cluster_idx} mapped to paragraph {para_idx}, "
                    f"but paragraph not in annotation_map, skipping"
                )
                continue

            # Render cluster to image using DocumentAnnotation rendering
            image_data, bbox = self._render_document_annotations_to_image(cluster)

            if not image_data:
                logger.warning(f"No image data rendered for cluster {cluster_idx}")
                continue

            # Create unique ID for this cluster
            annotation_uuid = str(uuid.uuid4())

            # Store result
            annotation_info = annotation_map[para_idx]
            if para_idx not in result:
                result[para_idx] = (annotation_info, [])

            result[para_idx][1].append((image_data, bbox, annotation_uuid))

            logger.info(
                f"Rendered cluster {cluster_idx + 1}/{len(clusters)} "
                f"with {len(cluster)} annotations → paragraph {para_idx}"
            )

        logger.info(
            f"Extracted annotation images for {len(result)} paragraphs (DocumentModel path)"
        )
        return result

    def _render_document_annotations_to_image(
        self,
        annotations: list["DocumentAnnotation"],
    ) -> tuple[bytes, BoundingBox]:
        """Render DocumentAnnotation objects to a PNG image.

        This uses the unified DocumentAnnotation type with StrokeData/HighlightData.

        Args:
            annotations: List of DocumentAnnotation objects to render

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
            if annotation.annotation_type == "stroke" and annotation.stroke_data:
                x, y, w, h = annotation.stroke_data.bounding_box
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x + w)
                max_y = max(max_y, y + h)
            elif annotation.annotation_type == "highlight" and annotation.highlight_data:
                for rect in annotation.highlight_data.rectangles:
                    x, y, w, h = rect
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x + w)
                    max_y = max(max_y, y + h)

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
            if annotation.annotation_type == "stroke" and annotation.stroke_data:
                self._draw_stroke_data(draw, annotation.stroke_data, min_x, min_y)
            elif annotation.annotation_type == "highlight" and annotation.highlight_data:
                self._draw_highlight_data(draw, annotation.highlight_data, min_x, min_y)

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

    def _draw_stroke_data(
        self,
        draw: ImageDraw.ImageDraw,
        stroke_data: "StrokeData",
        offset_x: float,
        offset_y: float,
    ) -> None:
        """Draw a stroke from StrokeData on the image.

        Args:
            draw: PIL ImageDraw object
            stroke_data: StrokeData object with points
            offset_x: X offset to subtract
            offset_y: Y offset to subtract
        """
        if len(stroke_data.points) < 2:
            return

        # Map color codes to RGB
        color_map = {
            0: "black",  # Black
            1: "gray",  # Grey
            2: "white",  # White (will be invisible on white bg)
            3: "yellow",  # Yellow highlighter
            4: "green",  # Green
            5: "pink",  # Pink
            6: "blue",  # Blue
            7: "red",  # Red
        }
        color = color_map.get(stroke_data.color, "black")

        # Draw lines between consecutive points (Point objects have .x, .y)
        points = [(p.x - offset_x, p.y - offset_y) for p in stroke_data.points]

        # Calculate line width based on thickness
        line_width = max(1, int(stroke_data.thickness * 2))

        draw.line(points, fill=color, width=line_width)

    def _draw_highlight_data(
        self,
        draw: ImageDraw.ImageDraw,
        highlight_data: "HighlightData",
        offset_x: float,
        offset_y: float,
    ) -> None:
        """Draw a highlight from HighlightData on the image.

        Args:
            draw: PIL ImageDraw object
            highlight_data: HighlightData object with rectangles
            offset_x: X offset to subtract
            offset_y: Y offset to subtract
        """
        # Map color codes to semi-transparent colors
        color_map = {
            3: (255, 255, 0, 128),  # Yellow
            4: (0, 255, 0, 128),  # Green
            5: (255, 192, 203, 128),  # Pink
            6: (173, 216, 230, 128),  # Light blue
        }
        color = color_map.get(highlight_data.color, (255, 255, 0, 128))

        # HighlightData.rectangles are tuples: (x, y, w, h)
        for rect in highlight_data.rectangles:
            x, y, w, h = rect
            x1 = x - offset_x
            y1 = y - offset_y
            x2 = x1 + w
            y2 = y1 + h

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
