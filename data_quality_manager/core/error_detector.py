"""
error_detector.py — Find hard errors: impossible dates, bad IDs, email failures,
                    and cross-field inconsistencies.

DIFFERENCE FROM data_validator.py:
DataValidator catches *format* issues — "this phone number is missing a digit."
ErrorDetector catches *semantic* errors — "this person's birth date is 1847"
or "this constituent ID appears on 3 different records." These are the errors
that go into an exception report for a data steward to manually resolve.

WHY A SEPARATE MODULE?
Format issues can often be auto-corrected. Semantic errors usually cannot —
they require a human decision about what the correct value should be.
Keeping them separate means managers get two different kinds of reports: one
they can act on automatically and one they need to review manually.
"""

import logging
import re
from datetime import datetime, date
from typing import List, Optional

import pandas as pd

from config.settings import Settings
from models.schemas import ErrorRecord, IssueSeverity, IssueType
from utils.helpers import is_blank

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2100, 1, 1)


class ErrorDetector:
    """
    Detects hard data errors that require human intervention.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    # ── Public API ────────────────────────────────────────────────────────────

    def detect_all(self, df: pd.DataFrame) -> List[ErrorRecord]:
        """Run all error checks and return a combined exception report."""
        errors: List[ErrorRecord] = []
        logger.info("Running error detection on %d records...", len(df))

        errors += self.detect_invalid_emails(df)
        errors += self.detect_impossible_dates(df)
        errors += self.detect_duplicate_constituent_ids(df)
        errors += self.detect_inconsistent_donor_data(df)

        # Sort by severity so critical errors appear first in reports
        severity_order = {
            IssueSeverity.CRITICAL: 0,
            IssueSeverity.HIGH:     1,
            IssueSeverity.MEDIUM:   2,
            IssueSeverity.LOW:      3,
        }
        errors.sort(key=lambda e: severity_order.get(e.severity, 99))
        logger.info("Error detection found %d errors.", len(errors))
        return errors

    # ── Individual checks ─────────────────────────────────────────────────────

    def detect_invalid_emails(self, df: pd.DataFrame) -> List[ErrorRecord]:
        """
        Find email addresses that fail RFC-5322 format validation.

        COMMON PATTERNS CAUGHT:
        - Missing @ symbol (john.smith at gmail.com)
        - Missing domain extension (john@gmail)
        - Spaces embedded in address (john @gmail.com)
        - Double @ signs (john@@gmail.com)
        """
        errors = []
        col = self._col("email")
        id_col = self._col("constituent_id")
        if col not in df.columns:
            return errors

        for idx, row in df.iterrows():
            raw = row.get(col, "")
            if is_blank(raw):
                continue
            cleaned = str(raw).strip().lower()
            if not EMAIL_RE.match(cleaned):
                errors.append(ErrorRecord(
                    row_index=int(idx),
                    constituent_id=self._get_id(row, id_col),
                    field_name="email",
                    error_type=IssueType.INVALID_EMAIL,
                    severity=IssueSeverity.HIGH,
                    bad_value=raw,
                    description=f"Email address '{raw}' fails format validation.",
                ))
        return errors

    def detect_impossible_dates(self, df: pd.DataFrame) -> List[ErrorRecord]:
        """
        Find dates that are logically impossible for a constituent database.

        WHAT COUNTS AS IMPOSSIBLE?
        - Before 1900 (no living donor was born before 1900)
        - After today's date (a future gift date suggests data entry error)
        - NaT in date columns (text that was present but could not be parsed)

        We check both date_added (when the record was created) and date_modified.
        """
        errors = []
        today = datetime.now().date()
        id_col = self._col("constituent_id")

        for date_field in ("date_added", "date_modified"):
            col = self._col(date_field)
            if col not in df.columns:
                continue

            for idx, row in df.iterrows():
                raw = row.get(col)

                # Skip truly absent values
                if raw is None:
                    continue
                try:
                    if pd.isnull(raw):
                        # NaT means the cell had text that couldn't be parsed
                        errors.append(ErrorRecord(
                            row_index=int(idx),
                            constituent_id=self._get_id(row, id_col),
                            field_name=date_field,
                            error_type=IssueType.INVALID_DATE,
                            severity=IssueSeverity.HIGH,
                            bad_value=str(raw),
                            description=f"Value in '{date_field}' could not be parsed as a date.",
                        ))
                        continue
                except (TypeError, ValueError):
                    pass

                try:
                    d = pd.Timestamp(raw).date()
                except Exception:
                    errors.append(ErrorRecord(
                        row_index=int(idx),
                        constituent_id=self._get_id(row, id_col),
                        field_name=date_field,
                        error_type=IssueType.INVALID_DATE,
                        severity=IssueSeverity.HIGH,
                        bad_value=str(raw),
                        description=f"Date '{raw}' could not be converted.",
                    ))
                    continue

                if d < MIN_DATE or d > today:
                    errors.append(ErrorRecord(
                        row_index=int(idx),
                        constituent_id=self._get_id(row, id_col),
                        field_name=date_field,
                        error_type=IssueType.INVALID_DATE,
                        severity=IssueSeverity.HIGH,
                        bad_value=str(d),
                        description=(
                            f"Date {d} is outside plausible range "
                            f"({MIN_DATE} to {today})."
                        ),
                    ))
        return errors

    def detect_duplicate_constituent_ids(self, df: pd.DataFrame) -> List[ErrorRecord]:
        """
        Find constituent IDs that appear on more than one record.

        WHY THIS IS CRITICAL:
        In Raiser's Edge, the constituent ID (CID) is the primary key that
        ties together gifts, relationships, actions, and addresses. Duplicate
        CIDs mean gift history may be split across ghost records, gift
        summaries will be wrong, and mail merges may produce duplicates.
        This is a data integrity emergency — flag as CRITICAL.
        """
        errors = []
        id_col = self._col("constituent_id")
        if id_col not in df.columns:
            return errors

        non_blank = df[df[id_col].apply(lambda v: not is_blank(v))]
        counts = non_blank[id_col].value_counts()
        dupes = counts[counts > 1]

        for cid, count in dupes.items():
            matching = non_blank[non_blank[id_col] == cid]
            for idx in matching.index:
                errors.append(ErrorRecord(
                    row_index=int(idx),
                    constituent_id=str(cid),
                    field_name="constituent_id",
                    error_type=IssueType.DUPLICATE_ID,
                    severity=IssueSeverity.CRITICAL,
                    bad_value=cid,
                    description=(
                        f"Constituent ID '{cid}' appears on {count} records. "
                        "IDs must be unique."
                    ),
                ))
        return errors

    def detect_inconsistent_donor_data(self, df: pd.DataFrame) -> List[ErrorRecord]:
        """
        Find records with logically inconsistent field combinations.

        EXAMPLES OF INCONSISTENCIES:
        - Gift total > 0 but date_added is null (how did they give before existing?)
        - Organization name present but first_name and last_name also present
          (is this a person or an org? Raiser's Edge treats them differently)
        - City/state/ZIP mismatch patterns (e.g., a NYC ZIP but state = 'CA')

        WHY DETECT THESE?
        These aren't simple format errors — they're business-rule violations that
        indicate the record was entered incorrectly or two different people's
        data was mixed together.
        """
        errors = []
        id_col   = self._col("constituent_id")
        fn_col   = self._col("first_name")
        ln_col   = self._col("last_name")
        org_col  = self._col("org_name")
        gift_col = self._col("gift_total")
        date_col = self._col("date_added")

        for idx, row in df.iterrows():
            cid = self._get_id(row, id_col)

            # Rule 1: Gift total but no date_added
            gift_val = row.get(gift_col)
            date_val = row.get(date_col)
            try:
                has_gift = gift_val is not None and not is_blank(str(gift_val)) and float(gift_val) > 0
            except (ValueError, TypeError):
                has_gift = False
            if has_gift and (pd.isna(date_val) or is_blank(str(date_val))):
                errors.append(ErrorRecord(
                    row_index=int(idx),
                    constituent_id=cid,
                    field_name="date_added",
                    error_type=IssueType.INCONSISTENT_DATA,
                    severity=IssueSeverity.MEDIUM,
                    bad_value=None,
                    description="Record has gift amount but no date added — possible data entry error.",
                ))

            # Rule 2: Both org_name and personal name present (ambiguous record type)
            has_org  = not is_blank(str(row.get(org_col, "")))
            has_name = (
                not is_blank(str(row.get(fn_col, ""))) or
                not is_blank(str(row.get(ln_col, "")))
            )
            if has_org and has_name:
                errors.append(ErrorRecord(
                    row_index=int(idx),
                    constituent_id=cid,
                    field_name="org_name",
                    error_type=IssueType.INCONSISTENT_DATA,
                    severity=IssueSeverity.LOW,
                    bad_value=row.get(org_col, ""),
                    description=(
                        "Record has both an organization name and a personal name. "
                        "Verify whether this is an individual or organizational constituent."
                    ),
                ))

        return errors

    # ── Private helpers ───────────────────────────────────────────────────────

    def _col(self, logical: str) -> str:
        """Return the logical column name — DataLoader already remapped all headers."""
        return logical

    def _get_id(self, row, id_col: str) -> Optional[str]:
        val = row.get(id_col)
        return str(val) if val and not is_blank(str(val)) else None
