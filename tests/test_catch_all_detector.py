from catch_all_detector import detect_catch_all


def test_detect_catch_all_likely(monkeypatch) -> None:
    outcomes = iter([
        {"status": "VALID", "smtp_code": 250, "smtp_message": "ok"},
        {"status": "VALID", "smtp_code": 250, "smtp_message": "ok"},
        {"status": "INVALID_MAILBOX", "smtp_code": 550, "smtp_message": "no"},
    ])

    def fake_verify(email: str, mx_hosts: list[str]) -> dict:
        return next(outcomes)

    monkeypatch.setattr("catch_all_detector.verify_mailbox", fake_verify)

    result = detect_catch_all("example.com", ["mx.example.com"])
    assert result.classification in {"LIKELY_CATCH_ALL", "DEFINITE_CATCH_ALL", "PARTIAL_CATCH_ALL"}
    assert result.probes_used >= 1
