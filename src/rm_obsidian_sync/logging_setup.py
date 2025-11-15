"""Logging configuration for reMarkable-Obsidian Sync.

Sets up hierarchical logging with console and file handlers.
"""

import logging
from pathlib import Path


def setup_logging(log_level: str, log_file: Path) -> None:
    """Configure application logging with console and file handlers.

    Creates a hierarchical logger structure:
    - Root logger: 'rm_obsidian_sync'
    - Component loggers: 'rm_obsidian_sync.config', 'rm_obsidian_sync.parser', etc.

    Args:
        log_level: Logging level for console output (debug, info, warning, error)
        log_file: Path to log file (will be created if it doesn't exist)

    Example:
        >>> setup_logging('info', Path('~/.local/share/rm-obsidian-sync/sync.log'))
    """
    # Create log file directory if needed
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Get root logger for this application
    root_logger = logging.getLogger("rm_obsidian_sync")
    root_logger.setLevel(logging.DEBUG)  # Capture everything, filter at handler level

    # Remove any existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Console handler (user-facing, respects log_level)
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, log_level.upper()))
    console_format = logging.Formatter("%(levelname)s: %(message)s")
    console.setFormatter(console_format)

    # File handler (detailed debugging, always DEBUG level)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)

    # Add handlers to root logger
    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)

    # Prevent propagation to root logger to avoid duplicate messages
    root_logger.propagate = False

    # Log initial message
    root_logger.debug(f"Logging initialized: console={log_level}, file=DEBUG")
