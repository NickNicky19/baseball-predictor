"""Centralized logging configuration for the prediction system."""

from __future__ import annotations

import logging
import sys
from typing import Optional


def setup_logging(
    level: int = logging.INFO,
    log_format: Optional[str] = None,
) -> None:
    """Configure root logger once for CLI, GUI, and batch jobs."""
    if log_format is None:
        log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)