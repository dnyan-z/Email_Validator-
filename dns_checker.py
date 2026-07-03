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
    Verify that the email's domain exists and provide SMTP targets.

    RFC 5321 behavior:
      - If MX records exist: use them in priority order.
      - If no MX records exist (or MX is absent): fall back to A/AAAA for the domain.
      - Never mark INVALID_DOMAIN if the domain exists via A/AAAA.

    Returns dict keys:
      email, status, reason, smtp_response, mx_hosts, dns_report, dns_latency_ms
    """
    start = time.perf_counter()
    domain = get_domain(email)

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    mx_hosts, mx_report = _get_mx_hosts(domain, build_detailed_report=True)

    # MX exists (after validation/sanitization)
    if mx_hosts:
        domain_report = _get_domain_report(domain)
        domain_report["mx_hosts"] = mx_hosts
        domain_report["mx_count"] = len(mx_hosts)
        domain_report["mx_raw_report"] = mx_report
        domain_report["latency_ms"] = latency_ms
        log.debug("DNS OK (MX) | %s | MX=%s | latency_ms=%.2f", email, mx_hosts, latency_ms)
        return _result(email, STATUS_VALID, "Domain and MX records exist", mx_hosts, domain_report, latency_ms)

    # No (usable) MX hosts: fall back to A/AAAA existence.
    has_a = _domain_has_record(domain, "A")
    has_aaaa = _domain_has_record(domain, "AAAA")
    if has_a or has_aaaa:
        smtp_fallback_hosts = [domain]
        domain_report = _get_domain_report(domain)
        domain_report["mx_hosts"] = []
        domain_report["mx_count"] = 0
        domain_report["mx_raw_report"] = mx_report
        domain_report["fallback_behavior"] = "A/AAAA_FALLBACK_SMTP_HOST=DOMAIN"
        domain_report["latency_ms"] = latency_ms
        reason = f"Domain '{domain}' has no usable MX; using A/AAAA fallback for SMTP"
        log.debug("DNS OK (A/AAAA fallback) | %s | %s", email, reason)
        return _result(email, STATUS_VALID, reason, smtp_fallback_hosts, domain_report, latency_ms)

    reason = f"Domain '{domain}' does not exist (no MX and no A/AAAA)"
    domain_report = _get_domain_report(domain)
    domain_report["fallback_behavior"] = "FAST_FAIL_NO_MX_NO_AAAA"
    domain_report["latency_ms"] = latency_ms
    log.debug("DNS FAIL | %s | %s", email, reason)
    return _result(email, STATUS_INVALID_DOMAIN, reason, [], domain_report, latency_ms)


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


def _get_mx_hosts(domain: str, build_detailed_report: bool = False) -> tuple[list[str], dict]:
    """
    Return usable MX hostnames in priority order.

    - RFC 5321: MX exchange must be a domain name, not empty/malformed.
    - Skip malformed or empty MX exchange names.
    - Cache usable MX results.

    Returns: (mx_hosts, report)
    """
    cached = _mx_cache.get(domain)
    if cached is not None:
        mx_hosts = list(cached)
        return mx_hosts, {"cached": True, "invalid_exchanges_skipped": 0}

    report: dict = {"cached": False, "invalid_exchanges_skipped": 0}

    try:
        answers = _resolver.resolve(domain, "MX")
        sorted_mx = sorted(answers, key=lambda r: r.preference)

        usable: list[str] = []
        invalid_count = 0
        for r in sorted_mx:
            exchange = str(r.exchange).rstrip(".").strip()
            if not exchange:
                invalid_count += 1
                continue
            # Basic hostname/domain sanity
            if " " in exchange or exchange.startswith(".") or exchange.endswith("."):
                invalid_count += 1
                continue
            usable.append(exchange)

        report["invalid_exchanges_skipped"] = invalid_count
        ttl = int(getattr(getattr(answers, "rrset", None), "ttl", MX_CACHE_TTL_SECONDS))
        _mx_cache.set(domain, usable, ttl_seconds=max(60, ttl))
        if build_detailed_report:
            report["mx_priority_pairs"] = [
                {"preference": r.preference, "exchange": str(r.exchange).rstrip(".").strip()}
                for r in sorted_mx
            ]
        return usable, report

    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout, dns.resolver.NoNameservers):
        _mx_cache.set(domain, [], ttl_seconds=120)
        return [], report


def _domain_has_record(domain: str, record_type: str) -> bool:
    answers, _ = _query_records(domain, record_type)
    return bool(answers)


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

    mx_hosts, _ = _get_mx_hosts(domain)
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
