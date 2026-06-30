"""
test_standardizers.py — Unit tests for every Standardizer method.

WHY UNIT TEST STANDARDIZERS?
Standardizers are pure functions (same input → same output, no side effects).
Pure functions are the easiest thing to test and the most important — they run
on every single field in every single record, so a subtle bug here multiplies
across 100,000 rows.

TESTING PHILOSOPHY:
Each test method follows Arrange-Act-Assert (AAA):
1. Arrange: set up input values
2. Act: call the function
3. Assert: verify the output

We test the BOUNDARY CONDITIONS (empty string, None, already-correct input)
as well as the COMMON CASES because boundary conditions are where bugs hide.
"""

import unittest
import sys
import os

# Allow imports from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.standardizers import Standardizer


class TestPhoneStandardizer(unittest.TestCase):

    def setUp(self):
        self.std = Standardizer()

    def test_already_formatted(self):
        result, changed = self.std.phone("(555) 867-5309")
        self.assertEqual(result, "(555) 867-5309")
        self.assertFalse(changed)

    def test_digits_only(self):
        result, changed = self.std.phone("5558675309")
        self.assertEqual(result, "(555) 867-5309")
        self.assertTrue(changed)

    def test_dots_format(self):
        result, changed = self.std.phone("555.867.5309")
        self.assertEqual(result, "(555) 867-5309")
        self.assertTrue(changed)

    def test_dashes_format(self):
        result, changed = self.std.phone("555-867-5309")
        self.assertEqual(result, "(555) 867-5309")
        self.assertTrue(changed)

    def test_blank_returns_empty(self):
        result, changed = self.std.phone("")
        self.assertEqual(result, "")
        self.assertFalse(changed)

    def test_none_returns_empty(self):
        result, changed = self.std.phone(None)
        self.assertEqual(result, "")
        self.assertFalse(changed)

    def test_invalid_phone_returns_original(self):
        # 7 digits — cannot be standardized
        result, changed = self.std.phone("5551234")
        self.assertFalse(changed)  # no valid standard form found


class TestEmailStandardizer(unittest.TestCase):

    def setUp(self):
        self.std = Standardizer()

    def test_lowercase(self):
        result, changed = self.std.email("John.Smith@GMAIL.COM")
        self.assertEqual(result, "john.smith@gmail.com")
        self.assertTrue(changed)

    def test_strip_whitespace(self):
        result, changed = self.std.email("  john@example.com  ")
        self.assertEqual(result, "john@example.com")
        self.assertTrue(changed)

    def test_already_clean(self):
        result, changed = self.std.email("john@example.com")
        self.assertEqual(result, "john@example.com")
        self.assertFalse(changed)

    def test_none(self):
        result, changed = self.std.email(None)
        self.assertEqual(result, "")
        self.assertFalse(changed)


class TestNameStandardizer(unittest.TestCase):

    def setUp(self):
        self.std = Standardizer()

    def test_all_caps(self):
        result, changed = self.std.name("JOHN SMITH")
        self.assertEqual(result, "John Smith")
        self.assertTrue(changed)

    def test_all_lower(self):
        result, changed = self.std.name("john smith")
        self.assertEqual(result, "John Smith")
        self.assertTrue(changed)

    def test_already_correct(self):
        result, changed = self.std.name("John Smith")
        self.assertEqual(result, "John Smith")
        self.assertFalse(changed)

    def test_hyphenated(self):
        result, changed = self.std.name("MARY-JANE WATSON")
        self.assertEqual(result, "Mary-Jane Watson")
        self.assertTrue(changed)

    def test_extra_spaces(self):
        result, changed = self.std.name("John   Smith")
        self.assertEqual(result, "John Smith")
        self.assertTrue(changed)

    def test_none(self):
        result, changed = self.std.name(None)
        self.assertEqual(result, "")
        self.assertFalse(changed)


class TestStateStandardizer(unittest.TestCase):

    def setUp(self):
        self.std = Standardizer()

    def test_full_name(self):
        result, changed = self.std.state("California")
        self.assertEqual(result, "CA")
        self.assertTrue(changed)

    def test_full_name_case_insensitive(self):
        result, changed = self.std.state("new york")
        self.assertEqual(result, "NY")
        self.assertTrue(changed)

    def test_already_abbreviated_lowercase(self):
        result, changed = self.std.state("ca")
        self.assertEqual(result, "CA")
        self.assertTrue(changed)

    def test_already_correct(self):
        result, changed = self.std.state("CA")
        self.assertEqual(result, "CA")
        self.assertFalse(changed)

    def test_none(self):
        result, changed = self.std.state(None)
        self.assertEqual(result, "")
        self.assertFalse(changed)


class TestZipStandardizer(unittest.TestCase):

    def setUp(self):
        self.std = Standardizer()

    def test_strip_excel_float(self):
        result, changed = self.std.zip_code("10001.0")
        self.assertEqual(result, "10001")
        self.assertTrue(changed)

    def test_zero_pad(self):
        result, changed = self.std.zip_code("1234")
        self.assertEqual(result, "01234")
        self.assertTrue(changed)

    def test_already_correct(self):
        result, changed = self.std.zip_code("10001")
        self.assertEqual(result, "10001")
        self.assertFalse(changed)

    def test_zip_plus_four(self):
        result, changed = self.std.zip_code("10001-1234")
        self.assertEqual(result, "10001-1234")

    def test_none(self):
        result, changed = self.std.zip_code(None)
        self.assertEqual(result, "")
        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
