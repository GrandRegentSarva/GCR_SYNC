"""Structured logging for gcr-sync.

Provides a configured logger that writes to both console and a rotating log file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "gcr-sync.log"

_initialized: bool = False


def setup_logger(level: str = "INFO") -> logging.Logger:
    """Configure and return the application logger.

    Creates the log directory if it doesn't exist. Sets up both a console
    handler (stdout) and a file handler with structured formatting.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    Returns:
        Configured logging.Logger instance.
    """
    global _initialized

    logger = logging.getLogger("gcr-sync")

    if _initialized:
        return logger

    # Create log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Set level
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    # Formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # Always log everything to file
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    _initialized = True
    return logger


def get_logger() -> logging.Logger:
    """Retrieve the application logger.

    Returns:
        The gcr-sync logger instance. If not yet initialized,
        initializes with default INFO level.
    """
    logger = logging.getLogger("gcr-sync")
    if not _initialized:
        return setup_logger()
    return logger
