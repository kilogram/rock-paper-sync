"""Runpods OCR service client.

Implements the OCR service protocol using Runpods serverless endpoints.
"""

import logging
import os
import time
from typing import Any

import httpx

from rock_paper_sync.ocr.base import BaseOCRService
from rock_paper_sync.ocr.protocol import ModelInfo, OCRServiceError

RUNPODS_API_BASE = "https://api.runpod.ai/v2"


class RunpodsOCRService(BaseOCRService):
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
        self._logger = logging.getLogger("rock_paper_sync.ocr.runpods")
        self._client = httpx.Client(
            base_url=f"{RUNPODS_API_BASE}/{self.endpoint_id}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

        self._logger.debug(f"Initialized Runpods OCR service with endpoint {self.endpoint_id}")

    def _submit_and_get_results(
        self, batch_input: dict[str, Any], request_count: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Submit batch and poll for results asynchronously.

        Runpods uses async job processing, so we submit to /run and poll
        for completion.

        Args:
            batch_input: Prepared batch payload
            request_count: Number of requests (for logging)

        Returns:
            Tuple of (output results list, processing time in ms)
        """
        start_time = time.time()

        # Submit job
        response = self._client.post("/run", json=batch_input)
        response.raise_for_status()
        job_data = response.json()
        job_id = job_data.get("id")

        if not job_id:
            raise OCRServiceError("No job ID returned from Runpods")

        self._logger.debug(f"Submitted OCR batch job {job_id} with {request_count} images")

        # Poll for completion
        result = self._poll_job(job_id)

        processing_time = int((time.time() - start_time) * 1000)
        outputs = result.get("output", {}).get("results", [])

        return outputs, processing_time

    def get_model_info(self) -> ModelInfo:
        """Get information about the current model.

        Returns:
            Model information including version and metrics

        Raises:
            OCRServiceError: If request fails
        """
        return super().get_model_info(default_base_model="microsoft/trocr-base-handwritten")

    def _poll_job(self, job_id: str, poll_interval: float = 1.0, max_retries: int = 3) -> dict:
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
                    self._logger.warning(
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
                    self._logger.warning(
                        f"Network error polling job {job_id}, retry {retry_count}/{max_retries}: {e}"
                    )
                    time.sleep(current_interval)
                    continue
                raise OCRServiceError(f"Job polling failed after {max_retries} retries: {e}")
