"""OCR service factory.

Creates appropriate OCR service instance based on configuration.
"""

import logging
from typing import TYPE_CHECKING

from rock_paper_sync.ocr.protocol import OCRServiceProtocol

if TYPE_CHECKING:
    from rock_paper_sync.config import OCRConfig

logger = logging.getLogger("rock_paper_sync.ocr.factory")


class OCRServiceError(Exception):
    """Exception raised for OCR service creation errors."""
    pass


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
        # Local Podman provider not yet implemented
        raise OCRServiceError(
            "Local Podman OCR service not yet implemented. "
            "Use provider='runpods' for now, or contribute the local implementation!"
        )

    else:
        raise OCRServiceError(
            f"Unknown OCR provider: {provider}. "
            f"Valid providers are: 'local', 'runpods'"
        )
