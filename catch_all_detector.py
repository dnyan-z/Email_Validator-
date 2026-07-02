"""
catch_all_detector.py
---------------------
Stage 4 – Catch-All Domain Detection.

A "catch-all" domain accepts mail for ANY local part, including completely
random ones.  If a domain accepts our deliberately nonsensical probe address
(e.g. zz_catchall_probe_xyz123@domain.com) with a 250 response, we know the
domain is catch-all and we cannot trust a 250 for the real address either.

Detection strategy:
  1. Build a probe email using a random-looking local part that can't
     plausibly exist (from config.CATCH_ALL_PROBE).
  2. Run the same SMTP check used in smtp_validator.
  3. If the probe returns 250 → domain is CATCH_ALL.
  4. If the probe is rejected (non-250) → domain is NOT catch-all, so a
     previous 250 for the real address is trustworthy.
"""

from config import CATCH_ALL_PROBE, STATUS_CATCH_ALL
from logger import get_logger
from smtp_validator import verify_mailbox

log = get_logger(__name__)


def is_catch_all(domain: str, mx_hosts: list[str]) -> bool:
    """
    Probe the domain with a nonsense address to detect catch-all behaviour.

    Parameters
    ----------
    domain : str
        The domain to probe, e.g. "example.com".
    mx_hosts : list[str]
        Ordered MX hosts for the domain (from dns_checker).

    Returns
    -------
    bool
        True  → domain accepts all addresses (catch-all).
        False → domain rejects unknown addresses (not catch-all).
    """
    probe_email = f"{CATCH_ALL_PROBE}@{domain}"
    result = verify_mailbox(probe_email, mx_hosts)

    if result["status"] == "VALID":
        log.debug("CATCH-ALL detected | %s | probe accepted", domain)
        return True

    log.debug("NOT catch-all | %s | probe rejected (%s)", domain, result["status"])
    return False


def mark_catch_all(existing_result: dict) -> dict:
    """
    Override a VALID result's status to CATCH_ALL.

    Parameters
    ----------
    existing_result : dict
        A result dict (from smtp_validator or dns_checker) that was
        previously VALID.

    Returns
    -------
    dict
        The same dict with status and reason updated.
    """
    updated = existing_result.copy()
    updated["status"] = STATUS_CATCH_ALL
    updated["reason"] = "Domain accepts all addresses (catch-all)"
    return updated
