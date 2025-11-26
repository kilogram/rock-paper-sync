"""OCR service factory.

Creates appropriate OCR service instance based on configuration.
"""

import logging
from typing import TYPE_CHECKING

from rock_paper_sync.ocr.protocol import OCRServiceProtocol, OCRServiceError

if TYPE_CHECKING:
    from rock_paper_sync.config import OCRConfig

logger = logging.getLogger("rock_paper_sync.ocr.factory")


def create_ocr_service(config: "OCRConfig") -> OCRServiceProtocol:
    """Create OCR service instance based on configuration.

    Args:
        config: OCR configuration

    Returns:
        OCR service instance implementing OCRServiceProtocol

    Raises:
        OCRServiceError: If provider is invalid or service creation fails
    """
    provider = config.provider

    if provider == "runpods":
        from rock_paper_sync.ocr.runpods import RunpodsOCRService

        logger.info("Creating Runpods OCR service")
        return RunpodsOCRService(
            endpoint_id=config.runpods_endpoint_id,
            api_key=config.runpods_api_key,
            timeout=config.timeout,
        )

    elif provider == "local":
        from rock_paper_sync.ocr.local import LocalOCRService

        logger.info("Creating local OCR service")
        return LocalOCRService(
            container_url=getattr(config, "local_container_url", "http://localhost:8000"),
            timeout=config.timeout,
        )

    else:
        raise OCRServiceError(
            f"Unknown OCR provider: {provider}. "
            f"Valid providers are: 'local', 'runpods'"
        )
