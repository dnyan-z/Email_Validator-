"""
excel_handler.py
----------------
Reads email data from the input Excel file and writes validation
results back to the output files.

Expected input columns (case-insensitive):
  • Mail ID     – the email address
  • Mail Status – optional existing status (will be overwritten)

Output adds:
  • Mail Status      – validation status code
  • Validation Reason – human-readable explanation
  • SMTP Response    – raw SMTP code + message
"""

import os

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.dataframe import dataframe_to_rows

from config import (
    FAILED_EXCEL,
    OUTPUT_DIR,
    OUTPUT_EXCEL,
    STATUS_VALID,
)
from logger import get_logger

log = get_logger(__name__)

# ── Colour palette for the status column ─────────────────────────────────────
_STATUS_COLOURS = {
    "VALID":             "C6EFCE",  # green
    "INVALID_FORMAT":    "FFEB9C",  # yellow
    "INVALID_DOMAIN":    "FFC7CE",  # red-light
    "INVALID_MAILBOX":   "FFC7CE",  # red-light
    "ACCESS_DENIED":     "FFCC99",  # orange
    "CATCH_ALL":         "BDD7EE",  # blue-light
    "TEMPORARY_FAILURE": "E2EFDA",  # grey-green
    "UNKNOWN":           "D9D9D9",  # grey
}


def read_emails(filepath: str) -> list[str]:
    """
    Read email addresses from an Excel file.

    Looks for a column whose name contains 'mail id' (case-insensitive).
    Falls back to the first column if no match is found.

    Parameters
    ----------
    filepath : str
        Path to the .xlsx input file.

    Returns
    -------
    list[str]
        All non-empty email strings found in the column.
    """
    log.info("Reading input file: %s", filepath)
    df = pd.read_excel(filepath, dtype=str)

    # Find the email column
    col = _find_column(df, "mail id") or df.columns[0]
    log.info("Using column '%s' for email addresses", col)

    emails = df[col].dropna().str.strip().tolist()
    emails = [e for e in emails if e]  # remove blank strings
    log.info("Found %d email addresses", len(emails))
    return emails


def write_results(results: list[dict]) -> None:
    """
    Write validation results to the output Excel file with colour-coding.

    Parameters
    ----------
    results : list[dict]
        Each dict must have keys: email, status, reason, smtp_response.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    df = pd.DataFrame(results)[["email", "status", "reason", "smtp_response"]]
    df.columns = ["Mail ID", "Mail Status", "Validation Reason", "SMTP Response"]

    wb = _build_workbook(df, sheet_name="Validation Results")
    wb.save(OUTPUT_EXCEL)
    log.info("Results saved → %s", OUTPUT_EXCEL)


def write_failed_emails(results: list[dict]) -> None:
    """
    Write only the failed / invalid email results to a separate file.

    Parameters
    ----------
    results : list[dict]
        Full results list; this function filters out VALID emails.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    failed = [r for r in results if r.get("status") != STATUS_VALID]
    if not failed:
        log.info("No failed emails – skipping failed_emails.xlsx")
        return

    df = pd.DataFrame(failed)[["email", "status", "reason", "smtp_response"]]
    df.columns = ["Mail ID", "Mail Status", "Validation Reason", "SMTP Response"]

    wb = _build_workbook(df, sheet_name="Failed Emails")
    wb.save(FAILED_EXCEL)
    log.info("Failed emails saved → %s (%d rows)", FAILED_EXCEL, len(failed))


# ── Private helpers ───────────────────────────────────────────────────────────

def _find_column(df: pd.DataFrame, keyword: str) -> str | None:
    """Return the first column name whose lowercase form contains keyword."""
    for col in df.columns:
        if keyword in col.lower():
            return col
    return None


def _build_workbook(df: pd.DataFrame, sheet_name: str) -> Workbook:
    """
    Convert a DataFrame into a formatted openpyxl Workbook.

    Parameters
    ----------
    df : pd.DataFrame
        Data to write (already has correct column names).
    sheet_name : str
        Name for the worksheet.

    Returns
    -------
    Workbook
        Ready-to-save workbook with header formatting and row colours.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    # ── Headers ───────────────────────────────────────────────────────────────
    header_font  = Font(bold=True, color="FFFFFF", name="Arial", size=11)
    header_fill  = PatternFill("solid", start_color="2E75B6")
    header_align = Alignment(horizontal="center", vertical="center")

    for col_idx, col_name in enumerate(df.columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font  = header_font
        cell.fill  = header_fill
        cell.alignment = header_align

    # ── Data rows ─────────────────────────────────────────────────────────────
    status_col_idx = list(df.columns).index("Mail Status") + 1

    for row_idx, row in enumerate(df.itertuples(index=False), start=2):
        for col_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.font      = Font(name="Arial", size=10)
            cell.alignment = Alignment(vertical="center")

        # Colour the status cell
        status_value = ws.cell(row=row_idx, column=status_col_idx).value or ""
        colour = _STATUS_COLOURS.get(status_value, "FFFFFF")
        ws.cell(row=row_idx, column=status_col_idx).fill = PatternFill(
            "solid", start_color=colour
        )

    # ── Column widths ─────────────────────────────────────────────────────────
    col_widths = [35, 22, 45, 20]
    for i, width in enumerate(col_widths, start=1):
        ws.column_dimensions[
            ws.cell(row=1, column=i).column_letter
        ].width = width

    ws.row_dimensions[1].height = 20

    return wb
