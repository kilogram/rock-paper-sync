"""OCR service protocol and data types.

Defines the interface for OCR services (local and cloud) and associated
data structures for inference and training.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol


# Exception hierarchy for OCR operations
class OCRError(Exception):
    """Base exception for all OCR-related errors."""

    pass


class OCRServiceError(OCRError):
    """Raised when OCR service communication or processing fails."""

    pass


class OCRDataError(OCRError):
    """Raised when OCR data validation or format is incorrect."""

    pass


class OCRConfigError(OCRError):
    """Raised when OCR configuration is invalid."""

    pass


class JobStatus(Enum):
    """Status of a training job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class BoundingBox:
    """Bounding box for an annotation region."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ParagraphContext:
    """Context for associating annotation with source content."""

    document_id: str
    page_number: int
    paragraph_index: int
    paragraph_text: str
    preceding_text: str = ""  # ~100 chars before
    following_text: str = ""  # ~100 chars after


@dataclass(frozen=True)
class OCRRequest:
    """Request for OCR recognition."""

    image: bytes
    annotation_uuid: str
    bounding_box: BoundingBox
    context: ParagraphContext


@dataclass
class OCRResult:
    """Result from OCR recognition."""

    annotation_uuid: str
    text: str
    confidence: float
    model_version: str
    bounding_box: BoundingBox
    context: ParagraphContext
    processing_time_ms: int = 0


@dataclass
class ModelInfo:
    """Information about the current OCR model."""

    version: str
    base_model: str
    is_fine_tuned: bool
    dataset_version: str | None
    created_at: datetime | None
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class TrainingJob:
    """Training job information."""

    job_id: str
    status: JobStatus
    dataset_version: str
    output_model_version: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    error_message: str | None = None


class OCRServiceProtocol(Protocol):
    """Protocol for OCR services supporting inference and training.

    Implementations (LocalOCRService, RunpodsOCRService) provide both
    text recognition and model fine-tuning capabilities.
    """

    def recognize(self, request: OCRRequest) -> OCRResult:
        """Recognize text in a single annotation image.

        Args:
            request: OCR request with image and context

        Returns:
            OCR result with recognized text and confidence
        """
        ...

    def recognize_batch(self, requests: list[OCRRequest]) -> list[OCRResult]:
        """Recognize text in multiple annotation images.

        Args:
            requests: List of OCR requests

        Returns:
            List of OCR results in same order as requests
        """
        ...

    def get_model_info(self) -> ModelInfo:
        """Get information about the current model.

        Returns:
            Model information including version and metrics
        """
        ...

    def health_check(self) -> bool:
        """Check if the service is available.

        Returns:
            True if service is healthy, False otherwise
        """
        ...

    def fine_tune(self, dataset_version: str) -> TrainingJob:
        """Initiate a fine-tuning job.

        Args:
            dataset_version: Version of the correction dataset to use

        Returns:
            Training job information
        """
        ...

    def get_training_job(self, job_id: str) -> TrainingJob:
        """Get status of a training job.

        Args:
            job_id: ID of the training job

        Returns:
            Current job status and metrics
        """
        ...
