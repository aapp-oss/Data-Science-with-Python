"""
report_generator.py — Produce Excel and CSV reports from analysis results.

REPORT PHILOSOPHY:
Every report should be self-contained — a non-technical person should be able
to open the Excel file and understand what it's telling them without needing
to know anything about the Python code that generated it.

REPORTS PRODUCED:
1. Quality Dashboard (Excel) — executive summary with a quality score,
   color-coded KPIs, and charts.
2. Duplicates Report (Excel) — every duplicate pair with side-by-side
   field comparison and confidence scores.
3. Validation Issues (Excel) — every field-level quality issue organized
   by severity.
4. Exception Report (Excel) — hard errors that require human resolution.
5. Merge Audit (Excel) — complete record of every merge performed.
6. CSV exports of all of the above for import into other systems.

WHY OPENPYXL INSTEAD OF JUST pd.to_excel()?
pd.to_excel() produces plain, unformatted spreadsheets. openpyxl lets us
add color-coding, bold headers, column widths, and multiple sheets — all of
which make the reports actually usable by data staff who spend their day in Excel.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import openpyxl
from openpyxl.styles import (
    Alignment, Font, PatternFill, Border, Side, numbers
)
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference

from config.settings import Settings
from models.schemas import (
    DuplicatePair, ErrorRecord, MergeResult, QualityReport, ValidationIssue
)

logger = logging.getLogger(__name__)

# ── Color constants (hex without #) ──────────────────────────────────────────
COLOR_HEADER    = "1F4E79"   # Dark navy blue
COLOR_GOOD      = "C6EFCE"   # Light green
COLOR_WARNING   = "FFEB9C"   # Light yellow
COLOR_BAD       = "FFC7CE"   # Light red
COLOR_CRITICAL  = "FF0000"   # Red text
COLOR_SUBHEADER = "D9E1F2"   # Light blue


class ReportGenerator:
    """
    Generates formatted Excel workbooks and CSV files from quality analysis results.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = Path(settings.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_all(self, report: QualityReport) -> dict:
        """
        Generate all reports and return a dict of {report_name: file_path}.
        """
        report.compute_quality_score()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        paths = {}

        paths["dashboard"]   = self._write_dashboard(report, timestamp)
        paths["duplicates"]  = self._write_duplicates_report(report.duplicate_pairs, timestamp)
        paths["validation"]  = self._write_validation_report(report.validation_issues, timestamp)
        paths["exceptions"]  = self._write_exception_report(report.error_records, timestamp)
        paths["merge_audit"] = self._write_merge_audit(report.merge_results, timestamp)

        logger.info("All reports written to: %s", self.output_dir)
        return paths

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def _write_dashboard(self, report: QualityReport, ts: str) -> str:
        path = str(self.output_dir / f"quality_dashboard_{ts}.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Quality Dashboard"

        # Title row
        ws.merge_cells("A1:F1")
        title_cell = ws["A1"]
        title_cell.value = "DATA QUALITY DASHBOARD"
        title_cell.font = Font(bold=True, size=16, color="FFFFFF")
        title_cell.fill = PatternFill("solid", fgColor=COLOR_HEADER)
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 30

        # Run info
        ws["A2"] = "Report Generated:"
        ws["B2"] = report.run_timestamp.strftime("%Y-%m-%d %H:%M:%S")
        ws["A3"] = "Total Records Analyzed:"
        ws["B3"] = report.total_records

        # Quality score (big number)
        ws["D2"] = "OVERALL QUALITY SCORE"
        ws["D2"].font = Font(bold=True, size=12)
        ws["D3"] = f"{report.overall_quality_score:.1f} / 100"
        ws["D3"].font = Font(bold=True, size=20,
                             color="00B050" if report.overall_quality_score >= 80 else
                                   "FF8C00" if report.overall_quality_score >= 60 else "FF0000")
        ws["D3"].alignment = Alignment(horizontal="center")
        ws.merge_cells("D3:F3")

        # KPI table
        kpi_data = [
            ("DUPLICATES", None, None),
            ("Exact Duplicates",          report.exact_duplicates,     COLOR_BAD if report.exact_duplicates else COLOR_GOOD),
            ("Fuzzy Duplicate Candidates",report.fuzzy_duplicates,     COLOR_WARNING if report.fuzzy_duplicates else COLOR_GOOD),
            ("Records Merged",            report.records_merged,       COLOR_SUBHEADER),
            ("DATA ERRORS", None, None),
            ("Invalid Emails",            report.invalid_emails,       COLOR_BAD if report.invalid_emails else COLOR_GOOD),
            ("Invalid Phone Numbers",     report.invalid_phones,       COLOR_WARNING if report.invalid_phones else COLOR_GOOD),
            ("Invalid/Impossible Dates",  report.invalid_dates,        COLOR_BAD if report.invalid_dates else COLOR_GOOD),
            ("Duplicate Constituent IDs", report.duplicate_ids,        COLOR_BAD if report.duplicate_ids else COLOR_GOOD),
            ("FIELD QUALITY", None, None),
            ("Missing Required Values",   report.missing_value_issues, COLOR_WARNING if report.missing_value_issues else COLOR_GOOD),
            ("Formatting Issues",         report.format_issues,        COLOR_WARNING if report.format_issues else COLOR_GOOD),
        ]

        row = 6
        ws[f"A{row}"] = "Metric"
        ws[f"B{row}"] = "Count"
        for col_letter in ("A", "B"):
            cell = ws[f"{col_letter}{row}"]
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=COLOR_HEADER)

        row += 1
        for label, value, color in kpi_data:
            if value is None:
                ws[f"A{row}"] = label
                ws[f"A{row}"].font = Font(bold=True)
                ws[f"A{row}"].fill = PatternFill("solid", fgColor=COLOR_SUBHEADER)
                ws.merge_cells(f"A{row}:B{row}")
            else:
                ws[f"A{row}"] = label
                ws[f"B{row}"] = value
                ws[f"B{row}"].alignment = Alignment(horizontal="center")
                if color:
                    ws[f"A{row}"].fill = PatternFill("solid", fgColor=color)
                    ws[f"B{row}"].fill = PatternFill("solid", fgColor=color)
            row += 1

        ws.column_dimensions["A"].width = 35
        ws.column_dimensions["B"].width = 12
        ws.column_dimensions["D"].width = 25

        wb.save(path)
        logger.info("Dashboard written: %s", path)
        return path

    # ── Duplicates report ─────────────────────────────────────────────────────

    def _write_duplicates_report(self, pairs: List[DuplicatePair], ts: str) -> str:
        path = str(self.output_dir / f"duplicates_report_{ts}.xlsx")
        if not pairs:
            pd.DataFrame().to_excel(path)
            return path

        rows = []
        for p in pairs:
            rows.append({
                "Match Type":       p.match_type.upper(),
                "Confidence Score": p.confidence_score,
                "Record A Index":   p.record_a_index,
                "Record A ID":      p.record_a_id or "",
                "Record B Index":   p.record_b_index,
                "Record B ID":      p.record_b_id or "",
                "Field Scores":     str(p.field_scores),
                "Auto Approved":    "YES" if p.confidence_score >= self.settings.auto_merge_threshold else "NEEDS REVIEW",
            })
        df = pd.DataFrame(rows).sort_values("Confidence Score", ascending=False)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Duplicate Pairs"
        self._write_df_to_sheet(ws, df, title="Duplicate Records Found")

        # Color-code by confidence
        score_col_idx = df.columns.get_loc("Confidence Score") + 1
        for row_idx in range(3, len(df) + 3):
            cell = ws.cell(row=row_idx, column=score_col_idx)
            try:
                score = float(cell.value)
                if score >= 92:
                    cell.fill = PatternFill("solid", fgColor=COLOR_BAD)
                elif score >= 75:
                    cell.fill = PatternFill("solid", fgColor=COLOR_WARNING)
            except (TypeError, ValueError):
                pass

        wb.save(path)
        logger.info("Duplicates report written: %s", path)
        return path

    # ── Validation issues report ──────────────────────────────────────────────

    def _write_validation_report(self, issues: List[ValidationIssue], ts: str) -> str:
        path = str(self.output_dir / f"validation_issues_{ts}.xlsx")
        if not issues:
            pd.DataFrame().to_excel(path)
            return path

        rows = [{
            "Row Index":       i.row_index,
            "Field":           i.field_name,
            "Issue Type":      i.issue_type.value,
            "Severity":        i.severity.value,
            "Current Value":   str(i.current_value),
            "Suggested Value": str(i.suggested_value) if i.suggested_value is not None else "",
            "Message":         i.message,
        } for i in issues]

        df = pd.DataFrame(rows)
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        df["_sort"] = df["Severity"].map(severity_order)
        df = df.sort_values("_sort").drop(columns=["_sort"])

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Validation Issues"
        self._write_df_to_sheet(ws, df, title="Data Validation Issues")

        sev_col = df.columns.get_loc("Severity") + 1
        sev_colors = {
            "CRITICAL": COLOR_BAD,
            "HIGH":     COLOR_BAD,
            "MEDIUM":   COLOR_WARNING,
            "LOW":      COLOR_GOOD,
        }
        for row_idx in range(3, len(df) + 3):
            cell = ws.cell(row=row_idx, column=sev_col)
            fill_color = sev_colors.get(str(cell.value), "FFFFFF")
            cell.fill = PatternFill("solid", fgColor=fill_color)

        wb.save(path)
        logger.info("Validation report written: %s", path)
        return path

    # ── Exception report ──────────────────────────────────────────────────────

    def _write_exception_report(self, errors: List[ErrorRecord], ts: str) -> str:
        path = str(self.output_dir / f"exception_report_{ts}.xlsx")
        if not errors:
            pd.DataFrame().to_excel(path)
            return path

        rows = [{
            "Row Index":      e.row_index,
            "Constituent ID": e.constituent_id or "",
            "Field":          e.field_name,
            "Error Type":     e.error_type.value,
            "Severity":       e.severity.value,
            "Bad Value":      str(e.bad_value),
            "Description":    e.description,
        } for e in errors]

        df = pd.DataFrame(rows)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Exception Report"
        self._write_df_to_sheet(ws, df, title="Exception Report — Requires Human Review")
        wb.save(path)
        logger.info("Exception report written: %s", path)
        return path

    # ── Merge audit ───────────────────────────────────────────────────────────

    def _write_merge_audit(self, merges: List[MergeResult], ts: str) -> str:
        path = str(self.output_dir / f"merge_audit_{ts}.xlsx")
        if not merges:
            pd.DataFrame().to_excel(path)
            return path

        rows = [{
            "Merged At":    m.merged_at.strftime("%Y-%m-%d %H:%M:%S"),
            "Source A ID":  m.source_a_id or "",
            "Source B ID":  m.source_b_id or "",
            "Approved By":  m.approved_by,
            "Field Sources": str(m.field_sources),
        } for m in merges]

        df = pd.DataFrame(rows)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Merge Audit"
        self._write_df_to_sheet(ws, df, title="Merge Audit Trail")
        wb.save(path)
        logger.info("Merge audit written: %s", path)
        return path

    # ── CSV exports ───────────────────────────────────────────────────────────

    def export_csv(self, df: pd.DataFrame, name: str) -> str:
        """Export a DataFrame to CSV in the output directory."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(self.output_dir / f"{name}_{ts}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("CSV exported: %s", path)
        return path

    # ── Shared formatting helper ──────────────────────────────────────────────

    def _write_df_to_sheet(
        self, ws, df: pd.DataFrame, title: Optional[str] = None
    ) -> None:
        """
        Write a DataFrame to a worksheet with formatted headers.
        Adds a title row if provided, then bold header row, then data.
        Autofit column widths.
        """
        start_row = 1

        if title:
            ws.merge_cells(f"A1:{get_column_letter(len(df.columns))}1")
            title_cell = ws["A1"]
            title_cell.value = title
            title_cell.font = Font(bold=True, size=13, color="FFFFFF")
            title_cell.fill = PatternFill("solid", fgColor=COLOR_HEADER)
            title_cell.alignment = Alignment(horizontal="center")
            ws.row_dimensions[1].height = 22
            start_row = 2

        # Header row
        header_fill = PatternFill("solid", fgColor=COLOR_SUBHEADER)
        header_font = Font(bold=True)
        for col_idx, header in enumerate(df.columns, start=1):
            cell = ws.cell(row=start_row, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        # Data rows
        for row_idx, row_data in enumerate(df.itertuples(index=False), start=start_row + 1):
            for col_idx, value in enumerate(row_data, start=1):
                ws.cell(row=row_idx, column=col_idx, value=value)

        # Auto-width columns (capped at 60 chars)
        for col_idx, col_name in enumerate(df.columns, start=1):
            max_len = max(
                len(str(col_name)),
                df.iloc[:, col_idx - 1].astype(str).str.len().max() if len(df) else 0,
            )
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 60)
