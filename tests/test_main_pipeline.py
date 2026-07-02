import main


def test_validate_single_invalid_format(monkeypatch) -> None:
    monkeypatch.setattr(main, "validate_syntax", lambda _: {"email": "bad", "status": "INVALID_FORMAT", "reason": "bad", "reason_code": "bad", "smtp_response": ""})
    result = main.validate_single("bad")
    assert result["status"] == "INVALID_FORMAT"
    assert "deliverability_score" in result


def test_validate_single_valid_flow(monkeypatch) -> None:
    monkeypatch.setattr(main, "validate_syntax", lambda _: {"email": "u@example.com", "status": "VALID", "reason": "ok", "reason_code": "ok", "smtp_response": "", "domain": "example.com"})
    monkeypatch.setattr(main, "check_domain", lambda _: {"email": "u@example.com", "status": "VALID", "reason": "ok", "reason_code": "ok", "smtp_response": "", "mx_hosts": ["mx.example.com"], "dns_report": {}, "dns_latency_ms": 1.0})
    monkeypatch.setattr(main, "verify_mailbox", lambda *_: {"email": "u@example.com", "status": "VALID", "reason": "ok", "reason_code": "ok", "smtp_response": "250 ok"})

    class CatchAll:
        classification = "NOT_CATCH_ALL"
        confidence = 90
        probes_used = 3
        details = []

    monkeypatch.setattr(main, "detect_catch_all", lambda *_: CatchAll())

    result = main.validate_single("u@example.com")
    assert result["status"] == "VALID"
    assert result["catch_all_result"] == "NOT_CATCH_ALL"
    assert "risk_score" in result
