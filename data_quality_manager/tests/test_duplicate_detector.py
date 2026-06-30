"""
test_duplicate_detector.py — Unit tests for DuplicateDetector.

These tests verify:
1. Exact duplicates (shared email) are always found.
2. Fuzzy near-duplicates (same name, slight variation) are flagged when above threshold.
3. Clearly different records produce no false positives.
4. Confidence scores are in the valid range [0, 100].
"""

import sys
import os
import unittest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import Settings
from core.duplicate_detector import DuplicateDetector


def make_detector():
    return DuplicateDetector(Settings())


def make_df(rows: list) -> pd.DataFrame:
    defaults = {
        "constituent_id": "",
        "first_name": "",
        "last_name": "",
        "full_name": "",
        "email": "",
        "phone": "",
        "address1": "",
        "address2": "",
        "city": "",
        "state": "",
        "zip": "10001",
        "date_added": None,
        "date_modified": None,
        "org_name": "",
        "gift_total": None,
        "_row_index": 0,
    }
    full_rows = []
    for i, row in enumerate(rows):
        r = defaults.copy()
        r.update(row)
        r["_row_index"] = i
        full_rows.append(r)
    return pd.DataFrame(full_rows)


class TestExactDuplicateDetection(unittest.TestCase):

    def test_shared_email_detected(self):
        d = make_detector()
        df = make_df([
            {"constituent_id": "CID001", "first_name": "John", "last_name": "Smith",
             "email": "john.smith@gmail.com", "zip": "10001"},
            {"constituent_id": "CID002", "first_name": "Jon",  "last_name": "Smyth",
             "email": "john.smith@gmail.com", "zip": "10001"},  # same email → exact dupe
        ])
        pairs = d.detect_exact(df)
        self.assertGreater(len(pairs), 0)
        self.assertEqual(pairs[0].match_type, "exact")
        self.assertEqual(pairs[0].confidence_score, 100.0)

    def test_different_emails_not_exact(self):
        d = make_detector()
        df = make_df([
            {"constituent_id": "CID001", "email": "alice@gmail.com", "zip": "10001"},
            {"constituent_id": "CID002", "email": "bob@gmail.com",   "zip": "10001"},
        ])
        pairs = d.detect_exact(df)
        self.assertEqual(len(pairs), 0)

    def test_duplicate_constituent_id_detected(self):
        d = make_detector()
        df = make_df([
            {"constituent_id": "CID999", "email": "a@x.com", "zip": "10001"},
            {"constituent_id": "CID999", "email": "b@y.com", "zip": "10001"},
        ])
        pairs = d.detect_exact(df)
        self.assertGreater(len(pairs), 0)


class TestFuzzyDuplicateDetection(unittest.TestCase):

    def test_near_identical_names_detected(self):
        d = make_detector()
        df = make_df([
            {"first_name": "John", "last_name": "Smith",
             "address1": "123 Main Street", "zip": "10001"},
            {"first_name": "Jon",  "last_name": "Smyth",   # slight variations
             "address1": "123 Main Street", "zip": "10001"},
        ])
        pairs = d.detect_fuzzy(df)
        self.assertGreater(len(pairs), 0)
        for p in pairs:
            self.assertGreaterEqual(p.confidence_score, 0.0)
            self.assertLessEqual(p.confidence_score, 100.0)

    def test_very_different_records_not_flagged(self):
        d = make_detector()
        df = make_df([
            {"first_name": "Alice", "last_name": "Anderson",
             "address1": "999 Oak Lane", "zip": "10001"},
            {"first_name": "Xavier", "last_name": "Zimmerman",
             "address1": "1 Broadway", "zip": "10001"},
        ])
        pairs = d.detect_fuzzy(df)
        self.assertEqual(len(pairs), 0)

    def test_confidence_scores_in_range(self):
        d = make_detector()
        df = make_df([
            {"first_name": "Mary", "last_name": "Jones",
             "address1": "50 Park Avenue", "zip": "10002"},
            {"first_name": "Mary", "last_name": "Jones",
             "address1": "50 Park Avenue", "zip": "10002"},
        ])
        pairs = d.detect_fuzzy(df)
        for p in pairs:
            self.assertGreaterEqual(p.confidence_score, 0.0)
            self.assertLessEqual(p.confidence_score, 100.0)


class TestDetectAll(unittest.TestCase):

    def test_no_double_counting(self):
        """A pair detected as exact should not also appear in fuzzy results."""
        d = make_detector()
        df = make_df([
            {"constituent_id": "CID001", "first_name": "John", "last_name": "Smith",
             "email": "john@x.com", "address1": "1 Main St", "zip": "10001"},
            {"constituent_id": "CID002", "first_name": "John", "last_name": "Smith",
             "email": "john@x.com", "address1": "1 Main St", "zip": "10001"},
        ])
        pairs = d.detect_all(df)
        # Check no duplicate index pairs
        seen = set()
        for p in pairs:
            key = (min(p.record_a_index, p.record_b_index),
                   max(p.record_a_index, p.record_b_index))
            self.assertNotIn(key, seen)
            seen.add(key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
