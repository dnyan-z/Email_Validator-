"""Stage 1 - Email syntax and metadata intelligence."""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from email.utils import parseaddr

from email_validator import EmailNotValidError, validate_email

from config import (
    ALLOW_DISPLAY_NAME,
    ALLOW_QUOTED_LOCAL,
    DISPOSABLE_LIST_PATH,
    ENABLE_SMTPUTF8,
    FREE_PROVIDER_LIST_PATH,
    ROLE_ACCOUNT_LIST_PATH,
    STATUS_INVALID_FORMAT,
    STATUS_VALID,
    TYPO_DATABASE_PATH,
)
from datasets import (
    classify_provider,
    is_random_local_part,
    load_disposable_map,
    load_free_providers,
    load_role_accounts,
    load_typo_map,
)
from logger import get_logger

log = get_logger(__name__)

_ROLE_ACCOUNTS = load_role_accounts(ROLE_ACCOUNT_LIST_PATH)
_FREE_PROVIDERS = load_free_providers(FREE_PROVIDER_LIST_PATH)
_DISPOSABLE_MAP = load_disposable_map(DISPOSABLE_LIST_PATH)
_TYPO_MAP = load_typo_map(TYPO_DATABASE_PATH)

_INVISIBLE_RE = re.compile(r"[\u200B-\u200D\u2060\uFEFF]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002700-\U000027BF]")

_context_lock = threading.RLock()
_duplicate_counts: dict[str, int] = {}


@dataclass(slots=True)
class SyntaxCheckOutcome:
    email: str
    status: str
    reason: str
    reason_code: str
    domain: str
    local_part: str


def set_bulk_context(raw_emails: list[str]) -> None:
    """Prepare duplicate counts for the current run."""
    counts: dict[str, int] = {}
    for raw in raw_emails:
        normalized = normalize_email(raw)
        counts[normalized] = counts.get(normalized, 0) + 1
    with _context_lock:
        _duplicate_counts.clear()
        _duplicate_counts.update(counts)


def normalize_email(raw: str) -> str:
    """Normalize whitespace, Unicode, and case for syntax checks."""
    value = "" if raw is None else str(raw)
    value = _INVISIBLE_RE.sub("", value)
    value = value.strip()
    value = unicodedata.normalize("NFKC", value)
    return value.lower()


def validate_syntax(raw_email: str) -> dict:
    """Validate syntax and return enriched metadata for downstream stages."""
    normalized = normalize_email(raw_email)
    outcome = _precheck(normalized)
    if outcome.status != STATUS_VALID:
        return _result(outcome)

    try:
        valid = validate_email(
            normalized,
            check_deliverability=False,
            allow_display_name=ALLOW_DISPLAY_NAME,
            allow_quoted_local=ALLOW_QUOTED_LOCAL,
            allow_smtputf8=ENABLE_SMTPUTF8,
        )
        normalized_email = valid.normalized
        local_part, domain = normalized_email.rsplit("@", 1)
    except EmailNotValidError as exc:
        return _result(
            SyntaxCheckOutcome(
                email=normalized,
                status=STATUS_INVALID_FORMAT,
                reason=f"RFC_VALIDATION_FAILED: {exc}",
                reason_code="RFC_VALIDATION_FAILED",
                domain="",
                local_part="",
            )
        )

    typo_suggestion = _TYPO_MAP.get(domain.lower(), "")
    is_typo = bool(typo_suggestion)
    local_part_base = local_part.split("+", 1)[0]
    is_plus_alias = "+" in local_part
    is_role = local_part_base.lower() in _ROLE_ACCOUNTS
    is_free = domain.lower() in _FREE_PROVIDERS
    disposable_provider = _DISPOSABLE_MAP.get(domain.lower(), "")
    is_disposable = bool(disposable_provider)
    provider_class = classify_provider(domain, _FREE_PROVIDERS)

    with _context_lock:
        duplicate_count = _duplicate_counts.get(normalized_email.lower(), 0)

    result = _result(
        SyntaxCheckOutcome(
            email=normalized_email,
            status=STATUS_VALID,
            reason="SYNTAX_OK",
            reason_code="SYNTAX_OK",
            domain=domain,
            local_part=local_part,
        )
    )
    result.update(
        {
            "normalized_email": normalized_email,
            "domain": domain,
            "local_part": local_part,
            "local_part_base": local_part_base,
            "is_duplicate": duplicate_count > 1,
            "duplicate_count": duplicate_count,
            "is_plus_addressing": is_plus_alias,
            "is_alias": is_plus_alias,
            "is_role_account": is_role,
            "provider_classification": provider_class,
            "is_free_provider": is_free,
            "is_disposable": is_disposable,
            "disposable_provider": disposable_provider or "",
            "disposable_confidence": 95 if is_disposable else 0,
            "is_typo_domain": is_typo,
            "typo_suggestion": typo_suggestion,
            "high_risk_local_part": is_random_local_part(local_part),
        }
    )
    return result


def _precheck(email: str) -> SyntaxCheckOutcome:
    if not email:
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "EMPTY_EMAIL", "EMPTY_EMAIL", "", "")
    if _CONTROL_RE.search(email):
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "CONTROL_CHAR_DETECTED", "CONTROL_CHAR_DETECTED", "", "")
    if _EMOJI_RE.search(email):
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "EMOJI_NOT_ALLOWED", "EMOJI_NOT_ALLOWED", "", "")

    display_name, parsed_addr = parseaddr(email)
    if display_name and not ALLOW_DISPLAY_NAME:
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "DISPLAY_NAME_NOT_ALLOWED", "DISPLAY_NAME_NOT_ALLOWED", "", "")
    email = parsed_addr or email

    at_count = email.count("@")
    if at_count != 1:
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "INVALID_AT_SYMBOL_COUNT", "INVALID_AT_SYMBOL_COUNT", "", "")

    local_part, domain = email.rsplit("@", 1)
    if not local_part or not domain:
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "MISSING_LOCAL_OR_DOMAIN", "MISSING_LOCAL_OR_DOMAIN", "", "")

    if email != email.strip():
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "LEADING_OR_TRAILING_SPACES", "LEADING_OR_TRAILING_SPACES", domain, local_part)
    if ".." in email:
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "CONSECUTIVE_DOTS", "CONSECUTIVE_DOTS", domain, local_part)
    if local_part.startswith(".") or local_part.endswith("."):
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "LEADING_OR_TRAILING_DOT_LOCAL", "LEADING_OR_TRAILING_DOT_LOCAL", domain, local_part)
    if domain.startswith(".") or domain.endswith("."):
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "LEADING_OR_TRAILING_DOT_DOMAIN", "LEADING_OR_TRAILING_DOT_DOMAIN", domain, local_part)

    if len(local_part.encode("utf-8")) > 64:
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "LOCAL_PART_TOO_LONG", "LOCAL_PART_TOO_LONG", domain, local_part)
    if len(domain.encode("idna")) > 255:
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "DOMAIN_TOO_LONG", "DOMAIN_TOO_LONG", domain, local_part)
    if len(email.encode("utf-8")) > 254:
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "EMAIL_TOO_LONG", "EMAIL_TOO_LONG", domain, local_part)

    if any(ord(ch) == 0 for ch in email):
        return SyntaxCheckOutcome(email, STATUS_INVALID_FORMAT, "INVALID_UNICODE", "INVALID_UNICODE", domain, local_part)

    return SyntaxCheckOutcome(email, STATUS_VALID, "PRECHECK_OK", "PRECHECK_OK", domain, local_part)


def _result(outcome: SyntaxCheckOutcome) -> dict:
    return {
        "email": outcome.email,
        "normalized_email": outcome.email,
        "status": outcome.status,
        "reason": outcome.reason,
        "reason_code": outcome.reason_code,
        "smtp_response": "",
        "domain": outcome.domain,
        "local_part": outcome.local_part,
        "is_duplicate": False,
        "duplicate_count": 1,
        "is_plus_addressing": False,
        "is_alias": False,
        "is_role_account": False,
        "provider_classification": "UNKNOWN",
        "is_free_provider": False,
        "is_disposable": False,
        "disposable_provider": "",
        "disposable_confidence": 0,
        "is_typo_domain": False,
        "typo_suggestion": "",
        "high_risk_local_part": False,
    }
