"""
audit_trail.py — Append-only log of every merge and data change.

WHY AN AUDIT TRAIL?
In donor databases, you may be asked months later: "Why did John Smith's
address change?" An audit trail lets you reconstruct the exact state of a
record before any merge. This is also a compliance requirement in many
nonprofit and healthcare contexts (HIPAA, state regulations).

DESIGN: We use JSON Lines (one JSON object per line) rather than a database.
This means the file is human-readable, easily imported into Excel, and
doesn't require any database server. Each entry is appended — never updated —
so history is immutable.
"""

import json
import logging
import math
import os
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class _AuditEncoder(json.JSONEncoder):
    """
    Custom encoder so audit events can contain pandas Timestamps, NaT, NaN,
    and numpy numeric types without raising TypeError.

    WHY A CUSTOM ENCODER?
    The audit log must store merged_record dicts that come directly from pandas
    DataFrame rows. Those rows contain Timestamp, NaT, and float('nan') values
    that the standard JSON encoder cannot handle. A custom encoder converts them
    to strings or null so the JSONL file stays valid JSON.
    """
    def default(self, obj):
        # pandas / datetime types
        try:
            import pandas as pd
            if isinstance(obj, pd.Timestamp):
                return obj.isoformat() if not pd.isnull(obj) else None
            if obj is pd.NaT:
                return None
        except ImportError:
            pass
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        # numpy numeric types
        try:
            import numpy as np
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return None if math.isnan(float(obj)) else float(obj)
            if isinstance(obj, np.bool_):
                return bool(obj)
        except ImportError:
            pass
        # plain float NaN / inf
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return super().default(obj)


class AuditTrail:
    """
    Appends structured audit events to a .jsonl file.

    A JSONL file has one complete JSON object per line, making it easy to
    stream, grep, and import into Excel/SQL without loading the whole file.
    """

    def __init__(self, file_path: str = "logs/audit_trail.jsonl"):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _write(self, event: Dict[str, Any]) -> None:
        """Append a single JSON event to the audit file."""
        event["session_id"] = self._session_id
        event["timestamp"] = datetime.now().isoformat()
        try:
            with self.file_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, cls=_AuditEncoder) + "\n")
        except OSError as exc:
            logger.error("Audit write failed: %s", exc)

    def log_merge(
        self,
        record_a_id: Optional[str],
        record_b_id: Optional[str],
        merged_record: Dict[str, Any],
        field_sources: Dict[str, str],
        confidence: float,
        approved_by: str = "auto",
    ) -> None:
        """Record that two constituent records were merged."""
        self._write({
            "event":         "MERGE",
            "record_a_id":   record_a_id,
            "record_b_id":   record_b_id,
            "merged_record": merged_record,
            "field_sources": field_sources,
            "confidence":    confidence,
            "approved_by":   approved_by,
        })

    def log_validation_fix(
        self,
        constituent_id: Optional[str],
        field_name: str,
        old_value: Any,
        new_value: Any,
        reason: str,
    ) -> None:
        """Record a data correction applied during standardization."""
        self._write({
            "event":          "VALIDATION_FIX",
            "constituent_id": constituent_id,
            "field":          field_name,
            "old_value":      str(old_value),
            "new_value":      str(new_value),
            "reason":         reason,
        })

    def log_rejection(
        self,
        record_a_id: Optional[str],
        record_b_id: Optional[str],
        reason: str,
        confidence: float,
    ) -> None:
        """Record that a potential duplicate pair was rejected after review."""
        self._write({
            "event":       "MERGE_REJECTED",
            "record_a_id": record_a_id,
            "record_b_id": record_b_id,
            "confidence":  confidence,
            "reason":      reason,
        })

    def log_run_summary(self, stats: Dict[str, Any]) -> None:
        """Record a high-level summary at the end of each analysis run."""
        self._write({"event": "RUN_SUMMARY", **stats})

    def export_to_dataframe(self):
        """Load the entire audit log into a pandas DataFrame for reporting."""
        import pandas as pd
        if not self.file_path.exists():
            return pd.DataFrame()
        records = []
        with self.file_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return pd.DataFrame(records)
