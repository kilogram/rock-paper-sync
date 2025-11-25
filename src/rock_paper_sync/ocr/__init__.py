"""OCR processing for handwritten annotations.

This module provides OCR capabilities for recognizing handwritten text
in reMarkable annotations, with support for local (Podman) and cloud
(Runpods) inference providers.

Key components:
- protocol.py: Service protocol and data types
- runpods.py: Runpods serverless implementation
- factory.py: Service instantiation
- markers.py: OCR marker parsing/generation
- corrections.py: Correction detection for training
- training.py: Training pipeline with DVC
- integration.py: Converter sync flow integration
"""

from rock_paper_sync.ocr.protocol import (
    BoundingBox,
    ModelInfo,
    OCRRequest,
    OCRResult,
    OCRServiceProtocol,
    ParagraphContext,
    TrainingJob,
)
from rock_paper_sync.ocr.factory import create_ocr_service
from rock_paper_sync.ocr.integration import OCRProcessor

__all__ = [
    "BoundingBox",
    "create_ocr_service",
    "ModelInfo",
    "OCRProcessor",
    "OCRRequest",
    "OCRResult",
    "OCRServiceProtocol",
    "ParagraphContext",
    "TrainingJob",
]
