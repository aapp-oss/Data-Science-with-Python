"""
generate_sample_data.py — Create realistic test datasets for development.

WHY GENERATE SYNTHETIC DATA?
Real constituent data is PII (personally identifiable information) and cannot
be shared in documentation, tests, or demos. Synthetic data lets you develop
and test against realistic-looking records without privacy concerns.

This generator intentionally creates:
- Clean records (should pass validation)
- Exact duplicates (same email or phone on two records)
- Fuzzy duplicates (same person, slightly different spellings)
- Records with formatting issues (phone not standardized)
- Records with hard errors (invalid email, impossible date)
- Records with missing required fields
"""

import random
import string
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import pandas as pd

FIRST_NAMES = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer",
    "Michael", "Linda", "William", "Barbara", "David", "Elizabeth",
    "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah",
    "Charles", "Karen", "Margaret", "Dorothy", "Lisa", "Nancy",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
    "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez",
    "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore",
    "Jackson", "Martin", "Lee", "Perez", "Thompson", "White",
]
STREETS = [
    "Main", "Oak", "Maple", "Cedar", "Elm", "Pine", "Washington",
    "Lake", "Hill", "Park", "River", "Sunset", "Highland", "Valley",
]
STREET_TYPES = ["Street", "Avenue", "Drive", "Road", "Lane", "Court", "Boulevard"]
CITIES = [
    ("New York",     "NY", "10001"),
    ("Los Angeles",  "CA", "90001"),
    ("Chicago",      "IL", "60601"),
    ("Houston",      "TX", "77001"),
    ("Phoenix",      "AZ", "85001"),
    ("Philadelphia", "PA", "19101"),
    ("San Antonio",  "TX", "78201"),
    ("San Diego",    "CA", "92101"),
    ("Dallas",       "TX", "75201"),
    ("Boston",       "MA", "02101"),
]
DOMAINS = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com"]
ORG_NAMES = [
    "Acme Foundation", "Green Future Corp", "Smith Family Trust",
    "Community Health Alliance", "United Way Local",
]


def random_date(start_year=2010, end_year=2024) -> str:
    start = datetime(start_year, 1, 1)
    end   = datetime(end_year, 12, 31)
    delta = end - start
    return (start + timedelta(days=random.randint(0, delta.days))).strftime("%Y-%m-%d")


def random_phone(formatted: bool = True) -> str:
    area = random.randint(200, 999)
    exch = random.randint(200, 999)
    line = random.randint(1000, 9999)
    if formatted:
        return f"({area}) {exch}-{line}"
    # Intentionally unformatted for testing
    styles = [
        f"{area}{exch}{line}",
        f"{area}-{exch}-{line}",
        f"{area}.{exch}.{line}",
        f"+1{area}{exch}{line}",
    ]
    return random.choice(styles)


def make_record(
    cid: str,
    first: str,
    last: str,
    city_info: tuple,
    gift: float,
    messy: bool = False,
    org: str = None,
) -> dict:
    city, state, zip_code = city_info
    email = f"{first.lower()}.{last.lower()}@{random.choice(DOMAINS)}"
    street_num = random.randint(1, 9999)
    street = f"{street_num} {random.choice(STREETS)} {random.choice(STREET_TYPES)}"

    if messy:
        # Introduce formatting issues
        phone = random_phone(formatted=False)
        state_raw = state.lower() if random.random() < 0.3 else state
        first_raw = first.upper() if random.random() < 0.4 else first.lower()
        last_raw  = last.upper()  if random.random() < 0.4 else last
        zip_raw   = str(int(zip_code))  # drops leading zero if present
    else:
        phone    = random_phone(formatted=True)
        state_raw = state
        first_raw = first
        last_raw  = last
        zip_raw   = zip_code

    return {
        "Constituent ID":   cid,
        "First Name":       first_raw,
        "Last Name":        last_raw,
        "Full Name":        f"{first_raw} {last_raw}",
        "Email Address":    email,
        "Phone Number":     phone,
        "Address Line 1":   street,
        "Address Line 2":   "",
        "City":             city,
        "State":            state_raw,
        "ZIP Code":         zip_raw,
        "Date Added":       random_date(),
        "Date Modified":    random_date(2020),
        "Organization Name": org or "",
        "Total Gift Amount": round(gift, 2),
    }


def generate_sample_data(
    n_clean: int = 80,
    n_exact_dupe: int = 5,
    n_fuzzy_dupe: int = 8,
    n_messy: int = 10,
    n_errors: int = 5,
    output_path: str = None,
) -> pd.DataFrame:
    """
    Generate a sample constituent dataset with known quality issues embedded.

    Parameters
    ----------
    n_clean       : Number of clean, valid records.
    n_exact_dupe  : Number of exact duplicate pairs to insert.
    n_fuzzy_dupe  : Number of fuzzy duplicate pairs to insert.
    n_messy       : Number of records with formatting issues.
    n_errors      : Number of records with hard errors.
    output_path   : If provided, save to this Excel file path.

    Returns a DataFrame you can pass directly to the pipeline.
    """
    records: List[dict] = []
    cid_counter = 1000

    def next_cid() -> str:
        nonlocal cid_counter
        cid_counter += 1
        return f"CID{cid_counter:06d}"

    # ── Clean records ─────────────────────────────────────────────────────────
    for _ in range(n_clean):
        first = random.choice(FIRST_NAMES)
        last  = random.choice(LAST_NAMES)
        city  = random.choice(CITIES)
        records.append(make_record(next_cid(), first, last, city, round(random.uniform(10, 5000), 2)))

    # ── Exact duplicates ──────────────────────────────────────────────────────
    # Take an existing clean record and insert a near-copy with same email
    for i in range(min(n_exact_dupe, len(records))):
        original = records[i].copy()
        original["Constituent ID"] = next_cid()  # Different CID but same email
        records.append(original)

    # ── Fuzzy duplicates ──────────────────────────────────────────────────────
    # Same person but with slight name/address variations
    TYPO_MAP = {"a": "e", "e": "a", "i": "l", "o": "0", "n": "m"}
    for i in range(min(n_fuzzy_dupe, n_clean)):
        original = records[i].copy()
        original["Constituent ID"] = next_cid()
        # Introduce a typo in last name
        last = original["Last Name"]
        if last:
            idx = random.randint(0, len(last) - 1)
            char = last[idx].lower()
            replacement = TYPO_MAP.get(char, char + "e")
            original["Last Name"] = last[:idx] + replacement + last[idx+1:]
        # Slightly vary address
        if original.get("Address Line 1"):
            original["Address Line 1"] = original["Address Line 1"].replace("Street", "St")
        # Use different but not exact email
        original["Email Address"] = ""  # blank email to avoid exact match
        records.append(original)

    # ── Messy formatting records ──────────────────────────────────────────────
    for _ in range(n_messy):
        first = random.choice(FIRST_NAMES)
        last  = random.choice(LAST_NAMES)
        city  = random.choice(CITIES)
        records.append(
            make_record(next_cid(), first, last, city, round(random.uniform(0, 1000), 2), messy=True)
        )

    # ── Hard error records ────────────────────────────────────────────────────
    # Invalid email
    for _ in range(max(1, n_errors // 3)):
        first = random.choice(FIRST_NAMES)
        last  = random.choice(LAST_NAMES)
        city  = random.choice(CITIES)
        r = make_record(next_cid(), first, last, city, 0)
        r["Email Address"] = f"{first.lower()}.{last.lower()}_at_gmail.com"  # missing @
        records.append(r)

    # Impossible date (year 1842)
    for _ in range(max(1, n_errors // 3)):
        first = random.choice(FIRST_NAMES)
        last  = random.choice(LAST_NAMES)
        city  = random.choice(CITIES)
        r = make_record(next_cid(), first, last, city, 0)
        r["Date Added"] = "1842-03-15"
        records.append(r)

    # Missing required field (blank last name)
    for _ in range(max(1, n_errors // 3)):
        first = random.choice(FIRST_NAMES)
        city  = random.choice(CITIES)
        r = make_record(next_cid(), first, "", city, 0)
        r["Last Name"] = ""
        r["Full Name"] = first
        records.append(r)

    # Org with inconsistent personal name (business rule violation)
    r = make_record(next_cid(), "John", "Smith", random.choice(CITIES), 500)
    r["Organization Name"] = random.choice(ORG_NAMES)
    records.append(r)

    # Duplicate constituent ID (force collision)
    collision_cid = "CID001001"
    for _ in range(2):
        first = random.choice(FIRST_NAMES)
        last  = random.choice(LAST_NAMES)
        city  = random.choice(CITIES)
        r = make_record(collision_cid, first, last, city, 100)
        records.append(r)

    random.shuffle(records)
    df = pd.DataFrame(records)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(output_path, index=False)
        print(f"Sample data saved to: {output_path} ({len(df)} records)")

    return df


if __name__ == "__main__":
    out = str(Path(__file__).parent / "sample_constituents.xlsx")
    df = generate_sample_data(
        n_clean=80, n_exact_dupe=5, n_fuzzy_dupe=8,
        n_messy=10, n_errors=5, output_path=out,
    )
    print(f"Generated {len(df)} total records.")
    print(f"Columns: {list(df.columns)}")
