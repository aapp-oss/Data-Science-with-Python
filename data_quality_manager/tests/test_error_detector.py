"""
test_error_detector.py — Unit tests for ErrorDetector.

Tests confirm that each type of hard error is detected reliably and that
clean records produce no false positives.
"""

import sys
import os
import unittest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import Settings
from core.error_detector import ErrorDetector
from models.schemas import IssueType, IssueSeverity


def make_detector():
    return ErrorDetector(Settings())


def base_row(**overrides) -> dict:
    row = {
        "constituent_id": "CID001",
        "first_name": "Jane",
        "last_name": "Doe",
        "full_name": "Jane Doe",
        "email": "jane.doe@example.com",
        "phone": "(555) 111-2222",
        "address1": "456 Oak Avenue",
        "address2": "",
        "city": "Boston",
        "state": "MA",
        "zip": "02101",
        "date_added": "2018-03-10",
        "date_modified": "2023-09-01",
        "org_name": "",
        "gift_total": "1000.00",
    }
    row.update(overrides)
    return row


class TestInvalidEmailDetection(unittest.TestCase):

    def test_missing_at_symbol(self):
        d = make_detector()
        df = pd.DataFrame([base_row(email="jane.doe_at_example.com")])
        errors = d.detect_invalid_emails(df)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].error_type, IssueType.INVALID_EMAIL)

    def test_valid_email_no_error(self):
        d = make_detector()
        df = pd.DataFrame([base_row()])
        errors = d.detect_invalid_emails(df)
        self.assertEqual(len(errors), 0)

    def test_blank_email_no_error(self):
        d = make_detector()
        df = pd.DataFrame([base_row(email="")])
        errors = d.detect_invalid_emails(df)
        self.assertEqual(len(errors), 0)


class TestImpossibleDateDetection(unittest.TestCase):

    def test_year_before_1900(self):
        d = make_detector()
        df = pd.DataFrame([base_row(date_added=pd.Timestamp("1842-03-15"))])
        df["date_added"] = pd.to_datetime(df["date_added"])
        df["date_modified"] = pd.to_datetime(df["date_modified"])
        errors = d.detect_impossible_dates(df)
        date_errors = [e for e in errors if e.error_type == IssueType.INVALID_DATE]
        self.assertGreater(len(date_errors), 0)

    def test_valid_date_no_error(self):
        d = make_detector()
        df = pd.DataFrame([base_row()])
        df["date_added"] = pd.to_datetime(df["date_added"])
        df["date_modified"] = pd.to_datetime(df["date_modified"])
        errors = d.detect_impossible_dates(df)
        self.assertEqual(len(errors), 0)


class TestDuplicateIdDetection(unittest.TestCase):

    def test_duplicate_cid_detected(self):
        d = make_detector()
        rows = [
            base_row(constituent_id="CID999", first_name="Alice"),
            base_row(constituent_id="CID999", first_name="Bob"),
        ]
        df = pd.DataFrame(rows)
        errors = d.detect_duplicate_constituent_ids(df)
        self.assertEqual(len(errors), 2)
        for e in errors:
            self.assertEqual(e.severity, IssueSeverity.CRITICAL)
            self.assertEqual(e.error_type, IssueType.DUPLICATE_ID)

    def test_unique_ids_no_error(self):
        d = make_detector()
        rows = [base_row(constituent_id="CID001"), base_row(constituent_id="CID002")]
        df = pd.DataFrame(rows)
        errors = d.detect_duplicate_constituent_ids(df)
        self.assertEqual(len(errors), 0)


class TestInconsistentDataDetection(unittest.TestCase):

    def test_gift_without_date(self):
        d = make_detector()
        row = base_row(gift_total="500", date_added="")
        df = pd.DataFrame([row])
        df["date_added"] = pd.to_datetime(df["date_added"], errors="coerce")
        df["date_modified"] = pd.to_datetime(df["date_modified"], errors="coerce")
        errors = d.detect_inconsistent_donor_data(df)
        inconsistent = [e for e in errors if e.error_type == IssueType.INCONSISTENT_DATA]
        self.assertGreater(len(inconsistent), 0)

    def test_org_and_personal_name(self):
        d = make_detector()
        row = base_row(org_name="Acme Foundation", first_name="John", last_name="Smith")
        df = pd.DataFrame([row])
        df["date_added"] = pd.to_datetime(df["date_added"], errors="coerce")
        df["date_modified"] = pd.to_datetime(df["date_modified"], errors="coerce")
        errors = d.detect_inconsistent_donor_data(df)
        inconsistent = [e for e in errors if e.error_type == IssueType.INCONSISTENT_DATA]
        self.assertGreater(len(inconsistent), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
