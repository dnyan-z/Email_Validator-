"""Centralized logging setup for the Email Validation System."""

import logging
import os
from datetime import date

from config import LOG_DIR, LOG_LEVEL, LOG_THREAD_INFO, LOG_TO_CONSOLE


def _resolve_log_level() -> int:
    level_name = (LOG_LEVEL or "DEBUG").upper()
    return getattr(logging, level_name, logging.DEBUG)


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

    logger.setLevel(_resolve_log_level())

    thread_fmt = " | thread=%(threadName)s" if LOG_THREAD_INFO else ""

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s"
        + thread_fmt
        + " | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler – captures DEBUG and above
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(_resolve_log_level())
    fh.setFormatter(fmt)

    # Console handler – INFO and above only
    if LOG_TO_CONSOLE:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    logger.addHandler(fh)
    logger.propagate = False

    return logger
