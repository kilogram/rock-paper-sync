"""Device test scenarios.

Each module contains test cases for specific functionality:
- annotation_tests: Annotation sync and marker verification
- ocr_tests: OCR recognition and correction workflows
"""

from .annotation_tests import (
    AnnotationRoundtripTest,
    NoHashLoopTest,
    ContentEditTest,
)
from .ocr_tests import (
    OCRRecognitionTest,
    OCRCorrectionTest,
    OCRStabilityTest,
)

# All test classes for runner
ALL_TESTS = [
    AnnotationRoundtripTest,
    NoHashLoopTest,
    ContentEditTest,
    OCRRecognitionTest,
    OCRCorrectionTest,
    OCRStabilityTest,
]

# Test lookup by name
TESTS_BY_NAME = {
    "annotation-roundtrip": AnnotationRoundtripTest,
    "no-hash-loop": NoHashLoopTest,
    "content-edit": ContentEditTest,
    "ocr-recognition": OCRRecognitionTest,
    "ocr-correction": OCRCorrectionTest,
    "ocr-stability": OCRStabilityTest,
}

__all__ = [
    "AnnotationRoundtripTest",
    "NoHashLoopTest",
    "ContentEditTest",
    "OCRRecognitionTest",
    "OCRCorrectionTest",
    "OCRStabilityTest",
    "ALL_TESTS",
    "TESTS_BY_NAME",
]
