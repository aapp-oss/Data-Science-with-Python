"""
record_merger.py — Merge duplicate record pairs into a single canonical record.

MERGE PHILOSOPHY:
Merging is irreversible in most CRMs, so this module is deliberately cautious:
  1. Pairs below auto_merge_threshold require human review before merging.
  2. Every merge is recorded in the audit trail with full field-level provenance.
  3. The "master" record (record_a) is kept; record_b is flagged as merged-away.

MERGE RULE APPLICATION:
Field values are resolved one at a time using the rules in Settings.
The default rule (PREFER_NON_EMPTY) is safe for unknown fields because it
never discards data — if either record has a value, that value is kept.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config.settings import MergeRule, Settings
from models.schemas import DuplicatePair, MergeResult
from utils.audit_trail import AuditTrail
from utils.helpers import is_blank

logger = logging.getLogger(__name__)


class RecordMerger:
    """
    Resolves duplicate pairs into merged records and writes audit events.
    """

    def __init__(self, settings: Settings, audit: AuditTrail):
        self.settings = settings
        self.audit = audit

    # ── Public API ────────────────────────────────────────────────────────────

    def review_and_merge(
        self,
        df: pd.DataFrame,
        pairs: List[DuplicatePair],
        interactive: bool = False,
    ) -> Tuple[pd.DataFrame, List[MergeResult]]:
        """
        Process duplicate pairs according to configured thresholds.

        Parameters
        ----------
        df          : The full constituent DataFrame.
        pairs       : DuplicatePair list from DuplicateDetector.
        interactive : If True, prompt the user to approve medium-confidence pairs.

        Returns
        -------
        merged_df    : DataFrame with duplicates removed / merged.
        merge_results: List of MergeResult audit objects.
        """
        merge_results: List[MergeResult] = []
        rows_to_drop: List[int] = []
        df = df.copy()

        auto_pairs   = [p for p in pairs if p.confidence_score >= self.settings.auto_merge_threshold]
        review_pairs = [p for p in pairs if p.confidence_score <  self.settings.auto_merge_threshold]

        logger.info(
            "Merging: %d auto-approve, %d need review.", len(auto_pairs), len(review_pairs)
        )

        # Auto-merge high-confidence pairs
        for pair in auto_pairs:
            result = self._merge_pair(df, pair, approved_by="auto")
            if result:
                merge_results.append(result)
                df.loc[pair.record_a_index] = pd.Series(result.merged_record)
                rows_to_drop.append(pair.record_b_index)

        # Interactive review for medium-confidence pairs
        if interactive:
            for pair in review_pairs:
                if self._prompt_user_review(df, pair):
                    result = self._merge_pair(df, pair, approved_by="manual")
                    if result:
                        merge_results.append(result)
                        df.loc[pair.record_a_index] = pd.Series(result.merged_record)
                        rows_to_drop.append(pair.record_b_index)
                else:
                    self.audit.log_rejection(
                        pair.record_a_id, pair.record_b_id,
                        reason="User rejected during interactive review",
                        confidence=pair.confidence_score,
                    )
        else:
            # Non-interactive: mark for manual review but don't merge
            for pair in review_pairs:
                pair.reviewed = False
                logger.debug(
                    "Pair (%s, %s) queued for manual review (score=%.1f).",
                    pair.record_a_id, pair.record_b_id, pair.confidence_score
                )

        # Drop the merged-away records
        merged_df = df.drop(index=list(set(rows_to_drop)), errors="ignore").reset_index(drop=True)
        logger.info(
            "Merge complete: %d records merged, %d removed from dataset.",
            len(merge_results), len(set(rows_to_drop))
        )
        return merged_df, merge_results

    def export_pending_review(
        self, df: pd.DataFrame, pairs: List[DuplicatePair], output_path: str
    ) -> None:
        """
        Export pairs that need manual review to an Excel file for offline approval.

        WHY EXPORT FOR REVIEW?
        Not every data manager wants to sit at a terminal typing Y/N. An Excel
        file lets the team review duplicates at their own pace, add notes, and
        return a signed-off list that can be re-imported.
        """
        import openpyxl
        from openpyxl.styles import PatternFill, Font

        review_pairs = [
            p for p in pairs
            if p.confidence_score < self.settings.auto_merge_threshold
        ]
        if not review_pairs:
            logger.info("No pairs require manual review.")
            return

        rows = []
        for pair in review_pairs:
            row_a = df.loc[pair.record_a_index] if pair.record_a_index in df.index else {}
            row_b = df.loc[pair.record_b_index] if pair.record_b_index in df.index else {}
            rows.append({
                "Confidence Score":    pair.confidence_score,
                "Match Type":          pair.match_type,
                "Field Scores":        str(pair.field_scores),
                "Record A - ID":       pair.record_a_id or "",
                "Record A - Name":     f"{row_a.get('first_name','')} {row_a.get('last_name','')}".strip(),
                "Record A - Email":    row_a.get("email", ""),
                "Record A - Phone":    row_a.get("phone", ""),
                "Record A - Address":  row_a.get("address1", ""),
                "Record B - ID":       pair.record_b_id or "",
                "Record B - Name":     f"{row_b.get('first_name','')} {row_b.get('last_name','')}".strip(),
                "Record B - Email":    row_b.get("email", ""),
                "Record B - Phone":    row_b.get("phone", ""),
                "Record B - Address":  row_b.get("address1", ""),
                "Approve? (Y/N)":      "",
                "Notes":               "",
            })

        pd.DataFrame(rows).to_excel(output_path, index=False)
        logger.info("Manual review file written to: %s", output_path)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _merge_pair(
        self,
        df: pd.DataFrame,
        pair: DuplicatePair,
        approved_by: str,
    ) -> Optional[MergeResult]:
        """
        Apply merge rules field-by-field and produce a MergeResult.
        record_a is the "master" that survives; record_b is the "duplicate" removed.
        """
        if pair.record_a_index not in df.index or pair.record_b_index not in df.index:
            logger.warning("Merge skipped — index no longer in DataFrame.")
            return None

        row_a = df.loc[pair.record_a_index].to_dict()
        row_b = df.loc[pair.record_b_index].to_dict()

        merged: Dict[str, Any] = {}
        field_sources: Dict[str, str] = {}

        for col in df.columns:
            val_a = row_a.get(col)
            val_b = row_b.get(col)
            rule = self.settings.field_merge_rules.get(col, self.settings.default_merge_rule)
            merged[col], source = self._apply_rule(col, val_a, val_b, rule, row_a, row_b)
            field_sources[col] = source

        result = MergeResult(
            merged_record=merged,
            source_a_index=pair.record_a_index,
            source_b_index=pair.record_b_index,
            source_a_id=pair.record_a_id,
            source_b_id=pair.record_b_id,
            field_sources=field_sources,
            merged_at=datetime.now(),
            approved_by=approved_by,
        )
        self.audit.log_merge(
            pair.record_a_id, pair.record_b_id,
            merged_record=merged,
            field_sources=field_sources,
            confidence=pair.confidence_score,
            approved_by=approved_by,
        )
        logger.debug(
            "Merged %s + %s → kept record_a. Approved by: %s",
            pair.record_a_id, pair.record_b_id, approved_by
        )
        return result

    def _apply_rule(
        self,
        col: str,
        val_a: Any,
        val_b: Any,
        rule: MergeRule,
        row_a: dict,
        row_b: dict,
    ) -> Tuple[Any, str]:
        """
        Apply a single merge rule and return (chosen_value, source_label).

        WHY RETURN A SOURCE LABEL?
        The audit trail needs to know "this phone number came from record B"
        so that if a merge is ever questioned, the reviewer can trace it.
        """
        blank_a = is_blank(str(val_a) if val_a is not None else "")
        blank_b = is_blank(str(val_b) if val_b is not None else "")

        if blank_a and blank_b:
            return ("", "neither")
        if blank_a:
            return (val_b, "record_b")
        if blank_b:
            return (val_a, "record_a")

        # Both have values — apply the rule
        if rule == MergeRule.PREFER_FIRST or rule == MergeRule.PREFER_NON_EMPTY:
            return (val_a, "record_a")

        if rule == MergeRule.PREFER_SECOND:
            return (val_b, "record_b")

        if rule == MergeRule.PREFER_LONGEST:
            if len(str(val_a)) >= len(str(val_b)):
                return (val_a, "record_a")
            return (val_b, "record_b")

        if rule == MergeRule.PREFER_MOST_RECENT:
            date_a = row_a.get("date_modified")
            date_b = row_b.get("date_modified")
            if pd.notna(date_a) and pd.notna(date_b):
                return (val_a, "record_a") if date_a >= date_b else (val_b, "record_b")
            return (val_a, "record_a")  # fallback

        return (val_a, "record_a")  # safe default

    def _prompt_user_review(self, df: pd.DataFrame, pair: DuplicatePair) -> bool:
        """
        Display a side-by-side comparison and ask the user to approve/reject.
        Only called in interactive mode.
        """
        row_a = df.loc[pair.record_a_index] if pair.record_a_index in df.index else {}
        row_b = df.loc[pair.record_b_index] if pair.record_b_index in df.index else {}

        display_fields = ["constituent_id", "first_name", "last_name", "email", "phone", "address1", "zip"]
        print("\n" + "="*70)
        print(f"POTENTIAL DUPLICATE — Confidence: {pair.confidence_score:.1f}%")
        print(f"Field scores: {pair.field_scores}")
        print(f"{'Field':<20} {'Record A':^24} {'Record B':^24}")
        print("-"*70)
        for field in display_fields:
            col = self.settings.get_column(field) or field
            v_a = str(row_a.get(col, row_a.get(field, ""))).strip()[:22]
            v_b = str(row_b.get(col, row_b.get(field, ""))).strip()[:22]
            print(f"  {field:<18} {v_a:<24} {v_b:<24}")
        print("="*70)

        while True:
            answer = input("Merge these records? [y/n/q to quit]: ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("q", "quit"):
                raise KeyboardInterrupt("User exited review.")
