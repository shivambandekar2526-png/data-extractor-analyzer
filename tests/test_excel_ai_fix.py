"""
Unit Tests for AI-Assisted Excel Fix & Restructuring Pipeline
=============================================================
Tests:
1. Multi-tier merged header resolution & data start detection on real Excel files.
2. Handling of banners, table artifacts, formula errors (#VALUE!, #REF!), and float IDs.
3. Automatic formula imputation (TOT = TH + IA, CG = Credits * GP, SGPI reconciliation).
4. Interactive grid AI Fix endpoint (/api/tabular/interactive-fix).
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.excel_csv_ingester import TabularIngester, _clean_json_val, _safe_str, _safe_float
from app import app


class TestExcelAiFix(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "output"))
        cls.ingester = TabularIngester(output_dir=cls.output_dir)
        cls.batch_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "Batch Data", "21-22 data"))
        cls.app_client = app.test_client()

    def test_formula_error_and_nan_cleaning(self):
        """Verify formula errors (#VALUE!, #REF!) and float .0 are sanitized."""
        self.assertEqual(_clean_json_val("#VALUE!"), "")
        self.assertEqual(_clean_json_val("#REF!"), "")
        self.assertEqual(_clean_json_val("#DIV/0!"), "")
        self.assertEqual(_clean_json_val("6015220.0"), "6015220")
        self.assertEqual(_clean_json_val(float("nan")), "")

        self.assertEqual(_safe_str("#VALUE!"), "")
        self.assertEqual(_safe_str("123.0"), "123")
        self.assertEqual(_safe_float("18F"), 18.0)
        self.assertEqual(_safe_float("40E"), 40.0)
        self.assertEqual(_safe_float("--"), None)

    def test_data_start_and_multi_tier_resolution(self):
        """Test detection on real multi-tier sheets."""
        files_to_check = {
            "2122-2nd.xlsx": 6,
            "2122-3rd.xlsx": 11,
            "2122-4th.xlsx": 8
        }
        for fname, expected_start in files_to_check.items():
            fpath = os.path.join(self.batch_dir, fname)
            if not os.path.isfile(fpath):
                continue

            sheets = self.ingester._get_excel_sheets(fpath)
            sheet = sheets[0] if sheets else None
            raw_rows = self.ingester._read_excel_raw(fpath, sheet, max_rows=None)
            self.assertGreater(len(raw_rows), 0)

            data_start = self.ingester._find_data_start_row(raw_rows)
            self.assertEqual(data_start, expected_start, f"Failed data start for {fname}")

            hmap = self.ingester._heuristic_structure_analysis(raw_rows, max(len(r) for r in raw_rows))
            col_map = hmap.get("column_mapping", {})
            self.assertIn("Seat_No", col_map.values())
            self.assertIn("Student_Name", col_map.values())

    def test_ai_fix_and_import_real_sheets(self):
        """Test end-to-end ai_fix_and_import on real marksheet."""
        fpath = os.path.join(self.batch_dir, "2122-3rd.xlsx")
        if not os.path.isfile(fpath):
            self.skipTest("2122-3rd.xlsx not found")

        res = self.ingester.ai_fix_and_import(fpath, pipeline_mode="local")
        self.assertTrue(res.get("success"))
        self.assertGreater(res.get("total_students", 0), 50)
        self.assertTrue(res.get("changes_count", 0) >= 0)

        students = res.get("students", [])
        self.assertGreater(len(students), 0)
        first_student = students[0]
        self.assertTrue(bool(first_student.get("Seat_No")))
        self.assertTrue(bool(first_student.get("Student_Name")))
        self.assertIn(first_student.get("Result_Status"), ("PASS", "FAIL", "ATKT"))

    def test_formula_auto_imputation(self):
        """Test auto-imputation of course TOT and CG in normalizer."""
        dirty_records = [
            {
                "Seat_No": "6015220.0",
                "Student_Name": "/SHARMA ANANYA",
                "CSC301_TH": "50",
                "CSC301_IA": "15",
                "CSC301_TOT": "",
                "CSC301_Credits": "3",
                "CSC301_GP": "9",
                "CSC301_CG": "",
                "SGPI": "0.00"
            }
        ]
        cleaned = self.ingester._normalize_student_records(dirty_records)
        self.assertEqual(len(cleaned), 1)
        rec = cleaned[0]

        # Seat_No trailing .0 stripped
        self.assertEqual(rec["Seat_No"], "6015220")
        # Name / prefix stripped and gender set to F
        self.assertEqual(rec["Student_Name"], "SHARMA ANANYA")
        self.assertEqual(rec["Gender"], "F")
        # TOT imputed: 50 + 15 = 65
        self.assertEqual(rec["CSC301_TOT"], 65)
        # CG imputed: 3 * 9 = 27
        self.assertEqual(rec["CSC301_CG"], 27)
        # SGPI calculated: 27 / 3 = 9.00
        self.assertEqual(rec["SGPI"], "9.00")
        self.assertEqual(rec["Result_Status"], "PASS")

    def test_interactive_fix_endpoint(self):
        """Test /api/tabular/interactive-fix endpoint."""
        payload = {
            "records": [
                {
                    "Seat_No": "101.0",
                    "Student_Name": "/PATIL PRIYA",
                    "CS401_TH": "45",
                    "CS401_IA": "18",
                    "CS401_TOT": "",
                    "CS401_Credits": "4",
                    "CS401_GP": "8",
                    "CS401_CG": "",
                    "SGPI": ""
                },
                {
                    "Seat_No": "102",
                    "Student_Name": "KUMAR RAHUL",
                    "CS401_TH": "15F",
                    "CS401_IA": "10",
                    "CS401_TOT": "",
                    "CS401_Credits": "4",
                    "CS401_GP": "0",
                    "CS401_CG": "",
                    "SGPI": ""
                }
            ],
            "folder_name": ""
        }
        resp = self.app_client.post(
            "/api/tabular/interactive-fix",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        records = data.get("records", [])
        self.assertEqual(len(records), 2)

        # Check Priya (pass)
        p1 = records[0]
        self.assertEqual(p1["Seat_No"], "101")
        self.assertEqual(p1["Student_Name"], "PATIL PRIYA")
        self.assertEqual(p1["Gender"], "F")
        self.assertEqual(p1["CS401_TOT"], 63)
        self.assertEqual(p1["CS401_CG"], 32)
        self.assertEqual(p1["Result_Status"], "PASS")

        # Check Rahul (fail)
        p2 = records[1]
        self.assertEqual(p2["CS401_TOT"], 25)
        self.assertEqual(p2["CS401_CG"], 0)
        self.assertEqual(p2["Result_Status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
