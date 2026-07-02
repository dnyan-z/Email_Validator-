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

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from catch_all_detector import is_catch_all, mark_catch_all
from config import (
    MAX_WORKERS,
    STATUS_INVALID_FORMAT,
    STATUS_INVALID_DOMAIN,
    STATUS_VALID,
    SUMMARY_REPORT,
    OUTPUT_DIR,
)
from dns_checker import check_domain, get_domain
from excel_handler import read_emails, write_failed_emails, write_results
from logger import get_logger
from smtp_validator import verify_mailbox
from syntax_validator import validate_syntax

import os

log = get_logger(__name__)

# Cache catch-all results per domain to avoid redundant probes
_catch_all_cache: dict[str, bool] = {}


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
    # ── Stage 1: Syntax ───────────────────────────────────────────────────────
    result = validate_syntax(raw_email)
    if result["status"] == STATUS_INVALID_FORMAT:
        log.info("INVALID FORMAT | %s", result["email"])
        return result

    email = result["email"]

    # ── Stage 2: DNS / MX ─────────────────────────────────────────────────────
    dns_result = check_domain(email)
    if dns_result["status"] == STATUS_INVALID_DOMAIN:
        log.info("INVALID DOMAIN | %s | %s", email, dns_result["reason"])
        return dns_result

    mx_hosts = dns_result.get("mx_hosts", [])

    # ── Stage 3: SMTP mailbox verification ────────────────────────────────────
    smtp_result = verify_mailbox(email, mx_hosts)

    # ── Stage 4: Catch-all detection (only if SMTP returned VALID) ────────────
    if smtp_result["status"] == STATUS_VALID:
        domain = get_domain(email)

        # Use cached result if we've already probed this domain
        if domain not in _catch_all_cache:
            _catch_all_cache[domain] = is_catch_all(domain, mx_hosts)

        if _catch_all_cache[domain]:
            smtp_result = mark_catch_all(smtp_result)
            log.info("CATCH-ALL | %s", email)
        else:
            log.info("VALID | %s", email)
    else:
        log.info("%s | %s | %s",
                 smtp_result["status"], email, smtp_result["reason"])

    return smtp_result


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
    results: list[dict | None] = [None] * len(emails)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_idx = {
            executor.submit(validate_single, email): idx
            for idx, email in enumerate(emails)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                log.error("Unexpected error for '%s': %s", emails[idx], exc)
                results[idx] = {
                    "email": emails[idx],
                    "status": "UNKNOWN",
                    "reason": f"Unexpected error: {exc}",
                    "smtp_response": "",
                }

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

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    total = len(results)
    lines = [
        "=" * 50,
        "  EMAIL VALIDATION SUMMARY REPORT",
        "=" * 50,
        f"  Total Emails   : {total}",
        "-" * 50,
    ]
    for status, count in sorted(counts.items()):
        pct = count / total * 100 if total else 0
        lines.append(f"  {status:<22}: {count:>5}  ({pct:.1f}%)")
    lines += ["=" * 50, ""]

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
    log.info("Validating %d emails with %d workers…", len(emails), MAX_WORKERS)
    results = validate_bulk(emails)

    elapsed = time.perf_counter() - start
    log.info("Validation complete in %.1f seconds", elapsed)

    # Step 3 – write outputs
    write_results(results)
    write_failed_emails(results)
    write_summary(results)

    log.info("All done. Check the 'output/' folder for results.")


if __name__ == "__main__":
    main()
