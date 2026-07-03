"""Central configuration for the Email Validation System."""

from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# -- SMTP Settings -------------------------------------------------------------

# Sender address used during SMTP handshake (never actually sends mail)
SMTP_SENDER = "manasranjandikshit01@gmail.com"

# Seconds to wait for an SMTP connection before timing out
SMTP_TIMEOUT = 15

# How many times to retry a failed SMTP connection
SMTP_RETRIES = 3

# Max seconds for exponential retry backoff per SMTP retry attempt.
SMTP_MAX_BACKOFF_SECONDS = 45

# Hard cap for total time spent in SMTP host attempts per email.
SMTP_TOTAL_TIMEOUT_SECONDS = 12

# Max MX hosts to attempt per email (priority order from DNS).
SMTP_MAX_MX_HOSTS = 5

# Reuse SMTP sessions per thread when possible.
SMTP_CONNECTION_REUSE = True

# Maximum cached SMTP sessions per worker thread.
SMTP_SESSION_CACHE_SIZE = 8

# Seconds to wait between retries (avoids hammering the server)
SMTP_RETRY_DELAY = 1

# SMTP command timeout for slow/tarpit servers.
SMTP_COMMAND_TIMEOUT = 20

# -- DNS Settings --------------------------------------------------------------

# Seconds to wait for a DNS response
DNS_TIMEOUT = 8

# DNS retry and exponential backoff controls.
DNS_RETRIES = 2
DNS_RETRY_DELAY = 1
DNS_MAX_BACKOFF_SECONDS = 10

# Optional DNS resolver nameservers list. Empty means system defaults.
DNS_NAMESERVERS: list[str] = ["8.8.8.8", "1.1.1.1"]

# Optional DKIM selectors to probe for TXT records (best effort only).
DKIM_SELECTORS = ["default", "selector1", "selector2", "google", "k1", "mail"]

# -- Caching ------------------------------------------------------------------

CACHE_DIR = os.path.join(BASE_DIR, "cache")
CACHE_DB_PATH = os.path.join(CACHE_DIR, "email_validator_cache.sqlite3")
CACHE_TTL_SECONDS = 3600
DNS_CACHE_TTL_SECONDS = 3600
MX_CACHE_TTL_SECONDS = 3600
SMTP_CACHE_TTL_SECONDS = 900
CATCH_ALL_CACHE_TTL_SECONDS = 7200

# In-memory LRU cache size per namespace.
CACHE_MEMORY_MAXSIZE = 5000

# -- Threading / Performance ---------------------------------------------------

# Number of parallel worker threads for bulk validation
MAX_WORKERS = 20

# Max emails validated in a single domain-aware batch.
DOMAIN_BATCH_SIZE = 200

# Optional per-domain throttling (seconds between SMTP validations).
PER_DOMAIN_RATE_LIMIT_SECONDS = 0.0

# -- Catch-All Detection -------------------------------------------------------

# Random local part used to probe for catch-all behaviour
CATCH_ALL_PROBE = "zz_catchall_probe_xyz123"

# Number of probe addresses used to classify catch-all behavior.
CATCH_ALL_PROBE_COUNT = 2

# Retry attempts when catch-all probing gets temporary SMTP outcomes.
CATCH_ALL_RETRIES = 1

# Skip catch-all probing if SMTP check was already slow.
CATCH_ALL_SKIP_IF_SMTP_LATENCY_MS_EXCEEDS = 2500

# Never exceed this probe count even if configured higher elsewhere.
CATCH_ALL_MAX_PROBE_COUNT = 5

# -- File Paths ----------------------------------------------------------------

# Folder where log files are written
LOG_DIR = "logs"

# Folder where output Excel / reports are written
OUTPUT_DIR = "output"

# Optional datasets for intelligence modules. If files do not exist,
# built-in defaults are used.
DISPOSABLE_LIST_PATH = os.path.join(BASE_DIR, "data", "disposable_domains.txt")
TYPO_DATABASE_PATH = os.path.join(BASE_DIR, "data", "typo_domains.csv")
ROLE_ACCOUNT_LIST_PATH = os.path.join(BASE_DIR, "data", "role_accounts.txt")
FREE_PROVIDER_LIST_PATH = os.path.join(BASE_DIR, "data", "free_providers.txt")

# Name of the validated results workbook
OUTPUT_EXCEL = "output/validation_results.xlsx"

# Name of the workbook containing only failed emails
FAILED_EXCEL = "output/failed_emails.xlsx"

# Plain-text summary report
SUMMARY_REPORT = "output/summary_report.txt"

# -- Logging ------------------------------------------------------------------

LOG_LEVEL = "DEBUG"
LOG_TO_CONSOLE = True
LOG_THREAD_INFO = True

# -- Validation / Classification ----------------------------------------------

ENABLE_SMTPUTF8 = True
ALLOW_QUOTED_LOCAL = True
ALLOW_DISPLAY_NAME = False
STRICT_RFC_LENGTHS = True

# Role account local-part defaults when no external file exists.
ROLE_ACCOUNT_DEFAULTS = [
	"admin", "info", "support", "sales", "billing", "contact", "help", "office",
	"security", "webmaster", "root", "hr", "jobs", "marketing", "team", "service",
]

# -- Validation Status Labels --------------------------------------------------
# These are the canonical strings written to the "Mail Status" column.

STATUS_VALID             = "VALID"
STATUS_INVALID_FORMAT    = "INVALID_FORMAT"
STATUS_INVALID_DOMAIN    = "INVALID_DOMAIN"
STATUS_INVALID_MAILBOX   = "INVALID_MAILBOX"
STATUS_ACCESS_DENIED     = "ACCESS_DENIED"
STATUS_CATCH_ALL         = "CATCH_ALL"
STATUS_TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
STATUS_UNKNOWN           = "UNKNOWN"
