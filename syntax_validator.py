"""
syntax_validator.py
-------------------
Stage 1 – Email Syntax Validation.

Responsibilities:
  • Normalise the raw email string (strip whitespace, lowercase)
  • Check the format with a regex pre-filter
  • Run a full RFC-compliant check via the email-validator library

Returns a dict describing the result so every stage shares the same
response shape.
"""

import re

from email_validator import EmailNotValidError, validate_email

from config import STATUS_INVALID_FORMAT, STATUS_VALID
from logger import get_logger

log = get_logger(__name__)

# Simple pre-filter regex – catches obviously malformed addresses quickly
_BASIC_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def normalize_email(raw: str) -> str:
    """
    Trim whitespace and convert the email to lowercase.

    Parameters
    ----------
    raw : str
        The raw email string from the spreadsheet cell.

    Returns
    -------
    str
        Cleaned, lowercase email address.
    """
    return raw.strip().lower()


def validate_syntax(raw_email: str) -> dict:
    """
    Validate the syntax of a single email address.

    Steps
    -----
    1. Normalise (strip + lowercase).
    2. Quick regex check.
    3. Full RFC check via email-validator.

    Parameters
    ----------
    raw_email : str
        The raw email value from the Excel cell.

    Returns
    -------
    dict with keys:
        email        – normalised email (or original on failure)
        status       – STATUS_VALID | STATUS_INVALID_FORMAT
        reason       – human-readable explanation
        smtp_response – empty string at this stage
    """
    email = normalize_email(raw_email)

    # ── Regex pre-check ──────────────────────────────────────────────────────
    if not _BASIC_RE.match(email):
        reason = f"Failed basic format check: '{email}'"
        log.debug("SYNTAX FAIL (regex) | %s | %s", email, reason)
        return _result(email, STATUS_INVALID_FORMAT, reason)

    # ── email-validator full check ────────────────────────────────────────────
    try:
        valid = validate_email(email, check_deliverability=False)
        normalised = valid.normalized  # library may further normalise
        log.debug("SYNTAX OK | %s", normalised)
        return _result(normalised, STATUS_VALID, "Syntax is valid")
    except EmailNotValidError as exc:
        reason = str(exc)
        log.debug("SYNTAX FAIL (library) | %s | %s", email, reason)
        return _result(email, STATUS_INVALID_FORMAT, reason)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _result(email: str, status: str, reason: str) -> dict:
    """Build the standard result dict for this module."""
    return {
        "email": email,
        "status": status,
        "reason": reason,
        "smtp_response": "",
    }
