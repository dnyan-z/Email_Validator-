"""Stage 4 - Catch-all domain detection with multi-probe classification."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from dataclasses import dataclass

from cache_utils import HybridTTLCache, SQLiteTTLCache
from config import (
    CACHE_DB_PATH,
    CACHE_MEMORY_MAXSIZE,
    CATCH_ALL_CACHE_TTL_SECONDS,
    CATCH_ALL_MAX_PROBE_COUNT,
    CATCH_ALL_PROBE,
    CATCH_ALL_PROBE_COUNT,
    CATCH_ALL_RETRIES,
    STATUS_CATCH_ALL,
)
from logger import get_logger
from smtp_validator import verify_mailbox

log = get_logger(__name__)

_sqlite_cache = SQLiteTTLCache(CACHE_DB_PATH)
_catch_all_cache = HybridTTLCache(
    namespace="catch_all",
    sqlite_cache=_sqlite_cache,
    memory_maxsize=CACHE_MEMORY_MAXSIZE,
    default_ttl_seconds=CATCH_ALL_CACHE_TTL_SECONDS,
)


@dataclass(slots=True)
class CatchAllResult:
    classification: str
    confidence: int
    accepted_probes: int
    rejected_probes: int
    unknown_probes: int
    probes_used: int
    details: list[dict]


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
    result = detect_catch_all(domain, mx_hosts)
    return result.classification in {"DEFINITE_CATCH_ALL", "LIKELY_CATCH_ALL"}


def detect_catch_all(domain: str, mx_hosts: list[str]) -> CatchAllResult:
    cache_key = f"{domain.lower()}|{'|'.join(mx_hosts).lower()}"
    cached = _catch_all_cache.get(cache_key)
    if cached is not None:
        return CatchAllResult(**cached)

    probe_count = max(1, min(CATCH_ALL_PROBE_COUNT, CATCH_ALL_MAX_PROBE_COUNT))
    likely_threshold = max(1, int((0.6 * probe_count) + 0.9999))
    accepted = 0
    rejected = 0
    unknown = 0
    details: list[dict] = []

    for idx in range(probe_count):
        probe = _build_probe_local_part(domain, idx)
        probe_email = f"{probe}@{domain}"
        response = None

        for attempt in range(CATCH_ALL_RETRIES + 1):
            response = verify_mailbox(probe_email, mx_hosts)
            status = response.get("status", "UNKNOWN")
            if status in {"TEMPORARY_FAILURE", "UNKNOWN"} and attempt < CATCH_ALL_RETRIES:
                time.sleep(0.5 * (2 ** attempt))
                continue
            break

        status = (response or {}).get("status", "UNKNOWN")
        if status == "VALID":
            accepted += 1
        elif status in {"INVALID_MAILBOX", "INVALID_DOMAIN", "ACCESS_DENIED"}:
            rejected += 1
        else:
            unknown += 1

        details.append(
            {
                "probe": probe_email,
                "status": status,
                "smtp_code": (response or {}).get("smtp_code", 0),
                "smtp_message": (response or {}).get("smtp_message", ""),
            }
        )

        remaining = probe_count - (idx + 1)

        # If we already have enough acceptances for LIKELY/DEFINITE classification,
        # remaining probes cannot reduce classification below LIKELY.
        if accepted >= likely_threshold:
            break

        # If even all remaining probes accepted cannot reach LIKELY, stop early.
        if accepted + remaining < likely_threshold:
            break

    classification, confidence = _classify(accepted, rejected, unknown, probe_count)
    result = CatchAllResult(
        classification=classification,
        confidence=confidence,
        accepted_probes=accepted,
        rejected_probes=rejected,
        unknown_probes=unknown,
        probes_used=probe_count,
        details=details,
    )
    _catch_all_cache.set(cache_key, asdict(result), ttl_seconds=CATCH_ALL_CACHE_TTL_SECONDS)
    log.debug(
        "CATCH-ALL check | %s | class=%s confidence=%s accepted=%s rejected=%s unknown=%s",
        domain,
        classification,
        confidence,
        accepted,
        rejected,
        unknown,
    )
    return result


def _build_probe_local_part(domain: str, idx: int) -> str:
    digest = hashlib.sha1(f"{domain}:{idx}:{time.time_ns()}".encode("utf-8")).hexdigest()[:10]
    return f"{CATCH_ALL_PROBE}_{idx}_{digest}"


def _classify(accepted: int, rejected: int, unknown: int, total: int) -> tuple[str, int]:
    if total == 0:
        return "UNKNOWN", 0
    acceptance_ratio = accepted / total
    if acceptance_ratio >= 0.9:
        return "DEFINITE_CATCH_ALL", 95
    if acceptance_ratio >= 0.6:
        return "LIKELY_CATCH_ALL", 80
    if accepted > 0 and rejected > 0:
        return "PARTIAL_CATCH_ALL", 65
    if rejected == total:
        return "NOT_CATCH_ALL", 90
    if unknown == total:
        return "UNKNOWN", 40
    return "UNKNOWN", 50


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
    updated["reason_code"] = "CATCH_ALL_DOMAIN"
    return updated


def get_cache_stats() -> dict[str, float | int]:
    return _catch_all_cache.stats()
