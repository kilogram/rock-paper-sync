"""Local Podman OCR service client.

Implements the OCR service protocol using a local Podman container running
the minimal OCR service. Suitable for testing and development.
"""

import logging
import time
from typing import Any

import httpx

from rock_paper_sync.ocr.base import BaseOCRService
from rock_paper_sync.ocr.protocol import ModelInfo, OCRServiceError


class LocalOCRService(BaseOCRService):
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
        self._logger = logging.getLogger("rock_paper_sync.ocr.local")
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

        self._logger.debug(f"Initialized local OCR service at {container_url}")

    def _submit_and_get_results(
        self, batch_input: dict[str, Any], request_count: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Submit batch and retrieve results synchronously.

        Local container processes requests synchronously, so we get results
        immediately from the /run endpoint.

        Args:
            batch_input: Prepared batch payload
            request_count: Number of requests (for logging)

        Returns:
            Tuple of (output results list, processing time in ms)
        """
        start_time = time.time()

        response = self._client.post("/run", json=batch_input)
        response.raise_for_status()
        job_data = response.json()

        processing_time = int((time.time() - start_time) * 1000)
        outputs = job_data.get("output", {}).get("results", [])

        return outputs, processing_time

    def get_model_info(self) -> ModelInfo:
        """Get information about the current model.

        Returns:
            Model information including version and metrics

        Raises:
            OCRServiceError: If request fails
        """
        return super().get_model_info(default_base_model="minimal")
