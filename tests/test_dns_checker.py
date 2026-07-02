from types import SimpleNamespace

import dns_checker


class _FakeAnswer:
    def __init__(self, values: list[str], ttl: int = 300) -> None:
        self._values = values
        self.rrset = SimpleNamespace(ttl=ttl)

    def __iter__(self):
        for item in self._values:
            yield item


def test_check_domain_success(monkeypatch) -> None:
    class FakeMX:
        def __init__(self, pref: int, exch: str) -> None:
            self.preference = pref
            self.exchange = exch

        def __str__(self) -> str:
            return f"{self.preference} {self.exchange}"

    def fake_resolve(name: str, record_type: str):
        if record_type == "MX":
            return [FakeMX(20, "mx2.example.com."), FakeMX(10, "mx1.example.com.")]
        if record_type in {"A", "NS", "SOA"}:
            return _FakeAnswer(["1.2.3.4"])
        return _FakeAnswer([])

    monkeypatch.setattr(dns_checker._resolver, "resolve", fake_resolve)

    result = dns_checker.check_domain("user@example.com")
    assert result["status"] == "VALID"
    assert result["mx_hosts"][0] == "mx1.example.com"
    assert "dns_report" in result
