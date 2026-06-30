"""
data_validator.py — Detect data quality issues and apply standardizations.

TWO MODES OF OPERATION:
1. DETECT: Scan the dataset and produce a list of ValidationIssue objects
   describing every problem found (used for the quality report).
2. STANDARDIZE: Apply fixes in-place and return the corrected DataFrame,
   logging each change to the audit trail.

WHY SEPARATE DETECT FROM STANDARDIZE?
You might want to run detection on Monday, review the report, and only
apply the fixes on Tuesday after a manager approves. Separating the two
operations lets you insert a human review step between them.
"""

import logging
import re
from typing import List, Tuple

import pandas as pd

from config.settings import Settings
from models.schemas import IssueSeverity, IssueType, ValidationIssue
from utils.audit_trail import AuditTrail
from utils.helpers import is_blank
from utils.standardizers import Standardizer

logger = logging.getLogger(__name__)


class DataValidator:
    """
    Validates and optionally standardizes constituent data fields.
    """

    def __init__(self, settings: Settings, audit: AuditTrail):
        self.settings = settings
        self.audit = audit
        self.std = Standardizer()

    # ── Public API ────────────────────────────────────────────────────────────

    def detect_issues(self, df: pd.DataFrame) -> List[ValidationIssue]:
        """
        Scan the entire DataFrame for validation problems.
        Returns a list of ValidationIssue objects — does NOT modify df.
        """
        issues: List[ValidationIssue] = []
        logger.info("Running data validation on %d records...", len(df))

        issues += self._check_missing_values(df)
        issues += self._check_phone_formats(df)
        issues += self._check_email_formats(df)
        issues += self._check_zip_formats(df)
        issues += self._check_state_formats(df)
        issues += self._check_name_formats(df)

        logger.info("Validation found %d issues.", len(issues))
        return issues

    def standardize(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply all standardizations to df and return the corrected copy.
        Each correction is logged to the audit trail.
        """
        df = df.copy()
        logger.info("Applying standardizations to %d records...", len(df))

        df = self._standardize_field(df, "phone",    self.std.phone,    "phone")
        df = self._standardize_field(df, "email",    self.std.email,    "email")
        df = self._standardize_field(df, "zip",      self.std.zip_code, "zip_code")
        df = self._standardize_field(df, "state",    self.std.state,    "state")
        df = self._standardize_field(df, "first_name", self.std.name,   "first_name")
        df = self._standardize_field(df, "last_name",  self.std.name,   "last_name")
        df = self._standardize_field(df, "address1", self.std.address,  "address1")
        df = self._standardize_field(df, "constituent_id",
                                     self.std.constituent_id, "constituent_id")

        logger.info("Standardization complete.")
        return df

    # ── Detection helpers ─────────────────────────────────────────────────────

    def _check_missing_values(self, df: pd.DataFrame) -> List[ValidationIssue]:
        """Flag blank values in required fields."""
        issues = []
        for logical_field in self.settings.required_fields:
            col = self._col(logical_field)
            if col not in df.columns:
                continue
            mask = df[col].apply(is_blank)
            for idx in df[mask].index:
                issues.append(ValidationIssue(
                    row_index=int(idx),
                    field_name=logical_field,
                    issue_type=IssueType.MISSING_VALUE,
                    severity=IssueSeverity.HIGH,
                    current_value=None,
                    message=f"Required field '{logical_field}' is blank.",
                ))
        return issues

    def _check_phone_formats(self, df: pd.DataFrame) -> List[ValidationIssue]:
        issues = []
        col = self._col("phone")
        if col not in df.columns:
            return issues
        for idx, raw in df[col].items():
            if is_blank(raw):
                continue
            standardized, changed = self.std.phone(str(raw))
            if not standardized:
                issues.append(ValidationIssue(
                    row_index=int(idx), field_name="phone",
                    issue_type=IssueType.INVALID_PHONE,
                    severity=IssueSeverity.MEDIUM,
                    current_value=raw,
                    message=f"Cannot parse phone number: '{raw}'",
                ))
            elif changed:
                issues.append(ValidationIssue(
                    row_index=int(idx), field_name="phone",
                    issue_type=IssueType.STANDARDIZATION,
                    severity=IssueSeverity.LOW,
                    current_value=raw, suggested_value=standardized,
                    message=f"Phone format inconsistent. Suggested: {standardized}",
                ))
        return issues

    def _check_email_formats(self, df: pd.DataFrame) -> List[ValidationIssue]:
        """
        Validate email addresses using a RFC-5322 compliant regex.

        WHY NOT USE email-validator LIBRARY HERE?
        The library performs DNS lookups which requires network access and is
        slow at scale. Regex catches the vast majority of format errors.
        Full DNS validation is better suited for a nightly batch job.
        """
        issues = []
        EMAIL_RE = re.compile(
            r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
        )
        col = self._col("email")
        if col not in df.columns:
            return issues
        for idx, raw in df[col].items():
            if is_blank(raw):
                continue
            cleaned = str(raw).strip().lower()
            if not EMAIL_RE.match(cleaned):
                issues.append(ValidationIssue(
                    row_index=int(idx), field_name="email",
                    issue_type=IssueType.INVALID_EMAIL,
                    severity=IssueSeverity.HIGH,
                    current_value=raw,
                    message=f"Invalid email format: '{raw}'",
                ))
        return issues

    def _check_zip_formats(self, df: pd.DataFrame) -> List[ValidationIssue]:
        issues = []
        ZIP_RE = re.compile(self.settings.zip_regex)
        col = self._col("zip")
        if col not in df.columns:
            return issues
        for idx, raw in df[col].items():
            if is_blank(raw):
                continue
            standardized, _ = self.std.zip_code(str(raw))
            if not ZIP_RE.match(standardized):
                issues.append(ValidationIssue(
                    row_index=int(idx), field_name="zip",
                    issue_type=IssueType.INVALID_FORMAT,
                    severity=IssueSeverity.MEDIUM,
                    current_value=raw,
                    message=f"Invalid ZIP code: '{raw}'",
                ))
        return issues

    def _check_state_formats(self, df: pd.DataFrame) -> List[ValidationIssue]:
        issues = []
        col = self._col("state")
        if col not in df.columns:
            return issues
        for idx, raw in df[col].items():
            if is_blank(raw):
                continue
            standardized, changed = self.std.state(str(raw))
            if changed and standardized != str(raw).strip().upper():
                issues.append(ValidationIssue(
                    row_index=int(idx), field_name="state",
                    issue_type=IssueType.STANDARDIZATION,
                    severity=IssueSeverity.LOW,
                    current_value=raw, suggested_value=standardized,
                    message=f"State '{raw}' should be abbreviated to '{standardized}'.",
                ))
        return issues

    def _check_name_formats(self, df: pd.DataFrame) -> List[ValidationIssue]:
        issues = []
        for field in ("first_name", "last_name"):
            col = self._col(field)
            if col not in df.columns:
                continue
            for idx, raw in df[col].items():
                if is_blank(raw):
                    continue
                standardized, changed = self.std.name(str(raw))
                if changed:
                    issues.append(ValidationIssue(
                        row_index=int(idx), field_name=field,
                        issue_type=IssueType.STANDARDIZATION,
                        severity=IssueSeverity.LOW,
                        current_value=raw, suggested_value=standardized,
                        message=f"Name casing issue: '{raw}' → '{standardized}'",
                    ))
        return issues

    # ── Standardization helper ────────────────────────────────────────────────

    def _standardize_field(
        self, df: pd.DataFrame, logical_name: str, method, method_name: str
    ) -> pd.DataFrame:
        """
        Apply a standardizer method to every cell in a logical column.
        Logs changed values to the audit trail.
        """
        col = self._col(logical_name)
        if col not in df.columns:
            return df

        id_col = self._col("constituent_id")

        def transform(row):
            raw = row[col]
            if is_blank(raw):
                return raw
            result, changed = method(str(raw))
            if changed:
                cid = row.get(id_col, "unknown") if id_col in row.index else "unknown"
                self.audit.log_validation_fix(
                    constituent_id=str(cid),
                    field_name=logical_name,
                    old_value=raw,
                    new_value=result,
                    reason=f"Standardizer: {method_name}",
                )
            return result

        df[col] = df.apply(transform, axis=1)
        return df

    def _col(self, logical: str) -> str:
        """Return the logical column name — DataLoader already remapped all headers."""
        return logical
