"""
settings.py — Central configuration for the Data Quality Manager.

WHY A DEDICATED SETTINGS MODULE?
All thresholds, column mappings, and rules live here so you never need to
hunt through business-logic code to adjust a number. Data engineers call this
"separation of configuration from logic" — it means you can give a settings
file to a non-programmer and they can tune behavior without touching code.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class MergeRule(Enum):
    """Determines which value wins when merging duplicate fields."""
    PREFER_LONGEST = "prefer_longest"        # Keep the longer non-empty string
    PREFER_MOST_RECENT = "prefer_most_recent"  # Keep value from most recently modified record
    PREFER_FIRST = "prefer_first"            # Keep value from the first (master) record
    PREFER_SECOND = "prefer_second"          # Keep value from the second (duplicate) record
    PREFER_NON_EMPTY = "prefer_non_empty"    # Keep whichever field is not blank


@dataclass
class FieldWeight:
    """
    Assigns importance to each field when computing a match confidence score.

    WHY WEIGHTED FIELDS?
    Not all matching fields are equally reliable. A shared email address is
    a much stronger signal than a shared first name. Weights let you express
    that business knowledge so the confidence score reflects real-world
    match quality, not just a simple average.
    """
    field_name: str
    weight: float          # 0.0 – 1.0; weights are normalized internally
    threshold: float = 85.0  # Minimum fuzzy score (0–100) to count as a match


@dataclass
class Settings:
    """
    Master configuration object passed to every module.

    WHY DATACLASSES?
    Python dataclasses give us typed, self-documenting config objects with
    no boilerplate. They're better than plain dicts because IDEs can
    auto-complete the field names and catch typos at write time.
    """

    # ── Column name mapping ──────────────────────────────────────────────────
    # Raiser's Edge exports use specific column headers. Map your actual
    # spreadsheet headers here so the rest of the code is header-agnostic.
    column_map: Dict[str, str] = field(default_factory=lambda: {
        "constituent_id": "Constituent ID",
        "first_name":     "First Name",
        "last_name":      "Last Name",
        "full_name":      "Full Name",
        "email":          "Email Address",
        "phone":          "Phone Number",
        "address1":       "Address Line 1",
        "address2":       "Address Line 2",
        "city":           "City",
        "state":          "State",
        "zip":            "ZIP Code",
        "date_added":     "Date Added",
        "date_modified":  "Date Modified",
        "org_name":       "Organization Name",
        "gift_total":     "Total Gift Amount",
    })

    # ── Duplicate detection thresholds ───────────────────────────────────────
    exact_match_fields: List[str] = field(default_factory=lambda: [
        "email", "phone", "constituent_id"
    ])

    fuzzy_match_weights: List[FieldWeight] = field(default_factory=lambda: [
        FieldWeight("last_name",  weight=0.30, threshold=88.0),
        FieldWeight("first_name", weight=0.20, threshold=85.0),
        FieldWeight("address1",   weight=0.25, threshold=80.0),
        FieldWeight("zip",        weight=0.15, threshold=100.0),
        FieldWeight("email",      weight=0.10, threshold=95.0),
    ])

    # Minimum weighted confidence score (0–100) to flag as a duplicate candidate
    fuzzy_confidence_threshold: float = 75.0

    # Blocking strategy: only compare records within the same block to avoid
    # O(n²) comparisons across 100k records.  "zip" means two records must
    # share the same ZIP before being compared at all.
    blocking_field: str = "zip"

    # ── Merge rules ──────────────────────────────────────────────────────────
    # Set per-field rules; fields not listed use the default.
    default_merge_rule: MergeRule = MergeRule.PREFER_NON_EMPTY
    field_merge_rules: Dict[str, MergeRule] = field(default_factory=lambda: {
        "email":         MergeRule.PREFER_NON_EMPTY,
        "phone":         MergeRule.PREFER_NON_EMPTY,
        "gift_total":    MergeRule.PREFER_MOST_RECENT,
        "date_modified": MergeRule.PREFER_MOST_RECENT,
        "first_name":    MergeRule.PREFER_LONGEST,
        "full_name":     MergeRule.PREFER_LONGEST,
        "address1":      MergeRule.PREFER_LONGEST,
    })

    # Automatically approve merges above this confidence level (skip manual review)
    auto_merge_threshold: float = 92.0

    # ── Validation ───────────────────────────────────────────────────────────
    required_fields: List[str] = field(default_factory=lambda: [
        "constituent_id", "last_name",
    ])
    phone_default_region: str = "US"
    zip_regex: str = r"^\d{5}(-\d{4})?$"

    # ── Output paths ─────────────────────────────────────────────────────────
    output_dir: str = "output"
    log_dir:    str = "logs"
    audit_file: str = "logs/audit_trail.jsonl"

    # ── Performance ──────────────────────────────────────────────────────────
    # Number of records processed per batch during fuzzy matching
    batch_size: int = 5_000

    def get_column(self, logical_name: str) -> Optional[str]:
        """Return the actual spreadsheet header for a logical field name."""
        return self.column_map.get(logical_name)

    def get_weight_map(self) -> Dict[str, FieldWeight]:
        """Return fuzzy weights keyed by logical field name for fast lookup."""
        return {fw.field_name: fw for fw in self.fuzzy_match_weights}
