"""Stage 2 - Domain and DNS intelligence checks with caching."""

from __future__ import annotations

import time

import dns.exception
import dns.resolver

from cache_utils import HybridTTLCache, SQLiteTTLCache
from config import (
        CACHE_DB_PATH,
        CACHE_MEMORY_MAXSIZE,
        CACHE_TTL_SECONDS,
        DKIM_SELECTORS,
        DNS_CACHE_TTL_SECONDS,
        DNS_MAX_BACKOFF_SECONDS,
        DNS_NAMESERVERS,
        DNS_RETRIES,
        DNS_RETRY_DELAY,
        DNS_TIMEOUT,
        MX_CACHE_TTL_SECONDS,
        STATUS_INVALID_DOMAIN,
        STATUS_VALID,
)
from logger import get_logger

log = get_logger(__name__)

_resolver = dns.resolver.Resolver(configure=True)
_resolver.timeout = DNS_TIMEOUT
_resolver.lifetime = DNS_TIMEOUT
if DNS_NAMESERVERS:
    _resolver.nameservers = DNS_NAMESERVERS

_sqlite_cache = SQLiteTTLCache(CACHE_DB_PATH)
_dns_cache = HybridTTLCache(
    namespace="dns",
    sqlite_cache=_sqlite_cache,
    memory_maxsize=CACHE_MEMORY_MAXSIZE,
    default_ttl_seconds=DNS_CACHE_TTL_SECONDS,
)
_mx_cache = HybridTTLCache(
    namespace="mx",
    sqlite_cache=_sqlite_cache,
    memory_maxsize=CACHE_MEMORY_MAXSIZE,
    default_ttl_seconds=MX_CACHE_TTL_SECONDS,
)


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
    return email.split("@", 1)[1].strip().lower()


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
    start = time.perf_counter()
    domain = get_domain(email)

    mx_hosts = _get_mx_hosts(domain)
    exists = _domain_exists_fast(domain, mx_hosts)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    if not exists:
        reason = f"Domain '{domain}' does not exist"
        log.debug("DNS FAIL | %s | %s", email, reason)
        domain_report = {
            "exists": False,
            "mx_hosts": [],
            "mx_count": 0,
            "latency_ms": latency_ms,
            "fallback_behavior": "FAST_EXISTENCE_CHECK",
        }
        return _result(email, STATUS_INVALID_DOMAIN, reason, [], domain_report, latency_ms)

    if not mx_hosts:
        reason = f"Domain '{domain}' has no MX records"
        log.debug("DNS FAIL | %s | %s", email, reason)
        domain_report = {
            "exists": True,
            "mx_hosts": [],
            "mx_count": 0,
            "latency_ms": latency_ms,
            "fallback_behavior": "FAST_EXISTENCE_CHECK",
        }
        return _result(email, STATUS_INVALID_DOMAIN, reason, [], domain_report, latency_ms)

    domain_report = _get_domain_report(domain)
    domain_report["mx_hosts"] = mx_hosts
    domain_report["mx_count"] = len(mx_hosts)
    domain_report["latency_ms"] = latency_ms
    log.debug("DNS OK | %s | MX=%s | latency_ms=%.2f", email, mx_hosts, latency_ms)
    return _result(email, STATUS_VALID, "Domain and MX records exist", mx_hosts, domain_report, latency_ms)


# ── Private helpers ───────────────────────────────────────────────────────────

def _query_records(name: str, record_type: str) -> tuple[list[str], float]:
    cache_key = f"{name}:{record_type}"
    cached = _dns_cache.get(cache_key)
    if cached is not None:
        return cached.get("answers", []), float(cached.get("ttl", CACHE_TTL_SECONDS))

    errors: list[str] = []
    for attempt in range(DNS_RETRIES + 1):
        try:
            answers = _resolver.resolve(name, record_type)
            ttl = float(getattr(getattr(answers, "rrset", None), "ttl", CACHE_TTL_SECONDS))
            result = [str(r).rstrip(".") for r in answers]
            _dns_cache.set(cache_key, {"answers": result, "ttl": ttl}, ttl_seconds=max(60, int(ttl)))
            return result, ttl
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers) as exc:
            errors.append(str(exc))
            break
        except dns.exception.Timeout as exc:
            errors.append(str(exc))
            if attempt < DNS_RETRIES:
                backoff = min(DNS_MAX_BACKOFF_SECONDS, DNS_RETRY_DELAY * (2 ** attempt))
                time.sleep(backoff)

    log.debug("DNS query failed | %s %s | errors=%s", name, record_type, errors)
    _dns_cache.set(cache_key, {"answers": [], "ttl": 120}, ttl_seconds=120)
    return [], 120.0


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
    cached = _mx_cache.get(domain)
    if cached is not None:
        return list(cached)

    try:
        answers = _resolver.resolve(domain, "MX")
        sorted_mx = sorted(answers, key=lambda r: r.preference)
        mx_hosts = [str(r.exchange).rstrip(".") for r in sorted_mx]
        ttl = int(getattr(getattr(answers, "rrset", None), "ttl", MX_CACHE_TTL_SECONDS))
        _mx_cache.set(domain, mx_hosts, ttl_seconds=max(60, ttl))
        return mx_hosts
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout, dns.resolver.NoNameservers):
        _mx_cache.set(domain, [], ttl_seconds=120)
        return []


def _domain_exists_fast(domain: str, mx_hosts: list[str]) -> bool:
    if mx_hosts:
        return True

    # Keep this intentionally minimal for fail-fast behavior on resolver issues.
    answers, _ = _query_records(domain, "A")
    if answers:
        return True

    return False


def _get_domain_report(domain: str) -> dict:
    a_records, a_ttl = _query_records(domain, "A")
    aaaa_records, aaaa_ttl = _query_records(domain, "AAAA")
    txt_records, txt_ttl = _query_records(domain, "TXT")
    ns_records, ns_ttl = _query_records(domain, "NS")
    soa_records, soa_ttl = _query_records(domain, "SOA")
    caa_records, caa_ttl = _query_records(domain, "CAA")
    dmarc_records, _ = _query_records(f"_dmarc.{domain}", "TXT")

    spf_records = [txt for txt in txt_records if txt.lower().startswith("v=spf1")]
    dkim_hits: dict[str, bool] = {}
    for selector in DKIM_SELECTORS:
        dkim_name = f"{selector}._domainkey.{domain}"
        dkim_txt, _ = _query_records(dkim_name, "TXT")
        dkim_hits[selector] = any("v=dkim1" in item.lower() for item in dkim_txt)

    mx_hosts = _get_mx_hosts(domain)
    mx_consistent = len(mx_hosts) == len(set(mx_hosts))
    tld = domain.rsplit(".", 1)[-1].lower() if "." in domain else ""
    domain_type = _infer_domain_type(domain, tld)

    return {
        "exists": bool(a_records or aaaa_records or ns_records or soa_records or mx_hosts),
        "a_records": a_records,
        "aaaa_records": aaaa_records,
        "txt_records": txt_records,
        "spf_records": spf_records,
        "dmarc_records": dmarc_records,
        "dkim_selectors": dkim_hits,
        "ns_records": ns_records,
        "soa_records": soa_records,
        "caa_records": caa_records,
        "dnssec_detected": bool(_query_records(domain, "DNSKEY")[0]),
        "mx_consistent": mx_consistent,
        "mx_broken": False,
        "mx_loop_detected": any(mx_host.lower() == domain.lower() for mx_host in mx_hosts),
        "missing_glue_best_effort": False,
        "ttl_seconds": {
            "A": a_ttl,
            "AAAA": aaaa_ttl,
            "TXT": txt_ttl,
            "NS": ns_ttl,
            "SOA": soa_ttl,
        },
        "domain_type": domain_type,
        "parked_indicator": any("parking" in txt.lower() for txt in txt_records),
        "under_construction_indicator": any("under construction" in txt.lower() for txt in txt_records),
        "suspended_indicator": any("suspend" in txt.lower() for txt in txt_records),
        "expired_indicator": False,
        "mail_host_exists": bool(mx_hosts),
        "fallback_behavior": "A/AAAA_AND_NS_CHECK",
    }


def _infer_domain_type(domain: str, tld: str) -> str:
    if tld == "edu":
        return "EDUCATIONAL"
    if tld in {"gov", "gouv", "govt"}:
        return "GOVERNMENT"
    if tld in {"mil"}:
        return "MILITARY"
    if tld in {"org", "ngo"}:
        return "NGO_OR_NONPROFIT"
    if domain.endswith(".cloud"):
        return "CLOUD_PROVIDER_INFERRED"
    return "CORPORATE_OR_PERSONAL"


def _result(
    email: str,
    status: str,
    reason: str,
    mx_hosts: list[str],
    dns_report: dict,
    dns_latency_ms: float,
) -> dict:
    """Build the standard result dict for this module."""
    return {
        "email": email,
        "status": status,
        "reason": reason,
        "reason_code": reason,
        "smtp_response": "",
        "mx_hosts": mx_hosts,
        "dns_report": dns_report,
        "dns_latency_ms": dns_latency_ms,
    }


def get_cache_stats() -> dict[str, float | int]:
    return {
        "dns": _dns_cache.stats(),
        "mx": _mx_cache.stats(),
    }
