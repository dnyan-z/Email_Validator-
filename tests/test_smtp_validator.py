import time

import smtp_validator


def test_verify_mailbox_returns_cached_result(monkeypatch) -> None:
    call_count = {"n": 0}

    class FakeResult:
        code = 250
        message = "OK"
        server_banner = "mx"
        tls_version = "TLSv1.3"
        tls_cipher = "TLS_AES_128_GCM_SHA256"
        supports_smtputf8 = True
        supports_pipelining = True
        supports_8bitmime = True
        supports_auth = False
        supports_size = True
        supports_starttls = True
        smtp_latency_ms = 10.0
        connection_ms = 5.0
        transcript = "CONNECT"
        greylist_detected = False
        throttled_detected = False

    def fake_check(email: str, mx: str):
        call_count["n"] += 1
        return FakeResult()

    monkeypatch.setattr(smtp_validator, "_smtp_check", fake_check)

    unique_email = f"user+{time.time_ns()}@example.com"
    first = smtp_validator.verify_mailbox(unique_email, ["mx.example.com"])
    second = smtp_validator.verify_mailbox(unique_email, ["mx.example.com"])

    assert first["status"] == "VALID"
    assert second["status"] == "VALID"
    assert call_count["n"] == 1
