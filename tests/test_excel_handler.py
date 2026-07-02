from pathlib import Path

import excel_handler


def test_write_results_creates_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(excel_handler, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(excel_handler, "OUTPUT_EXCEL", str(tmp_path / "validation_results.xlsx"))

    excel_handler.write_results(
        [
            {
                "email": "user@example.com",
                "normalized_email": "user@example.com",
                "domain": "example.com",
                "provider_classification": "CORPORATE_OR_CUSTOM",
                "status": "VALID",
                "reason": "ok",
                "reason_code": "ok",
                "is_role_account": False,
                "is_disposable": False,
                "is_free_provider": False,
                "smtp_code": 250,
                "smtp_message": "OK",
                "smtp_response": "250 OK",
                "catch_all_result": "NOT_CATCH_ALL",
                "risk_score": 10,
                "confidence_score": 90,
                "deliverability_score": 90,
                "validation_duration_ms": 10.0,
            }
        ]
    )

    assert (tmp_path / "validation_results.xlsx").exists()
