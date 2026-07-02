"""
main.py
-------
Entry point for the Email Validation System.

Usage:
    python main.py <path_to_input.xlsx>

Orchestration flow per email:
  1. Syntax validation   (syntax_validator)
  2. DNS / MX check      (dns_checker)
  3. SMTP verification   (smtp_validator)
  4. Catch-all detection (catch_all_detector)

All emails are processed in parallel using a ThreadPoolExecutor.
Results are saved to:
  • output/validation_results.xlsx
  • output/failed_emails.xlsx
  • output/summary_report.txt
"""

from __future__ import annotations

import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from catch_all_detector import detect_catch_all, mark_catch_all
from config import (
    CATCH_ALL_SKIP_IF_SMTP_LATENCY_MS_EXCEEDS,
    MAX_WORKERS,
    OUTPUT_DIR,
    STATUS_INVALID_FORMAT,
    STATUS_INVALID_DOMAIN,
    STATUS_VALID,
    SUMMARY_REPORT,
)
from dns_checker import check_domain, get_cache_stats as get_dns_cache_stats, get_domain
from excel_handler import read_emails, write_failed_emails, write_results
from logger import get_logger
from scoring import compute_scores
from smtp_validator import get_cache_stats as get_smtp_cache_stats, verify_mailbox
from syntax_validator import set_bulk_context, validate_syntax


try:
    from catch_all_detector import get_cache_stats as get_catch_all_cache_stats
except ImportError:  # pragma: no cover
    def get_catch_all_cache_stats() -> dict:
        return {}

log = get_logger(__name__)


def validate_single(raw_email: str) -> dict:
    """
    Run all validation stages for a single email address.

    Parameters
    ----------
    raw_email : str
        Raw email string from the Excel cell.

    Returns
    -------
    dict
        Final result with keys: email, status, reason, smtp_response.
    """
    started = time.perf_counter()
    stage_times: dict[str, float] = {}

    stage_start = time.perf_counter()
    result = validate_syntax(raw_email)
    stage_times["syntax_ms"] = round((time.perf_counter() - stage_start) * 1000, 2)

    if result["status"] == STATUS_INVALID_FORMAT:
        log.info("INVALID FORMAT | %s", result["email"])
        result["validation_duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        result.update(stage_times)
        _attach_scores(result)
        return result

    email = result["email"]

    stage_start = time.perf_counter()
    dns_result = check_domain(email)
    stage_times["dns_stage_ms"] = round((time.perf_counter() - stage_start) * 1000, 2)

    result.update(dns_result)
    if dns_result["status"] == STATUS_INVALID_DOMAIN:
        log.info("INVALID DOMAIN | %s | %s", email, dns_result["reason"])
        result["validation_duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        result.update(stage_times)
        _attach_scores(result)
        return result

    mx_hosts = dns_result.get("mx_hosts", [])

    stage_start = time.perf_counter()
    smtp_result = verify_mailbox(email, mx_hosts)
    stage_times["smtp_stage_ms"] = round((time.perf_counter() - stage_start) * 1000, 2)
    result.update(smtp_result)

    result.setdefault("catch_all_result", "UNKNOWN")
    result.setdefault("catch_all_confidence", 0)

    # ── Stage 4: Catch-all detection (only if SMTP returned VALID) ────────────
    if smtp_result["status"] == STATUS_VALID:
        domain = get_domain(email)
        smtp_latency_ms = float(result.get("smtp_latency_ms", 0.0) or 0.0)
        if smtp_latency_ms > CATCH_ALL_SKIP_IF_SMTP_LATENCY_MS_EXCEEDS:
            result["catch_all_result"] = "UNKNOWN"
            result["catch_all_confidence"] = 35
            result["catch_all_probe_count"] = 0
            result["catch_all_details"] = []
            result["reason"] = (
                f"{result.get('reason', '')} | Catch-all skipped due to SMTP latency {smtp_latency_ms:.1f}ms"
            ).strip(" |")
            log.info("CATCH-ALL SKIPPED (slow SMTP) | %s | %.1fms", email, smtp_latency_ms)
        else:
            stage_start = time.perf_counter()
            catch_all = detect_catch_all(domain, mx_hosts)
            stage_times["catch_all_stage_ms"] = round((time.perf_counter() - stage_start) * 1000, 2)
            result["catch_all_result"] = catch_all.classification
            result["catch_all_confidence"] = catch_all.confidence
            result["catch_all_probe_count"] = catch_all.probes_used
            result["catch_all_details"] = catch_all.details

            if catch_all.classification in {"DEFINITE_CATCH_ALL", "LIKELY_CATCH_ALL", "PARTIAL_CATCH_ALL"}:
                result = mark_catch_all(result)
                log.info("CATCH-ALL | %s", email)
            else:
                log.info("VALID | %s", email)
    else:
        log.info("%s | %s | %s",
                 result["status"], email, result["reason"])

    result["validation_duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result.update(stage_times)
    _attach_scores(result)
    return result


def validate_bulk(emails: list[str]) -> list[dict]:
    """
    Validate a list of emails using a thread pool for concurrency.

    Parameters
    ----------
    emails : list[str]
        Raw email addresses to validate.

    Returns
    -------
    list[dict]
        Results in the same order as the input list.
    """
    set_bulk_context(emails)
    results: list[dict | None] = [None] * len(emails)

    # Domain-aware ordering reduces repeated MX/catch-all work and improves cache reuse.
    domain_buckets: dict[str, list[tuple[int, str]]] = defaultdict(list)
    ordered_fallback: list[tuple[int, str]] = []
    for idx, email in enumerate(emails):
        try:
            domain = email.split("@", 1)[1].strip().lower()
            domain_buckets[domain].append((idx, email))
        except Exception:
            ordered_fallback.append((idx, email))

    ordered_items: list[tuple[int, str]] = []
    for _, items in sorted(domain_buckets.items(), key=lambda i: len(i[1]), reverse=True):
        ordered_items.extend(items)
    ordered_items.extend(ordered_fallback)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_idx = {
            executor.submit(validate_single, email): idx
            for idx, email in ordered_items[:]
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                log.exception("Unexpected error for '%s': %s", emails[idx], exc)
                result = {
                    "email": emails[idx],
                    "status": "UNKNOWN",
                    "reason": f"Unexpected error: {exc}",
                    "reason_code": "UNEXPECTED_ERROR",
                    "smtp_response": "",
                    "validation_duration_ms": 0.0,
                }
                _attach_scores(result)
                results[idx] = result

    return results  # type: ignore[return-value]


def write_summary(results: list[dict]) -> None:
    """
    Write a plain-text summary report to output/summary_report.txt.

    Parameters
    ----------
    results : list[dict]
        Full list of validation results.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    counts = Counter(r.get("status", "UNKNOWN") for r in results)
    providers = Counter(r.get("provider_classification", "UNKNOWN") for r in results)
    durations = [float(r.get("validation_duration_ms", 0.0)) for r in results]
    dns_failures = sum(1 for r in results if r.get("status") == "INVALID_DOMAIN")
    smtp_failures = sum(1 for r in results if r.get("status") in {"INVALID_MAILBOX", "TEMPORARY_FAILURE", "ACCESS_DENIED"})

    total = len(results)
    avg_ms = sum(durations) / len(durations) if durations else 0.0
    fastest = min(durations) if durations else 0.0
    slowest = max(durations) if durations else 0.0

    dns_cache_stats = get_dns_cache_stats()
    smtp_cache_stats = get_smtp_cache_stats()
    catch_cache_stats = get_catch_all_cache_stats()

    lines = [
        "=" * 70,
        "  EMAIL VALIDATION SUMMARY REPORT",
        "=" * 70,
        f"  Total emails               : {total}",
        f"  Valid                      : {counts.get('VALID', 0)}",
        f"  Invalid                    : {counts.get('INVALID_FORMAT', 0) + counts.get('INVALID_DOMAIN', 0) + counts.get('INVALID_MAILBOX', 0)}",
        f"  Disposable                 : {sum(1 for r in results if r.get('is_disposable'))}",
        f"  Role accounts              : {sum(1 for r in results if r.get('is_role_account'))}",
        f"  Catch-all                  : {counts.get('CATCH_ALL', 0)}",
        f"  Temporary failures         : {counts.get('TEMPORARY_FAILURE', 0)}",
        f"  Unknown                    : {counts.get('UNKNOWN', 0)}",
        f"  Average validation time ms : {avg_ms:.2f}",
        f"  Fastest validation ms      : {fastest:.2f}",
        f"  Slowest validation ms      : {slowest:.2f}",
        f"  DNS failures               : {dns_failures}",
        f"  SMTP failures              : {smtp_failures}",
        "-" * 70,
        "  Status distribution:",
    ]
    for status, count in sorted(counts.items()):
        pct = count / total * 100 if total else 0
        lines.append(f"    {status:<24} {count:>6} ({pct:>5.1f}%)")

    lines += [
        "-" * 70,
        "  Top failing domains:",
    ]
    failing_domains = Counter(
        r.get("domain", "")
        for r in results
        if r.get("status") not in {"VALID", "CATCH_ALL"} and r.get("domain")
    )
    for domain, count in failing_domains.most_common(10):
        lines.append(f"    {domain:<40} {count:>6}")

    lines += [
        "-" * 70,
        "  Top providers:",
    ]
    for provider, count in providers.most_common(10):
        lines.append(f"    {provider:<40} {count:>6}")

    deliverability_distribution = Counter(
        "90-100" if r.get("deliverability_score", 0) >= 90 else
        "75-89" if r.get("deliverability_score", 0) >= 75 else
        "50-74" if r.get("deliverability_score", 0) >= 50 else
        "0-49"
        for r in results
    )
    lines += [
        "-" * 70,
        "  Deliverability distribution:",
    ]
    for bucket in ["90-100", "75-89", "50-74", "0-49"]:
        lines.append(f"    {bucket:<40} {deliverability_distribution.get(bucket, 0):>6}")

    lines += [
        "-" * 70,
        "  Cache hit ratios:",
        f"    DNS cache stats      : {dns_cache_stats}",
        f"    SMTP cache stats     : {smtp_cache_stats}",
        f"    Catch-all cache stats: {catch_cache_stats}",
        "=" * 70,
        "",
    ]

    report = "\n".join(lines)
    with open(SUMMARY_REPORT, "w", encoding="utf-8") as fh:
        fh.write(report)

    # Also print to console
    print("\n" + report)
    log.info("Summary report saved → %s", SUMMARY_REPORT)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py <input_excel.xlsx>")
        sys.exit(1)

    input_file = sys.argv[1]

    log.info("=" * 60)
    log.info("Email Validation System – starting")
    log.info("Input file: %s", input_file)

    start = time.perf_counter()

    # Step 1 – read emails from Excel
    emails = read_emails(input_file)
    if not emails:
        log.warning("No email addresses found in the input file.")
        sys.exit(0)

    # Step 2 – validate all emails
    log.info("Validating %d emails with %d workers...", len(emails), MAX_WORKERS)
    results = validate_bulk(emails)

    elapsed = time.perf_counter() - start
    log.info("Validation complete in %.1f seconds", elapsed)

    # Step 3 – write outputs
    write_results(results)
    write_failed_emails(results)
    write_summary(results)

    log.info("All done. Check the 'output/' folder for results.")


def _attach_scores(result: dict) -> None:
    score = compute_scores(result)
    result["deliverability_score"] = score.deliverability_score
    result["risk_score"] = score.risk_score
    result["confidence_score"] = score.confidence_score
    result["validation_grade"] = score.validation_grade


if __name__ == "__main__":
    main()
