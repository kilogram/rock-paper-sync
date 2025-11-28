"""Entry point for minimal OCR service.

Runs the FastAPI server on port 8000.
"""

import uvicorn

if __name__ == "__main__":
    # Start FastAPI server
    uvicorn.run(
        "ocr_service.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
