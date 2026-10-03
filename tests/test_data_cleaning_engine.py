"""
Unit Tests for DataCleaningEngine
==================================
Tests local deterministic cleaning, suffix stripping, discrepancy repair,
missing value imputation, and change set generation.
"""

import unittest
from core.data_cleaning_engine import DataCleaningEngine, _safe_float


class TestDataCleaningEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DataCleaningEngine()

    def test_safe_float(self):
        self.assertEqual(_safe_float("18F"), 18.0)
        self.assertEqual(_safe_float("40E"), 40.0)
        self.assertEqual(_safe_float("32*"), 32.0)
        self.assertEqual(_safe_float("85.5"), 85.5)
        self.assertIsNone(_safe_float("--"))
        self.assertIsNone(_safe_float("ABS"))
        self.assertIsNone(_safe_float(None))

    def test_clean_dataset_formats_and_imputations(self):
        raw_data = [
            {
                "Seat_No": "7001 ",
                "Student_Name": "/ SHINDE ANANYA R",
                "Gender": "",
                "Result_Status": "P",
                "SGPI": "8.50",
                "Total_Marks": "0",  # Missing grand total
                "CSC801_TH": "52",
                "CSC801_IA": "18",
                "CSC801_TOT": None,   # Missing TOT (should be 52 + 18 = 70)
                "CSC802_TH": "18F",   # Suffix F
                "CSC802_IA": "12",
                "CSC802_TOT": "30"
            },
            {
                "Seat_No": "7002",
                "Student_Name": "VERMA RAHUL S",
                "Gender": "M",
                "Result_Status": "",  # Missing result (should be deduced PASS)
                "SGPI": "7.20",
                "Total_Marks": "100", # Discrepancy: printed 100, but sum is 45+15+30 = 90
                "CSC801_TH": "45",
                "CSC801_IA": "15",
                "CSC801_TOT": "40",   # Discrepancy: printed 40, but 45 + 15 = 60
                "CSC802_TH": "25",
                "CSC802_IA": "5",
                "CSC802_TOT": "30"
            }
        ]

        res = self.engine.clean_dataset(raw_data)
        cleaned = res["cleaned_records"]
        changes = res["changes"]
        summary = res["summary"]

        self.assertEqual(summary["total_rows"], 2)
        self.assertGreater(summary["total_changes"], 0)

        # Check Row 0 Cleaned:
        # 1. Name cleaned & gender imputed from leading slash
        self.assertEqual(cleaned[0]["Student_Name"], "SHINDE ANANYA R")
        self.assertEqual(cleaned[0]["Gender"], "F")

        # 2. CSC801_TOT imputed (52 + 18 = 70)
        self.assertEqual(cleaned[0]["CSC801_TOT"], 70.0)

        # 3. CSC802_TH stripped 18F -> 18.0
        self.assertEqual(cleaned[0]["CSC802_TH"], 18.0)

        # 4. Total marks imputed: 70 + 30 = 100
        self.assertEqual(cleaned[0]["Total_Marks"], 100.0)

        # Check Row 1 Cleaned:
        # 1. Result status deduced from SGPI (7.20 -> PASS)
        self.assertEqual(cleaned[1]["Result_Status"], "PASS")

        # 2. CSC801_TOT discrepancy fixed (40 -> 60)
        self.assertEqual(cleaned[1]["CSC801_TOT"], 60.0)

        # 3. Grand total discrepancy fixed (100 -> 60 + 30 = 90)
        self.assertEqual(cleaned[1]["Total_Marks"], 90.0)

        # Verify Changes Audit Trail
        types = set(c["change_type"] for c in changes)
        self.assertIn("format", types)
        self.assertIn("missing_imputed", types)
        self.assertIn("discrepancy_fixed", types)

    def test_apply_and_discard_changes(self):
        raw = [{"Seat_No": "101", "Student_Name": "  ALICE  ", "Total_Marks": "0"}]
        res = self.engine.clean_dataset(raw)
        changes = res["changes"]

        # Accept all changes
        applied = self.engine.apply_changes(raw, changes)
        self.assertEqual(applied[0]["Student_Name"], "ALICE")

        # Discard / empty accepted set
        discarded = self.engine.apply_changes(raw, changes, accepted_ids=set())
        self.assertEqual(discarded[0]["Student_Name"], "  ALICE  ")


if __name__ == "__main__":
    unittest.main()
