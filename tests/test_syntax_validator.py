from syntax_validator import set_bulk_context, validate_syntax


def test_detects_typo_role_plus_and_duplicate() -> None:
    emails = [" Admin+sales@Gmial.com ", "admin+sales@gmial.com"]
    set_bulk_context(emails)

    result = validate_syntax(emails[0])

    assert result["status"] == "VALID"
    assert result["is_role_account"] is True
    assert result["is_plus_addressing"] is True
    assert result["is_typo_domain"] is True
    assert result["typo_suggestion"] == "gmail.com"
    assert result["duplicate_count"] == 2


def test_invalid_format_reason_code() -> None:
    set_bulk_context(["bad@@example.com"])
    result = validate_syntax("bad@@example.com")
    assert result["status"] == "INVALID_FORMAT"
    assert result["reason_code"] == "INVALID_AT_SYMBOL_COUNT"
