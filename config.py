"""
config.py
---------
Central configuration for the Email Validation System.
All tunable parameters are defined here to keep other modules clean.
"""

# ── SMTP Settings ─────────────────────────────────────────────────────────────

# Sender address used during SMTP handshake (never actually sends mail)
SMTP_SENDER = "verify@yourdomain.com"

# Seconds to wait for an SMTP connection before timing out
SMTP_TIMEOUT = 10

# How many times to retry a failed SMTP connection
SMTP_RETRIES = 2

# Seconds to wait between retries (avoids hammering the server)
SMTP_RETRY_DELAY = 2

# ── DNS Settings ──────────────────────────────────────────────────────────────

# Seconds to wait for a DNS response
DNS_TIMEOUT = 5

# ── Threading / Performance ───────────────────────────────────────────────────

# Number of parallel worker threads for bulk validation
MAX_WORKERS = 10

# ── Catch-All Detection ───────────────────────────────────────────────────────

# Random local part used to probe for catch-all behaviour
CATCH_ALL_PROBE = "zz_catchall_probe_xyz123"

# ── File Paths ────────────────────────────────────────────────────────────────

# Folder where log files are written
LOG_DIR = "logs"

# Folder where output Excel / reports are written
OUTPUT_DIR = "output"

# Name of the validated results workbook
OUTPUT_EXCEL = "output/validation_results.xlsx"

# Name of the workbook containing only failed emails
FAILED_EXCEL = "output/failed_emails.xlsx"

# Plain-text summary report
SUMMARY_REPORT = "output/summary_report.txt"

# ── Validation Status Labels ──────────────────────────────────────────────────
# These are the canonical strings written to the "Mail Status" column.

STATUS_VALID             = "VALID"
STATUS_INVALID_FORMAT    = "INVALID_FORMAT"
STATUS_INVALID_DOMAIN    = "INVALID_DOMAIN"
STATUS_INVALID_MAILBOX   = "INVALID_MAILBOX"
STATUS_ACCESS_DENIED     = "ACCESS_DENIED"
STATUS_CATCH_ALL         = "CATCH_ALL"
STATUS_TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
STATUS_UNKNOWN           = "UNKNOWN"
