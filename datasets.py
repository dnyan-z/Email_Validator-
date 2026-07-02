"""Dataset loading helpers for typo, provider, disposable, and role-account intelligence."""

from __future__ import annotations

import os
from typing import Iterable


DEFAULT_ROLE_ACCOUNTS: set[str] = {
    "admin", "info", "support", "sales", "billing", "contact", "help", "office",
    "security", "webmaster", "root", "hr", "jobs", "marketing", "team", "service",
}

DEFAULT_FREE_PROVIDERS: set[str] = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "icloud.com",
    "yahoo.com", "proton.me", "protonmail.com", "zoho.com", "mail.com", "gmx.com", "fastmail.com",
}

DEFAULT_DISPOSABLE_PROVIDERS: dict[str, str] = {
    "mailinator.com": "Mailinator",
    "tempmail.com": "TempMail",
    "10minutemail.com": "10MinuteMail",
    "guerrillamail.com": "GuerrillaMail",
    "yopmail.com": "YOPmail",
    "trashmail.com": "TrashMail",
    "fakemail.net": "FakeMail",
    "maildrop.cc": "MailDrop",
    "tempinbox.com": "TempInbox",
}

DEFAULT_TYPO_MAP: dict[str, str] = {
    "gmial.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "hotnail.com": "hotmail.com",
    "yahho.com": "yahoo.com",
    "icloud.con": "icloud.com",
}


def _load_lines(path: str) -> list[str]:
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.strip().startswith("#")]


def load_role_accounts(path: str | None) -> set[str]:
    values = _load_lines(path or "")
    if not values:
        return set(DEFAULT_ROLE_ACCOUNTS)
    return {v.lower() for v in values}


def load_free_providers(path: str | None) -> set[str]:
    values = _load_lines(path or "")
    if not values:
        return set(DEFAULT_FREE_PROVIDERS)
    return {v.lower() for v in values}


def load_disposable_map(path: str | None) -> dict[str, str]:
    values = _load_lines(path or "")
    if not values:
        return dict(DEFAULT_DISPOSABLE_PROVIDERS)
    output: dict[str, str] = {}
    for item in values:
        if "," in item:
            domain, provider = item.split(",", 1)
            output[domain.strip().lower()] = provider.strip() or "Disposable"
        else:
            output[item.lower()] = "Disposable"
    return output


def load_typo_map(path: str | None) -> dict[str, str]:
    values = _load_lines(path or "")
    if not values:
        return dict(DEFAULT_TYPO_MAP)
    output: dict[str, str] = {}
    for item in values:
        if "," in item:
            bad, suggested = item.split(",", 1)
            output[bad.strip().lower()] = suggested.strip().lower()
    return output


def is_random_local_part(local_part: str) -> bool:
    """Simple heuristic to identify random-looking local-parts."""
    letters = sum(1 for ch in local_part if ch.isalpha())
    digits = sum(1 for ch in local_part if ch.isdigit())
    specials = sum(1 for ch in local_part if not ch.isalnum())
    length = len(local_part)
    if length >= 14 and digits >= 4 and specials >= 1:
        return True
    if length >= 18 and letters >= 8 and digits >= 3:
        return True
    return False


def classify_provider(domain: str, free_providers: Iterable[str]) -> str:
    domain = domain.lower()
    if domain in {d.lower() for d in free_providers}:
        return "FREE"
    return "CORPORATE_OR_CUSTOM"
