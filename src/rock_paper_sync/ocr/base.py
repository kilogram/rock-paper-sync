"""Base OCR service with shared functionality.

Provides common implementation for OCR service methods that are identical
across different backends (Local, Runpods, etc.).
"""

import base64
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx

from rock_paper_sync.ocr.protocol import (
    JobStatus,
    ModelInfo,
    OCRRequest,
    OCRResult,
    OCRServiceError,
    TrainingJob,
)


class BaseOCRService(ABC):
    """Abstract base class for OCR service implementations.

    Provides common functionality for:
    - Single-image recognition (delegates to batch)
    - Model info retrieval
    - Fine-tuning job submission
    - Training job status checking
    - Health checks
    - Resource management (context manager, close)

    Subclasses must implement:
    - _submit_and_get_results(): Backend-specific batch processing
    - __init__(): Backend-specific initialization

    Resource Management:
        This service holds HTTP connections that must be explicitly closed.
        Use as a context manager or call close() when done.
    """

    _client: httpx.Client
    timeout: float
    _logger: logging.Logger

    def recognize(self, request: OCRRequest) -> OCRResult:
        """Recognize text in a single annotation image.

        Args:
            request: OCR request with image and context

        Returns:
            OCR result with recognized text and confidence

        Raises:
            OCRServiceError: If recognition fails
        """
        results = self.recognize_batch([request])
        return results[0]

    def recognize_batch(self, requests: list[OCRRequest]) -> list[OCRResult]:
        """Recognize text in multiple annotation images.

        Args:
            requests: List of OCR requests

        Returns:
            List of OCR results in same order as requests

        Raises:
            OCRServiceError: If recognition fails
        """
        if not requests:
            return []

        # Prepare batch payload
        batch_input = self._build_batch_payload(requests)

        try:
            # Backend-specific submission and result retrieval
            outputs, processing_time = self._submit_and_get_results(batch_input, len(requests))

            # Validate result count
            if len(outputs) != len(requests):
                raise OCRServiceError(
                    f"Result count mismatch: expected {len(requests)}, got {len(outputs)}"
                )

            # Build result objects
            results = self._build_results(requests, outputs, processing_time)

            self._logger.info(f"OCR batch completed: {len(results)} results in {processing_time}ms")
            return results

        except httpx.HTTPStatusError as e:
            raise OCRServiceError(f"OCR API error: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            raise OCRServiceError(f"OCR request failed: {e}")

    @abstractmethod
    def _submit_and_get_results(
        self, batch_input: dict[str, Any], request_count: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Submit batch and retrieve results (backend-specific).

        Args:
            batch_input: Prepared batch payload
            request_count: Number of requests (for logging)

        Returns:
            Tuple of (output results list, processing time in ms)

        Raises:
            httpx.HTTPStatusError: On HTTP errors
            httpx.RequestError: On network errors
            OCRServiceError: On backend-specific errors
        """
        ...

    def _build_batch_payload(self, requests: list[OCRRequest]) -> dict[str, Any]:
        """Build the batch payload for OCR requests."""
        return {
            "input": {
                "action": "recognize_batch",
                "images": [
                    {
                        "uuid": req.annotation_uuid,
                        "image_b64": base64.b64encode(req.image).decode("utf-8"),
                        "context": {
                            "document_id": req.context.document_id,
                            "page_number": req.context.page_number,
                            "paragraph_index": req.context.paragraph_index,
                            "paragraph_text": req.context.paragraph_text,
                        },
                    }
                    for req in requests
                ],
            }
        }

    def _build_results(
        self,
        requests: list[OCRRequest],
        outputs: list[dict[str, Any]],
        total_processing_time: int,
    ) -> list[OCRResult]:
        """Build OCRResult objects from raw outputs."""
        results = []
        per_request_time = total_processing_time // len(requests)

        for req, output in zip(requests, outputs):
            results.append(
                OCRResult(
                    annotation_uuid=req.annotation_uuid,
                    text=output.get("text", ""),
                    confidence=output.get("confidence", 0.0),
                    model_version=output.get("model_version", "unknown"),
                    bounding_box=req.bounding_box,
                    context=req.context,
                    processing_time_ms=per_request_time,
                )
            )

        return results

    def get_model_info(self, default_base_model: str = "unknown") -> ModelInfo:
        """Get information about the current model.

        Args:
            default_base_model: Default base model name if not returned by API

        Returns:
            Model information including version and metrics

        Raises:
            OCRServiceError: If request fails
        """
        try:
            response = self._client.post(
                "/runsync",
                json={"input": {"action": "model_info"}},
            )
            response.raise_for_status()
            data = response.json().get("output", {})

            return ModelInfo(
                version=data.get("version", "unknown"),
                base_model=data.get("base_model", default_base_model),
                is_fine_tuned=data.get("is_fine_tuned", False),
                dataset_version=data.get("dataset_version"),
                created_at=datetime.fromisoformat(data["created_at"])
                if data.get("created_at")
                else None,
                metrics=data.get("metrics", {}),
            )

        except httpx.HTTPStatusError as e:
            raise OCRServiceError(f"Failed to get model info: {e.response.status_code}")
        except httpx.RequestError as e:
            raise OCRServiceError(f"Model info request failed: {e}")

    def fine_tune(self, dataset_version: str) -> TrainingJob:
        """Initiate a fine-tuning job.

        Args:
            dataset_version: Version of the correction dataset to use

        Returns:
            Training job information

        Raises:
            OCRServiceError: If job submission fails
        """
        try:
            response = self._client.post(
                "/run",
                json={
                    "input": {
                        "action": "fine_tune",
                        "dataset_version": dataset_version,
                    }
                },
            )
            response.raise_for_status()
            data = response.json()

            return TrainingJob(
                job_id=data.get("id", ""),
                status=JobStatus.PENDING,
                dataset_version=dataset_version,
                output_model_version=f"ft-{dataset_version}",
                started_at=datetime.now(),
            )

        except httpx.HTTPStatusError as e:
            raise OCRServiceError(f"Failed to start fine-tuning: {e.response.status_code}")
        except httpx.RequestError as e:
            raise OCRServiceError(f"Fine-tune request failed: {e}")

    def get_training_job(self, job_id: str) -> TrainingJob:
        """Get status of a training job.

        Args:
            job_id: ID of the training job

        Returns:
            Current job status and metrics

        Raises:
            OCRServiceError: If status request fails
        """
        try:
            response = self._client.get(f"/status/{job_id}")
            response.raise_for_status()
            data = response.json()

            status_map = {
                "IN_QUEUE": JobStatus.PENDING,
                "IN_PROGRESS": JobStatus.RUNNING,
                "COMPLETED": JobStatus.COMPLETED,
                "FAILED": JobStatus.FAILED,
            }

            output = data.get("output", {})
            return TrainingJob(
                job_id=job_id,
                status=status_map.get(data.get("status", ""), JobStatus.PENDING),
                dataset_version=output.get("dataset_version", ""),
                output_model_version=output.get("model_version", ""),
                started_at=datetime.fromisoformat(output["started_at"])
                if output.get("started_at")
                else None,
                completed_at=datetime.fromisoformat(output["completed_at"])
                if output.get("completed_at")
                else None,
                metrics=output.get("metrics", {}),
                error_message=data.get("error"),
            )

        except httpx.HTTPStatusError as e:
            raise OCRServiceError(f"Failed to get job status: {e.response.status_code}")
        except httpx.RequestError as e:
            raise OCRServiceError(f"Job status request failed: {e}")

    def health_check(self) -> bool:
        """Check if the service is available.

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            response = self._client.get("/health")
            return response.status_code == 200
        except Exception as e:
            self._logger.debug(f"Health check failed: {e}")
            return False

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "BaseOCRService":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
