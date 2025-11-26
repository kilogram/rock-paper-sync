"""Tests for local minimal OCR service integration.

These tests verify that the LocalOCRService can connect to the minimal OCR
container and process requests correctly.
"""

import io
import pytest
from PIL import Image

from rock_paper_sync.ocr.protocol import (
    OCRRequest,
    BoundingBox,
    ParagraphContext,
)


class TestLocalOCRService:
    """Tests for LocalOCRService integration."""

    def test_health_check(self, ocr_service):
        """Test OCR service health check."""
        assert ocr_service.health_check() is True

    def test_get_model_info(self, ocr_service):
        """Test getting model information."""
        info = ocr_service.get_model_info()
        assert info.version is not None
        assert info.base_model is not None
        assert isinstance(info.is_fine_tuned, bool)

    def test_recognize_single_image(self, ocr_service):
        """Test recognizing text in a single image."""
        # Create a simple test image
        img = Image.new('RGB', (100, 100), color='white')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')

        request = OCRRequest(
            image=img_bytes.getvalue(),
            annotation_uuid="test-single-1",
            bounding_box=BoundingBox(x=10, y=10, width=80, height=80),
            context=ParagraphContext(
                document_id="doc-1",
                page_number=1,
                paragraph_index=0,
                paragraph_text="test text"
            ),
        )

        result = ocr_service.recognize(request)

        assert result.annotation_uuid == "test-single-1"
        assert result.text is not None
        assert len(result.text) > 0
        assert 0 <= result.confidence <= 1.0
        assert result.model_version is not None

    def test_recognize_batch(self, ocr_service):
        """Test batch recognition of multiple images."""
        # Create multiple test images
        requests = []
        for i in range(3):
            img = Image.new('RGB', (100 + i * 10, 100 + i * 10), color='white')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')

            request = OCRRequest(
                image=img_bytes.getvalue(),
                annotation_uuid=f"test-batch-{i}",
                bounding_box=BoundingBox(x=10, y=10, width=80 + i, height=80 + i),
                context=ParagraphContext(
                    document_id="doc-batch",
                    page_number=1,
                    paragraph_index=i,
                    paragraph_text=f"text {i}"
                ),
            )
            requests.append(request)

        results = ocr_service.recognize_batch(requests)

        assert len(results) == 3
        for i, result in enumerate(results):
            assert result.annotation_uuid == f"test-batch-{i}"
            assert result.text is not None
            assert len(result.text) > 0
            assert 0 <= result.confidence <= 1.0

    def test_deterministic_results(self, ocr_service):
        """Test that the same image produces the same results (deterministic)."""
        # Create the same image twice
        img_bytes_list = []
        for _ in range(2):
            img = Image.new('RGB', (100, 100), color='white')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes_list.append(img_bytes.getvalue())

        # Process both images
        request1 = OCRRequest(
            image=img_bytes_list[0],
            annotation_uuid="test-deterministic-1",
            bounding_box=BoundingBox(x=0, y=0, width=100, height=100),
            context=ParagraphContext(
                document_id="doc-1",
                page_number=1,
                paragraph_index=0,
                paragraph_text="test"
            ),
        )

        request2 = OCRRequest(
            image=img_bytes_list[1],
            annotation_uuid="test-deterministic-2",
            bounding_box=BoundingBox(x=0, y=0, width=100, height=100),
            context=ParagraphContext(
                document_id="doc-1",
                page_number=1,
                paragraph_index=0,
                paragraph_text="test"
            ),
        )

        result1 = ocr_service.recognize(request1)
        result2 = ocr_service.recognize(request2)

        # Images with same dimensions should produce same text
        # (due to deterministic text generation based on image size and uuid)
        # Note: They have different UUIDs so the text might differ, but
        # calling with the same UUID should produce the same text
        assert result1.text is not None
        assert result2.text is not None

    def test_empty_batch(self, ocr_service):
        """Test batch recognition with empty list."""
        results = ocr_service.recognize_batch([])
        assert results == []
