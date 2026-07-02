"""
smtp_validator.py
-----------------
Stage 3 – SMTP Mailbox Verification.

Performs an SMTP handshake up to the RCPT TO command to verify whether
a mailbox exists.  NO email is ever sent.

Sequence:
  1. Connect to the MX host on port 25
  2. Send EHLO / HELO
  3. Send MAIL FROM: <verify@yourdomain.com>
  4. Send RCPT TO: <target@domain.com>
  5. Parse the 3-digit SMTP response code
  6. Immediately QUIT

Common response meanings:
  250  → Mailbox exists (VALID)
  550, 551, 553 → Mailbox does not exist (INVALID_MAILBOX)
  554  → User does not exist (INVALID_MAILBOX)
  421, 450, 451, 452 → Temporary failure
  503, 530, 535 → Access denied / auth required
  anything else → UNKNOWN
"""

import smtplib
import socket
import time

from config import (
    SMTP_RETRIES,
    SMTP_RETRY_DELAY,
    SMTP_SENDER,
    SMTP_TIMEOUT,
    STATUS_ACCESS_DENIED,
    STATUS_INVALID_MAILBOX,
    STATUS_TEMPORARY_FAILURE,
    STATUS_UNKNOWN,
    STATUS_VALID,
)
from logger import get_logger

log = get_logger(__name__)

# SMTP response-code → status mapping
_CODE_MAP = {
    # Success
    250: STATUS_VALID,
    251: STATUS_VALID,
    # Hard failures (mailbox doesn't exist)
    550: STATUS_INVALID_MAILBOX,
    551: STATUS_INVALID_MAILBOX,
    553: STATUS_INVALID_MAILBOX,
    554: STATUS_INVALID_MAILBOX,
    # Access / policy rejections
    503: STATUS_ACCESS_DENIED,
    530: STATUS_ACCESS_DENIED,
    535: STATUS_ACCESS_DENIED,
    # Temporary failures
    421: STATUS_TEMPORARY_FAILURE,
    450: STATUS_TEMPORARY_FAILURE,
    451: STATUS_TEMPORARY_FAILURE,
    452: STATUS_TEMPORARY_FAILURE,
}

_REASON_MAP = {
    STATUS_VALID:             "Mailbox exists",
    STATUS_INVALID_MAILBOX:   "Mailbox does not exist",
    STATUS_ACCESS_DENIED:     "Access denied by server",
    STATUS_TEMPORARY_FAILURE: "Temporary server failure",
    STATUS_UNKNOWN:           "Unknown SMTP response",
}


def verify_mailbox(email: str, mx_hosts: list[str]) -> dict:
    """
    Attempt SMTP verification against each MX host in priority order.

    Retries up to SMTP_RETRIES times per host on connection errors before
    moving to the next host.

    Parameters
    ----------
    email : str
        Normalised email address to verify.
    mx_hosts : list[str]
        Ordered list of MX hostnames (highest priority first).

    Returns
    -------
    dict with keys:
        email         – passed through unchanged
        status        – one of the STATUS_* constants
        reason        – human-readable explanation
        smtp_response – raw SMTP code + message, e.g. "250 OK"
    """
    if not mx_hosts:
        return _result(email, STATUS_TEMPORARY_FAILURE,
                       "No MX hosts provided", "N/A")

    last_error = "No connection attempt made"

    for mx in mx_hosts:
        for attempt in range(1, SMTP_RETRIES + 2):  # +1 for the initial try
            try:
                code, message = _smtp_check(email, mx)
                status = _CODE_MAP.get(code, STATUS_UNKNOWN)
                reason = _REASON_MAP.get(status, f"SMTP code {code}")
                smtp_resp = f"{code} {message}"
                log.debug("SMTP %s | %s | MX=%s | %s",
                          status, email, mx, smtp_resp)
                return _result(email, status, reason, smtp_resp)

            except smtplib.SMTPConnectError as exc:
                last_error = f"Connection error to {mx}: {exc}"
                log.debug("SMTP connect error (attempt %d) | %s | %s",
                          attempt, email, last_error)

            except smtplib.SMTPServerDisconnected as exc:
                last_error = f"Server disconnected ({mx}): {exc}"
                log.debug("SMTP disconnect | %s | %s", email, last_error)
                break  # No point retrying same host

            except (socket.timeout, TimeoutError):
                last_error = f"Timeout connecting to {mx}"
                log.debug("SMTP timeout | %s | %s", email, last_error)
                break  # Move to next MX

            except OSError as exc:
                last_error = f"Network error ({mx}): {exc}"
                log.debug("SMTP OS error | %s | %s", email, last_error)
                break

            finally:
                if attempt <= SMTP_RETRIES:
                    time.sleep(SMTP_RETRY_DELAY)

    log.debug("SMTP exhausted all hosts | %s | %s", email, last_error)
    return _result(email, STATUS_TEMPORARY_FAILURE, last_error, "N/A")


# ── Private helpers ───────────────────────────────────────────────────────────

def _smtp_check(email: str, mx_host: str) -> tuple[int, str]:
    """
    Open a real SMTP connection, perform the handshake up to RCPT TO,
    then immediately QUIT.  Nothing is ever sent.

    Parameters
    ----------
    email : str
        Target email address for RCPT TO.
    mx_host : str
        SMTP server hostname.

    Returns
    -------
    tuple[int, str]
        (SMTP response code, response message) from RCPT TO.

    Raises
    ------
    smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected,
    socket.timeout, OSError
    """
    with smtplib.SMTP(timeout=SMTP_TIMEOUT) as smtp:
        smtp.connect(mx_host, 25)
        smtp.ehlo_or_helo_if_needed()
        smtp.mail(SMTP_SENDER)
        code, message = smtp.rcpt(email)
        smtp.quit()

    # message may be bytes in some Python builds
    if isinstance(message, bytes):
        message = message.decode(errors="replace")

    return code, message


def _result(email: str, status: str, reason: str, smtp_response: str) -> dict:
    """Build the standard result dict for this module."""
    return {
        "email": email,
        "status": status,
        "reason": reason,
        "smtp_response": smtp_response,
    }
