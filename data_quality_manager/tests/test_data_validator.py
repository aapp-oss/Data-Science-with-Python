"""
test_data_validator.py — Unit tests for DataValidator.

We test DETECT mode only here (no audit trail side effects).
Each test creates a small DataFrame with a known issue, runs the validator,
and asserts that the expected issue is flagged with the right type and severity.
"""

import sys
import os
import unittest
import pandas as pd
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import Settings
from core.data_validator import DataValidator
from models.schemas import IssueType, IssueSeverity
from utils.audit_trail import AuditTrail


def make_validator():
    settings = Settings()
    audit = MagicMock(spec=AuditTrail)
    return DataValidator(settings, audit)


def minimal_df(**overrides) -> pd.DataFrame:
    """Return a single-row DataFrame with sane defaults, optionally overridden."""
    row = {
        "constituent_id": "CID001",
        "first_name": "John",
        "last_name": "Smith",
        "full_name": "John Smith",
        "email": "john.smith@gmail.com",
        "phone": "(555) 123-4567",
        "address1": "123 Main Street",
        "address2": "",
        "city": "New York",
        "state": "NY",
        "zip": "10001",
        "date_added": "2020-01-01",
        "date_modified": "2022-06-15",
        "org_name": "",
        "gift_total": "500.00",
    }
    row.update(overrides)
    return pd.DataFrame([row])


class TestMissingValueDetection(unittest.TestCase):

    def test_missing_last_name_flagged(self):
        v = make_validator()
        df = minimal_df(last_name="")
        issues = v.detect_issues(df)
        types = [i.issue_type for i in issues]
        self.assertIn(IssueType.MISSING_VALUE, types)

    def test_present_last_name_not_flagged(self):
        v = make_validator()
        df = minimal_df(last_name="Smith")
        issues = v.detect_issues(df)
        missing = [i for i in issues if i.issue_type == IssueType.MISSING_VALUE]
        self.assertEqual(len(missing), 0)


class TestEmailValidation(unittest.TestCase):

    def test_invalid_email_missing_at(self):
        v = make_validator()
        df = minimal_df(email="john.smith_at_gmail.com")
        issues = v.detect_issues(df)
        invalid = [i for i in issues if i.issue_type == IssueType.INVALID_EMAIL]
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0].severity, IssueSeverity.HIGH)

    def test_invalid_email_no_tld(self):
        v = make_validator()
        df = minimal_df(email="john@gmail")
        issues = v.detect_issues(df)
        invalid = [i for i in issues if i.issue_type == IssueType.INVALID_EMAIL]
        self.assertEqual(len(invalid), 1)

    def test_valid_email_not_flagged(self):
        v = make_validator()
        df = minimal_df(email="john.smith@gmail.com")
        issues = v.detect_issues(df)
        invalid = [i for i in issues if i.issue_type == IssueType.INVALID_EMAIL]
        self.assertEqual(len(invalid), 0)

    def test_blank_email_not_flagged_as_invalid(self):
        # A blank email is a MISSING_VALUE issue, not INVALID_EMAIL
        v = make_validator()
        df = minimal_df(email="")
        issues = v.detect_issues(df)
        invalid = [i for i in issues if i.issue_type == IssueType.INVALID_EMAIL]
        self.assertEqual(len(invalid), 0)


class TestPhoneValidation(unittest.TestCase):

    def test_unformatted_phone_flagged_for_standardization(self):
        v = make_validator()
        df = minimal_df(phone="5551234567")
        issues = v.detect_issues(df)
        std_issues = [i for i in issues if i.issue_type == IssueType.STANDARDIZATION
                      and i.field_name == "phone"]
        self.assertGreater(len(std_issues), 0)


class TestZipValidation(unittest.TestCase):

    def test_invalid_zip_flagged(self):
        v = make_validator()
        df = minimal_df(zip="ABCDE")
        issues = v.detect_issues(df)
        zip_issues = [i for i in issues if i.field_name == "zip"
                      and i.issue_type == IssueType.INVALID_FORMAT]
        self.assertEqual(len(zip_issues), 1)

    def test_valid_zip_not_flagged(self):
        v = make_validator()
        df = minimal_df(zip="10001")
        issues = v.detect_issues(df)
        zip_issues = [i for i in issues if i.field_name == "zip"
                      and i.issue_type == IssueType.INVALID_FORMAT]
        self.assertEqual(len(zip_issues), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
