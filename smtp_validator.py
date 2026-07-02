"""Stage 3 - SMTP mailbox validation with richer diagnostics and caching."""

from __future__ import annotations

import smtplib
import socket
import time
from dataclasses import dataclass

from cache_utils import HybridTTLCache, SQLiteTTLCache
from config import (
    CACHE_DB_PATH,
    CACHE_MEMORY_MAXSIZE,
    SMTP_CACHE_TTL_SECONDS,
    SMTP_COMMAND_TIMEOUT,
    SMTP_MAX_MX_HOSTS,
    SMTP_MAX_BACKOFF_SECONDS,
    SMTP_RETRIES,
    SMTP_RETRY_DELAY,
    SMTP_SENDER,
    SMTP_TOTAL_TIMEOUT_SECONDS,
    SMTP_TIMEOUT,
    STATUS_ACCESS_DENIED,
    STATUS_INVALID_MAILBOX,
    STATUS_TEMPORARY_FAILURE,
    STATUS_UNKNOWN,
    STATUS_VALID,
)
from logger import get_logger

log = get_logger(__name__)

_sqlite_cache = SQLiteTTLCache(CACHE_DB_PATH)
_smtp_cache = HybridTTLCache(
    namespace="smtp",
    sqlite_cache=_sqlite_cache,
    memory_maxsize=CACHE_MEMORY_MAXSIZE,
    default_ttl_seconds=SMTP_CACHE_TTL_SECONDS,
)

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

_STATUS_CODE_DETAILS = {
    250: "Requested mail action okay, mailbox accepted",
    251: "User not local; mailbox may still be accepted",
    252: "Cannot VRFY user but accepted for delivery attempt",
    421: "Service not available; closing transmission channel",
    450: "Mailbox unavailable (temporary)",
    451: "Requested action aborted: local error",
    452: "Insufficient system storage",
    500: "Syntax error, command unrecognized",
    501: "Syntax error in parameters or arguments",
    502: "Command not implemented",
    503: "Bad sequence of commands",
    504: "Command parameter not implemented",
    521: "Server does not accept mail",
    550: "Mailbox unavailable (permanent)",
    551: "User not local",
    552: "Exceeded storage allocation",
    553: "Mailbox name not allowed",
    554: "Transaction failed",
}


@dataclass(slots=True)
class SMTPCheckResult:
    code: int
    message: str
    server_banner: str
    tls_version: str
    tls_cipher: str
    supports_smtputf8: bool
    supports_pipelining: bool
    supports_8bitmime: bool
    supports_auth: bool
    supports_size: bool
    supports_starttls: bool
    smtp_latency_ms: float
    connection_ms: float
    transcript: str
    greylist_detected: bool
    throttled_detected: bool


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
    cache_key = f"{email.lower()}::{','.join(mx_hosts).lower()}"
    cached = _smtp_cache.get(cache_key)
    if cached is not None:
        return cached

    if not mx_hosts:
        return _result(email, STATUS_TEMPORARY_FAILURE, "NO_MX_HOSTS_PROVIDED", "N/A")

    last_error = "No connection attempt made"
    smtp_started = time.perf_counter()
    mx_hosts = mx_hosts[: max(1, SMTP_MAX_MX_HOSTS)]

    for mx in mx_hosts:
        if time.perf_counter() - smtp_started > SMTP_TOTAL_TIMEOUT_SECONDS:
            last_error = "SMTP total time budget exceeded"
            log.debug("SMTP budget exceeded | %s | elapsed=%.2fs", email, time.perf_counter() - smtp_started)
            break

        for attempt in range(1, SMTP_RETRIES + 2):  # +1 for the initial try
            should_retry = False
            try:
                check = _smtp_check(email, mx)
                status = _map_status(check.code, check.message)
                code = check.code
                message = check.message
                reason = _REASON_MAP.get(status, f"SMTP code {code}")
                smtp_resp = f"{code} {message}"
                log.debug("SMTP %s | %s | MX=%s | %s",
                          status, email, mx, smtp_resp)
                result = _result(email, status, reason, smtp_resp)
                result.update(
                    {
                        "smtp_code": code,
                        "smtp_message": message,
                        "smtp_code_explanation": _STATUS_CODE_DETAILS.get(code, "Unmapped SMTP code"),
                        "smtp_features": {
                            "smtputf8": check.supports_smtputf8,
                            "pipelining": check.supports_pipelining,
                            "8bitmime": check.supports_8bitmime,
                            "auth": check.supports_auth,
                            "size": check.supports_size,
                            "starttls": check.supports_starttls,
                        },
                        "server_banner": check.server_banner,
                        "tls_version": check.tls_version,
                        "tls_cipher": check.tls_cipher,
                        "smtp_latency_ms": check.smtp_latency_ms,
                        "connection_latency_ms": check.connection_ms,
                        "smtp_transcript": check.transcript,
                        "greylist_detected": check.greylist_detected,
                        "throttled_detected": check.throttled_detected,
                        "mx_host_used": mx,
                        "attempt_count": attempt,
                    }
                )
                _smtp_cache.set(cache_key, result, ttl_seconds=SMTP_CACHE_TTL_SECONDS)
                return result

            except smtplib.SMTPConnectError as exc:
                last_error = f"Connection error to {mx}: {exc}"
                log.debug("SMTP connect error (attempt %d) | %s | %s",
                          attempt, email, last_error)
                should_retry = attempt <= SMTP_RETRIES

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

            if time.perf_counter() - smtp_started > SMTP_TOTAL_TIMEOUT_SECONDS:
                last_error = "SMTP total time budget exceeded"
                break

            if should_retry:
                backoff = min(SMTP_MAX_BACKOFF_SECONDS, SMTP_RETRY_DELAY * (2 ** (attempt - 1)))
                time.sleep(backoff)

    log.debug("SMTP exhausted all hosts | %s | %s", email, last_error)
    result = _result(email, STATUS_TEMPORARY_FAILURE, last_error, "N/A")
    result.update(
        {
            "smtp_code": 0,
            "smtp_message": last_error,
            "smtp_code_explanation": "No successful SMTP handshake",
            "smtp_features": {},
            "server_banner": "",
            "tls_version": "",
            "tls_cipher": "",
            "smtp_latency_ms": 0.0,
            "connection_latency_ms": 0.0,
            "smtp_transcript": "",
            "greylist_detected": False,
            "throttled_detected": False,
            "mx_host_used": "",
            "attempt_count": SMTP_RETRIES + 1,
        }
    )
    _smtp_cache.set(cache_key, result, ttl_seconds=120)
    return result


# ── Private helpers ───────────────────────────────────────────────────────────

def _smtp_check(email: str, mx_host: str) -> SMTPCheckResult:
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
    start = time.perf_counter()
    transcript: list[str] = []
    tls_version = ""
    tls_cipher = ""
    server_banner = ""

    with smtplib.SMTP(timeout=SMTP_TIMEOUT) as smtp:
        conn_start = time.perf_counter()
        code, banner = smtp.connect(mx_host, 25)
        connection_ms = (time.perf_counter() - conn_start) * 1000
        server_banner = banner.decode(errors="replace") if isinstance(banner, bytes) else str(banner)
        transcript.append(f"CONNECT {mx_host}:25 -> {code} {server_banner}")

        smtp.sock.settimeout(SMTP_COMMAND_TIMEOUT)
        ehlo_code, ehlo_msg = smtp.ehlo()
        if ehlo_code >= 400:
            helo_code, helo_msg = smtp.helo()
            transcript.append(f"HELO -> {helo_code} {helo_msg}")
        else:
            transcript.append(f"EHLO -> {ehlo_code} {ehlo_msg}")

        features = {k.lower(): v for k, v in getattr(smtp, "esmtp_features", {}).items()}
        supports_starttls = "starttls" in features

        if supports_starttls:
            try:
                tls_code, tls_msg = smtp.starttls()
                transcript.append(f"STARTTLS -> {tls_code} {tls_msg}")
                smtp.ehlo()
                if smtp.sock is not None:
                    tls_version = getattr(smtp.sock, "version", lambda: "")() or ""
                    cipher = getattr(smtp.sock, "cipher", lambda: None)()
                    if cipher:
                        tls_cipher = str(cipher[0])
            except smtplib.SMTPException as exc:
                transcript.append(f"STARTTLS_FAILED -> {exc}")

        mail_code, mail_msg = smtp.mail(SMTP_SENDER)
        transcript.append(f"MAIL FROM -> {mail_code} {mail_msg}")
        rcpt_code, rcpt_msg = smtp.rcpt(email)
        transcript.append(f"RCPT TO -> {rcpt_code} {rcpt_msg}")

        smtp.quit()

    message = rcpt_msg.decode(errors="replace") if isinstance(rcpt_msg, bytes) else str(rcpt_msg)
    features = {k.lower(): v for k, v in features.items()}
    lower_msg = message.lower()

    return SMTPCheckResult(
        code=rcpt_code,
        message=message,
        server_banner=server_banner,
        tls_version=tls_version,
        tls_cipher=tls_cipher,
        supports_smtputf8="smtputf8" in features,
        supports_pipelining="pipelining" in features,
        supports_8bitmime="8bitmime" in features,
        supports_auth="auth" in features,
        supports_size="size" in features,
        supports_starttls=supports_starttls,
        smtp_latency_ms=(time.perf_counter() - start) * 1000,
        connection_ms=connection_ms,
        transcript="\n".join(transcript),
        greylist_detected=rcpt_code in {421, 450, 451, 452} and "grey" in lower_msg,
        throttled_detected=(
            rcpt_code in {421, 450, 451, 452}
            and any(token in lower_msg for token in ("rate", "throttle", "slow", "try again"))
        ),
    )


def _map_status(code: int, message: str) -> str:
    if code in _CODE_MAP:
        return _CODE_MAP[code]
    if 200 <= code < 300:
        return STATUS_VALID
    if 500 <= code < 600:
        return STATUS_INVALID_MAILBOX
    if 400 <= code < 500:
        return STATUS_TEMPORARY_FAILURE
    return STATUS_UNKNOWN


def _result(email: str, status: str, reason: str, smtp_response: str) -> dict:
    """Build the standard result dict for this module."""
    return {
        "email": email,
        "status": status,
        "reason": reason,
        "reason_code": reason,
        "smtp_response": smtp_response,
    }


def get_cache_stats() -> dict[str, float | int]:
    return _smtp_cache.stats()
