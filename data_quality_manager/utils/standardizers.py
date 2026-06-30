"""
standardizers.py — Transform raw field values into a consistent, canonical form.

WHY STANDARDIZE BEFORE MATCHING OR REPORTING?
"(555) 867-5309", "555.867.5309", and "5558675309" are the same phone number
but look completely different to string-comparison code. Standardizing first
means the matching logic only has to deal with one canonical form, which
dramatically reduces false negatives (missed duplicates).

Each method returns (standardized_value, was_changed) so the caller can
decide whether to log a data-correction event in the audit trail.
"""

import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Mapping from common state abbreviations and full names → 2-letter code.
# Keeping this inline (rather than importing a library) makes the code
# dependency-free for this specific lookup and easy to customize.
STATE_MAP = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}


class Standardizer:
    """
    Stateless collection of field-level standardization methods.

    STATELESS DESIGN: Methods only transform values — they don't read from or
    write to any external state. This makes them trivial to unit-test and safe
    to call from multiple threads.
    """

    # ── Phone numbers ─────────────────────────────────────────────────────────

    @staticmethod
    def phone(raw: Optional[str], region: str = "US") -> Tuple[str, bool]:
        """
        Attempt to parse and reformat a phone number as (###) ###-####.
        Returns (result, was_changed).

        WHY USE phonenumbers LIBRARY?
        It handles edge cases like extensions, international prefixes, and
        toll-free numbers that regex alone cannot reliably handle.
        """
        if not raw or not str(raw).strip():
            return ("", False)
        original = str(raw).strip()
        try:
            import phonenumbers
            parsed = phonenumbers.parse(original, region)
            if phonenumbers.is_valid_number(parsed):
                formatted = phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.NATIONAL
                )
                return (formatted, formatted != original)
        except Exception:
            pass
        # Fallback: strip non-digits and reformat if exactly 10 digits
        digits = re.sub(r"\D", "", original)
        if len(digits) == 10:
            formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
            return (formatted, formatted != original)
        return (original, False)

    # ── Email ─────────────────────────────────────────────────────────────────

    @staticmethod
    def email(raw: Optional[str]) -> Tuple[str, bool]:
        """Lowercase and strip whitespace from email addresses."""
        if not raw or not str(raw).strip():
            return ("", False)
        original = str(raw)          # preserve raw before any transformation
        cleaned = original.strip().lower()
        return (cleaned, cleaned != original)

    # ── Names ─────────────────────────────────────────────────────────────────

    @staticmethod
    def name(raw: Optional[str]) -> Tuple[str, bool]:
        """
        Title-case a name and strip extra whitespace.
        Handles hyphenated and apostrophe names (O'Brien, Smith-Jones).
        """
        if not raw or not str(raw).strip():
            return ("", False)
        original = str(raw).strip()
        # Collapse multiple spaces
        cleaned = re.sub(r"\s+", " ", original)
        # Title-case each word, preserving hyphens and apostrophes
        cleaned = re.sub(
            r"[A-Za-z']+(?:-[A-Za-z']+)*",
            lambda m: "-".join(w.capitalize() for w in m.group().split("-")),
            cleaned,
        )
        return (cleaned, cleaned != original)

    # ── State ─────────────────────────────────────────────────────────────────

    @staticmethod
    def state(raw: Optional[str]) -> Tuple[str, bool]:
        """
        Normalize state to a 2-letter uppercase abbreviation.
        Accepts full names ('California') and existing abbreviations ('ca').
        """
        if not raw or not str(raw).strip():
            return ("", False)
        original = str(raw).strip()
        upper = original.upper()
        # Already a valid 2-letter code
        if re.match(r"^[A-Z]{2}$", upper) and upper in STATE_MAP.values():
            return (upper, upper != original)
        # Try full-name lookup
        abbr = STATE_MAP.get(original.strip().lower())
        if abbr:
            return (abbr, True)
        return (original, False)

    # ── ZIP code ──────────────────────────────────────────────────────────────

    @staticmethod
    def zip_code(raw: Optional[str]) -> Tuple[str, bool]:
        """
        Normalize ZIP codes: strip leading/trailing whitespace, zero-pad
        5-digit ZIPs shorter than 5 digits (e.g. Excel strips leading zeros).
        """
        if not raw or not str(raw).strip():
            return ("", False)
        original = str(raw).strip()
        # Remove any accidental ".0" from Excel float conversion (e.g. "10001.0")
        cleaned = re.sub(r"\.0+$", "", original)
        digits_only = re.sub(r"[^\d-]", "", cleaned)
        # Zero-pad 5-digit ZIPs
        if re.match(r"^\d{1,5}$", digits_only):
            digits_only = digits_only.zfill(5)
        # Validate ZIP+4 format
        if re.match(r"^\d{5}(-\d{4})?$", digits_only):
            return (digits_only, digits_only != original)
        return (original, False)

    # ── Address ───────────────────────────────────────────────────────────────

    @staticmethod
    def address(raw: Optional[str]) -> Tuple[str, bool]:
        """
        Title-case and strip an address line.
        Expands common abbreviations (St → Street, etc.) for consistency.

        WHY ONLY BASIC NORMALIZATION HERE?
        Full USPS CASS address certification requires a licensed API. This
        handles the most common cosmetic issues without external dependencies.
        """
        ABBREVS = {
            r"\bSt\.?\b":  "Street",
            r"\bAve\.?\b": "Avenue",
            r"\bDr\.?\b":  "Drive",
            r"\bRd\.?\b":  "Road",
            r"\bBlvd\.?\b": "Boulevard",
            r"\bLn\.?\b":  "Lane",
            r"\bCt\.?\b":  "Court",
            r"\bPl\.?\b":  "Place",
            r"\bApt\.?\b": "Apt",
        }
        if not raw or not str(raw).strip():
            return ("", False)
        original = str(raw).strip()
        cleaned = re.sub(r"\s+", " ", original)
        cleaned = cleaned.title()
        for pattern, replacement in ABBREVS.items():
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        return (cleaned, cleaned != original)

    # ── Constituent ID ────────────────────────────────────────────────────────

    @staticmethod
    def constituent_id(raw: Optional[str]) -> Tuple[str, bool]:
        """Strip whitespace and uppercase constituent IDs."""
        if not raw or not str(raw).strip():
            return ("", False)
        original = str(raw).strip()
        cleaned = original.upper().strip()
        return (cleaned, cleaned != original)
