"""
Unit Tests for Interactive Excel Grid Synchronization Endpoint
=============================================================
Verifies that editing, adding rows, adding columns, and saving datasets
via /api/dataset/sync properly persists CSV, Excel, and Catalog JSON files.
"""

import json
import os
import shutil
import tempfile
import unittest

from app import app, OUTPUT_DIR


class TestInteractiveExcelSync(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        self.test_folder = "test_interactive_sync_run"
        self.folder_path = os.path.join(OUTPUT_DIR, self.test_folder)

    def tearDown(self):
        if os.path.isdir(self.folder_path):
            shutil.rmtree(self.folder_path, ignore_errors=True)

    def test_sync_dataset_creates_csv_excel_and_catalog(self):
        sample_students = [
            {
                "Seat_No": "522001",
                "Name": "Alice Smith",
                "Candidate_Type": "Regular",
                "CSC501_TOT": "85",
                "SGPI": "9.20",
                "Result": "Successful",
                "Custom_Remarks": "Distinction candidate"
            },
            {
                "Seat_No": "522002",
                "Name": "Bob Jones",
                "Candidate_Type": "Regular",
                "CSC501_TOT": "42",
                "SGPI": "6.10",
                "Result": "Successful",
                "Custom_Remarks": "Cleared"
            }
        ]
        columns = ["Seat_No", "Name", "Candidate_Type", "CSC501_TOT", "SGPI", "Result", "Custom_Remarks"]

        response = self.app.post(
            "/api/dataset/sync",
            data=json.dumps({
                "folder_name": self.test_folder,
                "students": sample_students,
                "columns": columns,
                "catalog": {"exam_name": "Test Exam"}
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("count"), 2)
        self.assertEqual(data.get("columns_count"), 7)

        # Check CSV existence and content
        csv_file = os.path.join(self.folder_path, f"{self.test_folder}_extracted_data.csv")
        self.assertTrue(os.path.isfile(csv_file))
        with open(csv_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("522001", content)
            self.assertIn("Alice Smith", content)
            self.assertIn("Custom_Remarks", content)
            self.assertIn("Distinction candidate", content)

        # Check Excel existence
        excel_file = os.path.join(self.folder_path, f"{self.test_folder}_extracted_data.xlsx")
        self.assertTrue(os.path.isfile(excel_file))
        self.assertGreater(os.path.getsize(excel_file), 100)

        # Check Catalog JSON existence
        cat_file = os.path.join(self.folder_path, f"{self.test_folder}_catalog.json")
        self.assertTrue(os.path.isfile(cat_file))
        with open(cat_file, "r", encoding="utf-8") as f:
            cdata = json.load(f)
            self.assertEqual(cdata.get("exam_name"), "Test Exam")


if __name__ == "__main__":
    unittest.main()
