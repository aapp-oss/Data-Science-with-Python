"""
data_loader.py — Reads source files and maps columns to the internal schema.

WHY A DEDICATED LOADER?
Every module downstream of this one expects data in a consistent shape with
known column names. The loader is the single point that translates the
"messy real-world export" (where columns may be named anything) into the
"clean internal format" (where columns have predictable names). This means
only ONE place needs to change when a Blackbaud export format changes.

DESIGN PATTERN: This is the "Adapter" pattern — the loader adapts an external
interface (the CSV/XLSX file) to the internal interface (canonical DataFrame).
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import Settings
from utils.helpers import is_blank

logger = logging.getLogger(__name__)


class DataLoader:
    """
    Loads constituent data from CSV or Excel files into a normalized DataFrame.

    SUPPORTED FORMATS:
    - .csv  (any delimiter; auto-detected)
    - .xlsx / .xls  (first sheet by default; configurable)
    - .txt  (tab-delimited, common for Raiser's Edge exports)
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, file_path: str, sheet_name: Optional[str] = None) -> pd.DataFrame:
        """
        Load a file and return a DataFrame with canonical column names.

        Parameters
        ----------
        file_path  : Path to the source file.
        sheet_name : For Excel files, the sheet to load (default: first sheet).

        Returns
        -------
        pd.DataFrame with columns matching Settings.column_map logical names.
        An extra column `_row_index` preserves the original file row number
        for cross-referencing in reports.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        logger.info("Loading file: %s", path.name)
        raw_df = self._read_file(path, sheet_name)
        logger.info("  Rows loaded: %d  |  Columns: %d", len(raw_df), len(raw_df.columns))

        df = self._remap_columns(raw_df)
        df = self._cast_types(df)
        df["_row_index"] = range(len(df))

        missing_required = self._check_required_columns(df)
        if missing_required:
            logger.warning("Required columns not found in source: %s", missing_required)

        logger.info("  Load complete. %d records ready for processing.", len(df))
        return df

    # ── Private helpers ───────────────────────────────────────────────────────

    def _read_file(self, path: Path, sheet_name: Optional[str]) -> pd.DataFrame:
        """Dispatch to the correct reader based on file extension."""
        suffix = path.suffix.lower()

        if suffix in (".xlsx", ".xls"):
            kwargs = {"sheet_name": sheet_name or 0, "dtype": str}
            return pd.read_excel(path, **kwargs)

        if suffix in (".csv", ".txt"):
            # Try comma first; fall back to tab for RE exports
            try:
                df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
                if len(df.columns) == 1:
                    df = pd.read_csv(path, sep="\t", dtype=str, encoding="utf-8-sig")
            except UnicodeDecodeError:
                df = pd.read_csv(path, dtype=str, encoding="latin-1")
            return df

        raise ValueError(f"Unsupported file format: {suffix}")

    def _remap_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Build a new DataFrame with logical column names.

        The column_map in Settings maps logical names (e.g. "email") to actual
        spreadsheet headers (e.g. "Email Address"). We invert that map here to
        translate actual → logical, then create the standardized DataFrame.

        Columns not in the map are preserved under their original names so no
        data is discarded — we might need them for the audit trail.
        """
        # Build reverse map: "Email Address" → "email"
        reverse_map = {v: k for k, v in self.settings.column_map.items()}

        renamed = df.rename(columns=reverse_map)
        # Fill any logical columns that weren't in the source file with empty strings
        for logical_name in self.settings.column_map:
            if logical_name not in renamed.columns:
                renamed[logical_name] = ""

        return renamed

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert string columns to appropriate types.

        WHY KEEP EVERYTHING AS STRINGS INITIALLY?
        When reading with dtype=str we avoid pandas silently converting
        "00123" to 123 (losing the leading zeros in ZIP codes) or converting
        blank cells to NaN in ways that break string operations. We cast only
        the fields we actually need as non-strings.
        """
        # Numeric cast for gift totals
        if "gift_total" in df.columns:
            df["gift_total"] = pd.to_numeric(
                df["gift_total"].str.replace(r"[$,]", "", regex=True),
                errors="coerce"
            )

        # Date casts — coerce errors so bad dates become NaT (not an error crash)
        for date_col in ("date_added", "date_modified"):
            if date_col in df.columns:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

        # Strip whitespace from all remaining string columns
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].apply(lambda col: col.str.strip())

        return df

    def _check_required_columns(self, df: pd.DataFrame) -> list:
        """Return a list of required logical columns absent from the DataFrame."""
        return [
            col for col in self.settings.required_fields
            if col not in df.columns or df[col].apply(is_blank).all()
        ]

    # ── Convenience factory ───────────────────────────────────────────────────

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
        """
        Wrap a pre-existing DataFrame (e.g. from a test) as if it were loaded.
        Adds _row_index if missing.
        """
        if "_row_index" not in df.columns:
            df = df.copy()
            df["_row_index"] = range(len(df))
        return df
