"""
duplicate_detector.py — Find exact and fuzzy duplicate constituent records.

THE CORE ALGORITHMIC CHALLENGE:
Comparing every record against every other record is O(n²). For 100,000 records
that's 5 billion comparisons — too slow. Professional deduplication tools solve
this with BLOCKING: first group records into "blocks" where duplicates are
likely to appear (e.g., same ZIP code), then only compare within each block.
This reduces comparisons from O(n²) to roughly O(n * avg_block_size).

TWO-PHASE APPROACH:
Phase 1 — EXACT matching: fast hash-based lookup. If two records share the
           same email or constituent ID, they are very likely duplicates.
Phase 2 — FUZZY matching: RapidFuzz string similarity within each block.
           Weighted field scores are combined into a single confidence score.
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd
from rapidfuzz import fuzz

from config.settings import Settings
from models.schemas import DuplicatePair
from utils.helpers import normalize_string, is_blank

logger = logging.getLogger(__name__)


class DuplicateDetector:
    """
    Detects exact and fuzzy duplicate records in a constituent DataFrame.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._weight_map = settings.get_weight_map()

    # ── Public API ────────────────────────────────────────────────────────────

    def detect_all(self, df: pd.DataFrame) -> List[DuplicatePair]:
        """
        Run both exact and fuzzy detection and return a combined list of pairs.
        Deduplicates so the same pair is never returned twice.
        """
        logger.info("Starting duplicate detection on %d records...", len(df))
        exact_pairs  = self.detect_exact(df)
        fuzzy_pairs  = self.detect_fuzzy(df)

        # Merge: skip any fuzzy pair whose index pair already appears in exact
        exact_index_pairs = {
            (p.record_a_index, p.record_b_index) for p in exact_pairs
        }
        unique_fuzzy = [
            p for p in fuzzy_pairs
            if (p.record_a_index, p.record_b_index) not in exact_index_pairs
        ]
        all_pairs = exact_pairs + unique_fuzzy
        logger.info(
            "Detection complete: %d exact, %d fuzzy unique pairs found.",
            len(exact_pairs), len(unique_fuzzy)
        )
        return all_pairs

    def detect_exact(self, df: pd.DataFrame) -> List[DuplicatePair]:
        """
        Find records that share an exact value in any of the exact_match_fields.

        WHY EXACT FIRST?
        Exact matches are 100% reliable signals and computationally cheap.
        Finding them first also lets us skip those pairs during fuzzy matching.
        """
        pairs: List[DuplicatePair] = []
        seen_pairs = set()

        for logical_field in self.settings.exact_match_fields:
            col = self.settings.get_column(logical_field) or logical_field
            actual_col = col if col in df.columns else (logical_field if logical_field in df.columns else None)
            if actual_col is None:
                continue

            # Group by the field value; any group with >1 member has duplicates
            non_blank = df[df[actual_col].apply(lambda v: not is_blank(v))]
            grouped = non_blank.groupby(actual_col)

            for value, group in grouped:
                if len(group) < 2:
                    continue
                indices = list(group.index)
                for i in range(len(indices)):
                    for j in range(i + 1, len(indices)):
                        idx_a, idx_b = indices[i], indices[j]
                        key = (min(idx_a, idx_b), max(idx_a, idx_b))
                        if key in seen_pairs:
                            continue
                        seen_pairs.add(key)
                        pairs.append(DuplicatePair(
                            record_a_index=idx_a,
                            record_b_index=idx_b,
                            record_a_id=self._get_id(df, idx_a),
                            record_b_id=self._get_id(df, idx_b),
                            match_type="exact",
                            confidence_score=100.0,
                            field_scores={logical_field: 100.0},
                        ))

        logger.debug("Exact duplicates found: %d", len(pairs))
        return pairs

    def detect_fuzzy(self, df: pd.DataFrame) -> List[DuplicatePair]:
        """
        Find fuzzy duplicate pairs using blocking + weighted field similarity.

        BLOCKING STRATEGY:
        We only compare records within the same ZIP code block. Two records in
        different ZIP codes could still be duplicates (data entry error), but
        ZIP-blocking eliminates ~99% of unnecessary comparisons at the cost of
        very few missed duplicates. A real production system might add a second
        pass with name-initial blocking to catch cross-ZIP duplicates.
        """
        pairs: List[DuplicatePair] = []
        # Try the mapped column name first, then fall back to the logical name.
        # After DataLoader, columns are in logical form, so the fallback is typical.
        blocking_col = self.settings.get_column(self.settings.blocking_field)
        if blocking_col not in df.columns:
            blocking_col = self.settings.blocking_field
        if blocking_col not in df.columns:
            blocking_col = None

        if blocking_col:
            blocks = self._build_blocks(df, blocking_col)
        else:
            # No blocking column available — compare everything (slow for large sets)
            logger.warning("No blocking column found; comparing all records (may be slow).")
            blocks = {"ALL": list(df.index)}

        total_comparisons = 0
        for block_key, indices in blocks.items():
            if len(indices) < 2:
                continue
            block_df = df.loc[indices]
            block_pairs = self._compare_block(block_df)
            pairs.extend(block_pairs)
            total_comparisons += len(indices) * (len(indices) - 1) // 2

        logger.debug(
            "Fuzzy matching: %d blocks, %d comparisons, %d pairs above threshold.",
            len(blocks), total_comparisons, len(pairs)
        )
        return pairs

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_blocks(self, df: pd.DataFrame, col: str) -> Dict[str, List]:
        """Group record indices by the value of the blocking column."""
        blocks: Dict[str, List] = defaultdict(list)
        for idx, row in df.iterrows():
            key = normalize_string(str(row.get(col, ""))) or "__NO_VALUE__"
            blocks[key].append(idx)
        return dict(blocks)

    def _compare_block(self, block_df: pd.DataFrame) -> List[DuplicatePair]:
        """Compare every pair within a block and return those above threshold."""
        pairs = []
        indices = list(block_df.index)
        rows = {idx: block_df.loc[idx].to_dict() for idx in indices}

        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                idx_a, idx_b = indices[i], indices[j]
                score, breakdown = self._compute_similarity(rows[idx_a], rows[idx_b])
                if score >= self.settings.fuzzy_confidence_threshold:
                    pairs.append(DuplicatePair(
                        record_a_index=idx_a,
                        record_b_index=idx_b,
                        record_a_id=self._get_id_from_row(rows[idx_a]),
                        record_b_id=self._get_id_from_row(rows[idx_b]),
                        match_type="fuzzy",
                        confidence_score=round(score, 2),
                        field_scores=breakdown,
                    ))
        return pairs

    def _compute_similarity(
        self, row_a: dict, row_b: dict
    ) -> Tuple[float, Dict[str, float]]:
        """
        Compute a weighted similarity score between two rows.

        HOW WEIGHTED SCORING WORKS:
        For each field with a configured weight, we compute a 0–100 fuzzy
        similarity score using RapidFuzz. We then multiply that score by the
        field's weight and sum the results. The final score is on a 0–100 scale.
        Fields with blank values in either record contribute 0 to the score but
        their weight is still counted in the denominator, so missing data
        naturally lowers the overall confidence.
        """
        total_weight = sum(fw.weight for fw in self.settings.fuzzy_match_weights)
        weighted_sum = 0.0
        breakdown: Dict[str, float] = {}

        for fw in self.settings.fuzzy_match_weights:
            col = self.settings.get_column(fw.field_name) or fw.field_name
            val_a = normalize_string(str(row_a.get(col, "") or row_a.get(fw.field_name, "")))
            val_b = normalize_string(str(row_b.get(col, "") or row_b.get(fw.field_name, "")))

            if not val_a or not val_b:
                field_score = 0.0
            else:
                # token_sort_ratio handles word-order differences ("John Smith" vs "Smith John")
                field_score = fuzz.token_sort_ratio(val_a, val_b)

            breakdown[fw.field_name] = round(field_score, 1)
            weighted_sum += field_score * fw.weight

        composite = (weighted_sum / total_weight) if total_weight > 0 else 0.0
        return composite, breakdown

    def _get_id(self, df: pd.DataFrame, idx: int) -> Optional[str]:
        row = df.loc[idx]
        col = self.settings.get_column("constituent_id") or "constituent_id"
        val = row.get(col) or row.get("constituent_id")
        return str(val) if val and not is_blank(str(val)) else None

    def _get_id_from_row(self, row: dict) -> Optional[str]:
        col = self.settings.get_column("constituent_id") or "constituent_id"
        val = row.get(col) or row.get("constituent_id")
        return str(val) if val and not is_blank(str(val)) else None
