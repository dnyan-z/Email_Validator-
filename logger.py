"""
logger.py
---------
Centralised logging setup for the Email Validation System.

Creates two handlers:
  - File handler  → logs/email_validator_YYYY-MM-DD.log
  - Console handler → INFO+ messages on stdout

Usage:
    from logger import get_logger
    log = get_logger(__name__)
    log.info("Starting validation…")
"""

import logging
import os
from datetime import date

from config import LOG_DIR


def get_logger(name: str) -> logging.Logger:
    """
    Return a configured Logger instance.

    Parameters
    ----------
    name : str
        Typically __name__ of the calling module.

    Returns
    -------
    logging.Logger
        Logger with both file and console handlers attached.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    log_file = os.path.join(LOG_DIR, f"email_validator_{date.today()}.log")

    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if get_logger is called more than once
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler – captures DEBUG and above
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console handler – INFO and above only
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger
