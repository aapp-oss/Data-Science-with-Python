"""
helpers.py — Small, reusable utility functions shared across modules.

WHY A HELPERS MODULE?
These functions are too general to belong in any one business-logic module
but too small to deserve their own file. Centralizing them prevents the same
3-line function from being copy-pasted into five different files, which is
how subtle divergence bugs are born.
"""

import re
import unicodedata
from typing import Generator, Iterator, Optional
import pandas as pd


def normalize_string(value: Optional[str]) -> str:
    """
    Convert a string to a lowercase, stripped, ASCII-only form for comparison.

    WHY NORMALIZE BEFORE COMPARING?
    "SMITH", "Smith", "smith", and "Smîth" should all compare equal when
    doing name matching. Normalization collapses cosmetic differences so the
    fuzzy matcher focuses on substantive differences.
    """
    if not value or not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return value.strip().lower()


def safe_get(record: dict, key: str, default: str = "") -> str:
    """Return a string value from a dict, guarding against None and NaN."""
    val = record.get(key, default)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val).strip()


def chunk_dataframe(df: pd.DataFrame, size: int) -> Generator[pd.DataFrame, None, None]:
    """
    Yield successive slices of a DataFrame.

    WHY CHUNK LARGE DATASETS?
    Fuzzy matching 100k records against each other would require holding
    all pair combinations in memory. Processing in chunks keeps memory use
    bounded and lets us show progress to the user.
    """
    for start in range(0, len(df), size):
        yield df.iloc[start : start + size]


def is_blank(value) -> bool:
    """Return True if value is None, NaN, or an empty/whitespace string."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def camel_to_words(name: str) -> str:
    """Convert 'CamelCase' column headers to 'Camel Case' for display."""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", name)


def format_confidence(score: float) -> str:
    """Return a human-readable confidence label."""
    if score >= 92:
        return f"{score:.1f}% (High)"
    if score >= 75:
        return f"{score:.1f}% (Medium)"
    return f"{score:.1f}% (Low)"
