"""Entry point for OCR service."""

import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="TrOCR Service")
    parser.add_argument("command", choices=["serve", "serverless", "train"], help="Command to run")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--dataset", help="Dataset version for training")
    parser.add_argument("--output", help="Output model version for training")

    args = parser.parse_args()

    if args.command == "serve":
        from ocr_service.app import app

        uvicorn.run(app, host=args.host, port=args.port)
    elif args.command == "serverless":
        from ocr_service.handler import start_serverless

        start_serverless()
    elif args.command == "train":
        if not args.dataset or not args.output:
            parser.error("--dataset and --output required for training")
        from ocr_service.training import run_training

        run_training(args.dataset, args.output)


if __name__ == "__main__":
    main()
