"""Risk and deliverability scoring utilities for validation outputs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ScoreResult:
    deliverability_score: int
    risk_score: int
    confidence_score: int
    validation_grade: str


def compute_scores(result: dict) -> ScoreResult:
    status = result.get("status", "UNKNOWN")
    risk = 10
    confidence = 85
    deliverability = 85

    if status in {"INVALID_FORMAT", "INVALID_DOMAIN", "INVALID_MAILBOX"}:
        risk = 90
        confidence = 95
        deliverability = 5
    elif status == "CATCH_ALL":
        risk = 65
        confidence = 70
        deliverability = 55
    elif status == "TEMPORARY_FAILURE":
        risk = 60
        confidence = 50
        deliverability = 35
    elif status == "ACCESS_DENIED":
        risk = 55
        confidence = 45
        deliverability = 40
    elif status == "UNKNOWN":
        risk = 70
        confidence = 35
        deliverability = 25

    if result.get("is_disposable"):
        risk += 20
        confidence -= 10
    if result.get("is_role_account"):
        risk += 10
    if result.get("catch_all_result") in {"DEFINITE_CATCH_ALL", "LIKELY_CATCH_ALL"}:
        risk += 15
        deliverability -= 10
    if result.get("is_typo_domain"):
        risk += 10
        confidence -= 10
    if result.get("greylist_detected"):
        risk += 8
        deliverability -= 8

    risk = max(0, min(100, risk))
    confidence = max(0, min(100, confidence))
    deliverability = max(0, min(100, deliverability))

    if deliverability >= 80:
        grade = "A"
    elif deliverability >= 65:
        grade = "B"
    elif deliverability >= 50:
        grade = "C"
    elif deliverability >= 30:
        grade = "D"
    else:
        grade = "F"

    return ScoreResult(
        deliverability_score=deliverability,
        risk_score=risk,
        confidence_score=confidence,
        validation_grade=grade,
    )
