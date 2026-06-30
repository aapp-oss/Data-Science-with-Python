"""
schemas.py — Data models (schemas) for the entire application.

WHY EXPLICIT DATA MODELS?
When data flows through many modules (loader → validator → deduplicator →
merger → reporter), it's easy for bugs to hide in "shape mismatches" — one
module puts data in a dict with key "conf_score" and another reads "score".
Explicit dataclass models make the expected shape a contract enforced by
Python itself. If you change a field name, every callsite breaks loudly
instead of silently returning None.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class IssueSeverity(Enum):
    """Traffic-light severity so reports can filter by urgency."""
    CRITICAL = "CRITICAL"   # Must fix before merge (e.g., duplicate constituent ID)
    HIGH     = "HIGH"       # Likely data error (invalid email, impossible date)
    MEDIUM   = "MEDIUM"     # Formatting issue (phone not standardized)
    LOW      = "LOW"        # Advisory / cosmetic (missing optional field)


class IssueType(Enum):
    """Categorizes what kind of problem was found."""
    MISSING_VALUE      = "Missing Value"
    INVALID_FORMAT     = "Invalid Format"
    DUPLICATE_ID       = "Duplicate Constituent ID"
    FUZZY_DUPLICATE    = "Fuzzy Duplicate"
    EXACT_DUPLICATE    = "Exact Duplicate"
    INVALID_EMAIL      = "Invalid Email"
    INVALID_PHONE      = "Invalid Phone"
    INVALID_DATE       = "Invalid Date"
    INCONSISTENT_DATA  = "Inconsistent Data"
    STANDARDIZATION    = "Needs Standardization"


@dataclass
class ConstituentRecord:
    """
    A single constituent (donor/contact) record.

    WHY NOT JUST USE A PANDAS ROW?
    Pandas rows are generic — they have no field names baked in. A dataclass
    makes each field explicit, so you get IDE auto-complete, type hints, and
    documentation all in one place. We convert DataFrames to/from these
    objects only at module boundaries.
    """
    row_index:      int
    constituent_id: Optional[str]
    first_name:     Optional[str]
    last_name:      Optional[str]
    full_name:      Optional[str]
    email:          Optional[str]
    phone:          Optional[str]
    address1:       Optional[str]
    address2:       Optional[str]
    city:           Optional[str]
    state:          Optional[str]
    zip_code:       Optional[str]
    date_added:     Optional[datetime]
    date_modified:  Optional[datetime]
    org_name:       Optional[str]
    gift_total:     Optional[float]
    raw_data:       Dict[str, Any] = field(default_factory=dict)

    def display_name(self) -> str:
        if self.full_name:
            return self.full_name
        parts = filter(None, [self.first_name, self.last_name])
        return " ".join(parts) or "(no name)"


@dataclass
class DuplicatePair:
    """
    Represents two records believed to be duplicates of each other.

    WHY STORE BOTH THE RAW SCORE AND BREAKDOWN?
    The breakdown (per-field similarity scores) lets a human reviewer
    understand *why* the algorithm flagged these two records. Without it,
    "78% match" means nothing actionable.
    """
    record_a_index:     int
    record_b_index:     int
    record_a_id:        Optional[str]
    record_b_id:        Optional[str]
    match_type:         str          # "exact" | "fuzzy"
    confidence_score:   float        # 0–100
    field_scores:       Dict[str, float] = field(default_factory=dict)
    reviewed:           bool = False
    approved_for_merge: bool = False
    notes:              str  = ""

    def is_high_confidence(self, threshold: float = 92.0) -> bool:
        return self.confidence_score >= threshold


@dataclass
class MergeResult:
    """
    The outcome of merging two duplicate records into one.

    WHY STORE field_sources?
    Audit trails in data management must answer "where did this value come
    from?" field_sources records exactly that so you can reconstruct the
    pre-merge state if needed.
    """
    merged_record:  Dict[str, Any]
    source_a_index: int
    source_b_index: int
    source_a_id:    Optional[str]
    source_b_id:    Optional[str]
    field_sources:  Dict[str, str]   # field_name → "record_a" | "record_b" | "both"
    merged_at:      datetime = field(default_factory=datetime.now)
    approved_by:    str = "auto"


@dataclass
class ValidationIssue:
    """A single data quality issue found on a specific record + field."""
    row_index:   int
    field_name:  str
    issue_type:  IssueType
    severity:    IssueSeverity
    current_value: Any
    suggested_value: Optional[Any] = None
    message:     str = ""


@dataclass
class ErrorRecord:
    """
    A record that failed a hard validation check (invalid email, bad date, etc.)
    These go into the exception report.
    """
    row_index:      int
    constituent_id: Optional[str]
    field_name:     str
    error_type:     IssueType
    severity:       IssueSeverity
    bad_value:      Any
    description:    str


@dataclass
class QualityReport:
    """
    Top-level summary produced after a full analysis run.
    This is the "dashboard" data structure that the ReportGenerator renders.
    """
    total_records:          int = 0
    exact_duplicates:       int = 0
    fuzzy_duplicates:       int = 0
    records_merged:         int = 0
    missing_value_issues:   int = 0
    format_issues:          int = 0
    invalid_emails:         int = 0
    invalid_phones:         int = 0
    invalid_dates:          int = 0
    duplicate_ids:          int = 0
    overall_quality_score:  float = 0.0   # 0–100; higher is better
    run_timestamp:          datetime = field(default_factory=datetime.now)
    validation_issues:      List[ValidationIssue] = field(default_factory=list)
    error_records:          List[ErrorRecord]      = field(default_factory=list)
    duplicate_pairs:        List[DuplicatePair]    = field(default_factory=list)
    merge_results:          List[MergeResult]      = field(default_factory=list)

    def compute_quality_score(self) -> float:
        """
        Returns a 0–100 score representing overall data quality.
        Weighted penalty formula: critical errors hurt more than minor ones.
        Stores the result and returns it.
        """
        if self.total_records == 0:
            self.overall_quality_score = 0.0
            return 0.0

        n = self.total_records
        penalties = (
            (self.exact_duplicates    * 5.0) +
            (self.fuzzy_duplicates    * 2.0) +
            (self.invalid_emails      * 3.0) +
            (self.invalid_phones      * 2.0) +
            (self.invalid_dates       * 4.0) +
            (self.duplicate_ids       * 5.0) +
            (self.missing_value_issues * 1.5) +
            (self.format_issues       * 0.5)
        )
        raw = max(0.0, 100.0 - (penalties / n) * 10)
        self.overall_quality_score = round(min(100.0, raw), 1)
        return self.overall_quality_score
