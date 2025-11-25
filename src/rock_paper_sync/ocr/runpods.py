"""Runpods OCR service client.

Implements the OCR service protocol using Runpods serverless endpoints.
"""

import base64
import json
import logging
import os
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

logger = logging.getLogger("rock_paper_sync.ocr.runpods")

RUNPODS_API_BASE = "https://api.runpod.ai/v2"


class RunpodsOCRService:
    """OCR service implementation using Runpods serverless endpoints.

    Implements OCRServiceProtocol for cloud-based inference and training.

    Resource Management:
        This service holds HTTP connections that must be explicitly closed.
        Use as a context manager or call close() when done:

            # Option 1: Context manager (preferred)
            with RunpodsOCRService(endpoint_id, api_key) as service:
                results = service.recognize_batch(requests)

            # Option 2: Explicit cleanup
            service = RunpodsOCRService(endpoint_id, api_key)
            try:
                results = service.recognize_batch(requests)
            finally:
                service.close()
    """

    def __init__(
        self,
        endpoint_id: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        """Initialize Runpods OCR service.

        Args:
            endpoint_id: Runpods endpoint ID (or from RPS_RUNPODS_ENDPOINT_ID env var)
            api_key: Runpods API key (or from RUNPODS_API_KEY env var)
            timeout: Request timeout in seconds

        Raises:
            OCRServiceError: If credentials are missing
        """
        self.endpoint_id = endpoint_id or os.environ.get("RPS_RUNPODS_ENDPOINT_ID")
        self.api_key = api_key or os.environ.get("RUNPODS_API_KEY")

        if not self.endpoint_id:
            raise OCRServiceError(
                "Runpods endpoint ID required. Set RPS_RUNPODS_ENDPOINT_ID environment variable "
                "or provide endpoint_id parameter."
            )

        if not self.api_key:
            raise OCRServiceError(
                "Runpods API key required. Set RUNPODS_API_KEY environment variable "
                "or provide api_key parameter."
            )

        self.timeout = timeout
        self._client = httpx.Client(
            base_url=f"{RUNPODS_API_BASE}/{self.endpoint_id}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

        logger.debug(f"Initialized Runpods OCR service with endpoint {self.endpoint_id}")

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

        Uses Runpods async endpoint for batch processing.

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
            # Submit job
            response = self._client.post("/run", json=batch_input)
            response.raise_for_status()
            job_data = response.json()
            job_id = job_data.get("id")

            if not job_id:
                raise OCRServiceError("No job ID returned from Runpods")

            logger.debug(f"Submitted OCR batch job {job_id} with {len(requests)} images")

            # Poll for completion
            result = self._poll_job(job_id)

            processing_time = int((time.time() - start_time) * 1000)

            # Parse results
            outputs = result.get("output", {}).get("results", [])
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
            raise OCRServiceError(f"Runpods API error: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            raise OCRServiceError(f"Runpods request failed: {e}")

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
                base_model=data.get("base_model", "microsoft/trocr-base-handwritten"),
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
            logger.warning(f"Health check failed: {e}")
            return False

    def _poll_job(
        self, job_id: str, poll_interval: float = 1.0, max_retries: int = 3
    ) -> dict:
        """Poll for job completion with exponential backoff and retry logic.

        Args:
            job_id: Job ID to poll
            poll_interval: Initial seconds between polls
            max_retries: Max retries for transient network errors

        Returns:
            Job result data

        Raises:
            OCRServiceError: If job fails or times out
        """
        start_time = time.time()
        current_interval = poll_interval
        max_interval = 30.0
        backoff_factor = 1.5
        retry_count = 0

        while True:
            if time.time() - start_time > self.timeout:
                raise OCRServiceError(f"Job {job_id} timed out after {self.timeout}s")

            try:
                response = self._client.get(f"/status/{job_id}")
                response.raise_for_status()
                data = response.json()
                retry_count = 0  # Reset on success

                status = data.get("status")
                if status == "COMPLETED":
                    return data
                elif status == "FAILED":
                    error = data.get("error", "Unknown error")
                    raise OCRServiceError(f"Job {job_id} failed: {error}")
                elif status in ("IN_QUEUE", "IN_PROGRESS"):
                    time.sleep(current_interval)
                    # Exponential backoff to reduce API load
                    current_interval = min(current_interval * backoff_factor, max_interval)
                else:
                    raise OCRServiceError(f"Unknown job status: {status}")

            except httpx.HTTPStatusError as e:
                # Retry on transient server errors
                if e.response.status_code in (502, 503, 504) and retry_count < max_retries:
                    retry_count += 1
                    logger.warning(
                        f"Transient error {e.response.status_code} polling job {job_id}, "
                        f"retry {retry_count}/{max_retries}"
                    )
                    time.sleep(current_interval)
                    continue
                raise OCRServiceError(
                    f"Job polling failed: {e.response.status_code} - {e.response.text}"
                )
            except httpx.RequestError as e:
                # Retry on network errors
                if retry_count < max_retries:
                    retry_count += 1
                    logger.warning(
                        f"Network error polling job {job_id}, retry {retry_count}/{max_retries}: {e}"
                    )
                    time.sleep(current_interval)
                    continue
                raise OCRServiceError(f"Job polling failed after {max_retries} retries: {e}")

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "RunpodsOCRService":
        return self

    def __exit__(self, *args) -> None:
        self.close()
