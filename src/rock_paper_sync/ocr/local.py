"""Local Podman OCR service client.

Implements the OCR service protocol using a local Podman container running
the minimal OCR service. Suitable for testing and development.
"""

import base64
import json
import logging
import time
from datetime import datetime

import httpx

from rock_paper_sync.ocr.protocol import (
    JobStatus,
    ModelInfo,
    OCRRequest,
    OCRResult,
    OCRServiceError,
    OCRServiceProtocol,
    TrainingJob,
)

logger = logging.getLogger("rock_paper_sync.ocr.local")


class LocalOCRService:
    """OCR service implementation using a local Podman container.

    Implements OCRServiceProtocol for local inference and training using
    a containerized OCR service.

    Resource Management:
        This service holds HTTP connections that must be explicitly closed.
        Use as a context manager or call close() when done:

            # Option 1: Context manager (preferred)
            with LocalOCRService(container_url) as service:
                results = service.recognize_batch(requests)

            # Option 2: Explicit cleanup
            service = LocalOCRService(container_url)
            try:
                results = service.recognize_batch(requests)
            finally:
                service.close()
    """

    def __init__(
        self,
        container_url: str = "http://localhost:8000",
        timeout: float = 30.0,
    ) -> None:
        """Initialize local OCR service.

        Args:
            container_url: URL of the local OCR container (default: http://localhost:8000)
            timeout: Request timeout in seconds

        Raises:
            OCRServiceError: If service is not available
        """
        self.container_url = container_url
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=container_url,
            timeout=timeout,
        )

        # Verify service is available
        if not self.health_check():
            self._client.close()
            raise OCRServiceError(
                f"Local OCR service not available at {container_url}. "
                "Make sure the container is running: "
                "podman run -d -p 8000:8000 rock-paper-sync-ocr-minimal:latest"
            )

        logger.debug(f"Initialized local OCR service at {container_url}")

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
        batch_input = {
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

        start_time = time.time()

        try:
            # Submit batch (local container processes synchronously)
            response = self._client.post("/run", json=batch_input)
            response.raise_for_status()
            job_data = response.json()

            processing_time = int((time.time() - start_time) * 1000)

            # Parse results
            outputs = job_data.get("output", {}).get("results", [])
            if len(outputs) != len(requests):
                raise OCRServiceError(
                    f"Result count mismatch: expected {len(requests)}, got {len(outputs)}"
                )

            # Build result objects
            results = []
            for req, output in zip(requests, outputs):
                results.append(
                    OCRResult(
                        annotation_uuid=req.annotation_uuid,
                        text=output.get("text", ""),
                        confidence=output.get("confidence", 0.0),
                        model_version=output.get("model_version", "unknown"),
                        bounding_box=req.bounding_box,
                        context=req.context,
                        processing_time_ms=processing_time // len(requests),
                    )
                )

            logger.info(f"OCR batch completed: {len(results)} results in {processing_time}ms")
            return results

        except httpx.HTTPStatusError as e:
            raise OCRServiceError(f"Local OCR API error: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            raise OCRServiceError(f"Local OCR request failed: {e}")

    def get_model_info(self) -> ModelInfo:
        """Get information about the current model.

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
                base_model=data.get("base_model", "minimal"),
                is_fine_tuned=data.get("is_fine_tuned", False),
                dataset_version=data.get("dataset_version"),
                created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
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
                started_at=datetime.fromisoformat(output["started_at"]) if output.get("started_at") else None,
                completed_at=datetime.fromisoformat(output["completed_at"]) if output.get("completed_at") else None,
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
            logger.debug(f"Health check failed: {e}")
            return False

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "LocalOCRService":
        return self

    def __exit__(self, *args) -> None:
        self.close()
