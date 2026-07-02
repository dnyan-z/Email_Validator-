"""
dns_checker.py
--------------
Stage 2 – Domain / MX Record Validation.

Responsibilities:
  • Extract the domain part from the email address
  • Verify the domain resolves (A or AAAA record exists)
  • Look up MX records to confirm the domain accepts mail
  • Return the list of MX hostnames (used later by the SMTP validator)

Uses dnspython for all DNS queries.
"""

import dns.exception
import dns.resolver

from config import DNS_TIMEOUT, STATUS_INVALID_DOMAIN, STATUS_VALID
from logger import get_logger

log = get_logger(__name__)

# Configure a single resolver instance with our timeout
_resolver = dns.resolver.Resolver()
_resolver.lifetime = DNS_TIMEOUT


def get_domain(email: str) -> str:
    """
    Extract the domain portion from a normalised email address.

    Parameters
    ----------
    email : str
        A normalised email, e.g. "user@example.com".

    Returns
    -------
    str
        The domain part, e.g. "example.com".
    """
    return email.split("@", 1)[1]


def check_domain(email: str) -> dict:
    """
    Verify that the email's domain exists and has MX records.

    Parameters
    ----------
    email : str
        Normalised email address.

    Returns
    -------
    dict with keys:
        email         – passed through unchanged
        status        – STATUS_VALID | STATUS_INVALID_DOMAIN
        reason        – human-readable explanation
        smtp_response – empty at this stage
        mx_hosts      – list of MX hostnames sorted by priority (empty on fail)
    """
    domain = get_domain(email)

    # ── Step 1: Does the domain resolve at all? ───────────────────────────────
    if not _domain_exists(domain):
        reason = f"Domain '{domain}' does not exist (no DNS record)"
        log.debug("DNS FAIL (no A/AAAA) | %s | %s", email, reason)
        return _result(email, STATUS_INVALID_DOMAIN, reason, [])

    # ── Step 2: Does it have MX records? ─────────────────────────────────────
    mx_hosts = _get_mx_hosts(domain)
    if not mx_hosts:
        reason = f"Domain '{domain}' has no MX records"
        log.debug("DNS FAIL (no MX) | %s | %s", email, reason)
        return _result(email, STATUS_INVALID_DOMAIN, reason, [])

    log.debug("DNS OK | %s | MX: %s", email, mx_hosts)
    return _result(email, STATUS_VALID, "Domain and MX records exist", mx_hosts)


# ── Private helpers ───────────────────────────────────────────────────────────

def _domain_exists(domain: str) -> bool:
    """
    Return True if the domain has at least one A or AAAA record.

    Parameters
    ----------
    domain : str
        The domain to query.
    """
    for record_type in ("A", "AAAA"):
        try:
            _resolver.resolve(domain, record_type)
            return True
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer,
                dns.exception.Timeout, dns.resolver.NoNameservers):
            continue
    return False


def _get_mx_hosts(domain: str) -> list[str]:
    """
    Return a list of MX hostnames sorted by preference (lowest = highest prio).

    Parameters
    ----------
    domain : str
        The domain to query for MX records.

    Returns
    -------
    list[str]
        Sorted MX hostnames, or an empty list if none are found.
    """
    try:
        answers = _resolver.resolve(domain, "MX")
        # Sort by preference value (lower = higher priority)
        sorted_mx = sorted(answers, key=lambda r: r.preference)
        return [str(r.exchange).rstrip(".") for r in sorted_mx]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer,
            dns.exception.Timeout, dns.resolver.NoNameservers):
        return []


def _result(email: str, status: str, reason: str, mx_hosts: list) -> dict:
    """Build the standard result dict for this module."""
    return {
        "email": email,
        "status": status,
        "reason": reason,
        "smtp_response": "",
        "mx_hosts": mx_hosts,
    }
